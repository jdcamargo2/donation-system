"""Focused coverage for protected persisted-file preview and download (F1–F7)."""

from pathlib import Path
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.operations.file_access import (
    can_preview_persisted_file,
    get_safe_persisted_file_preview_type,
    sanitize_download_filename,
)
from apps.operations.models import (
    Project,
    ProjectDocument,
    ProjectUpdateAttachment,
    SupportingDocument,
)
from apps.operations.role_services import sync_operation_roles
from apps.operations.roles import (
    ROLE_EXTERNAL_AUDITOR,
    ROLE_FIELD_OPERATOR,
    ROLE_PROJECT_COMMITTEE,
)
from apps.operations.services import register_advance
from apps.operations.tests.helpers import (
    create_expense,
    create_institution,
    create_project,
    create_user,
)

# Minimal valid signatures for whitelist fixtures.
PNG_BYTES = (
    b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01'
    b'\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00'
    b'\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82'
)
PDF_BYTES = b'%PDF-1.1\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n'
JPEG_BYTES = (
    b'\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00'
    b'\xff\xd9'
)
TXT_BYTES = b'hello protected preview\n'
SVG_BYTES = b'<svg xmlns="http://www.w3.org/2000/svg"><rect/></svg>'
HTML_BYTES = b'<html><body>x</body></html>'
ZIP_BYTES = b'PK\x03\x04' + b'\x00' * 26
BIN_BYTES = b'\x00\x01\x02\x03unknown'


class ProtectedFileHelperTests(TestCase):
    def test_whitelist_resolves_controlled_mime(self):
        cases = {
            'a.png': 'image/png',
            'a.JPG': 'image/jpeg',
            'a.jpeg': 'image/jpeg',
            'a.webp': 'image/webp',
            'a.gif': 'image/gif',
            'a.pdf': 'application/pdf',
            'a.txt': 'text/plain; charset=utf-8',
        }
        for name, mime in cases.items():
            payload = {
                'a.png': PNG_BYTES,
                'a.JPG': JPEG_BYTES,
                'a.jpeg': JPEG_BYTES,
                'a.webp': b'RIFF....WEBP',
                'a.gif': b'GIF89a',
                'a.pdf': PDF_BYTES,
                'a.txt': TXT_BYTES,
            }[name]
            field = SimpleUploadedFile(name, payload)
            with self.subTest(name=name):
                self.assertEqual(get_safe_persisted_file_preview_type(field), mime)
                self.assertTrue(can_preview_persisted_file(field))

    def test_active_and_unknown_types_are_download_only(self):
        for name in ('x.svg', 'x.html', 'x.htm', 'x.xml', 'x.js', 'x.docx', 'x.bin', 'x.zip'):
            field = SimpleUploadedFile(name, b'x')
            with self.subTest(name=name):
                self.assertIsNone(get_safe_persisted_file_preview_type(field))
                self.assertFalse(can_preview_persisted_file(field))

    def test_filename_sanitization_strips_paths_and_control_chars(self):
        class _FakeField:
            name = 'project_update_attachments/2026/08/evil\r\n"name".pdf'

        safe = sanitize_download_filename(_FakeField())
        self.assertNotIn('/', safe)
        self.assertNotIn('\\', safe)
        self.assertNotIn('"', safe)
        self.assertNotIn('\r', safe)
        self.assertNotIn('\n', safe)


@override_settings()
class ProtectedFilePreviewEndpointTests(TestCase):
    def setUp(self):
        self.media = TemporaryDirectory()
        self.addCleanup(self.media.cleanup)
        self.override = override_settings(MEDIA_ROOT=self.media.name)
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.admin = create_user('preview-admin')
        self.project = create_project(code='PRJ-PREV-001')
        self.project.status = Project.Status.ACTIVE
        self.project.save(update_fields=('status',))
        self.update = register_advance(
            self.project.pk,
            'Avance preview',
            'Detalle',
            created_by=self.admin,
            reported_by=self.admin,
        )

    def _attachment(self, filename, content):
        return ProjectUpdateAttachment.objects.create(
            project_update=self.update,
            file=SimpleUploadedFile(filename, content),
            uploaded_by=self.admin,
            title=filename,
        )

    def _urls(self, attachment):
        args = (self.project.pk, self.update.pk, attachment.pk)
        return (
            reverse('project_update_attachment_preview', args=args),
            reverse('project_update_attachment_download', args=args),
        )

    def test_authorized_preview_png_and_pdf(self):
        self.client.force_login(self.admin)
        for filename, content, mime in (
            ('shot.png', PNG_BYTES, 'image/png'),
            ('doc.pdf', PDF_BYTES, 'application/pdf'),
        ):
            attachment = self._attachment(filename, content)
            preview_url, download_url = self._urls(attachment)
            with self.subTest(filename=filename):
                preview = self.client.get(preview_url)
                self.assertEqual(preview.status_code, 200)
                self.assertIn('inline;', preview['Content-Disposition'])
                self.assertEqual(preview['Content-Type'], mime)
                self.assertEqual(preview['X-Content-Type-Options'], 'nosniff')
                self.assertEqual(preview['Cache-Control'], 'private, no-store')

                download = self.client.get(download_url)
                self.assertEqual(download.status_code, 200)
                self.assertIn('attachment;', download['Content-Disposition'])
                self.assertEqual(download['X-Content-Type-Options'], 'nosniff')
                self.assertEqual(download['Cache-Control'], 'private, no-store')

    def test_unsupported_preview_returns_404_but_download_works(self):
        self.client.force_login(self.admin)
        for filename, content in (
            ('bad.svg', SVG_BYTES),
            ('page.html', HTML_BYTES),
            ('pack.zip', ZIP_BYTES),
            ('data.bin', BIN_BYTES),
        ):
            attachment = self._attachment(filename, content)
            preview_url, download_url = self._urls(attachment)
            with self.subTest(filename=filename):
                self.assertEqual(self.client.get(preview_url).status_code, 404)
                download = self.client.get(download_url)
                self.assertEqual(download.status_code, 200)
                self.assertIn('attachment;', download['Content-Disposition'])

    def test_missing_row_and_missing_physical_file_return_404(self):
        self.client.force_login(self.admin)
        missing_preview = reverse(
            'project_update_attachment_preview',
            args=[self.project.pk, self.update.pk, 999999],
        )
        self.assertEqual(self.client.get(missing_preview).status_code, 404)

        attachment = self._attachment('gone.pdf', PDF_BYTES)
        Path(attachment.file.path).unlink()
        preview_url, download_url = self._urls(attachment)
        self.assertEqual(self.client.get(preview_url).status_code, 404)
        self.assertEqual(self.client.get(download_url).status_code, 404)

    def test_mismatched_parent_ids_return_404(self):
        self.client.force_login(self.admin)
        attachment = self._attachment('scoped.pdf', PDF_BYTES)
        other_project = create_project(code='PRJ-PREV-OTHER')
        other_project.status = Project.Status.ACTIVE
        other_project.save(update_fields=('status',))
        other_update = register_advance(
            other_project.pk,
            'Otro',
            'Detalle',
            created_by=self.admin,
            reported_by=self.admin,
        )
        bad = reverse(
            'project_update_attachment_download',
            args=[other_project.pk, other_update.pk, attachment.pk],
        )
        self.assertEqual(self.client.get(bad).status_code, 404)

    def test_anonymous_denied(self):
        attachment = self._attachment('anon.pdf', PDF_BYTES)
        preview_url, download_url = self._urls(attachment)
        self.assertEqual(self.client.get(preview_url).status_code, 302)
        self.assertEqual(self.client.get(download_url).status_code, 302)

    def test_direct_permission_user_without_role_name(self):
        attachment = self._attachment('direct.png', PNG_BYTES)
        user = get_user_model().objects.create_user('direct-file', password='pass-12345')
        user.user_permissions.add(
            *Permission.objects.filter(
                content_type__app_label='operations',
                codename__in=(
                    'view_project',
                    'view_projectupdate',
                    'view_projectupdateattachment',
                ),
            )
        )
        self.client.force_login(user)
        preview_url, download_url = self._urls(attachment)
        self.assertEqual(self.client.get(preview_url).status_code, 200)
        self.assertEqual(self.client.get(download_url).status_code, 200)

    def test_ui_shows_ver_and_descargar_for_previewable(self):
        attachment = self._attachment('ui.png', PNG_BYTES)
        self.client.force_login(self.admin)
        response = self.client.get(reverse('project_update_detail', args=[self.update.pk]))
        content = response.content.decode()
        preview_url, download_url = self._urls(attachment)
        self.assertIn('>Ver</a>', content)
        self.assertIn('target="_blank"', content)
        self.assertIn('rel="noopener"', content)
        self.assertIn(preview_url, content)
        self.assertIn(download_url, content)
        self.assertNotIn(attachment.file.url, content)

    def test_ui_hides_ver_for_non_previewable(self):
        attachment = self._attachment('pack.zip', ZIP_BYTES)
        self.client.force_login(self.admin)
        response = self.client.get(reverse('project_update_detail', args=[self.update.pk]))
        content = response.content.decode()
        preview_url, download_url = self._urls(attachment)
        self.assertIn('Vista previa no disponible', content)
        self.assertNotIn(preview_url, content)
        self.assertIn(download_url, content)


class ProtectedFileRoleMatrixTests(TestCase):
    def setUp(self):
        self.media = TemporaryDirectory()
        self.addCleanup(self.media.cleanup)
        self.override = override_settings(MEDIA_ROOT=self.media.name)
        self.override.enable()
        self.addCleanup(self.override.disable)
        sync_operation_roles()
        self.admin = create_user('role-file-admin')
        self.project = create_project(code='PRJ-ROLE-FILE')
        self.project.status = Project.Status.ACTIVE
        self.project.save(update_fields=('status',))
        self.update = register_advance(
            self.project.pk,
            'Avance roles',
            'Detalle',
            created_by=self.admin,
            reported_by=self.admin,
        )
        self.attachment = ProjectUpdateAttachment.objects.create(
            project_update=self.update,
            file=SimpleUploadedFile('role.png', PNG_BYTES),
            uploaded_by=self.admin,
        )
        self.expense = create_expense()
        self.expense.allocation.project = self.project
        self.expense.allocation.save(update_fields=('project',))
        self.support = SupportingDocument.objects.create(
            expense=self.expense,
            title='Soporte rol',
            document=SimpleUploadedFile('support.pdf', PDF_BYTES),
        )
        self.institution = create_institution()
        self.institution.legal_document = SimpleUploadedFile('legal.pdf', PDF_BYTES)
        self.institution.save()
        self.project_document = ProjectDocument.objects.create(
            project=self.project,
            document_type=ProjectDocument.DocumentType.REPORT,
            title='Informe',
            file=SimpleUploadedFile('report.pdf', PDF_BYTES),
            uploaded_by=self.admin,
        )

    def _user_for_role(self, username, role_name):
        user = get_user_model().objects.create_user(username=username, password='pass-12345')
        user.groups.add(Group.objects.get(name=role_name))
        return user

    def test_operator_can_preview_update_evidence_and_project_support(self):
        operator = self._user_for_role('op-files', ROLE_FIELD_OPERATOR)
        self.client.force_login(operator)
        preview = reverse(
            'project_update_attachment_preview',
            args=[self.project.pk, self.update.pk, self.attachment.pk],
        )
        download = reverse(
            'project_update_attachment_download',
            args=[self.project.pk, self.update.pk, self.attachment.pk],
        )
        self.assertEqual(self.client.get(preview).status_code, 200)
        self.assertEqual(self.client.get(download).status_code, 200)

        support_download = reverse(
            'project_supporting_document_download',
            args=[self.project.pk, self.support.pk],
        )
        self.assertEqual(self.client.get(support_download).status_code, 200)

        # No Institution / ProjectDocument unless permitted.
        self.assertEqual(
            self.client.get(
                reverse('institution_legal_document_download', args=[self.institution.pk])
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.get(
                reverse(
                    'project_document_download',
                    args=[self.project.pk, self.project_document.pk],
                )
            ).status_code,
            403,
        )
        # No expense detail / financial surface.
        self.assertEqual(
            self.client.get(reverse('expense_detail', args=[self.expense.pk])).status_code,
            403,
        )

        detail = self.client.get(reverse('project_update_detail', args=[self.update.pk]))
        self.assertContains(detail, '>Ver</a>')
        self.assertContains(detail, '>Descargar</a>')
        self.assertNotContains(detail, '>Eliminar</button>')

    def test_committee_can_access_update_evidence(self):
        committee = self._user_for_role('committee-files', ROLE_PROJECT_COMMITTEE)
        self.client.force_login(committee)
        preview = reverse(
            'project_update_attachment_preview',
            args=[self.project.pk, self.update.pk, self.attachment.pk],
        )
        self.assertEqual(self.client.get(preview).status_code, 200)
        self.assertEqual(
            self.client.get(
                reverse(
                    'project_document_download',
                    args=[self.project.pk, self.project_document.pk],
                )
            ).status_code,
            200,
        )

    def test_auditor_can_access_support_not_update_attachment_without_perm(self):
        auditor = self._user_for_role('auditor-files', ROLE_EXTERNAL_AUDITOR)
        self.client.force_login(auditor)
        # Auditor lacks view_projectupdateattachment.
        self.assertEqual(
            self.client.get(
                reverse(
                    'project_update_attachment_download',
                    args=[self.project.pk, self.update.pk, self.attachment.pk],
                )
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.get(
                reverse(
                    'supporting_document_download',
                    args=[self.expense.pk, self.support.pk],
                )
            ).status_code,
            200,
        )


class ExpenseRequestAttachmentProtectedFileTests(TestCase):
    def setUp(self):
        from decimal import Decimal

        from apps.operations.expense_request_services import (
            add_expense_request_attachments,
            create_expense_request,
        )
        from apps.operations.tests.helpers import TEST_DATE, create_allocation

        self.media = TemporaryDirectory()
        self.addCleanup(self.media.cleanup)
        self.override = override_settings(MEDIA_ROOT=self.media.name)
        self.override.enable()
        self.addCleanup(self.override.disable)
        sync_operation_roles()
        self.admin = create_user('er6-prev-admin')
        self.operator = self._user('er6-prev-op', ROLE_FIELD_OPERATOR)
        self.other = self._user('er6-prev-op-b', ROLE_FIELD_OPERATOR)
        allocation = create_allocation(amount=Decimal('100.00'))
        self.request_obj = create_expense_request(
            fund_allocation=allocation,
            requested_amount=Decimal('20.00'),
            purpose='Preview ER6',
            requested_date=TEST_DATE,
            actor=self.operator,
        )
        self.attachment = add_expense_request_attachments(
            expense_request_id=self.request_obj.pk,
            files=[SimpleUploadedFile('er6.png', PNG_BYTES)],
            title='ER6 PNG',
            actor=self.operator,
        )[0]

    def _user(self, username, role_name):
        user = get_user_model().objects.create_user(username=username, password='pass-12345')
        user.groups.add(Group.objects.get(name=role_name))
        return user

    def test_owner_and_admin_preview_download_nested_scope(self):
        preview = reverse(
            'expense_request_attachment_preview',
            args=[self.request_obj.pk, self.attachment.pk],
        )
        download = reverse(
            'expense_request_attachment_download',
            args=[self.request_obj.pk, self.attachment.pk],
        )
        for actor in (self.operator, self.admin):
            self.client.force_login(actor)
            preview_response = self.client.get(preview)
            self.assertEqual(preview_response.status_code, 200)
            self.assertIn('inline;', preview_response['Content-Disposition'])
            self.assertEqual(preview_response['Content-Type'], 'image/png')
            download_response = self.client.get(download)
            self.assertEqual(download_response.status_code, 200)
            self.assertIn('attachment;', download_response['Content-Disposition'])

        self.client.force_login(self.other)
        self.assertEqual(self.client.get(preview).status_code, 404)
        self.assertEqual(self.client.get(download).status_code, 404)


class DebugMediaHardeningTests(TestCase):
    def test_core_urls_does_not_mount_private_media(self):
        import core.urls as core_urls

        source = Path(core_urls.__file__).read_text()
        self.assertNotIn('document_root=settings.MEDIA_ROOT', source)
        self.assertNotIn('static(settings.MEDIA_URL', source)
