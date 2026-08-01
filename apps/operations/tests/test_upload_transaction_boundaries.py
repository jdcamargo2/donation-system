from decimal import Decimal
from queue import Queue
from threading import Event, Thread
from unittest import skipUnless
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import close_old_connections, connection, connections
from django.test import TransactionTestCase

from apps.operations.models import (
    AuditLog,
    Project,
    ProjectUpdateAttachment,
    ProjectUpdateImmutableError,
    ProjectUpdateRemediation,
    ProjectUpdateRemediationError,
    ProjectUpdateRemediationAttachment,
    OperationalCodeSequence,
    OPERATIONAL_CODE_PREFIXES,
    SupportingDocument,
)
from apps.operations.services import (
    add_project_update_attachment,
    add_project_update_remediation_attachment,
    create_supporting_document,
    create_expense,
    create_project_update_remediation,
    create_project_update_review,
    create_project_update_review_decision,
    publish_project_update,
    register_advance,
    submit_project_update_remediation,
)
from apps.operations.tests.helpers import (
    TEST_DATE,
    create_allocation,
    create_project,
    create_user,
)


class RecordingStorage:
    def __init__(self, *, fail_save=False):
        self.fail_save = fail_save
        self.saved = []
        self.deleted = []

    def save(self, name, content, max_length=None):
        self.saved.append((name, connection.in_atomic_block))
        if self.fail_save:
            raise OSError('storage failed')
        return f'stored/{name}'

    def generate_filename(self, filename):
        return filename

    def delete(self, name):
        self.deleted.append((name, connection.in_atomic_block))


class PausingStorage(RecordingStorage):
    def __init__(self):
        super().__init__()
        self.saved_file = Event()
        self.resume = Event()

    def save(self, name, content, max_length=None):
        stored_name = super().save(name, content, max_length)
        self.saved_file.set()
        self.resume.wait(timeout=10)
        return stored_name


class UploadTransactionBoundaryTests(TransactionTestCase):
    def setUp(self):
        OperationalCodeSequence.objects.bulk_create(
            [
                OperationalCodeSequence(namespace=namespace, prefix=prefix, next_value=1)
                for namespace, prefix in OPERATIONAL_CODE_PREFIXES.items()
            ],
            ignore_conflicts=True,
        )
        self.actor = create_user('upload-boundary-user')
        self.project = create_project(code='PRJ-UPLOAD-BOUNDARY')
        self.project.status = Project.Status.ACTIVE
        self.project.save(update_fields=('status',))

    def unpublished(self):
        return register_advance(
            self.project.pk, 'No publicado', 'Contenido', created_by=self.actor, reported_by=self.actor
        )

    def storage_for(self, model, field):
        storage = RecordingStorage()
        return storage, patch.object(model._meta.get_field(field), 'storage', storage)

    def test_advance_attachment_storage_is_outside_transaction_and_compensates_audit_failure(self):
        update = self.unpublished()
        storage, storage_patch = self.storage_for(ProjectUpdateAttachment, 'file')
        audit_count = AuditLog.objects.count()
        with storage_patch, patch('apps.operations.services.log_create', side_effect=RuntimeError('audit failed')):
            with self.assertRaises(RuntimeError):
                add_project_update_attachment(
                    update_id=update.pk,
                    title='Evidencia',
                    file=SimpleUploadedFile('proof.pdf', b'proof'),
                    actor=self.actor,
                )
        self.assertEqual(storage.saved[0][1], False)
        self.assertEqual(storage.deleted, [(storage.saved[0][0].replace('project_update_attachments/', 'stored/project_update_attachments/'), False)])
        self.assertFalse(ProjectUpdateAttachment.objects.exists())
        self.assertEqual(AuditLog.objects.count(), audit_count)

    def test_register_advance_keeps_created_update_and_prior_attachments_when_later_upload_fails(self):
        storage, storage_patch = self.storage_for(ProjectUpdateAttachment, 'file')
        original_save = storage.save

        def fail_second(name, content, max_length=None):
            if storage.saved:
                storage.fail_save = True
            return original_save(name, content, max_length)

        with storage_patch, patch.object(storage, 'save', side_effect=fail_second):
            with self.assertRaises(OSError):
                register_advance(
                    self.project.pk, 'Con varios archivos', 'Contenido',
                    attachments=(SimpleUploadedFile('one.pdf', b'1'), SimpleUploadedFile('two.pdf', b'2')),
                    created_by=self.actor,
                    reported_by=self.actor,
                )
        update = self.project.updates.get(title='Con varios archivos')
        self.assertEqual(update.attachments.count(), 1)
        self.assertEqual(storage.saved[0][1], False)

    def test_expense_support_storage_is_outside_transaction_and_preserves_audit_contract(self):
        allocation = create_allocation(project=self.project, amount=Decimal('100.00'))
        storage, storage_patch = self.storage_for(SupportingDocument, 'document')
        with storage_patch:
            expense = create_expense(
                allocation=allocation, expense_date=TEST_DATE, category='food', amount=Decimal('10.00'),
                reason='Compra', provider_or_recipient='Proveedor', payment_method='cash',
                description='', observations='', actor=self.actor,
                support_file=SimpleUploadedFile('invoice.pdf', b'invoice'),
            )
        self.assertEqual(storage.saved[0][1], False)
        self.assertEqual(expense.supporting_documents.count(), 1)
        self.assertTrue(AuditLog.objects.filter(entity_id=str(expense.pk)).exists())

    def test_standalone_support_storage_is_outside_transaction_and_compensates_audit_failure(self):
        allocation = create_allocation(project=self.project, amount=Decimal('100.00'))
        expense = create_expense(
            allocation=allocation, expense_date=TEST_DATE, category='food', amount=Decimal('10.00'),
            reason='Compra', provider_or_recipient='Proveedor', payment_method='cash',
            description='', observations='', actor=self.actor,
            support_file=SimpleUploadedFile('initial.pdf', b'initial'),
        )
        storage, storage_patch = self.storage_for(SupportingDocument, 'document')
        audit_count = AuditLog.objects.count()

        with storage_patch, patch('apps.operations.services.log_action', side_effect=RuntimeError('audit failed')):
            with self.assertRaises(RuntimeError):
                create_supporting_document(
                    expense_id=expense.pk,
                    title='Factura adicional',
                    file=SimpleUploadedFile('additional.pdf', b'additional'),
                    notes='',
                    actor=self.actor,
                )

        self.assertEqual(storage.saved[0][1], False)
        self.assertEqual(
            storage.deleted,
            [(
                storage.saved[0][0].replace(
                    'supporting_documents/', 'stored/supporting_documents/'
                ),
                False,
            )],
        )
        self.assertFalse(expense.supporting_documents.filter(title='Factura adicional').exists())
        self.assertEqual(AuditLog.objects.count(), audit_count)


@skipUnless(connection.vendor == 'postgresql', 'Requires PostgreSQL row-level locking')
class UploadTransactionRaceTests(UploadTransactionBoundaryTests):
    def run_in_thread(self, operation):
        results = Queue()

        def run():
            close_old_connections()
            try:
                results.put(operation())
            except BaseException as exc:
                results.put(exc)
            finally:
                connections.close_all()

        thread = Thread(target=run)
        thread.start()
        return thread, results

    def test_publishing_between_upload_and_confirmation_compensates_attachment(self):
        update = self.unpublished()
        storage = PausingStorage()
        audit_count = AuditLog.objects.count()
        with patch.object(ProjectUpdateAttachment._meta.get_field('file'), 'storage', storage):
            thread, results = self.run_in_thread(lambda: add_project_update_attachment(
                update_id=update.pk, title='Carrera',
                file=SimpleUploadedFile('race.pdf', b'race'), actor=self.actor,
            ))
            self.assertTrue(storage.saved_file.wait(timeout=10))
            publisher, published = self.run_in_thread(
                lambda: publish_project_update(update.pk, self.actor)
            )
            publisher.join(timeout=10)
            self.assertNotIsInstance(published.get_nowait(), BaseException)
            storage.resume.set()
            thread.join(timeout=10)
        self.assertIsInstance(results.get_nowait(), ProjectUpdateImmutableError)
        self.assertFalse(ProjectUpdateAttachment.objects.filter(project_update=update).exists())
        self.assertEqual(len(storage.deleted), 1)
        self.assertEqual(AuditLog.objects.count(), audit_count + 1)

    def test_submission_between_upload_and_confirmation_compensates_remediation_attachment(self):
        update = self.unpublished()
        publish_project_update(update.pk, self.actor)
        review = create_project_update_review(update_id=update.pk, observations='Revisión.', actor=self.actor)
        decision = create_project_update_review_decision(
            review_id=review.pk, outcome='observed', rationale='Fundamento.', actor=self.actor,
        )
        remediation = create_project_update_remediation(
            decision_id=decision.pk, response='Respuesta.', actor=self.actor,
        )
        storage = PausingStorage()
        audit_count = AuditLog.objects.count()
        with patch.object(ProjectUpdateRemediationAttachment._meta.get_field('file'), 'storage', storage):
            thread, results = self.run_in_thread(lambda: add_project_update_remediation_attachment(
                remediation_id=remediation.pk, title='Carrera',
                file=SimpleUploadedFile('race.pdf', b'race'), actor=self.actor,
            ))
            self.assertTrue(storage.saved_file.wait(timeout=10))
            submitter, submitted = self.run_in_thread(
                lambda: submit_project_update_remediation(remediation_id=remediation.pk, actor=self.actor)
            )
            submitter.join(timeout=10)
            self.assertNotIsInstance(submitted.get_nowait(), BaseException)
            storage.resume.set()
            thread.join(timeout=10)
        self.assertIsInstance(results.get_nowait(), ProjectUpdateRemediationError)
        self.assertFalse(ProjectUpdateRemediationAttachment.objects.filter(remediation=remediation).exists())
        self.assertEqual(len(storage.deleted), 1)
        self.assertEqual(AuditLog.objects.count(), audit_count + 1)
