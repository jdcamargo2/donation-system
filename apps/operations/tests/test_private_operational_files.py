from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.operations.models import Project, ProjectUpdateAttachment
from apps.operations.services import publish_project_update, register_advance
from apps.operations.tests.helpers import create_project, create_user


class PrivateOperationalFileDownloadTests(TestCase):
    def setUp(self):
        self.media = TemporaryDirectory()
        self.addCleanup(self.media.cleanup)
        self.override = override_settings(MEDIA_ROOT=self.media.name)
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.user = create_user()
        self.project = create_project()
        self.project.status = Project.Status.ACTIVE
        self.project.save(update_fields=('status',))
        self.update = register_advance(
            self.project.pk, 'Avance', 'Detalle', created_by=self.user, reported_by=self.user
        )
        self.attachment = ProjectUpdateAttachment.objects.create(
            project_update=self.update,
            file=SimpleUploadedFile('evidence.pdf', b'evidence'),
            uploaded_by=self.user,
        )

    def test_anonymous_access_redirects_to_login(self):
        response = self.client.get(reverse('project_update_attachment_download', args=[self.attachment.pk]))
        self.assertEqual(response.status_code, 302)

    def test_authenticated_user_without_permission_receives_403(self):
        user = get_user_model().objects.create_user('no-file-permission', password='pass-12345')
        self.client.force_login(user)
        response = self.client.get(reverse('project_update_attachment_download', args=[self.attachment.pk]))
        self.assertEqual(response.status_code, 403)

    def test_attachment_download_uses_safe_basename_and_does_not_mutate(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse('project_update_attachment_download', args=[self.attachment.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Disposition'], 'attachment; filename="evidence.pdf"')
        self.assertEqual(ProjectUpdateAttachment.objects.count(), 1)

    def test_published_attachment_download_remains_available_with_read_permission(self):
        publish_project_update(self.update.pk, self.user)
        self.client.force_login(self.user)

        response = self.client.get(reverse('project_update_attachment_download', args=[self.attachment.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Disposition'], 'attachment; filename="evidence.pdf"')
