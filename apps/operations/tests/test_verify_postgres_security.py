"""
PostgreSQL integration tests for verify_postgres_security.

Catalog/object existence and mutation probes run against the real migrated
test database. Runtime-role privilege separation is mocked: the disposable
test DB typically connects as table owner/superuser, which must NOT be
classified as a successful runtime-role verification. Real role separation
remains a staging/infrastructure responsibility.
"""

from io import StringIO
from unittest import skipUnless
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import TestCase, TransactionTestCase

from apps.operations.management.commands.verify_postgres_security import (
    APPEND_ONLY_TARGETS,
    AUDITLOG_FUNCTION,
    AUDITLOG_TABLE,
    AUDITLOG_TRIGGER,
    CRITICAL_CHECK_CONSTRAINTS,
    CRITICAL_UNIQUE_COLUMNS,
    CategoryResult,
    Command,
    EXPENSEREQUESTEVENT_FUNCTION,
    EXPENSEREQUESTEVENT_TABLE,
    EXPENSEREQUESTEVENT_TRIGGER,
    PROBE_MARKER,
    constraint_exists,
    function_exists,
    get_trigger_state,
    unique_column_constraint_exists,
)
from apps.operations.models import AuditLog, ExpenseRequestEvent
from apps.operations.tests.helpers import create_user

POSTGRESQL_REQUIRED = 'Requires PostgreSQL for security verification.'
COMMAND_MODULE = 'apps.operations.management.commands.verify_postgres_security'


def _runtime_role_ok(**_kwargs):
    return CategoryResult(name='runtime role', ok=True, details=['mocked'])


@skipUnless(connection.vendor == 'postgresql', POSTGRESQL_REQUIRED)
class VerifyPostgresSecurityCatalogTests(TestCase):
    """
    PRE: migrations through 0029+ applied on PostgreSQL.
    POST: required functions, triggers, and critical constraints are present.
    Runtime-role checks are intentionally out of scope here.
    """

    def test_auditlog_function_and_enabled_trigger_exist(self):
        with connection.cursor() as cursor:
            self.assertTrue(function_exists(cursor, AUDITLOG_FUNCTION))
            state = get_trigger_state(
                cursor,
                table=AUDITLOG_TABLE,
                trigger_name=AUDITLOG_TRIGGER,
                function_name=AUDITLOG_FUNCTION,
            )
        self.assertIsNotNone(state)
        self.assertTrue(state['exists'])
        self.assertTrue(state['enabled'])
        self.assertEqual(state['tgenabled'], 'O')
        self.assertTrue(state['function_matches'])

    def test_expense_request_event_function_and_enabled_trigger_exist(self):
        with connection.cursor() as cursor:
            self.assertTrue(function_exists(cursor, EXPENSEREQUESTEVENT_FUNCTION))
            state = get_trigger_state(
                cursor,
                table=EXPENSEREQUESTEVENT_TABLE,
                trigger_name=EXPENSEREQUESTEVENT_TRIGGER,
                function_name=EXPENSEREQUESTEVENT_FUNCTION,
            )
        self.assertIsNotNone(state)
        self.assertTrue(state['exists'])
        self.assertTrue(state['enabled'])
        self.assertEqual(state['tgenabled'], 'O')
        self.assertTrue(state['function_matches'])

    def test_critical_check_constraints_exist_and_validated(self):
        with connection.cursor() as cursor:
            for table, name in CRITICAL_CHECK_CONSTRAINTS:
                info = constraint_exists(
                    cursor, table=table, constraint_name=name, expected_type='c'
                )
                self.assertIsNotNone(info, msg=name)
                self.assertTrue(info['type_matches'], msg=name)
                self.assertTrue(info['convalidated'], msg=name)

    def test_operational_code_uniqueness_constraints_exist(self):
        with connection.cursor() as cursor:
            for table, column in CRITICAL_UNIQUE_COLUMNS:
                self.assertTrue(
                    unique_column_constraint_exists(
                        cursor, table=table, column=column
                    ),
                    msg=f'{table}.{column}',
                )

    def test_command_succeeds_with_mocked_runtime_role(self):
        """
        Catalog + probes succeed on a fully migrated DB. Runtime-role posture
        is mocked because the test connection is typically the table owner.
        """
        before_audit = AuditLog.objects.count()
        before_events = ExpenseRequestEvent.objects.count()
        out = StringIO()

        with patch.object(Command, '_verify_runtime_role', side_effect=_runtime_role_ok):
            call_command('verify_postgres_security', stdout=out)

        output = out.getvalue()
        self.assertIn('PostgreSQL security verification:', output)
        self.assertIn('backend: ok', output)
        self.assertIn('AuditLog append-only: ok', output)
        self.assertIn('ExpenseRequestEvent append-only: ok', output)
        self.assertIn('critical constraints: ok', output)
        self.assertIn('all categories ok', output)
        self.assertNotIn('PASSWORD', output)
        self.assertNotIn('postgres://', output)
        self.assertEqual(AuditLog.objects.count(), before_audit)
        self.assertEqual(ExpenseRequestEvent.objects.count(), before_events)
        self.assertFalse(
            AuditLog.objects.filter(model_name=PROBE_MARKER).exists()
        )
        self.assertFalse(
            ExpenseRequestEvent.objects.filter(
                metadata__source=PROBE_MARKER
            ).exists()
        )

    def test_command_fails_when_catalog_check_mocked_missing(self):
        before_audit = AuditLog.objects.count()
        out = StringIO()

        with patch.object(Command, '_verify_runtime_role', side_effect=_runtime_role_ok):
            with patch(
                f'{COMMAND_MODULE}.function_exists',
                return_value=False,
            ):
                with self.assertRaises(CommandError) as raised:
                    call_command('verify_postgres_security', stdout=out)

        self.assertIn('append-only', str(raised.exception))
        self.assertNotIn('all categories ok', out.getvalue())
        self.assertEqual(AuditLog.objects.count(), before_audit)

    def test_owner_runtime_role_is_not_classified_success(self):
        """
        When the real connection is the table owner, full command must fail
        the runtime-role category (owner success is not valid verification).
        """
        with connection.cursor() as cursor:
            cursor.execute('SELECT current_user;')
            current = cursor.fetchone()[0]
            cursor.execute(
                'SELECT pg_get_userbyid(relowner) FROM pg_class c '
                'JOIN pg_namespace n ON n.oid = c.relnamespace '
                'WHERE n.nspname = %s AND c.relname = %s;',
                ['public', AUDITLOG_TABLE],
            )
            owner = cursor.fetchone()[0]

        if current != owner:
            self.skipTest('Test DB role is not table owner; owner-path N/A.')

        out = StringIO()
        with self.assertRaises(CommandError) as raised:
            call_command('verify_postgres_security', stdout=out)

        self.assertIn('runtime role', str(raised.exception))
        self.assertIn('runtime role: FAILED', out.getvalue())


@skipUnless(connection.vendor == 'postgresql', POSTGRESQL_REQUIRED)
class VerifyPostgresSecurityMutationProbeTests(TestCase):
    """
    PRE: append-only triggers installed.
    POST: command probes reject UPDATE/DELETE and leave no persistent rows.
    """

    def test_auditlog_mutation_probe_rolls_back(self):
        before = AuditLog.objects.count()
        create_user(username='probe-baseline-user')
        baseline_count = AuditLog.objects.count()

        command = Command()
        target = next(t for t in APPEND_ONLY_TARGETS if t.label == 'AuditLog')
        failures = command._probe_append_only_mutations(target)

        self.assertEqual(failures, [])
        self.assertEqual(AuditLog.objects.count(), baseline_count)
        self.assertFalse(
            AuditLog.objects.filter(entity_id=PROBE_MARKER).exists()
        )
        self.assertGreaterEqual(baseline_count, before)

    def test_expense_request_event_mutation_probe_rolls_back(self):
        before = ExpenseRequestEvent.objects.count()
        command = Command()
        target = next(
            t for t in APPEND_ONLY_TARGETS if t.label == 'ExpenseRequestEvent'
        )
        failures = command._probe_append_only_mutations(target)

        self.assertEqual(failures, [])
        self.assertEqual(ExpenseRequestEvent.objects.count(), before)
        self.assertFalse(
            ExpenseRequestEvent.objects.filter(
                metadata__source=PROBE_MARKER
            ).exists()
        )


@skipUnless(connection.vendor == 'postgresql', POSTGRESQL_REQUIRED)
class VerifyPostgresSecurityProbeIsolationTests(TransactionTestCase):
    """
    PRE: PostgreSQL with triggers.
    POST: probes do not alter pre-existing append-only rows.
    """

    def test_existing_auditlog_row_unchanged_by_command(self):
        from apps.operations.services import log_action
        from apps.operations.tests.helpers import create_project

        user = create_user(username='verify-pg-existing')
        project = create_project(code='PRJ-VRF001')
        existing = log_action(
            user,
            AuditLog.Action.CREATED,
            project,
            'Existing row for verification isolation.',
        )
        original_summary = existing.summary
        original_pk = existing.pk
        before = AuditLog.objects.count()

        out = StringIO()
        with patch.object(Command, '_verify_runtime_role', side_effect=_runtime_role_ok):
            call_command('verify_postgres_security', stdout=out)

        existing.refresh_from_db()
        self.assertEqual(existing.summary, original_summary)
        self.assertEqual(existing.pk, original_pk)
        self.assertEqual(AuditLog.objects.count(), before)
        self.assertIn('all categories ok', out.getvalue())
