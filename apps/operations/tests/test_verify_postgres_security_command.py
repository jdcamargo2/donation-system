"""Unit tests for verify_postgres_security (mocked connection / helpers)."""

from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase

from apps.operations.management.commands.verify_postgres_security import (
    ACCEPTED_TRIGGER_ENABLED_STATES,
    CategoryResult,
    Command,
    CRITICAL_CHECK_CONSTRAINTS,
    DANGEROUS_PRIVILEGES,
    REPORTED_PRIVILEGES,
    REQUIRED_PRIVILEGES,
    UNSUPPORTED_BACKEND_MESSAGE,
)

COMMAND_MODULE = 'apps.operations.management.commands.verify_postgres_security'


class VerifyPostgresSecurityBackendTests(SimpleTestCase):
    def test_non_postgresql_backend_raises_command_error(self):
        mock_connection = MagicMock()
        mock_connection.vendor = 'sqlite'
        out = StringIO()
        err = StringIO()

        with patch(f'{COMMAND_MODULE}.connection', mock_connection):
            with self.assertRaises(CommandError) as raised:
                call_command(
                    'verify_postgres_security',
                    stdout=out,
                    stderr=err,
                )

        self.assertEqual(str(raised.exception), UNSUPPORTED_BACKEND_MESSAGE)
        combined = out.getvalue() + err.getvalue()
        self.assertNotIn('all categories ok', combined)
        self.assertNotIn('Configuracion runtime de PostgreSQL segura', combined)
        mock_connection.cursor.assert_not_called()

    def test_non_postgresql_does_not_claim_success(self):
        mock_connection = MagicMock()
        mock_connection.vendor = 'mysql'
        out = StringIO()

        with patch(f'{COMMAND_MODULE}.connection', mock_connection):
            with self.assertRaises(CommandError):
                call_command('verify_postgres_security', stdout=out)

        self.assertNotIn('all categories ok', out.getvalue())
        self.assertNotIn('backend: ok', out.getvalue())


class VerifyPostgresSecuritySummaryTests(SimpleTestCase):
    def test_success_summary_is_concise_and_secret_free(self):
        command = Command()
        out = StringIO()
        command.stdout = out
        command.style = MagicMock()
        command.style.SUCCESS = lambda text: text
        command.style.ERROR = lambda text: text

        results = [
            CategoryResult(name='backend', ok=True),
            CategoryResult(name='runtime role', ok=True),
            CategoryResult(name='AuditLog append-only', ok=True),
            CategoryResult(name='ExpenseRequestEvent append-only', ok=True),
            CategoryResult(name='critical constraints', ok=True),
        ]
        command._print_summary(results, verbosity=1)
        output = out.getvalue()

        self.assertIn('PostgreSQL security verification:', output)
        self.assertIn('backend: ok', output)
        self.assertIn('runtime role: ok', output)
        self.assertIn('AuditLog append-only: ok', output)
        self.assertIn('ExpenseRequestEvent append-only: ok', output)
        self.assertIn('critical constraints: ok', output)
        self.assertNotIn('PASSWORD', output)
        self.assertNotIn('localhost', output)
        self.assertNotIn('sigedon_app', output)

    def test_failure_raises_command_error_with_categories(self):
        mock_connection = MagicMock()
        mock_connection.vendor = 'postgresql'
        out = StringIO()

        with patch(f'{COMMAND_MODULE}.connection', mock_connection):
            with patch.object(
                Command,
                '_verify_backend',
                return_value=CategoryResult(name='backend', ok=True),
            ):
                with patch.object(
                    Command,
                    '_verify_runtime_role',
                    return_value=CategoryResult(
                        name='runtime role',
                        ok=False,
                        failures=[
                            'Current database role is too privileged '
                            'for runtime verification.'
                        ],
                    ),
                ):
                    with patch.object(
                        Command,
                        '_verify_append_only',
                        side_effect=[
                            CategoryResult(name='AuditLog append-only', ok=True),
                            CategoryResult(
                                name='ExpenseRequestEvent append-only', ok=True
                            ),
                        ],
                    ):
                        with patch.object(
                            Command,
                            '_verify_critical_constraints',
                            return_value=CategoryResult(
                                name='critical constraints', ok=True
                            ),
                        ):
                            with self.assertRaises(CommandError) as raised:
                                call_command(
                                    'verify_postgres_security', stdout=out
                                )

        self.assertIn('runtime role', str(raised.exception))
        self.assertNotIn('PASSWORD', str(raised.exception))
        self.assertIn('runtime role: FAILED', out.getvalue())


class VerifyPostgresSecurityFailureClassificationTests(SimpleTestCase):
    """Catalog/privilege failure classification with fully mocked DB access."""

    def _patched_connection(self):
        mock_conn = MagicMock()
        mock_conn.vendor = 'postgresql'
        mock_conn.cursor.return_value.__enter__.return_value = MagicMock()
        mock_conn.cursor.return_value.__exit__.return_value = False
        return patch(f'{COMMAND_MODULE}.connection', mock_conn)

    def test_missing_auditlog_trigger_fails(self):
        command = Command()
        with self._patched_connection():
            with patch(f'{COMMAND_MODULE}.function_exists', return_value=True):
                with patch(
                    f'{COMMAND_MODULE}.get_trigger_state',
                    return_value=None,
                ):
                    result = command._verify_append_only('AuditLog', verbosity=1)
        self.assertFalse(result.ok)
        self.assertTrue(
            any('Missing trigger' in item for item in result.failures)
        )

    def test_disabled_auditlog_trigger_fails(self):
        command = Command()
        with self._patched_connection():
            with patch(f'{COMMAND_MODULE}.function_exists', return_value=True):
                with patch(
                    f'{COMMAND_MODULE}.get_trigger_state',
                    return_value={
                        'exists': True,
                        'tgenabled': 'D',
                        'enabled': False,
                        'function_matches': True,
                        'function_name': 'operations_auditlog_reject_mutation',
                    },
                ):
                    result = command._verify_append_only('AuditLog', verbosity=1)
        self.assertFalse(result.ok)
        self.assertTrue(
            any('not active' in item for item in result.failures)
        )

    def test_missing_expense_request_event_trigger_fails(self):
        command = Command()
        with self._patched_connection():
            with patch(f'{COMMAND_MODULE}.function_exists', return_value=True):
                with patch(
                    f'{COMMAND_MODULE}.get_trigger_state',
                    return_value=None,
                ):
                    result = command._verify_append_only(
                        'ExpenseRequestEvent', verbosity=1
                    )
        self.assertFalse(result.ok)
        self.assertTrue(
            any('Missing trigger' in item for item in result.failures)
        )

    def test_disabled_expense_request_event_trigger_fails(self):
        command = Command()
        with self._patched_connection():
            with patch(f'{COMMAND_MODULE}.function_exists', return_value=True):
                with patch(
                    f'{COMMAND_MODULE}.get_trigger_state',
                    return_value={
                        'exists': True,
                        'tgenabled': 'R',
                        'enabled': False,
                        'function_matches': True,
                        'function_name': (
                            'operations_expenserequestevent_reject_mutation'
                        ),
                    },
                ):
                    result = command._verify_append_only(
                        'ExpenseRequestEvent', verbosity=1
                    )
        self.assertFalse(result.ok)
        self.assertTrue(
            any('not active' in item for item in result.failures)
        )

    def test_missing_function_fails(self):
        command = Command()
        with self._patched_connection():
            with patch(f'{COMMAND_MODULE}.function_exists', return_value=False):
                with patch(
                    f'{COMMAND_MODULE}.get_trigger_state',
                    return_value={
                        'exists': True,
                        'tgenabled': 'O',
                        'enabled': True,
                        'function_matches': True,
                        'function_name': 'operations_auditlog_reject_mutation',
                    },
                ):
                    with patch.object(
                        Command, '_probe_append_only_mutations', return_value=[]
                    ):
                        result = command._verify_append_only(
                            'AuditLog', verbosity=1
                        )
        self.assertFalse(result.ok)
        self.assertTrue(
            any('Missing trigger function' in item for item in result.failures)
        )

    def test_update_unexpectedly_succeeds_fails(self):
        command = Command()
        with self._patched_connection():
            with patch(f'{COMMAND_MODULE}.function_exists', return_value=True):
                with patch(
                    f'{COMMAND_MODULE}.get_trigger_state',
                    return_value={
                        'exists': True,
                        'tgenabled': 'O',
                        'enabled': True,
                        'function_matches': True,
                        'function_name': 'operations_auditlog_reject_mutation',
                    },
                ):
                    with patch.object(
                        Command,
                        '_probe_append_only_mutations',
                        return_value=[
                            'Protected mutation unexpectedly succeeds '
                            '(UPDATE on AuditLog).'
                        ],
                    ):
                        result = command._verify_append_only(
                            'AuditLog', verbosity=1
                        )
        self.assertFalse(result.ok)
        self.assertTrue(
            any('UPDATE' in item for item in result.failures)
        )

    def test_delete_unexpectedly_succeeds_fails(self):
        command = Command()
        with self._patched_connection():
            with patch(f'{COMMAND_MODULE}.function_exists', return_value=True):
                with patch(
                    f'{COMMAND_MODULE}.get_trigger_state',
                    return_value={
                        'exists': True,
                        'tgenabled': 'O',
                        'enabled': True,
                        'function_matches': True,
                        'function_name': (
                            'operations_expenserequestevent_reject_mutation'
                        ),
                    },
                ):
                    with patch.object(
                        Command,
                        '_probe_append_only_mutations',
                        return_value=[
                            'Protected mutation unexpectedly succeeds '
                            '(DELETE on ExpenseRequestEvent).'
                        ],
                    ):
                        result = command._verify_append_only(
                            'ExpenseRequestEvent', verbosity=1
                        )
        self.assertFalse(result.ok)
        self.assertTrue(
            any('DELETE' in item for item in result.failures)
        )

    def test_missing_critical_constraint_fails(self):
        command = Command()
        with self._patched_connection():
            with patch(
                f'{COMMAND_MODULE}.constraint_exists',
                return_value=None,
            ):
                with patch(
                    f'{COMMAND_MODULE}.unique_column_constraint_exists',
                    return_value=True,
                ):
                    result = command._verify_critical_constraints(verbosity=1)
        self.assertFalse(result.ok)
        self.assertTrue(
            any('Missing critical constraint' in item for item in result.failures)
        )
        self.assertGreaterEqual(
            len(result.failures), len(CRITICAL_CHECK_CONSTRAINTS)
        )

    def test_unvalidated_critical_constraint_fails(self):
        command = Command()

        def fake_constraint_exists(*_args, **_kwargs):
            return {
                'exists': True,
                'contype': 'c',
                'convalidated': False,
                'type_matches': True,
            }

        with self._patched_connection():
            with patch(
                f'{COMMAND_MODULE}.constraint_exists',
                side_effect=fake_constraint_exists,
            ):
                with patch(
                    f'{COMMAND_MODULE}.unique_column_constraint_exists',
                    return_value=True,
                ):
                    result = command._verify_critical_constraints(verbosity=1)
        self.assertFalse(result.ok)
        self.assertTrue(
            any(
                'Unvalidated critical constraint' in item
                for item in result.failures
            )
        )

    def test_superuser_runtime_role_fails(self):
        command = Command()
        role = {
            'current_user': 'postgres',
            'session_user': 'postgres',
            'session_replication_role': 'origin',
            'is_superuser': True,
            'has_replication': False,
            'rolinherit': True,
        }
        with self._patched_connection():
            with patch(f'{COMMAND_MODULE}.current_role_info', return_value=role):
                with patch(
                    f'{COMMAND_MODULE}.table_owner', return_value='sigedon_owner'
                ):
                    with patch(
                        f'{COMMAND_MODULE}.has_table_privilege',
                        return_value=True,
                    ):
                        result = command._verify_runtime_role(verbosity=1)
        self.assertFalse(result.ok)
        self.assertTrue(
            any('too privileged' in item for item in result.failures)
        )

    def test_protected_table_owner_fails(self):
        command = Command()
        role = {
            'current_user': 'sigedon_owner',
            'session_user': 'sigedon_owner',
            'session_replication_role': 'origin',
            'is_superuser': False,
            'has_replication': False,
            'rolinherit': True,
        }
        with self._patched_connection():
            with patch(f'{COMMAND_MODULE}.current_role_info', return_value=role):
                with patch(
                    f'{COMMAND_MODULE}.table_owner', return_value='sigedon_owner'
                ):
                    with patch(
                        f'{COMMAND_MODULE}.has_table_privilege',
                        return_value=True,
                    ):
                        result = command._verify_runtime_role(verbosity=1)
        self.assertFalse(result.ok)
        self.assertTrue(
            any('owns protected tables' in item for item in result.failures)
        )

    def test_non_origin_replication_role_fails(self):
        command = Command()
        role = {
            'current_user': 'sigedon_app',
            'session_user': 'sigedon_app',
            'session_replication_role': 'replica',
            'is_superuser': False,
            'has_replication': False,
            'rolinherit': True,
        }

        def privilege(*_args, **kwargs):
            if kwargs.get('table') == 'operations_donation':
                return True
            return kwargs.get('privilege') in REQUIRED_PRIVILEGES

        with self._patched_connection():
            with patch(f'{COMMAND_MODULE}.current_role_info', return_value=role):
                with patch(
                    f'{COMMAND_MODULE}.table_owner', return_value='sigedon_owner'
                ):
                    with patch(
                        f'{COMMAND_MODULE}.has_table_privilege',
                        side_effect=privilege,
                    ):
                        result = command._verify_runtime_role(verbosity=1)
        self.assertFalse(result.ok)
        self.assertTrue(
            any('session_replication_role' in item for item in result.failures)
        )

    def test_excessive_update_privilege_fails(self):
        command = Command()
        role = {
            'current_user': 'sigedon_app',
            'session_user': 'sigedon_app',
            'session_replication_role': 'origin',
            'is_superuser': False,
            'has_replication': False,
            'rolinherit': True,
        }
        operational_privs = ('SELECT', 'INSERT', 'UPDATE', 'DELETE')

        def privilege(*_args, **kwargs):
            if kwargs.get('table') == 'operations_auditlog':
                return kwargs.get('privilege') in ('SELECT', 'INSERT', 'UPDATE')
            if kwargs.get('table') == 'operations_expenserequestevent':
                return kwargs.get('privilege') in REQUIRED_PRIVILEGES
            return kwargs.get('privilege') in operational_privs

        with self._patched_connection():
            with patch(f'{COMMAND_MODULE}.current_role_info', return_value=role):
                with patch(
                    f'{COMMAND_MODULE}.table_owner', return_value='sigedon_owner'
                ):
                    with patch(
                        f'{COMMAND_MODULE}.has_table_privilege',
                        side_effect=privilege,
                    ):
                        result = command._verify_runtime_role(verbosity=1)
        self.assertFalse(result.ok)
        self.assertTrue(
            any('excessive UPDATE' in item for item in result.failures)
        )

    def test_expense_request_event_privilege_contract_included(self):
        from apps.operations.management.commands.verify_postgres_security import (
            APPEND_ONLY_TARGETS,
        )

        target = next(
            item for item in APPEND_ONLY_TARGETS if item.label == 'ExpenseRequestEvent'
        )
        self.assertTrue(target.harden_privileges)
        sql = (
            Path(__file__).resolve().parents[3]
            / 'deploy'
            / 'postgresql'
            / 'harden_runtime_role.sql'
        ).read_text(encoding='utf-8')
        self.assertIn('operations_expenserequestevent', sql)
        self.assertIn('operations_auditlog', sql)

    def test_excessive_delete_truncate_trigger_on_expense_event_fail(self):
        command = Command()
        role = {
            'current_user': 'sigedon_app',
            'session_user': 'sigedon_app',
            'session_replication_role': 'origin',
            'is_superuser': False,
            'has_replication': False,
            'rolinherit': True,
        }
        operational_privs = ('SELECT', 'INSERT', 'UPDATE', 'DELETE')

        for dangerous in ('DELETE', 'TRUNCATE', 'TRIGGER', 'UPDATE'):
            with self.subTest(dangerous=dangerous):

                dangerous_priv = dangerous

                def privilege(*_args, **kwargs):
                    table = kwargs.get('table')
                    priv = kwargs.get('privilege')
                    if table == 'operations_auditlog':
                        return priv in REQUIRED_PRIVILEGES
                    if table == 'operations_expenserequestevent':
                        return priv in (*REQUIRED_PRIVILEGES, dangerous_priv)
                    return priv in operational_privs

                with self._patched_connection():
                    with patch(
                        f'{COMMAND_MODULE}.current_role_info', return_value=role
                    ):
                        with patch(
                            f'{COMMAND_MODULE}.table_owner',
                            return_value='sigedon_owner',
                        ):
                            with patch(
                                f'{COMMAND_MODULE}.has_table_privilege',
                                side_effect=privilege,
                            ):
                                result = command._verify_runtime_role(verbosity=1)
                self.assertFalse(result.ok)
                self.assertTrue(
                    any(
                        f'excessive {dangerous}' in item
                        and 'ExpenseRequestEvent' in item
                        for item in result.failures
                    )
                )

    def test_missing_required_insert_on_expense_event_fails(self):
        command = Command()
        role = {
            'current_user': 'sigedon_app',
            'session_user': 'sigedon_app',
            'session_replication_role': 'origin',
            'is_superuser': False,
            'has_replication': False,
            'rolinherit': True,
        }
        operational_privs = ('SELECT', 'INSERT', 'UPDATE', 'DELETE')

        def privilege(*_args, **kwargs):
            table = kwargs.get('table')
            priv = kwargs.get('privilege')
            if table == 'operations_auditlog':
                return priv in REQUIRED_PRIVILEGES
            if table == 'operations_expenserequestevent':
                return priv == 'SELECT'
            return priv in operational_privs

        with self._patched_connection():
            with patch(f'{COMMAND_MODULE}.current_role_info', return_value=role):
                with patch(
                    f'{COMMAND_MODULE}.table_owner', return_value='sigedon_owner'
                ):
                    with patch(
                        f'{COMMAND_MODULE}.has_table_privilege',
                        side_effect=privilege,
                    ):
                        result = command._verify_runtime_role(verbosity=1)
        self.assertFalse(result.ok)
        self.assertTrue(
            any(
                'lacks required INSERT' in item and 'ExpenseRequestEvent' in item
                for item in result.failures
            )
        )
        combined = ' '.join(result.failures)
        self.assertNotIn('sigedon_app', combined)

    def test_accepted_trigger_states_only_origin(self):
        self.assertEqual(ACCEPTED_TRIGGER_ENABLED_STATES, frozenset({'O'}))

    def test_privilege_contract_includes_references(self):
        self.assertIn('REFERENCES', REPORTED_PRIVILEGES)
        self.assertIn('REFERENCES', DANGEROUS_PRIVILEGES)
        self.assertEqual(REQUIRED_PRIVILEGES, ('SELECT', 'INSERT'))
