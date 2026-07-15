import shutil
import tempfile
from pathlib import Path
from unittest import skipUnless

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext

from apps.operations.models import AuditLog, Expense, Project, SupportingDocument
from apps.operations.services import (
    SupportingDocumentError,
    create_supporting_document,
    delete_supporting_document,
)
from apps.operations.tests.helpers import create_expense, create_user


TEMP_MEDIA_ROOT = tempfile.mkdtemp()


@override_settings(MEDIA_ROOT=TEMP_MEDIA_ROOT)
class SupportingDocumentServiceTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TEMP_MEDIA_ROOT, ignore_errors=True)

    def setUp(self):
        self.actor = create_user('support-service-user')
        self.expense = create_expense(reason='Gasto con servicios de soporte')
        self.expense.allocation.project.status = Project.Status.ACTIVE
        self.expense.allocation.project.save(update_fields=('status', 'updated_at'))

    def uploaded_file(self, name='service-support.pdf', content=b'support'):
        return SimpleUploadedFile(name, content, content_type='application/pdf')

    def create_document(self, *, title='Soporte de servicio', name='service-support.pdf'):
        return create_supporting_document(
            expense_id=self.expense.pk,
            title=title,
            file=self.uploaded_file(name),
            notes='Nota interna.',
            actor=self.actor,
        )

    def test_create_supporting_document_persists_metadata_and_audit(self):
        document = self.create_document()

        self.assertEqual(document.expense_id, self.expense.pk)
        self.assertEqual(document.title, 'Soporte de servicio')
        self.assertEqual(document.notes, 'Nota interna.')
        self.assertTrue(document.document.name.startswith('supporting_documents/'))
        self.assertTrue(
            AuditLog.objects.filter(
                action=AuditLog.Action.CREATED,
                entity_id=str(document.pk),
                entity_label='Soporte de servicio',
                summary='Documento soporte adjuntado.',
            ).exists()
        )

    def test_create_supporting_document_rejects_missing_expense_before_upload(self):
        audit_count = AuditLog.objects.count()

        with self.assertRaises(Expense.DoesNotExist):
            create_supporting_document(
                expense_id=self.expense.pk + 9999,
                title='Entidad inexistente',
                file=self.uploaded_file('missing-parent.pdf'),
                notes='',
                actor=self.actor,
            )

        self.assertEqual(AuditLog.objects.count(), audit_count)

    def test_delete_supporting_document_removes_redundant_row_and_audits(self):
        retained = self.create_document(title='Soporte conservado', name='retained.pdf')
        deleted = self.create_document(title='Soporte eliminado', name='deleted.pdf')

        expense_id = delete_supporting_document(
            document_id=deleted.pk,
            actor=self.actor,
        )

        self.assertEqual(expense_id, self.expense.pk)
        self.assertTrue(SupportingDocument.objects.filter(pk=retained.pk).exists())
        self.assertFalse(SupportingDocument.objects.filter(pk=deleted.pk).exists())
        self.assertTrue(
            AuditLog.objects.filter(
                action=AuditLog.Action.ANNULLED,
                entity_id=str(deleted.pk),
                entity_label='Soporte eliminado',
                summary='Documento soporte eliminado.',
            ).exists()
        )

    def test_delete_supporting_document_rejects_last_document_without_audit(self):
        document = self.create_document()
        audit_count = AuditLog.objects.count()

        with self.assertRaisesMessage(
            SupportingDocumentError,
            'El gasto debe conservar su documento soporte.',
        ):
            delete_supporting_document(document_id=document.pk, actor=self.actor)

        self.assertTrue(SupportingDocument.objects.filter(pk=document.pk).exists())
        self.assertEqual(AuditLog.objects.count(), audit_count)

    def test_delete_supporting_document_rejects_annulled_expense(self):
        first = self.create_document(title='Primer soporte', name='first.pdf')
        self.create_document(title='Segundo soporte', name='second.pdf')
        self.expense.status = Expense.Status.ANNULLED
        self.expense.save(update_fields=('status', 'updated_at'))
        audit_count = AuditLog.objects.count()

        with self.assertRaises(SupportingDocumentError):
            delete_supporting_document(document_id=first.pk, actor=self.actor)

        self.assertTrue(SupportingDocument.objects.filter(pk=first.pk).exists())
        self.assertEqual(AuditLog.objects.count(), audit_count)

    def test_delete_preserves_existing_physical_file_by_current_retention_policy(self):
        self.create_document(title='Soporte conservado', name='keep.pdf')
        deleted = self.create_document(title='Soporte con archivo retenido', name='retained-file.pdf')
        stored_path = Path(deleted.document.path)
        self.assertTrue(stored_path.exists())

        delete_supporting_document(document_id=deleted.pk, actor=self.actor)

        self.assertTrue(stored_path.exists())

    @skipUnless(connection.vendor == 'postgresql', 'Requires PostgreSQL FOR UPDATE SQL')
    def test_delete_revalidates_expense_and_document_under_row_locks(self):
        self.create_document(title='Soporte conservado', name='keep-lock.pdf')
        deleted = self.create_document(title='Soporte eliminable', name='delete-lock.pdf')

        with CaptureQueriesContext(connection) as queries:
            delete_supporting_document(document_id=deleted.pk, actor=self.actor)

        locking_sql = [query['sql'] for query in queries if 'FOR UPDATE' in query['sql']]
        self.assertTrue(any('operations_expense' in sql for sql in locking_sql))
        self.assertTrue(any('operations_supportingdocument' in sql for sql in locking_sql))
