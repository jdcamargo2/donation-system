from unittest import skipUnless

from django.db import IntegrityError, connection, transaction
from django.test import TransactionTestCase

from apps.operations.models import AuditLog
from apps.operations.services import log_action
from apps.operations.tests.helpers import create_project, create_user

POSTGRESQL_TRIGGER_REQUIRED = 'Requires the PostgreSQL append-only trigger.'


@skipUnless(connection.vendor == 'postgresql', POSTGRESQL_TRIGGER_REQUIRED)
class AuditLogPostgresqlTriggerTests(TransactionTestCase):
    """
    PRE: migrations up to 0018_auditlog_append_only_trigger are applied on a
    real PostgreSQL database (no mocks).
    POST: verifies the database-level trigger rejects UPDATE, DELETE and
    TRUNCATE on operations_auditlog while INSERT and SELECT keep working.
    """

    def setUp(self):
        self.user = create_user()
        self.project = create_project()
        self.log = log_action(
            self.user,
            AuditLog.Action.CREATED,
            self.project,
            'Proyecto creado.',
        )

    def _select_summary(self, pk):
        with connection.cursor() as cursor:
            cursor.execute(
                'SELECT summary FROM operations_auditlog WHERE id = %s', [pk]
            )
            row = cursor.fetchone()
        return row[0] if row else None

    def test_authorized_insert_via_cursor_succeeds(self):
        # PRE: the row does not exist yet; INSERT is never intercepted by the trigger.
        # POST: a direct SQL INSERT persists a new append-only event.
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO operations_auditlog "
                    "(action, model_name, entity_id, entity_label, summary, created_at) "
                    "VALUES ('created', 'Test', 'raw-insert-1', 'Raw insert', 'Insertado por SQL directo.', now())"
                )

        self.assertTrue(
            AuditLog.objects.filter(entity_id='raw-insert-1').exists()
        )

    def test_direct_update_is_rejected(self):
        original_summary = self.log.summary

        with self.assertRaises(IntegrityError) as raised:
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(
                        'UPDATE operations_auditlog SET summary = %s WHERE id = %s',
                        ['Alterado por SQL directo.', self.log.pk],
                    )

        self.assertIn('append-only', str(raised.exception))
        self.assertNotIn(original_summary, str(raised.exception))
        self.assertEqual(self._select_summary(self.log.pk), original_summary)

    def test_direct_delete_is_rejected(self):
        with self.assertRaises(IntegrityError) as raised:
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(
                        'DELETE FROM operations_auditlog WHERE id = %s',
                        [self.log.pk],
                    )

        self.assertIn('append-only', str(raised.exception))
        self.assertTrue(AuditLog.objects.filter(pk=self.log.pk).exists())

    def test_direct_truncate_is_rejected(self):
        with self.assertRaises(IntegrityError) as raised:
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute('TRUNCATE operations_auditlog')

        self.assertIn('append-only', str(raised.exception))
        self.assertEqual(self._select_summary(self.log.pk), self.log.summary)

    def test_rejected_update_error_does_not_expose_event_content(self):
        sensitive_summary = 'Contiene detalle financiero sensible: USD 1234.56.'
        sensitive_log = log_action(
            self.user,
            AuditLog.Action.UPDATED,
            self.project,
            sensitive_summary,
        )

        with self.assertRaises(IntegrityError) as raised:
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(
                        'UPDATE operations_auditlog SET summary = %s WHERE id = %s',
                        ['Alterado.', sensitive_log.pk],
                    )

        error_text = str(raised.exception)
        self.assertNotIn(sensitive_summary, error_text)
        self.assertNotIn('1234.56', error_text)
        self.assertEqual(self._select_summary(sensitive_log.pk), sensitive_summary)

    def test_orm_and_log_action_keep_working_after_rejected_mutation(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(
                        'DELETE FROM operations_auditlog WHERE id = %s',
                        [self.log.pk],
                    )

        second_log = log_action(
            self.user,
            AuditLog.Action.UPDATED,
            self.project,
            'Proyecto actualizado tras intento rechazado.',
        )

        self.assertIsNotNone(second_log.pk)
        self.assertEqual(
            AuditLog.objects.get(pk=second_log.pk).summary,
            'Proyecto actualizado tras intento rechazado.',
        )
        self.assertTrue(AuditLog.objects.filter(pk=self.log.pk).exists())
