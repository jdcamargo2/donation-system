"""
Verificacion operativa de protecciones PostgreSQL de SIGEDON.

PRE: Django apunta a la base que debe aceptar trafico o a un entorno
     restaurado aislado; el rol conectado debe ser el runtime previsto.
POST: sale 0 solo cuando backend, rol runtime, triggers append-only,
      constraints criticos y sondas de mutacion (con rollback) confirman
      el contrato. No repara, no altera esquema, no concede privilegios,
      no deja filas de sonda. Codigo distinto de 0 ante cualquier fallo.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError, connection, transaction
from django.utils import timezone

# ---------------------------------------------------------------------------
# Trusted repository constants (migrations 0018 / 0029 / 0006 / 0019 / 0027)
# ---------------------------------------------------------------------------

SCHEMA = 'public'

AUDITLOG_TABLE = 'operations_auditlog'
AUDITLOG_FUNCTION = 'operations_auditlog_reject_mutation'
AUDITLOG_TRIGGER = 'operations_auditlog_append_only'

EXPENSEREQUESTEVENT_TABLE = 'operations_expenserequestevent'
EXPENSEREQUESTEVENT_FUNCTION = 'operations_expenserequestevent_reject_mutation'
EXPENSEREQUESTEVENT_TRIGGER = 'operations_expenserequestevent_append_only'

# CREATE TRIGGER without ENABLE REPLICA/ALWAYS → tgenabled = 'O' (origin/local).
ACCEPTED_TRIGGER_ENABLED_STATES = frozenset({'O'})

PROBE_MARKER = 'postgres_security_probe'

# Privileges reported/checked for hardened append-only AuditLog
# (deploy/postgresql/harden_runtime_role.sql).
REPORTED_PRIVILEGES = (
    'SELECT',
    'INSERT',
    'UPDATE',
    'DELETE',
    'TRUNCATE',
    'REFERENCES',
    'TRIGGER',
)
REQUIRED_PRIVILEGES = ('SELECT', 'INSERT')
DANGEROUS_PRIVILEGES = (
    'UPDATE',
    'DELETE',
    'TRUNCATE',
    'REFERENCES',
    'TRIGGER',
)

# Privileges the runtime role still needs on ordinary operational tables.
RUNTIME_REQUIRED_TABLE_PRIVILEGES = ('SELECT', 'INSERT', 'UPDATE', 'DELETE')
RUNTIME_SMOKE_TABLE = 'operations_donation'

# Critical CHECK constraints whose absence would permit financial/audit corruption.
CRITICAL_CHECK_CONSTRAINTS = (
    # (table, constraint_name) — migration 0019
    ('operations_donation', 'operations_donation_currency_is_usd'),
    ('operations_expense', 'operations_expense_currency_is_usd'),
    # migration 0006
    ('operations_donation', 'operations_donation_amount_gt_zero'),
    ('operations_expense', 'operations_expense_amount_gt_zero'),
    ('operations_fundallocation', 'operations_allocation_amount_gt_zero'),
    # migration 0027 (reservation/amount integrity)
    ('operations_expenserequest', 'operations_expenserequest_requested_amount_gt_zero'),
    ('operations_expenserequest', 'operations_expenserequest_reserved_amount_gte_zero'),
    ('operations_expenserequest', 'operations_expenserequest_reserved_lte_requested'),
)

# Operational-code uniqueness (unique=True columns; names are backend-generated).
CRITICAL_UNIQUE_COLUMNS = (
    ('operations_project', 'code'),
    ('operations_donation', 'code'),
    ('operations_fundallocation', 'code'),
    ('operations_expense', 'code'),
    ('operations_expenserequest', 'code'),
)

UNSUPPORTED_BACKEND_MESSAGE = (
    'PostgreSQL security verification requires a PostgreSQL database.'
)


@dataclass(frozen=True)
class AppendOnlyTarget:
    label: str
    table: str
    function: str
    trigger: str
    harden_privileges: bool


APPEND_ONLY_TARGETS = (
    AppendOnlyTarget(
        label='AuditLog',
        table=AUDITLOG_TABLE,
        function=AUDITLOG_FUNCTION,
        trigger=AUDITLOG_TRIGGER,
        harden_privileges=True,
    ),
    AppendOnlyTarget(
        label='ExpenseRequestEvent',
        table=EXPENSEREQUESTEVENT_TABLE,
        function=EXPENSEREQUESTEVENT_FUNCTION,
        trigger=EXPENSEREQUESTEVENT_TRIGGER,
        harden_privileges=True,
    ),
)


@dataclass
class CategoryResult:
    name: str
    ok: bool
    failures: list[str] = field(default_factory=list)
    details: list[str] = field(default_factory=list)


def qualified(table: str) -> str:
    return f'{SCHEMA}.{table}'


# ---------------------------------------------------------------------------
# Catalog helpers (parameterized; identifiers from trusted constants only)
# ---------------------------------------------------------------------------

def function_exists(cursor, function_name: str, schema: str = SCHEMA) -> bool:
    """
    PRE: function_name is a trusted repository constant.
    POST: True iff a non-aggregate function with that name exists in schema.
    """
    cursor.execute(
        """
        SELECT EXISTS (
            SELECT 1
            FROM pg_proc p
            JOIN pg_namespace n ON n.oid = p.pronamespace
            WHERE n.nspname = %s
              AND p.proname = %s
              AND p.prokind = 'f'
        );
        """,
        [schema, function_name],
    )
    return bool(cursor.fetchone()[0])


def get_trigger_state(
    cursor,
    *,
    table: str,
    trigger_name: str,
    function_name: str,
    schema: str = SCHEMA,
) -> dict | None:
    """
    PRE: table/trigger/function names are trusted repository constants.
    POST: returns {exists, enabled, function_matches, tgenabled} or None if absent.
    """
    cursor.execute(
        """
        SELECT t.tgenabled, p.proname
        FROM pg_trigger t
        JOIN pg_class c ON c.oid = t.tgrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_proc p ON p.oid = t.tgfoid
        WHERE n.nspname = %s
          AND c.relname = %s
          AND t.tgname = %s
          AND NOT t.tgisinternal;
        """,
        [schema, table, trigger_name],
    )
    row = cursor.fetchone()
    if row is None:
        return None
    tgenabled, proname = row[0], row[1]
    return {
        'exists': True,
        'tgenabled': tgenabled,
        'enabled': tgenabled in ACCEPTED_TRIGGER_ENABLED_STATES,
        'function_matches': proname == function_name,
        'function_name': proname,
    }


def constraint_exists(
    cursor,
    *,
    table: str,
    constraint_name: str,
    expected_type: str = 'c',
    schema: str = SCHEMA,
) -> dict | None:
    """
    PRE: table/constraint names are trusted; expected_type is a pg_constraint.contype.
    POST: returns {exists, contype, convalidated} or None if missing.
    """
    cursor.execute(
        """
        SELECT c.contype, c.convalidated
        FROM pg_constraint c
        JOIN pg_class rel ON rel.oid = c.conrelid
        JOIN pg_namespace n ON n.oid = rel.relnamespace
        WHERE n.nspname = %s
          AND rel.relname = %s
          AND c.conname = %s;
        """,
        [schema, table, constraint_name],
    )
    row = cursor.fetchone()
    if row is None:
        return None
    contype, convalidated = row[0], row[1]
    return {
        'exists': True,
        'contype': contype,
        'convalidated': bool(convalidated),
        'type_matches': contype == expected_type,
    }


def unique_column_constraint_exists(
    cursor,
    *,
    table: str,
    column: str,
    schema: str = SCHEMA,
) -> bool:
    """
    PRE: table/column are trusted repository constants.
    POST: True iff a single-column UNIQUE constraint covers the column.
    """
    cursor.execute(
        """
        SELECT EXISTS (
            SELECT 1
            FROM pg_constraint c
            JOIN pg_class rel ON rel.oid = c.conrelid
            JOIN pg_namespace n ON n.oid = rel.relnamespace
            JOIN pg_attribute a
              ON a.attrelid = c.conrelid
             AND a.attnum = c.conkey[1]
             AND NOT a.attisdropped
            WHERE n.nspname = %s
              AND rel.relname = %s
              AND c.contype = 'u'
              AND a.attname = %s
              AND array_length(c.conkey, 1) = 1
        );
        """,
        [schema, table, column],
    )
    return bool(cursor.fetchone()[0])


def current_role_info(cursor) -> dict:
    """
    PRE: connected to PostgreSQL.
    POST: returns coarse role posture (no passwords/DSN).
    """
    cursor.execute(
        """
        SELECT
            current_user,
            session_user,
            current_setting('session_replication_role', true),
            r.rolsuper,
            r.rolreplication,
            r.rolinherit
        FROM pg_roles r
        WHERE r.rolname = current_user;
        """
    )
    row = cursor.fetchone()
    if row is None:
        return {
            'current_user': None,
            'session_user': None,
            'session_replication_role': None,
            'is_superuser': True,
            'has_replication': True,
            'rolinherit': True,
        }
    return {
        'current_user': row[0],
        'session_user': row[1],
        'session_replication_role': row[2] or 'origin',
        'is_superuser': bool(row[3]),
        'has_replication': bool(row[4]),
        'rolinherit': bool(row[5]),
    }


def table_owner(cursor, table: str, schema: str = SCHEMA) -> str | None:
    cursor.execute(
        """
        SELECT pg_get_userbyid(c.relowner)
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = %s AND c.relname = %s;
        """,
        [schema, table],
    )
    row = cursor.fetchone()
    return row[0] if row else None


def has_table_privilege(
    cursor,
    *,
    role: str,
    table: str,
    privilege: str,
    schema: str = SCHEMA,
) -> bool:
    cursor.execute(
        'SELECT has_table_privilege(%s, %s, %s);',
        [role, f'{schema}.{table}', privilege],
    )
    return bool(cursor.fetchone()[0])


def can_disable_trigger(cursor, role_info: dict, owner: str | None) -> bool:
    """
    PRE: role_info from current_role_info; owner is table owner or None.
    POST: True when the current role can alter/disable triggers (owner/superuser).
    """
    if role_info['is_superuser']:
        return True
    current = role_info['current_user']
    return bool(owner and current and owner == current)


# ---------------------------------------------------------------------------
# Command
# ---------------------------------------------------------------------------

class Command(BaseCommand):
    help = (
        'Verificacion de protecciones PostgreSQL de SIGEDON (append-only, '
        'constraints financieros criticos y postura del rol runtime). '
        'Requiere PostgreSQL y credenciales del rol runtime final. '
        'Las sondas de mutacion usan transacciones con rollback garantizado. '
        'No repara, no altera esquema ni privilegios. Codigo 0 solo si el '
        'contrato esta activo; distinto de 0 en cualquier fallo. '
        'Ejemplo: python manage.py verify_postgres_security'
    )

    def handle(self, *args, **options):
        """
        PRE: connection points at the database under verification.
        POST: prints a concise category summary; raises CommandError on any
        failed category. Never prints DSN, passwords, hosts, or row values.
        """
        verbosity = options.get('verbosity', 1)

        if connection.vendor != 'postgresql':
            raise CommandError(UNSUPPORTED_BACKEND_MESSAGE)

        results: list[CategoryResult] = []
        try:
            results.append(self._verify_backend())
            results.append(self._verify_runtime_role(verbosity=verbosity))
            results.append(self._verify_append_only('AuditLog', verbosity=verbosity))
            results.append(
                self._verify_append_only('ExpenseRequestEvent', verbosity=verbosity)
            )
            results.append(self._verify_critical_constraints(verbosity=verbosity))
        except CommandError:
            raise
        except DatabaseError as exc:
            raise CommandError(
                'Verification probe error while inspecting PostgreSQL catalogs.'
            ) from exc

        self._print_summary(results, verbosity=verbosity)

        failed = [r for r in results if not r.ok]
        if failed:
            categories = ', '.join(r.name for r in failed)
            raise CommandError(
                f'PostgreSQL security verification failed: {categories}.'
            )

        if verbosity >= 1:
            self.stdout.write(
                self.style.SUCCESS('PostgreSQL security verification: all categories ok.')
            )

    # -- category runners ---------------------------------------------------

    def _verify_backend(self) -> CategoryResult:
        return CategoryResult(name='backend', ok=True, details=['postgresql'])

    def _verify_runtime_role(self, *, verbosity: int) -> CategoryResult:
        failures: list[str] = []
        details: list[str] = []
        with connection.cursor() as cursor:
            role = current_role_info(cursor)
            if role['is_superuser']:
                failures.append(
                    'Current database role is too privileged for runtime verification.'
                )
            if role['has_replication']:
                failures.append(
                    'Current database role has replication privilege forbidden for runtime.'
                )
            replication_role = (role['session_replication_role'] or '').lower()
            if replication_role != 'origin':
                failures.append(
                    'Protected trigger is not active for normal application writes '
                    '(session_replication_role is not origin).'
                )

            owned = []
            for target in APPEND_ONLY_TARGETS:
                owner = table_owner(cursor, target.table)
                if owner and owner == role['current_user']:
                    owned.append(target.label)
                    if can_disable_trigger(cursor, role, owner):
                        failures.append(
                            f'Current database role can alter/disable protected '
                            f'triggers on {target.label}.'
                        )
            if owned:
                failures.append(
                    'Current database role owns protected tables; '
                    'owner-role success is not a valid runtime verification.'
                )

            # Hardened append-only privilege contracts (AuditLog + ExpenseRequestEvent).
            role_name = role['current_user']
            for target in APPEND_ONLY_TARGETS:
                if not target.harden_privileges:
                    continue
                privileges = {
                    priv: has_table_privilege(
                        cursor, role=role_name, table=target.table, privilege=priv
                    )
                    for priv in REPORTED_PRIVILEGES
                }
                for priv in REQUIRED_PRIVILEGES:
                    if not privileges.get(priv):
                        failures.append(
                            f'Runtime role lacks required {priv} on append-only '
                            f'{target.label}.'
                        )
                for priv in DANGEROUS_PRIVILEGES:
                    if privileges.get(priv):
                        failures.append(
                            f'Runtime role has excessive {priv} on append-only '
                            f'{target.label}.'
                        )

            # Ordinary operational privileges still required for app runtime.
            for priv in RUNTIME_REQUIRED_TABLE_PRIVILEGES:
                if not has_table_privilege(
                    cursor,
                    role=role_name,
                    table=RUNTIME_SMOKE_TABLE,
                    privilege=priv,
                ):
                    failures.append(
                        f'Runtime role lacks required application privilege {priv} '
                        f'on operational tables.'
                    )

            if verbosity >= 2:
                details.append('role posture inspected via pg_roles / has_table_privilege')
                details.append(
                    f"session_replication_role={replication_role or 'unknown'}"
                )

        return CategoryResult(
            name='runtime role',
            ok=not failures,
            failures=failures,
            details=details,
        )

    def _verify_append_only(self, label: str, *, verbosity: int) -> CategoryResult:
        target = next(t for t in APPEND_ONLY_TARGETS if t.label == label)
        failures: list[str] = []
        details: list[str] = []
        category = f'{label} append-only'

        with connection.cursor() as cursor:
            if not function_exists(cursor, target.function):
                failures.append(f'Missing trigger function for {label}.')
            state = get_trigger_state(
                cursor,
                table=target.table,
                trigger_name=target.trigger,
                function_name=target.function,
            )
            if state is None:
                failures.append(f'Missing trigger for {label}.')
            else:
                if not state['enabled']:
                    failures.append(
                        f'Protected trigger is not active for normal application '
                        f'writes ({label}).'
                    )
                if not state['function_matches']:
                    failures.append(
                        f'Trigger for {label} does not invoke the expected function.'
                    )
                if verbosity >= 2:
                    details.append(
                        f"trigger={target.trigger} tgenabled={state['tgenabled']}"
                    )
                    details.append(f"function={state['function_name']}")

        if not failures:
            probe_failures = self._probe_append_only_mutations(target)
            failures.extend(probe_failures)

        return CategoryResult(
            name=category,
            ok=not failures,
            failures=failures,
            details=details,
        )

    def _verify_critical_constraints(self, *, verbosity: int) -> CategoryResult:
        failures: list[str] = []
        details: list[str] = []
        with connection.cursor() as cursor:
            for table, name in CRITICAL_CHECK_CONSTRAINTS:
                info = constraint_exists(
                    cursor, table=table, constraint_name=name, expected_type='c'
                )
                if info is None:
                    failures.append(f'Missing critical constraint: {name}.')
                    continue
                if not info['type_matches']:
                    failures.append(
                        f'Critical constraint type mismatch: {name}.'
                    )
                if not info['convalidated']:
                    failures.append(
                        f'Unvalidated critical constraint: {name}.'
                    )
                if verbosity >= 2:
                    details.append(f'check ok: {name}')

            for table, column in CRITICAL_UNIQUE_COLUMNS:
                if not unique_column_constraint_exists(
                    cursor, table=table, column=column
                ):
                    failures.append(
                        f'Missing operational-code uniqueness on {table}.{column}.'
                    )
                elif verbosity >= 2:
                    details.append(f'unique ok: {table}.{column}')

        return CategoryResult(
            name='critical constraints',
            ok=not failures,
            failures=failures,
            details=details,
        )

    # -- mutation probes (rollback-only) ------------------------------------

    def _probe_append_only_mutations(self, target: AppendOnlyTarget) -> list[str]:
        """
        PRE: catalog checks for target already passed.
        POST: proves UPDATE and DELETE fail against a temporary row; all probe
        rows are removed via savepoint rollback (no set_rollback, so nested
        TestCase transactions are not poisoned).
        """
        failures: list[str] = []
        try:
            with transaction.atomic():
                outer_sid = transaction.savepoint()
                try:
                    if target.label == 'AuditLog':
                        probe_pk, original_value = self._insert_auditlog_probe()
                        update_sql = (
                            f'UPDATE {qualified(target.table)} '
                            f'SET summary = %s WHERE id = %s'
                        )
                        update_params = [f'{PROBE_MARKER}-mutated', probe_pk]
                        select_sql = (
                            f'SELECT summary FROM {qualified(target.table)} '
                            f'WHERE id = %s'
                        )
                    else:
                        probe_pk, original_value = (
                            self._insert_expense_request_event_probe()
                        )
                        update_sql = (
                            f'UPDATE {qualified(target.table)} '
                            f'SET reason = %s WHERE id = %s'
                        )
                        update_params = [f'{PROBE_MARKER}-mutated', probe_pk]
                        select_sql = (
                            f'SELECT reason FROM {qualified(target.table)} '
                            f'WHERE id = %s'
                        )

                    sid = transaction.savepoint()
                    try:
                        with connection.cursor() as cursor:
                            cursor.execute(update_sql, update_params)
                        transaction.savepoint_rollback(sid)
                        failures.append(
                            f'Protected mutation unexpectedly succeeds '
                            f'(UPDATE on {target.label}).'
                        )
                    except DatabaseError:
                        transaction.savepoint_rollback(sid)

                    with connection.cursor() as cursor:
                        cursor.execute(select_sql, [probe_pk])
                        row = cursor.fetchone()
                    if row is None or row[0] != original_value:
                        failures.append(
                            f'Probe row changed after rejected UPDATE on '
                            f'{target.label}.'
                        )

                    sid = transaction.savepoint()
                    try:
                        with connection.cursor() as cursor:
                            cursor.execute(
                                f'DELETE FROM {qualified(target.table)} '
                                f'WHERE id = %s',
                                [probe_pk],
                            )
                        transaction.savepoint_rollback(sid)
                        failures.append(
                            f'Protected mutation unexpectedly succeeds '
                            f'(DELETE on {target.label}).'
                        )
                    except DatabaseError:
                        transaction.savepoint_rollback(sid)

                    with connection.cursor() as cursor:
                        cursor.execute(
                            f'SELECT 1 FROM {qualified(target.table)} '
                            f'WHERE id = %s',
                            [probe_pk],
                        )
                        if cursor.fetchone() is None:
                            failures.append(
                                f'Probe row missing after rejected DELETE on '
                                f'{target.label}.'
                            )
                finally:
                    transaction.savepoint_rollback(outer_sid)
        except DatabaseError:
            failures.append(
                f'Verification probe error while testing {target.label} append-only.'
            )
        return failures

    def _insert_auditlog_probe(self) -> tuple[int, str]:
        from apps.operations.models import AuditLog

        # Direct ORM insert is the legitimate append path; fields are synthetic.
        event = AuditLog.objects.create(
            action=AuditLog.Action.CREATED,
            model_name=PROBE_MARKER,
            entity_id=PROBE_MARKER,
            entity_label=PROBE_MARKER,
            summary=PROBE_MARKER,
        )
        return event.pk, PROBE_MARKER

    def _insert_expense_request_event_probe(self) -> tuple[int, str]:
        """
        PRE: outer atomic transaction will roll back.
        POST: inserts one ExpenseRequestEvent via the legitimate INSERT path
        using a minimal temporary graph with explicit operational codes
        (table-row sequence counters roll back with the transaction).
        """
        from django.contrib.auth import get_user_model

        from apps.operations.models import (
            Donation,
            ExpenseRequest,
            ExpenseRequestEvent,
            FundAllocation,
            Institution,
            Project,
        )

        suffix = timezone.now().strftime('%Y%m%d%H%M%S%f')
        token = suffix[-8:]
        actor = get_user_model().objects.create_user(
            username=f'{PROBE_MARKER}-{token}',
            password='!',
        )
        institution = Institution.objects.create(
            name=f'{PROBE_MARKER}-{token}',
            role=Institution.Role.DONOR,
            institution_type='foundation',
            country='VE',
        )
        project = Project.objects.create(
            code=f'PRJ-P{token}',
            name=f'{PROBE_MARKER}-{token}',
            estimated_budget=Decimal('100.00'),
        )
        donation = Donation.objects.create(
            code=f'DON-P{token}',
            donor=institution,
            amount=Decimal('100.00'),
            currency='USD',
            objective=PROBE_MARKER,
            status=Donation.Status.RECEIVED,
        )
        allocation = FundAllocation.objects.create(
            code=f'ASG-P{token}',
            donation=donation,
            project=project,
            budget_category='health_psychosocial',
            amount=Decimal('50.00'),
            allocation_date=timezone.localdate(),
            status=FundAllocation.Status.ACTIVE,
        )
        request = ExpenseRequest.objects.create(
            code=f'SGS-P{token}',
            fund_allocation=allocation,
            requested_by=actor,
            requested_amount=Decimal('10.00'),
            purpose=PROBE_MARKER,
            requested_date=timezone.localdate(),
            status=ExpenseRequest.Status.PENDING_DECISION,
        )
        event = ExpenseRequestEvent.objects.create(
            expense_request=request,
            event_type=ExpenseRequestEvent.EventType.CREATED,
            actor=actor,
            from_status='',
            to_status=request.status,
            requested_amount=request.requested_amount,
            allocation_balance_before=Decimal('50.00'),
            allocation_balance_after=Decimal('50.00'),
            reason='',
            metadata={'source': PROBE_MARKER},
        )
        return event.pk, ''


    def _print_summary(self, results: list[CategoryResult], *, verbosity: int):
        self.stdout.write('PostgreSQL security verification:')
        for result in results:
            status = 'ok' if result.ok else 'FAILED'
            style = self.style.SUCCESS if result.ok else self.style.ERROR
            self.stdout.write(style(f'  {result.name}: {status}'))
            if not result.ok and verbosity >= 1:
                for failure in result.failures:
                    self.stdout.write(f'    - {failure}')
            if verbosity >= 2:
                for detail in result.details:
                    self.stdout.write(f'    · {detail}')
