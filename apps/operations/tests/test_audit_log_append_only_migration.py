from unittest import skipUnless

from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

POSTGRESQL_REQUIRED = 'Requires PostgreSQL for the append-only migration cycle.'
PREVIOUS = ('operations', '0017_projectupdateremediation_and_more')
TARGET = ('operations', '0018_auditlog_append_only_trigger')
AUDITLOG_TRIGGER_FUNCTION = 'operations_auditlog_reject_mutation'
AUDITLOG_TRIGGER_NAME = 'operations_auditlog_append_only'
AUDITLOG_QUALIFIED_NAME = 'public.operations_auditlog'


@skipUnless(connection.vendor == 'postgresql', POSTGRESQL_REQUIRED)
class AuditLogAppendOnlyMigrationCycleTests(TransactionTestCase):
    """
    PRE: PostgreSQL is the active backend; leaf migrations are currently applied.
    POST: 0018 installs and removes the function/trigger correctly across a full
    migrate → reverse → reapply cycle. Leaf migrations are always restored.
    """

    def _restore_leaf_migrations(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())

    def setUp(self):
        super().setUp()
        self.addCleanup(self._restore_leaf_migrations)

    def _function_exists(self):
        with connection.cursor() as cursor:
            cursor.execute(
                'SELECT EXISTS ('
                '  SELECT 1 FROM pg_proc p'
                '  JOIN pg_namespace n ON n.oid = p.pronamespace'
                '  WHERE n.nspname = %s AND p.proname = %s'
                ');',
                ['public', AUDITLOG_TRIGGER_FUNCTION],
            )
            return bool(cursor.fetchone()[0])

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

    def _migrate_to(self, target):
        # Fresh executor so applied_migrations reflects the DB after reverse/apply.
        executor = MigrationExecutor(connection)
        executor.migrate([target])

    def _assert_mutation_rejected(self):
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO operations_auditlog "
                "(action, model_name, entity_id, entity_label, summary, created_at) "
                "VALUES ('created', 'MigrationCycle', 'mig-cycle-1', 'Cycle', "
                "'Insertado en ciclo de migracion.', now()) "
                "RETURNING id;"
            )
            row_id = cursor.fetchone()[0]

        with self.assertRaises(IntegrityError) as raised:
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(
                        'UPDATE operations_auditlog SET summary = %s WHERE id = %s',
                        ['Alterado en ciclo de migracion.', row_id],
                    )
        self.assertIn('append-only', str(raised.exception))

    def test_migrate_rollback_and_reapply_0018(self):
        self._migrate_to(PREVIOUS)
        self.assertFalse(self._function_exists())
        self.assertFalse(self._trigger_exists())

        self._migrate_to(TARGET)
        self.assertTrue(self._function_exists())
        self.assertTrue(self._trigger_exists())
        self._assert_mutation_rejected()

        self._migrate_to(PREVIOUS)
        self.assertFalse(self._function_exists())
        self.assertFalse(self._trigger_exists())

        self._migrate_to(TARGET)
        self.assertTrue(self._function_exists())
        self.assertTrue(self._trigger_exists())
