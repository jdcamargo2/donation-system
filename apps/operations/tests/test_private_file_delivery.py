"""Authorized private-file delivery against NoPathPrivateStorage (offline)."""

from __future__ import annotations

import logging
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import FileResponse
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.operations.private_files import PROTECTED_PREVIEW_CSP
from apps.operations.models import Project, ProjectUpdateAttachment
from apps.operations.services import register_advance
from apps.operations.tests.helpers import create_project, create_user
from core.tests.storage_backends import NoPathPrivateStorage

PNG_BYTES = (
    b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01'
    b'\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00'
    b'\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82'
)
PDF_BYTES = b'%PDF-1.1\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n'
ZIP_BYTES = b'PK\x03\x04' + b'\x00' * 26

NOPATH_BACKEND = 'core.tests.storage_backends.NoPathPrivateStorage'


def _storages(location: str) -> dict:
    return {
        'default': {
            'BACKEND': NOPATH_BACKEND,
            'OPTIONS': {
                'location': location,
                'base_url': 'https://fake-private-storage.test/',
            },
        },
        'staticfiles': {
            'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage',
        },
    }


@override_settings()
class PrivateFileDeliveryNoPathTests(TestCase):
    def setUp(self):
        self.media = TemporaryDirectory()
        self.addCleanup(self.media.cleanup)
        self.override = override_settings(
            MEDIA_ROOT=self.media.name,
            STORAGES=_storages(self.media.name),
            SIGEDON_PRIVATE_STORAGE='filesystem',
            SIGEDON_PRIVATE_FILE_DELIVERY='stream',
        )
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.admin = create_user('nopath-file-admin')
        self.project = create_project(code='PRJ-NOPATH-001')
        self.project.status = Project.Status.ACTIVE
        self.project.save(update_fields=('status',))
        self.update = register_advance(
            self.project.pk,
            'Avance nopath',
            'Detalle',
            created_by=self.admin,
            reported_by=self.admin,
        )

    def _storage(self) -> NoPathPrivateStorage:
        from django.core.files.storage import default_storage

        storage = default_storage
        if hasattr(storage, '_wrapped'):
            storage = storage._wrapped
        self.assertIsInstance(storage, NoPathPrivateStorage)
        return storage

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

    def test_authorized_download_and_preview(self):
        self.client.force_login(self.admin)
        attachment = self._attachment('shot.png', PNG_BYTES)
        storage = self._storage()
        with self.assertRaises(NotImplementedError):
            storage.path(attachment.file.name)

        preview_url, download_url = self._urls(attachment)
        preview = self.client.get(preview_url)
        self.assertEqual(preview.status_code, 200)
        self.assertIn('inline;', preview['Content-Disposition'])
        self.assertEqual(preview['Content-Type'], 'image/png')
        self.assertEqual(preview['Cache-Control'], 'private, no-store')
        self.assertEqual(preview['Content-Security-Policy'], PROTECTED_PREVIEW_CSP)
        self.assertIsInstance(preview, FileResponse)
        b''.join(preview.streaming_content)

        download = self.client.get(download_url)
        self.assertEqual(download.status_code, 200)
        self.assertIn('attachment;', download['Content-Disposition'])
        self.assertEqual(download['Cache-Control'], 'private, no-store')
        self.assertNotIn('Content-Security-Policy', download)
        self.assertIsInstance(download, FileResponse)
        b''.join(download.streaming_content)

    def test_forbidden_access(self):
        attachment = self._attachment('secret.pdf', PDF_BYTES)
        preview_url, download_url = self._urls(attachment)
        self.assertEqual(self.client.get(preview_url).status_code, 302)
        self.assertEqual(self.client.get(download_url).status_code, 302)

        limited = get_user_model().objects.create_user(
            'nopath-limited', password='pass-12345'
        )
        self.client.force_login(limited)
        self.assertIn(self.client.get(preview_url).status_code, (403, 404))
        self.assertIn(self.client.get(download_url).status_code, (403, 404))

    def test_missing_object_returns_404(self):
        self.client.force_login(self.admin)
        attachment = self._attachment('gone.pdf', PDF_BYTES)
        storage = self._storage()
        storage.delete(attachment.file.name)
        preview_url, download_url = self._urls(attachment)
        self.assertEqual(self.client.get(preview_url).status_code, 404)
        self.assertEqual(self.client.get(download_url).status_code, 404)

    def test_storage_read_failure(self):
        self.client.force_login(self.admin)
        attachment = self._attachment('fail.pdf', PDF_BYTES)
        storage = self._storage()
        storage.fail_open_names.add(attachment.file.name)
        _, download_url = self._urls(attachment)
        response = self.client.get(download_url)
        self.assertEqual(response.status_code, 404)

    def test_inline_vs_attachment_decision(self):
        self.client.force_login(self.admin)
        png = self._attachment('view.png', PNG_BYTES)
        zip_file = self._attachment('pack.zip', ZIP_BYTES)
        png_preview, png_download = self._urls(png)
        zip_preview, zip_download = self._urls(zip_file)

        preview = self.client.get(png_preview)
        self.assertEqual(preview.status_code, 200)
        self.assertIn('inline;', preview['Content-Disposition'])

        download = self.client.get(png_download)
        self.assertEqual(download.status_code, 200)
        self.assertIn('attachment;', download['Content-Disposition'])

        self.assertEqual(self.client.get(zip_preview).status_code, 404)
        zip_dl = self.client.get(zip_download)
        self.assertEqual(zip_dl.status_code, 200)
        self.assertIn('attachment;', zip_dl['Content-Disposition'])

    def test_content_disposition_sanitization(self):
        self.client.force_login(self.admin)
        nasty = 'evil\r\n"name".pdf'
        attachment = self._attachment(nasty, PDF_BYTES)
        _, download_url = self._urls(attachment)
        response = self.client.get(download_url)
        self.assertEqual(response.status_code, 200)
        disposition = response['Content-Disposition']
        self.assertNotIn('\r', disposition)
        self.assertNotIn('\n', disposition)
        self.assertRegex(disposition, r'filename="[^"\r\n]+"')
        filename_value = disposition.split('filename=', 1)[-1].strip().strip('"')
        self.assertNotIn('"', filename_value)
        self.assertNotIn('\r', filename_value)
        self.assertNotIn('\n', filename_value)

    def test_cache_control_private_no_store(self):
        self.client.force_login(self.admin)
        attachment = self._attachment('cache.pdf', PDF_BYTES)
        preview_url, download_url = self._urls(attachment)
        for url in (preview_url, download_url):
            response = self.client.get(url)
            self.assertEqual(response['Cache-Control'], 'private, no-store')
            self.assertEqual(response['X-Content-Type-Options'], 'nosniff')

    def test_url_absent_from_logs(self):
        self.client.force_login(self.admin)
        attachment = self._attachment('logged.png', PNG_BYTES)
        storage = self._storage()
        preview_url, download_url = self._urls(attachment)

        with self.assertLogs('sigedon.storage', level='DEBUG') as captured:
            # Force at least one logger call path by patching after success path.
            logging.getLogger('sigedon.storage').debug(
                'delivery_probe field=%s', attachment.file.name
            )
            self.assertEqual(self.client.get(preview_url).status_code, 200)
            self.assertEqual(self.client.get(download_url).status_code, 200)

        joined = '\n'.join(captured.output)
        self.assertNotIn('fake-private-storage.test', joined)
        self.assertNotIn('X-Amz-Signature', joined)
        # url() must not be required for stream mode.
        self.assertEqual(storage.url_generation_count, 0)

    def test_stream_mode_uses_file_response_without_path(self):
        self.client.force_login(self.admin)
        attachment = self._attachment('stream.pdf', PDF_BYTES)
        storage = self._storage()
        with self.assertRaises(NotImplementedError):
            storage.path(attachment.file.name)
        _, download_url = self._urls(attachment)
        response = self.client.get(download_url)
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response, FileResponse)
        self.assertEqual(storage.url_generation_count, 0)


@override_settings()
class PrivateFileSignedRedirectTests(TestCase):
    def setUp(self):
        self.media = TemporaryDirectory()
        self.addCleanup(self.media.cleanup)
        self.override = override_settings(
            MEDIA_ROOT=self.media.name,
            STORAGES=_storages(self.media.name),
            SIGEDON_PRIVATE_STORAGE='filesystem',
            SIGEDON_PRIVATE_FILE_DELIVERY='signed_redirect',
        )
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.admin = create_user('nopath-signed-admin')
        self.project = create_project(code='PRJ-NOPATH-SIGNED')
        self.project.status = Project.Status.ACTIVE
        self.project.save(update_fields=('status',))
        self.update = register_advance(
            self.project.pk,
            'Avance signed',
            'Detalle',
            created_by=self.admin,
            reported_by=self.admin,
        )

    def _storage(self) -> NoPathPrivateStorage:
        from django.core.files.storage import default_storage

        storage = default_storage
        if hasattr(storage, '_wrapped'):
            storage = storage._wrapped
        self.assertIsInstance(storage, NoPathPrivateStorage)
        return storage

    def _attachment(self):
        return ProjectUpdateAttachment.objects.create(
            project_update=self.update,
            file=SimpleUploadedFile('signed.pdf', PDF_BYTES),
            uploaded_by=self.admin,
        )

    def test_authorized_generates_one_url_unauthorized_zero(self):
        attachment = self._attachment()
        storage = self._storage()
        storage.url_generation_count = 0
        download_url = reverse(
            'project_update_attachment_download',
            args=[self.project.pk, self.update.pk, attachment.pk],
        )

        # Unauthorized: must not mint signed URLs.
        anon = self.client.get(download_url)
        self.assertEqual(anon.status_code, 302)
        self.assertEqual(storage.url_generation_count, 0)

        limited = get_user_model().objects.create_user(
            'nopath-signed-limited', password='pass-12345'
        )
        limited.user_permissions.add(
            *Permission.objects.filter(
                content_type__app_label='operations',
                codename='view_project',
            )
        )
        self.client.force_login(limited)
        forbidden = self.client.get(download_url)
        self.assertIn(forbidden.status_code, (403, 404))
        self.assertEqual(storage.url_generation_count, 0)

        self.client.force_login(self.admin)
        authorized = self.client.get(download_url)
        self.assertEqual(authorized.status_code, 302)
        self.assertEqual(storage.url_generation_count, 1)
        location = authorized['Location']
        self.assertIn('fake-private-storage.test', location)
        self.assertIn('X-Amz-Signature=', location)
        self.assertIn('ResponseContentDisposition=', location)
        self.assertIn('ResponseContentType=', location)
        self.assertIsNotNone(storage.last_url_parameters)
        self.assertIn('ResponseContentDisposition', storage.last_url_parameters)
        self.assertIn('ResponseContentType', storage.last_url_parameters)
        self.assertEqual(authorized['Cache-Control'], 'private, no-store')

    def test_parameters_passed_to_backend(self):
        self.client.force_login(self.admin)
        attachment = self._attachment()
        storage = self._storage()
        storage.url_generation_count = 0
        download_url = reverse(
            'project_update_attachment_download',
            args=[self.project.pk, self.update.pk, attachment.pk],
        )
        response = self.client.get(download_url)
        self.assertEqual(response.status_code, 302)
        params = storage.last_url_parameters
        self.assertEqual(params['ResponseContentType'], 'application/pdf')
        self.assertIn('attachment;', params['ResponseContentDisposition'])
        self.assertIn('filename="', params['ResponseContentDisposition'])

    def test_typeerror_falls_back_to_stream_without_bare_url(self):
        self.client.force_login(self.admin)
        attachment = self._attachment()
        storage = self._storage()
        storage.reject_url_parameters = True
        storage.url_generation_count = 0
        download_url = reverse(
            'project_update_attachment_download',
            args=[self.project.pk, self.update.pk, attachment.pk],
        )
        with self.assertLogs('sigedon.storage', level='INFO') as captured:
            response = self.client.get(download_url)
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response, FileResponse)
        self.assertIn('attachment;', response['Content-Disposition'])
        self.assertNotIn('Location', response)
        self.assertEqual(storage.url_generation_count, 0)
        joined = '\n'.join(captured.output)
        self.assertIn('parameters_typeerror', joined)
        self.assertNotIn('X-Amz-Signature', joined)
        self.assertNotIn('fake-private-storage.test', joined)
        b''.join(response.streaming_content)

    def test_unsupported_parameters_backend_streams(self):
        bare_backend = 'core.tests.storage_backends.BareUrlPrivateStorage'
        with override_settings(
            STORAGES={
                'default': {
                    'BACKEND': bare_backend,
                    'OPTIONS': {
                        'location': self.media.name,
                        'base_url': 'https://fake-private-storage.test/',
                    },
                },
                'staticfiles': {
                    'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage',
                },
            },
            SIGEDON_PRIVATE_FILE_DELIVERY='signed_redirect',
        ):
            from django.core.files.storage import storages

            attachment = ProjectUpdateAttachment.objects.create(
                project_update=self.update,
                file=SimpleUploadedFile('bare.pdf', PDF_BYTES),
                uploaded_by=self.admin,
            )
            storage = storages['default']
            if hasattr(storage, '_wrapped'):
                storage = storage._wrapped
            storage.url_generation_count = 0
            self.client.force_login(self.admin)
            download_url = reverse(
                'project_update_attachment_download',
                args=[self.project.pk, self.update.pk, attachment.pk],
            )
            response = self.client.get(download_url)
            self.assertEqual(response.status_code, 200)
            self.assertIsInstance(response, FileResponse)
            self.assertEqual(storage.url_generation_count, 0)
            b''.join(response.streaming_content)

    def test_inline_preview_always_streams_under_signed_redirect(self):
        self.client.force_login(self.admin)
        attachment = ProjectUpdateAttachment.objects.create(
            project_update=self.update,
            file=SimpleUploadedFile('preview.png', PNG_BYTES),
            uploaded_by=self.admin,
        )
        storage = self._storage()
        storage.url_generation_count = 0
        preview_url = reverse(
            'project_update_attachment_preview',
            args=[self.project.pk, self.update.pk, attachment.pk],
        )
        response = self.client.get(preview_url)
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response, FileResponse)
        self.assertIn('inline;', response['Content-Disposition'])
        self.assertIn('sandbox', response['Content-Security-Policy'])
        self.assertNotIn('unsafe-inline', response['Content-Security-Policy'])
        self.assertNotIn('unsafe-eval', response['Content-Security-Policy'])
        self.assertEqual(storage.url_generation_count, 0)
        self.assertNotIn('Location', response)
        b''.join(response.streaming_content)

    def test_signed_missing_object_404_and_provider_outage_503(self):
        self.client.force_login(self.admin)
        attachment = self._attachment()
        storage = self._storage()
        download_url = reverse(
            'project_update_attachment_download',
            args=[self.project.pk, self.update.pk, attachment.pk],
        )
        storage.delete(attachment.file.name)
        self.assertEqual(self.client.get(download_url).status_code, 404)

        attachment2 = self._attachment()
        storage.provider_unavailable_exists = True
        storage.url_generation_count = 0
        download_url2 = reverse(
            'project_update_attachment_download',
            args=[self.project.pk, self.update.pk, attachment2.pk],
        )
        response = self.client.get(download_url2)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(storage.url_generation_count, 0)
        self.assertNotIn('X-Amz-Signature', response.content.decode())
