"""BUG-E2E-001: CLOSED projects are read-only for advances and project documents."""

from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.operations.models import (
    Project,
    ProjectDocument,
    ProjectUpdate,
    ProjectUpdateAttachment,
    ProjectUpdateRemediationAttachment,
)
from apps.operations.role_services import sync_operation_roles
from apps.operations.roles import ROLE_EXTERNAL_AUDITOR
from apps.operations.services import (
    OperationalEntityFinalizedError,
    ProjectUpdateImmutableError,
    ProjectUpdateRemediationError,
    add_project_update_attachment,
    add_project_update_remediation_attachment,
    create_project_update_remediation,
    create_project_update_review,
    create_project_update_review_decision,
    ensure_project_allows_operational_mutation,
    project_allows_operational_mutation,
    publish_project_update,
    register_advance,
    update_project_update,
)
from apps.operations.tests.helpers import create_project, create_user


class ClosedProjectOperationalFreezeTests(TestCase):
    def setUp(self):
        self.media = TemporaryDirectory()
        self.addCleanup(self.media.cleanup)
        self.override = override_settings(MEDIA_ROOT=self.media.name)
        self.override.enable()
        self.addCleanup(self.override.disable)
        sync_operation_roles()
        self.admin = create_user('closed-freeze-admin')
        self.active = create_project(code='PRJ-FREEZE-ACTIVE', name='Activo freeze')
        self.active.status = Project.Status.ACTIVE
        self.active.save(update_fields=('status', 'updated_at'))
        self.closed = create_project(code='PRJ-FREEZE-CLOSED', name='Cerrado freeze')
        self.closed.status = Project.Status.CLOSED
        self.closed.save(update_fields=('status', 'updated_at'))

    def _create_document(self, project, *, title='Plan histórico'):
        return ProjectDocument.objects.create(
            project=project,
            document_type=ProjectDocument.DocumentType.WORK_PLAN,
            title=title,
            file=SimpleUploadedFile('plan.pdf', b'plan-bytes', content_type='application/pdf'),
            uploaded_by=self.admin,
        )

    def _create_unpublished_on_active(self, *, title='Avance previo'):
        return register_advance(
            project_id=self.active.pk,
            title=title,
            description='Contenido previo al cierre.',
            update_date=date(2026, 7, 12),
            created_by=self.admin,
            reported_by=self.admin,
        )

    def _close_project(self, project):
        project.status = Project.Status.CLOSED
        project.save(update_fields=('status', 'updated_at'))
        project.refresh_from_db()
        return project

    def test_helper_predicate_and_ensure(self):
        self.assertTrue(project_allows_operational_mutation(self.active))
        self.assertFalse(project_allows_operational_mutation(self.closed))
        ensure_project_allows_operational_mutation(self.active)
        with self.assertRaises(OperationalEntityFinalizedError):
            ensure_project_allows_operational_mutation(self.closed)

    def test_closed_project_detail_hides_mutation_ctas_keeps_history_notice(self):
        document = self._create_document(self.closed)
        self.client.force_login(self.admin)

        response = self.client.get(reverse('project_detail', args=[self.closed.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Registrar avance')
        self.assertNotContains(
            response,
            reverse('project_update_create_for_project', args=[self.closed.pk]),
        )
        self.assertNotContains(response, 'Agregar documento')
        self.assertNotContains(
            response,
            reverse('project_document_create', args=[self.closed.pk]),
        )
        self.assertNotContains(response, f'project-document-delete-form-{document.pk}')
        self.assertNotContains(response, '>Eliminar</a>')
        self.assertContains(
            response,
            reverse('project_document_download', args=[self.closed.pk, document.pk]),
        )
        self.assertContains(
            response,
            'Este proyecto está cerrado. Los avances y documentos se conservan como registro histórico.',
        )

    def test_active_project_detail_still_shows_permitted_actions(self):
        document = self._create_document(self.active, title='Plan activo')
        self.client.force_login(self.admin)

        response = self.client.get(reverse('project_detail', args=[self.active.pk]))

        self.assertContains(response, 'Registrar avance')
        self.assertContains(response, 'Agregar documento')
        self.assertContains(response, f'project-document-delete-form-{document.pk}')
        self.assertNotContains(
            response,
            'Este proyecto está cerrado. Los avances y documentos se conservan como registro histórico.',
        )

    def test_read_only_role_sees_view_download_only_on_closed_project(self):
        document = self._create_document(self.closed, title='Doc lectura')
        reader = get_user_model().objects.create_user(
            username='closed-freeze-reader', password='pass-12345'
        )
        for codename in (
            'view_project',
            'view_projectdocument',
            'view_projectupdate',
        ):
            reader.user_permissions.add(
                Permission.objects.get(content_type__app_label='operations', codename=codename)
            )
        self.client.force_login(reader)

        response = self.client.get(reverse('project_detail', args=[self.closed.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Registrar avance')
        self.assertNotContains(response, 'Agregar documento')
        self.assertNotContains(response, f'project-document-delete-form-{document.pk}')
        self.assertContains(response, 'Doc lectura')
        self.assertContains(
            response,
            reverse('project_document_download', args=[self.closed.pk, document.pk]),
        )
        self.assertContains(response, 'Descargar')

    def test_external_auditor_hides_mutation_ctas_on_closed_project(self):
        self._create_document(self.closed, title='Doc auditor')
        auditor = get_user_model().objects.create_user(
            username='closed-freeze-auditor', password='pass-12345'
        )
        auditor.groups.add(Group.objects.get(name=ROLE_EXTERNAL_AUDITOR))
        self.client.force_login(auditor)

        response = self.client.get(reverse('project_detail', args=[self.closed.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Registrar avance')
        self.assertNotContains(response, 'Agregar documento')
        self.assertNotContains(response, '>Eliminar</a>')
        self.assertContains(response, 'Doc auditor')

    def test_direct_post_create_update_on_closed_project_rejected(self):
        self.client.force_login(self.admin)
        before = ProjectUpdate.objects.count()

        response = self.client.post(
            reverse('project_update_create_for_project', args=[self.closed.pk]),
            data={
                'title': 'Avance prohibido',
                'description': 'No debe persistir.',
                'update_date': '2026-07-12',
                'reported_by': self.admin.pk,
            },
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(ProjectUpdate.objects.count(), before)
        self.assertFalse(
            ProjectUpdate.objects.filter(project=self.closed, title='Avance prohibido').exists()
        )

    def test_edit_unpublished_update_on_closed_project_rejected(self):
        update = self._create_unpublished_on_active(title='Avance a congelar')
        self._close_project(self.active)
        self.client.force_login(self.admin)

        with self.assertRaises(ProjectUpdateImmutableError):
            update_project_update(
                update_id=update.pk,
                project=self.active,
                title='Intento de edición',
                description='No debe guardarse.',
                update_date=date(2026, 7, 13),
                reported_by=self.admin,
                actor=self.admin,
            )
        response = self.client.post(
            reverse('project_update_update', args=[update.pk]),
            data={
                'project': self.active.pk,
                'title': 'Intento de edición vía vista',
                'description': 'No debe guardarse.',
                'update_date': '2026-07-13',
                'reported_by': self.admin.pk,
            },
        )
        self.assertEqual(response.status_code, 403)
        update.refresh_from_db()
        self.assertEqual(update.title, 'Avance a congelar')

    def test_attachment_mutations_on_closed_project_rejected(self):
        update = self._create_unpublished_on_active(title='Avance con adjunto')
        attachment = add_project_update_attachment(
            update_id=update.pk,
            title='Evidencia previa',
            file=SimpleUploadedFile('prior.pdf', b'prior'),
            actor=self.admin,
        )
        stored_name = attachment.file.name
        self.assertTrue(default_storage.exists(stored_name))
        self._close_project(self.active)
        self.client.force_login(self.admin)

        with self.assertRaises(ProjectUpdateImmutableError):
            add_project_update_attachment(
                update_id=update.pk,
                title='Adjunto tardío',
                file=SimpleUploadedFile('late.pdf', b'late'),
                actor=self.admin,
            )
        response = self.client.post(
            reverse('project_update_attachment_create', args=[update.pk]),
            data={'files': SimpleUploadedFile('route-late.pdf', b'late')},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(update.attachments.count(), 1)
        self.assertTrue(default_storage.exists(stored_name))

        delete_response = self.client.post(
            reverse('project_update_attachment_delete', args=[attachment.pk])
        )
        self.assertEqual(delete_response.status_code, 403)
        self.assertTrue(ProjectUpdateAttachment.objects.filter(pk=attachment.pk).exists())
        self.assertTrue(default_storage.exists(stored_name))

    def test_existing_updates_remain_readable_on_closed_project(self):
        update = self._create_unpublished_on_active(title='Histórico legible')
        publish_project_update(update.pk, self.admin)
        self._close_project(self.active)
        self.client.force_login(self.admin)

        detail = self.client.get(reverse('project_update_detail', args=[update.pk]))
        project_detail = self.client.get(reverse('project_detail', args=[self.active.pk]))

        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, 'Histórico legible')
        self.assertNotContains(detail, 'Agregar adjuntos')
        self.assertNotContains(detail, 'Publicar')
        self.assertEqual(project_detail.status_code, 200)
        self.assertContains(project_detail, 'Histórico legible')

    def test_remediation_attachment_rejected_on_closed_project(self):
        update = self._create_unpublished_on_active(title='Avance observado')
        publish_project_update(update.pk, self.admin)
        review = create_project_update_review(
            update_id=update.pk, observations='Observaciones.', actor=self.admin
        )
        decision = create_project_update_review_decision(
            review_id=review.pk,
            outcome='observed',
            rationale='Fundamento.',
            actor=self.admin,
        )
        remediation = create_project_update_remediation(
            decision_id=decision.pk, response='Respuesta.', actor=self.admin
        )
        self._close_project(self.active)
        self.client.force_login(self.admin)

        with self.assertRaises(ProjectUpdateRemediationError):
            add_project_update_remediation_attachment(
                remediation_id=remediation.pk,
                title='Tardío',
                file=SimpleUploadedFile('remediation-late.pdf', b'late'),
                actor=self.admin,
            )
        response = self.client.post(
            reverse('project_update_remediation_attachment_create', args=[remediation.pk]),
            data={
                'title': 'Tardío vía vista',
                'file': SimpleUploadedFile('remediation-route.pdf', b'late'),
            },
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(
            ProjectUpdateRemediationAttachment.objects.filter(remediation=remediation).exists()
        )

    def test_direct_post_add_document_on_closed_project_rejected_without_storage_write(self):
        self.client.force_login(self.admin)
        before_count = ProjectDocument.objects.filter(project=self.closed).count()
        media_before = {
            str(path.relative_to(self.media.name))
            for path in Path(self.media.name).rglob('*')
            if path.is_file()
        }

        response = self.client.post(
            reverse('project_document_create', args=[self.closed.pk]),
            data={
                'document_type': ProjectDocument.DocumentType.PROPOSAL,
                'title': 'Documento prohibido',
                'description': 'No debe persistir.',
                'file': SimpleUploadedFile('forbidden.pdf', b'forbidden'),
            },
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            ProjectDocument.objects.filter(project=self.closed).count(),
            before_count,
        )
        media_after = {
            str(path.relative_to(self.media.name))
            for path in Path(self.media.name).rglob('*')
            if path.is_file()
        }
        self.assertEqual(media_after, media_before)

    def test_direct_post_delete_document_on_closed_project_rejected_keeps_file(self):
        document = self._create_document(self.closed, title='Conservar')
        stored_name = document.file.name
        self.assertTrue(default_storage.exists(stored_name))
        self.client.force_login(self.admin)

        response = self.client.post(reverse('project_document_delete', args=[document.pk]))

        self.assertEqual(response.status_code, 403)
        self.assertTrue(ProjectDocument.objects.filter(pk=document.pk).exists())
        self.assertTrue(default_storage.exists(stored_name))

    def test_view_and_download_remain_allowed_for_authorized_user(self):
        document = self._create_document(self.closed, title='Descargable')
        self.client.force_login(self.admin)

        preview = self.client.get(
            reverse('project_document_preview', args=[self.closed.pk, document.pk])
        )
        download = self.client.get(
            reverse('project_document_download', args=[self.closed.pk, document.pk])
        )

        self.assertEqual(preview.status_code, 200)
        self.assertEqual(download.status_code, 200)
        self.assertIn('attachment', download['Content-Disposition'])

    def test_active_project_document_and_update_create_still_work(self):
        self.client.force_login(self.admin)

        doc_response = self.client.post(
            reverse('project_document_create', args=[self.active.pk]),
            data={
                'document_type': ProjectDocument.DocumentType.REPORT,
                'title': 'Documento activo',
                'description': 'Permitido.',
                'file': SimpleUploadedFile('active.pdf', b'active'),
            },
        )
        update_response = self.client.post(
            reverse('project_update_create_for_project', args=[self.active.pk]),
            data={
                'title': 'Avance activo',
                'description': 'Permitido.',
                'update_date': '2026-07-12',
                'reported_by': self.admin.pk,
            },
        )

        self.assertRedirects(doc_response, reverse('project_detail', args=[self.active.pk]))
        self.assertRedirects(update_response, reverse('project_detail', args=[self.active.pk]))
        self.assertTrue(
            ProjectDocument.objects.filter(project=self.active, title='Documento activo').exists()
        )
        self.assertTrue(
            ProjectUpdate.objects.filter(project=self.active, title='Avance activo').exists()
        )

    def test_unauthorized_user_cannot_infer_closed_project_via_document_create(self):
        stranger = get_user_model().objects.create_user(
            username='closed-freeze-stranger', password='pass-12345'
        )
        self.client.force_login(stranger)

        response = self.client.post(
            reverse('project_document_create', args=[self.closed.pk]),
            data={
                'document_type': ProjectDocument.DocumentType.PROPOSAL,
                'title': 'Sondeo',
                'file': SimpleUploadedFile('probe.pdf', b'probe'),
            },
        )

        # Permission gate remains authoritative before lifecycle disclosure.
        self.assertEqual(response.status_code, 403)
        self.assertFalse(
            ProjectDocument.objects.filter(project=self.closed, title='Sondeo').exists()
        )

    def test_anonymous_document_create_redirects_to_login(self):
        response = self.client.post(
            reverse('project_document_create', args=[self.closed.pk]),
            data={
                'document_type': ProjectDocument.DocumentType.PROPOSAL,
                'title': 'Anónimo',
                'file': SimpleUploadedFile('anon.pdf', b'anon'),
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.url)

    def test_admin_cannot_save_document_on_closed_project(self):
        from django.contrib.admin.sites import site

        from apps.operations.admin import ProjectDocumentAdmin

        model_admin = ProjectDocumentAdmin(ProjectDocument, site)
        document = ProjectDocument(
            project=self.closed,
            document_type=ProjectDocument.DocumentType.OTHER,
            title='Admin bypass',
            file=SimpleUploadedFile('admin.pdf', b'admin'),
            uploaded_by=self.admin,
        )
        with self.assertRaises(OperationalEntityFinalizedError):
            model_admin.save_model(request=None, obj=document, form=None, change=False)
        self.assertFalse(
            ProjectDocument.objects.filter(project=self.closed, title='Admin bypass').exists()
        )
