from decimal import Decimal

from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.operations.models import (
    ExpenseRequest,
    ExpenseRequestAttachment,
    ExpenseRequestAttachmentMutationError,
    OperationalCodeSequence,
    SupportingDocument,
)
from apps.operations.tests.helpers import (
    create_allocation,
    create_expense,
    create_expense_request,
    create_user,
)


@override_settings(MEDIA_ROOT='/tmp/sigedon_er1_media_tests')
class ExpenseRequestAttachmentTests(TestCase):
    def setUp(self):
        OperationalCodeSequence.objects.update_or_create(
            namespace='expense_request',
            defaults={'prefix': 'SGS', 'next_value': 1},
        )
        self.user = create_user(username='er-attachment-actor')
        self.allocation = create_allocation()
        self.request = create_expense_request(
            fund_allocation=self.allocation,
            requested_by=self.user,
        )

    def tearDown(self):
        for attachment in ExpenseRequestAttachment.objects.all():
            name = attachment.file.name
            try:
                attachment.delete()
            except ExpenseRequestAttachmentMutationError:
                if name and default_storage.exists(name):
                    default_storage.delete(name)

    def _upload(self, title='Evidencia', name='evidencia.pdf'):
        return ExpenseRequestAttachment.objects.create(
            expense_request=self.request,
            file=SimpleUploadedFile(name, b'%PDF evidencia'),
            title=title,
            uploaded_by=self.user,
            notes='Nota de evidencia',
        )

    def test_attachment_can_be_added_while_pending(self):
        attachment = self._upload()

        self.assertEqual(attachment.expense_request_id, self.request.pk)
        self.assertEqual(
            str(attachment),
            f'{self.request.code} · Evidencia',
        )
        self.assertTrue(attachment.file.name.startswith('expense_request_attachments/'))

    def test_update_and_delete_allowed_while_pending(self):
        attachment = self._upload()
        attachment.title = 'Evidencia actualizada'
        attachment.save(update_fields=['title'])
        attachment.refresh_from_db()
        self.assertEqual(attachment.title, 'Evidencia actualizada')

        file_name = attachment.file.name
        attachment.delete()
        self.assertFalse(ExpenseRequestAttachment.objects.filter(pk=attachment.pk).exists())
        if default_storage.exists(file_name):
            default_storage.delete(file_name)

    def _freeze_request(self, status, **extra):
        for field, value in extra.items():
            setattr(self.request, field, value)
        self.request.status = status
        self.request.save()

    def test_mutation_rejected_for_non_pending_statuses(self):
        decided_at = timezone.now()
        cases = (
            (
                ExpenseRequest.Status.APPROVED_RESERVED,
                {
                    'decided_by': self.user,
                    'decided_at': decided_at,
                    'reserved_amount': Decimal('15.00'),
                    'reserved_at': decided_at,
                },
            ),
            (
                ExpenseRequest.Status.DENIED,
                {
                    'decided_by': self.user,
                    'decided_at': decided_at,
                    'decision_note': 'Denegada por justificación insuficiente documentada.',
                },
            ),
            (
                ExpenseRequest.Status.WITHDRAWN,
                {
                    'terminal_by': self.user,
                    'terminal_at': decided_at,
                    'terminal_reason': 'Solicitud retirada por causa operativa documentada.',
                },
            ),
            (
                ExpenseRequest.Status.ANNULLED,
                {
                    'terminal_by': self.user,
                    'terminal_at': decided_at,
                    'terminal_reason': 'Solicitud anulada por causa operativa documentada.',
                },
            ),
        )

        for status, extras in cases:
            with self.subTest(status=status):
                pending = create_expense_request(
                    fund_allocation=self.allocation,
                    requested_by=self.user,
                    purpose=f'Adjunto {status}',
                )
                attachment = ExpenseRequestAttachment.objects.create(
                    expense_request=pending,
                    file=SimpleUploadedFile(f'{status}.pdf', b'%PDF'),
                    title='Adjunto',
                    uploaded_by=self.user,
                )
                for field, value in extras.items():
                    setattr(pending, field, value)
                pending.status = status
                pending.save()

                attachment.title = 'Mutación'
                with self.assertRaises(ExpenseRequestAttachmentMutationError):
                    attachment.save()
                with self.assertRaises(ExpenseRequestAttachmentMutationError):
                    attachment.delete()
                with self.assertRaises(ExpenseRequestAttachmentMutationError):
                    ExpenseRequestAttachment.objects.filter(pk=attachment.pk).update(
                        title='Bulk'
                    )
                with self.assertRaises(ExpenseRequestAttachmentMutationError):
                    ExpenseRequestAttachment.objects.filter(pk=attachment.pk).delete()

                if attachment.file.name and default_storage.exists(attachment.file.name):
                    default_storage.delete(attachment.file.name)

    def test_mutation_rejected_for_fulfilled(self):
        expense = create_expense(allocation=self.allocation, amount=Decimal('10.00'))
        pending = create_expense_request(
            fund_allocation=self.allocation,
            requested_by=self.user,
            purpose='Adjunto fulfilled',
            requested_amount=Decimal('10.00'),
        )
        attachment = ExpenseRequestAttachment.objects.create(
            expense_request=pending,
            file=SimpleUploadedFile('fulfilled.pdf', b'%PDF'),
            title='Adjunto',
            uploaded_by=self.user,
        )
        decided_at = timezone.now()
        pending.status = ExpenseRequest.Status.FULFILLED
        pending.decided_by = self.user
        pending.decided_at = decided_at
        pending.reserved_amount = Decimal('10.00')
        pending.reserved_at = decided_at
        pending.expense = expense
        pending.save()

        with self.assertRaises(ExpenseRequestAttachmentMutationError):
            attachment.save()
        with self.assertRaises(ExpenseRequestAttachmentMutationError):
            ExpenseRequestAttachment.objects.filter(pk=attachment.pk).delete()

        if attachment.file.name and default_storage.exists(attachment.file.name):
            default_storage.delete(attachment.file.name)

    def test_cannot_create_attachment_on_non_pending_request(self):
        decided_at = timezone.now()
        self._freeze_request(
            ExpenseRequest.Status.DENIED,
            decided_by=self.user,
            decided_at=decided_at,
            decision_note='Denegada por justificación insuficiente documentada.',
        )

        with self.assertRaises(ExpenseRequestAttachmentMutationError):
            self._upload(title='Tarde')

    def test_attachment_is_not_supporting_document(self):
        attachment = self._upload()

        self.assertFalse(
            SupportingDocument.objects.filter(title=attachment.title).exists()
        )
        self.assertNotEqual(
            ExpenseRequestAttachment._meta.get_field('file').upload_to,
            SupportingDocument._meta.get_field('document').upload_to,
        )
