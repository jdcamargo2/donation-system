"""Expense Request attachment file delivery, freeze readability, and orphan safety (ER6)."""

from decimal import Decimal
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.storage import FileSystemStorage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.operations.expense_request_services import (
    add_expense_request_attachments,
    annul_expense_request,
    approve_expense_request,
    create_expense_request,
    delete_expense_request_attachment,
    deny_expense_request,
    fulfill_expense_request,
    withdraw_expense_request,
)
from apps.operations.models import (
    AuditLog,
    ExpenseRequestAttachment,
)
from apps.operations.role_services import sync_operation_roles
from apps.operations.roles import (
    ROLE_EXTERNAL_AUDITOR,
    ROLE_FIELD_OPERATOR,
    ROLE_PROJECT_COMMITTEE,
    ROLE_SIGEDON_ADMIN,
)
from apps.operations.tests.helpers import TEST_DATE, create_allocation


PNG_BYTES = (
    b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01'
    b'\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00'
    b'\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82'
)
PDF_BYTES = b'%PDF-1.1\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n'
TXT_BYTES = b'hello expense request attachment\n'
ZIP_BYTES = b'PK\x03\x04' + b'\x00' * 26


class RecordingStorage(FileSystemStorage):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.saved = []
        self.deleted = []
        self.fail_save = False

    def save(self, name, content, max_length=None):
        if self.fail_save:
            raise OSError('forced storage failure')
        stored = super().save(name, content, max_length=max_length)
        self.saved.append((stored, False))
        return stored

    def delete(self, name):
        self.deleted.append(name)
        return super().delete(name)


class ExpenseRequestAttachmentFileTests(TestCase):
    def setUp(self):
        self.media = TemporaryDirectory()
        self.addCleanup(self.media.cleanup)
        self.override = override_settings(MEDIA_ROOT=self.media.name)
        self.override.enable()
        self.addCleanup(self.override.disable)
        sync_operation_roles()
        self.allocation = create_allocation(amount=Decimal('600.00'))
        self.admin = self._user('er6-file-admin', ROLE_SIGEDON_ADMIN)
        self.operator = self._user('er6-file-operator', ROLE_FIELD_OPERATOR)
        self.other_operator = self._user('er6-file-operator-b', ROLE_FIELD_OPERATOR)
        self.committee = self._user('er6-file-committee', ROLE_PROJECT_COMMITTEE)
        self.auditor = self._user('er6-file-auditor', ROLE_EXTERNAL_AUDITOR)
        self.request_obj = create_expense_request(
            fund_allocation=self.allocation,
            requested_amount=Decimal('30.00'),
            purpose='Solicitud ER6 files',
            requested_date=TEST_DATE,
            actor=self.operator,
        )

    def _user(self, username, role_name):
        user = get_user_model().objects.create_user(username=username, password='pass-12345')
        user.groups.add(Group.objects.get(name=role_name))
        return user

    def _add(self, *, filename, content, title='Adjunto', request_obj=None, actor=None):
        request_obj = request_obj or self.request_obj
        actor = actor or self.operator
        return add_expense_request_attachments(
            expense_request_id=request_obj.pk,
            files=[SimpleUploadedFile(filename, content)],
            title=title,
            actor=actor,
        )[0]

    def _urls(self, attachment, request_obj=None):
        request_obj = request_obj or self.request_obj
        args = (request_obj.pk, attachment.pk)
        return (
            reverse('expense_request_attachment_preview', args=args),
            reverse('expense_request_attachment_download', args=args),
        )

    def test_preview_and_download_safe_headers_for_whitelist_types(self):
        cases = (
            ('shot.png', PNG_BYTES, 'image/png'),
            ('doc.pdf', PDF_BYTES, 'application/pdf'),
            ('note.txt', TXT_BYTES, 'text/plain; charset=utf-8'),
        )
        for filename, content, mime in cases:
            with self.subTest(filename=filename):
                attachment = self._add(filename=filename, content=content, title=filename)
                preview_url, download_url = self._urls(attachment)
                for actor in (self.admin, self.committee, self.auditor, self.operator):
                    self.client.force_login(actor)
                    preview = self.client.get(preview_url)
                    self.assertEqual(preview.status_code, 200)
                    self.assertIn('inline;', preview['Content-Disposition'])
                    self.assertEqual(preview['Content-Type'], mime)
                    self.assertEqual(preview['X-Content-Type-Options'], 'nosniff')
                    self.assertEqual(preview['Cache-Control'], 'private, no-store')
                    preview_body = b''.join(preview.streaming_content)
                    self.assertNotIn(self.media.name.encode(), preview_body)

                    download = self.client.get(download_url)
                    self.assertEqual(download.status_code, 200)
                    self.assertIn('attachment;', download['Content-Disposition'])
                    self.assertEqual(b''.join(download.streaming_content), content)
                    self.assertEqual(download['X-Content-Type-Options'], 'nosniff')
                    self.assertEqual(download['Cache-Control'], 'private, no-store')

    def test_unsupported_preview_404_but_download_works(self):
        attachment = self._add(filename='bundle.zip', content=ZIP_BYTES, title='Zip')
        preview_url, download_url = self._urls(attachment)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(preview_url).status_code, 404)
        download = self.client.get(download_url)
        self.assertEqual(download.status_code, 200)
        self.assertEqual(b''.join(download.streaming_content), ZIP_BYTES)

        detail = self.client.get(reverse('expense_request_detail', args=[self.request_obj.pk]))
        self.assertContains(detail, 'Vista previa no disponible')
        self.assertNotContains(detail, preview_url)
        self.assertContains(detail, download_url)

    def test_unrelated_operator_and_parent_mismatch_are_404(self):
        attachment = self._add(filename='secret.pdf', content=PDF_BYTES)
        other = create_expense_request(
            fund_allocation=self.allocation,
            requested_amount=Decimal('8.00'),
            purpose='Otra solicitud file',
            requested_date=TEST_DATE,
            actor=self.other_operator,
        )
        preview_url, download_url = self._urls(attachment)
        self.client.force_login(self.other_operator)
        self.assertEqual(self.client.get(preview_url).status_code, 404)
        self.assertEqual(self.client.get(download_url).status_code, 404)

        self.client.force_login(self.admin)
        mismatched_preview = reverse(
            'expense_request_attachment_preview',
            args=[other.pk, attachment.pk],
        )
        mismatched_download = reverse(
            'expense_request_attachment_download',
            args=[other.pk, attachment.pk],
        )
        self.assertEqual(self.client.get(mismatched_preview).status_code, 404)
        self.assertEqual(self.client.get(mismatched_download).status_code, 404)

        missing = reverse(
            'expense_request_attachment_download',
            args=[self.request_obj.pk, 999999],
        )
        self.assertEqual(self.client.get(missing).status_code, 404)

    def test_anonymous_redirects_to_login(self):
        attachment = self._add(filename='anon.pdf', content=PDF_BYTES)
        preview_url, download_url = self._urls(attachment)
        for url in (preview_url, download_url):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 302)
            self.assertIn('/accounts/login/', response['Location'])

    def test_files_remain_readable_after_each_freeze_transition(self):
        transitions = [
            (
                'approved',
                lambda req: approve_expense_request(req, actor=self.committee),
            ),
            (
                'denied',
                lambda req: deny_expense_request(
                    req, decision_note='Denegada por el comité', actor=self.committee
                ),
            ),
            (
                'withdrawn',
                lambda req: withdraw_expense_request(
                    req, reason='Retirada por el solicitante', actor=self.operator
                ),
            ),
            (
                'annulled',
                lambda req: annul_expense_request(
                    req, reason='Anulada por administración', actor=self.admin
                ),
            ),
        ]
        for label, mutate in transitions:
            with self.subTest(label=label):
                pending = create_expense_request(
                    fund_allocation=self.allocation,
                    requested_amount=Decimal('9.00'),
                    purpose=f'Read after {label}',
                    requested_date=TEST_DATE,
                    actor=self.operator,
                )
                attachment = self._add(
                    filename=f'{label}.pdf',
                    content=PDF_BYTES,
                    title=label,
                    request_obj=pending,
                )
                mutate(pending)
                preview_url, download_url = self._urls(attachment, request_obj=pending)
                self.client.force_login(self.admin)
                self.assertEqual(self.client.get(preview_url).status_code, 200)
                download = self.client.get(download_url)
                self.assertEqual(download.status_code, 200)
                self.assertEqual(b''.join(download.streaming_content), PDF_BYTES)
                self.assertTrue(ExpenseRequestAttachment.objects.filter(pk=attachment.pk).exists())

        pending = create_expense_request(
            fund_allocation=self.allocation,
            requested_amount=Decimal('10.00'),
            purpose='Read after fulfilled',
            requested_date=TEST_DATE,
            actor=self.operator,
        )
        attachment = self._add(
            filename='fulfilled.pdf',
            content=PDF_BYTES,
            title='fulfilled',
            request_obj=pending,
        )
        approved = approve_expense_request(pending, actor=self.committee)
        fulfill_expense_request(
            approved,
            expense_date=TEST_DATE,
            amount=Decimal('10.00'),
            reason='Ejecución',
            provider_or_recipient='Proveedor',
            payment_method='bank_transfer',
            description='Detalle',
            support_file=SimpleUploadedFile('support.pdf', PDF_BYTES),
            support_title='Soporte',
            category='food',
            actor=self.admin,
        )
        preview_url, download_url = self._urls(attachment, request_obj=pending)
        self.client.force_login(self.committee)
        self.assertEqual(self.client.get(preview_url).status_code, 200)
        self.assertEqual(self.client.get(download_url).status_code, 200)

    def test_audit_failure_compensates_stored_upload_without_rows(self):
        storage = RecordingStorage(location=self.media.name)
        audit_count = AuditLog.objects.count()
        with (
            patch.object(
                ExpenseRequestAttachment._meta.get_field('file'),
                'storage',
                storage,
            ),
            patch(
                'apps.operations.expense_request_services.log_create',
                side_effect=RuntimeError('audit failed'),
            ),
        ):
            with self.assertRaises(RuntimeError):
                add_expense_request_attachments(
                    expense_request_id=self.request_obj.pk,
                    files=[SimpleUploadedFile('orphan.pdf', PDF_BYTES)],
                    title='Orphan',
                    actor=self.operator,
                )
        self.assertFalse(ExpenseRequestAttachment.objects.exists())
        self.assertEqual(AuditLog.objects.count(), audit_count)
        self.assertEqual(len(storage.saved), 1)
        self.assertEqual(len(storage.deleted), 1)

    def test_second_file_failure_rolls_back_all_rows_and_compensates(self):
        storage = RecordingStorage(location=self.media.name)
        original_save = storage.save

        def fail_second(name, content, max_length=None):
            if storage.saved:
                raise OSError('second file failed')
            return original_save(name, content, max_length)

        with (
            patch.object(
                ExpenseRequestAttachment._meta.get_field('file'),
                'storage',
                storage,
            ),
            patch.object(storage, 'save', side_effect=fail_second),
        ):
            with self.assertRaises(OSError):
                add_expense_request_attachments(
                    expense_request_id=self.request_obj.pk,
                    files=[
                        SimpleUploadedFile('one.pdf', PDF_BYTES),
                        SimpleUploadedFile('two.pdf', PDF_BYTES),
                    ],
                    title='Multi',
                    actor=self.operator,
                )
        self.assertFalse(ExpenseRequestAttachment.objects.exists())
        self.assertEqual(len(storage.saved), 1)
        self.assertEqual(len(storage.deleted), 1)

    def test_stale_parent_between_get_and_post_returns_404_without_files(self):
        self.client.force_login(self.operator)
        create_url = reverse(
            'expense_request_attachment_create', args=[self.request_obj.pk]
        )
        self.assertEqual(self.client.get(create_url).status_code, 200)
        approve_expense_request(self.request_obj, actor=self.committee)
        response = self.client.post(
            create_url,
            {
                'title': 'Late',
                'files': SimpleUploadedFile('late.pdf', PDF_BYTES),
            },
        )
        self.assertEqual(response.status_code, 404)
        self.assertFalse(ExpenseRequestAttachment.objects.exists())

    def test_failed_delete_preserves_row_and_file(self):
        attachment = self._add(filename='keep.pdf', content=PDF_BYTES, title='Keep')
        stored_name = attachment.file.name
        storage = attachment.file.storage
        with patch.object(
            ExpenseRequestAttachment,
            'delete',
            side_effect=RuntimeError('db delete failed'),
        ):
            with self.assertRaises(RuntimeError):
                delete_expense_request_attachment(
                    expense_request_id=self.request_obj.pk,
                    attachment_id=attachment.pk,
                    actor=self.operator,
                )
        self.assertTrue(ExpenseRequestAttachment.objects.filter(pk=attachment.pk).exists())
        self.assertTrue(storage.exists(stored_name))
