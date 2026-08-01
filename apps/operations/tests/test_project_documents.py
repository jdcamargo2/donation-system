from datetime import date
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.operations.models import Project, ProjectDocument, ProjectUpdateAttachment
from apps.operations.services import publish_project_update, register_advance
from apps.operations.tests.helpers import create_project, create_user


class ProjectDocumentTests(TestCase):
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

    def create_document(self):
        # PRE: self.project y self.user existen y MEDIA_ROOT es temporal.
        # POST: retorna un documento con archivo privado persistido para el proyecto.
        return ProjectDocument.objects.create(
            project=self.project,
            document_type=ProjectDocument.DocumentType.WORK_PLAN,
            title='Plan operativo',
            file=SimpleUploadedFile('plan.pdf', b'plan-data', content_type='application/pdf'),
            uploaded_by=self.user,
        )

    def create_draft(self):
        # PRE: el proyecto está activo.
        # POST: retorna un avance DRAFT apto para recibir adjuntos.
        return register_advance(
            self.project.pk,
            'Avance con adjuntos',
            'Registro operativo.',
            update_date=date(2026, 7, 12),
            created_by=self.user,
            reported_by=self.user,
        )

    def test_create_project_document(self):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse('project_document_create', args=[self.project.pk]),
            data={
                'document_type': ProjectDocument.DocumentType.PROPOSAL,
                'title': 'Propuesta inicial',
                'description': 'Documento base.',
                'file': SimpleUploadedFile('proposal.pdf', b'proposal'),
            },
        )
        document = ProjectDocument.objects.get(title='Propuesta inicial')
        self.assertRedirects(response, reverse('project_detail', args=[self.project.pk]))
        self.assertEqual(document.uploaded_by, self.user)

    def test_download_project_document_with_permission(self):
        document = self.create_document()
        self.client.force_login(self.user)
        response = self.client.get(reverse('project_document_download', args=[document.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Disposition'], 'attachment; filename="plan.pdf"')

    def test_download_without_permission_is_blocked(self):
        document = self.create_document()
        user = get_user_model().objects.create_user('sin-permiso', password='pass-12345')
        self.client.force_login(user)
        response = self.client.get(reverse('project_document_download', args=[document.pk]))
        self.assertEqual(response.status_code, 403)

    def test_create_project_update_with_multiple_attachments(self):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse('project_update_create_for_project', args=[self.project.pk]),
            data={
                'title': 'Avance múltiple',
                'description': 'Incluye dos archivos.',
                'update_date': '2026-07-12',
                'reported_by': self.user.pk,
                'attachments': [
                    SimpleUploadedFile('foto.jpg', b'photo'),
                    SimpleUploadedFile('reporte.pdf', b'report'),
                ],
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(ProjectUpdateAttachment.objects.count(), 2)

    def test_published_update_rejects_new_attachment(self):
        update = self.create_draft()
        publish_project_update(update.pk, self.user)
        self.client.force_login(self.user)
        response = self.client.post(
            reverse('project_update_attachment_create', args=[update.pk]),
            data={'files': SimpleUploadedFile('late.pdf', b'late')},
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(update.attachments.exists())

    def test_file_is_exposed_only_through_protected_download(self):
        document = self.create_document()
        self.client.force_login(self.user)
        response = self.client.get(reverse('project_detail', args=[self.project.pk]))
        self.assertContains(response, reverse('project_document_download', args=[document.pk]))
        self.assertNotContains(response, document.file.url)

    def test_detail_document_delete_uses_post_confirmation_with_get_fallback(self):
        document = self.create_document()
        self.client.force_login(self.user)
        delete_url = reverse('project_document_delete', args=[document.pk])

        response = self.client.get(reverse('project_detail', args=[self.project.pk]))
        content = response.content.decode()

        self.assertContains(response, f'href="{delete_url}"')
        self.assertContains(response, 'data-confirm-title="¿Eliminar este documento?"')
        self.assertIn(
            f'id="project-document-delete-form-{document.pk}" method="post" action="{delete_url}"',
            content,
        )
        self.assertIn('name="csrfmiddlewaretoken"', content)
        fallback_response = self.client.get(delete_url)
        self.assertEqual(fallback_response.status_code, 200)
        self.assertTrue(ProjectDocument.objects.filter(pk=document.pk).exists())

    def test_published_update_attachment_cannot_be_deleted(self):
        update = self.create_draft()
        attachment = ProjectUpdateAttachment.objects.create(
            project_update=update,
            file=SimpleUploadedFile('proof.pdf', b'proof'),
            uploaded_by=self.user,
        )
        publish_project_update(update.pk, self.user)
        self.client.force_login(self.user)
        response = self.client.post(reverse('project_update_attachment_delete', args=[attachment.pk]))
        self.assertEqual(response.status_code, 403)
        self.assertTrue(ProjectUpdateAttachment.objects.filter(pk=attachment.pk).exists())
