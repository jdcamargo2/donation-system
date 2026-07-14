from unittest import skipUnless
from unittest.mock import MagicMock

from django.core.management.color import no_style
from django.core.management.sql import sql_flush
from django.db import IntegrityError, connection, transaction
from django.db.backends.base.operations import BaseDatabaseOperations
from django.test import SimpleTestCase, TransactionTestCase

from apps.operations.db_compat import (
    _BYPASS_MARKER,
    _ORIGINAL_EXECUTE_SQL_FLUSH,
    _execute_sql_flush_with_auditlog_bypass,
    apply_flush_trigger_bypass,
)
from apps.operations.models import AuditLog
from apps.operations.services import log_action
from apps.operations.tests.helpers import create_project, create_user

POSTGRESQL_REQUIRED = 'Requires PostgreSQL for the flush trigger bypass.'
AUDITLOG_TRIGGER_NAME = 'operations_auditlog_append_only'
AUDITLOG_QUALIFIED_NAME = 'public.operations_auditlog'


class FlushTriggerBypassIdempotencyTests(SimpleTestCase):
    """
    PRE: apply_flush_trigger_bypass may already have run via AppConfig.ready().
    POST: repeated registration keeps a single wrapper layer and a stable
    reference; non-PostgreSQL backends stay untouched.
    """

    def test_repeated_registration_keeps_single_wrapper_layer(self):
        # PRE: the patch may already be installed; calling again must be a no-op.
        apply_flush_trigger_bypass()
        first = BaseDatabaseOperations.execute_sql_flush
        apply_flush_trigger_bypass()
        apply_flush_trigger_bypass()
        second = BaseDatabaseOperations.execute_sql_flush

        self.assertIs(first, _execute_sql_flush_with_auditlog_bypass)
        self.assertIs(second, _execute_sql_flush_with_auditlog_bypass)
        self.assertIs(first, second)
        self.assertIsNot(
            _ORIGINAL_EXECUTE_SQL_FLUSH,
            _execute_sql_flush_with_auditlog_bypass,
        )

    def test_postgresql_prepends_bypass_marker_exactly_once(self):
        apply_flush_trigger_bypass()
        captured = {}

        def capture_original(self, sql_list):
            captured['sql_list'] = list(sql_list)
            return None

        import apps.operations.db_compat as db_compat

        ops = MagicMock()
        ops.connection.vendor = 'postgresql'
        original_sql = ['TRUNCATE operations_auditlog CASCADE;']
        real_original = db_compat._ORIGINAL_EXECUTE_SQL_FLUSH
        db_compat._ORIGINAL_EXECUTE_SQL_FLUSH = capture_original
        try:
            _execute_sql_flush_with_auditlog_bypass(ops, original_sql)
        finally:
            db_compat._ORIGINAL_EXECUTE_SQL_FLUSH = real_original

        self.assertEqual(captured['sql_list'], [_BYPASS_MARKER, *original_sql])
        self.assertEqual(captured['sql_list'].count(_BYPASS_MARKER), 1)
        self.assertIs(
            BaseDatabaseOperations.execute_sql_flush,
            _execute_sql_flush_with_auditlog_bypass,
        )

    def test_non_postgresql_backend_is_unchanged(self):
        captured = {}

        def capture_original(self, sql_list):
            captured['sql_list'] = list(sql_list)
            return None

        import apps.operations.db_compat as db_compat

        ops = MagicMock()
        ops.connection.vendor = 'sqlite'
        original_sql = ['DELETE FROM operations_auditlog;']
        real_original = db_compat._ORIGINAL_EXECUTE_SQL_FLUSH
        db_compat._ORIGINAL_EXECUTE_SQL_FLUSH = capture_original
        try:
            _execute_sql_flush_with_auditlog_bypass(ops, original_sql)
        finally:
            db_compat._ORIGINAL_EXECUTE_SQL_FLUSH = real_original

        self.assertEqual(captured['sql_list'], original_sql)
        self.assertNotIn(_BYPASS_MARKER, captured['sql_list'])


@skipUnless(connection.vendor == 'postgresql', POSTGRESQL_REQUIRED)
class FlushTriggerBypassPostgresqlTests(TransactionTestCase):
    """
    PRE: migration 0018 is applied; AppConfig.ready() registered the bypass.
    POST: real Django flush succeeds with the trigger present; a SQL error
    after SET LOCAL does not leave session_replication_role = replica.
    """

    def setUp(self):
        apply_flush_trigger_bypass()
        self.user = create_user(username='flush-bypass-user')
        self.project = create_project(code='PRJ-FLUSH-1')

    def _trigger_exists(self):
        with connection.cursor() as cursor:
            cursor.execute(
                'SELECT EXISTS ('
                '  SELECT 1 FROM pg_trigger'
                '  WHERE tgrelid = %s::regclass'
                '    AND tgname = %s'
                '    AND NOT tgisinternal'
                ');',
                [AUDITLOG_QUALIFIED_NAME, AUDITLOG_TRIGGER_NAME],
            )
            return bool(cursor.fetchone()[0])

    def _session_replication_role(self):
        with connection.cursor() as cursor:
            cursor.execute('SHOW session_replication_role;')
            return cursor.fetchone()[0]

    def _run_real_flush(self):
        # Real Django flush SQL path (same as manage.py flush → execute_sql_flush).
        sql_list = sql_flush(
            no_style(),
            connection,
            reset_sequences=False,
            allow_cascade=False,
        )
        connection.ops.execute_sql_flush(sql_list)

    def test_real_flush_with_active_trigger_empties_table_and_keeps_protection(self):
        self.assertTrue(self._trigger_exists())
        log_action(
            self.user,
            AuditLog.Action.CREATED,
            self.project,
            'Evento previo al flush.',
        )
        self.assertGreater(AuditLog.objects.count(), 0)

        self._run_real_flush()

        self.assertEqual(AuditLog.objects.count(), 0)
        self.assertTrue(self._trigger_exists())

        # Flush vacía todas las tablas Django; recrear actor/entidad para el INSERT.
        self.user = create_user(username='flush-bypass-user-after')
        self.project = create_project(code='PRJ-FLUSH-2')
        fresh = log_action(
            self.user,
            AuditLog.Action.CREATED,
            self.project,
            'Evento posterior al flush.',
        )
        self.assertIsNotNone(fresh.pk)

        with self.assertRaises(IntegrityError) as raised_update:
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(
                        'UPDATE operations_auditlog SET summary = %s WHERE id = %s',
                        ['Alterado tras flush.', fresh.pk],
                    )
        self.assertIn('append-only', str(raised_update.exception))

        with self.assertRaises(IntegrityError) as raised_delete:
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(
                        'DELETE FROM operations_auditlog WHERE id = %s',
                        [fresh.pk],
                    )
        self.assertIn('append-only', str(raised_delete.exception))

        with self.assertRaises(IntegrityError) as raised_truncate:
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute('TRUNCATE operations_auditlog')
        self.assertIn('append-only', str(raised_truncate.exception))
        self.assertTrue(AuditLog.objects.filter(pk=fresh.pk).exists())

    def test_error_during_flush_restores_session_replication_role_to_origin(self):
        self.assertEqual(self._session_replication_role(), 'origin')

        with self.assertRaises(Exception) as raised:
            connection.ops.execute_sql_flush(['SELECT 1/0'])

        # Recover this connection after the aborted atomic; do not touch globals
        # or share the connection across threads.
        connection.rollback()

        self.assertEqual(self._session_replication_role(), 'origin')
        self.assertNotEqual(self._session_replication_role(), 'replica')
        self.assertIn('division by zero', str(raised.exception).lower())
