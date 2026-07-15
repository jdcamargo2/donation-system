from io import StringIO
from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase

from apps.operations.management.commands.verify_postgres_security import (
    DANGEROUS_PRIVILEGES,
    REPORTED_PRIVILEGES,
)

COMMAND_MODULE = 'apps.operations.management.commands.verify_postgres_security'


def build_mock_connection(
    *,
    vendor='postgresql',
    current_user='sigedon_app',
    is_superuser=False,
    table_owner='sigedon_owner',
    privileges=None,
    trigger_installed=True,
):
    """
    PRE: privileges maps privilege names to booleans for the six reported
    privileges; missing entries default to False.
    POST: returns a MagicMock standing in for django.db.connection whose
    cursor yields fetchone() results in the exact order _collect_report
    issues its queries (current_user, rolsuper, owner, 6 privileges, trigger).
    """
    privileges = privileges or {}
    ordered_privileges = ('SELECT', 'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE', 'TRIGGER')

    fetchone_results = [
        (current_user,),
        (is_superuser,),
        (table_owner,),
        *[(bool(privileges.get(name, False)),) for name in ordered_privileges],
        (trigger_installed,),
    ]

    mock_cursor = MagicMock()
    mock_cursor.fetchone.side_effect = fetchone_results

    mock_connection = MagicMock()
    mock_connection.vendor = vendor
    mock_connection.cursor.return_value.__enter__.return_value = mock_cursor
    mock_connection.cursor.return_value.__exit__.return_value = False
    return mock_connection


class VerifyPostgresSecurityCommandTests(SimpleTestCase):
    """
    PRE: the command under test never touches a real database; the
    connection is fully mocked so results are deterministic.
    POST: verifies exit-code semantics and that no sensitive value leaks.
    """

    def run_command(self, mock_connection):
        out = StringIO()
        with patch(f'{COMMAND_MODULE}.connection', mock_connection):
            call_command('verify_postgres_security', stdout=out)
        return out.getvalue()

    def test_non_postgresql_backend_reports_not_applicable_without_error(self):
        mock_connection = build_mock_connection(vendor='sqlite')

        output = self.run_command(mock_connection)

        self.assertIn('no aplica', output)
        self.assertIn('sqlite', output)
        mock_connection.cursor.assert_not_called()

    def test_secure_runtime_role_requires_select_and_insert(self):
        mock_connection = build_mock_connection(
            current_user='sigedon_app',
            is_superuser=False,
            table_owner='sigedon_owner',
            privileges={'SELECT': True, 'INSERT': True},
            trigger_installed=True,
        )

        output = self.run_command(mock_connection)

        self.assertIn('Configuracion runtime de PostgreSQL segura.', output)
        self.assertIn('Usuario runtime: sigedon_app', output)
        self.assertIn('Privilegio SELECT sobre operations_auditlog: si', output)
        self.assertIn('Privilegio INSERT sobre operations_auditlog: si', output)

    def test_missing_select_raises_command_error(self):
        mock_connection = build_mock_connection(
            current_user='sigedon_app',
            is_superuser=False,
            table_owner='sigedon_owner',
            privileges={'SELECT': False, 'INSERT': True},
            trigger_installed=True,
        )

        with patch(f'{COMMAND_MODULE}.connection', mock_connection):
            with self.assertRaises(CommandError) as raised:
                call_command('verify_postgres_security', stdout=StringIO())

        self.assertIn('SELECT', str(raised.exception))
        self.assertIn('no posee', str(raised.exception))

    def test_missing_insert_raises_command_error(self):
        mock_connection = build_mock_connection(
            current_user='sigedon_app',
            is_superuser=False,
            table_owner='sigedon_owner',
            privileges={'SELECT': True, 'INSERT': False},
            trigger_installed=True,
        )

        with patch(f'{COMMAND_MODULE}.connection', mock_connection):
            with self.assertRaises(CommandError) as raised:
                call_command('verify_postgres_security', stdout=StringIO())

        self.assertIn('INSERT', str(raised.exception))
        self.assertIn('no posee', str(raised.exception))

    def test_references_is_not_part_of_exit_criteria(self):
        # Decision: REFERENCES is intentionally outside the exit-code contract.
        # Role hardening may still REVOKE it in SQL, but this command neither
        # reports nor fails on REFERENCES, to keep the verified privilege set
        # limited to REPORTED_PRIVILEGES / DANGEROUS_PRIVILEGES.
        self.assertNotIn('REFERENCES', REPORTED_PRIVILEGES)
        self.assertNotIn('REFERENCES', DANGEROUS_PRIVILEGES)

        mock_connection = build_mock_connection(
            current_user='sigedon_app',
            is_superuser=False,
            table_owner='sigedon_owner',
            privileges={'SELECT': True, 'INSERT': True},
            trigger_installed=True,
        )

        output = self.run_command(mock_connection)

        self.assertIn('Configuracion runtime de PostgreSQL segura.', output)
        self.assertNotIn('REFERENCES', output)

    def test_superuser_runtime_role_raises_command_error(self):
        mock_connection = build_mock_connection(
            current_user='postgres',
            is_superuser=True,
            table_owner='sigedon_owner',
            privileges={'SELECT': True, 'INSERT': True},
            trigger_installed=True,
        )

        with patch(f'{COMMAND_MODULE}.connection', mock_connection):
            with self.assertRaises(CommandError) as raised:
                call_command('verify_postgres_security', stdout=StringIO())

        self.assertIn('superusuario', str(raised.exception))

    def test_table_owner_runtime_role_raises_command_error(self):
        mock_connection = build_mock_connection(
            current_user='sigedon_owner',
            is_superuser=False,
            table_owner='sigedon_owner',
            privileges={'SELECT': True, 'INSERT': True},
            trigger_installed=True,
        )

        with patch(f'{COMMAND_MODULE}.connection', mock_connection):
            with self.assertRaises(CommandError) as raised:
                call_command('verify_postgres_security', stdout=StringIO())

        self.assertIn('propietario', str(raised.exception))

    def test_dangerous_privilege_raises_command_error(self):
        for dangerous in ('UPDATE', 'DELETE', 'TRUNCATE', 'TRIGGER'):
            with self.subTest(privilege=dangerous):
                mock_connection = build_mock_connection(
                    current_user='sigedon_app',
                    is_superuser=False,
                    table_owner='sigedon_owner',
                    privileges={'SELECT': True, 'INSERT': True, dangerous: True},
                    trigger_installed=True,
                )

                with patch(f'{COMMAND_MODULE}.connection', mock_connection):
                    with self.assertRaises(CommandError) as raised:
                        call_command('verify_postgres_security', stdout=StringIO())

                self.assertIn(dangerous, str(raised.exception))

    def test_missing_trigger_raises_command_error(self):
        mock_connection = build_mock_connection(
            current_user='sigedon_app',
            is_superuser=False,
            table_owner='sigedon_owner',
            privileges={'SELECT': True, 'INSERT': True},
            trigger_installed=False,
        )

        with patch(f'{COMMAND_MODULE}.connection', mock_connection):
            with self.assertRaises(CommandError) as raised:
                call_command('verify_postgres_security', stdout=StringIO())

        self.assertIn('trigger append-only no esta instalado', str(raised.exception))

    def test_report_never_prints_password_or_connection_string(self):
        mock_connection = build_mock_connection(
            current_user='sigedon_app',
            privileges={'SELECT': True, 'INSERT': True},
        )
        mock_connection.settings_dict = {
            'PASSWORD': 'super-secret-password',
            'NAME': 'db_sigedon',
            'HOST': 'db.internal',
        }

        output = self.run_command(mock_connection)

        self.assertNotIn('super-secret-password', output)
        self.assertNotIn('db.internal', output)
