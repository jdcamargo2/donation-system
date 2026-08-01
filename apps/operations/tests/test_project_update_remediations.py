from pathlib import Path
from tempfile import TemporaryDirectory

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser, Permission
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.operations.forms import ProjectUpdateRemediationAttachmentForm
from apps.operations.models import (
    AuditLog,
    Project,
    ProjectUpdateRemediation,
    ProjectUpdateRemediationAttachment,
    ProjectUpdateRemediationError,
)
from apps.operations.services import (
    add_project_update_remediation_attachment,
    create_project_update_remediation,
    create_project_update_review,
    create_project_update_review_decision,
    delete_project_update_remediation_attachment,
    publish_project_update,
    register_advance,
    resolve_project_update_remediation,
    submit_project_update_remediation,
    update_project_update_remediation,
)
from apps.operations.tests.helpers import create_project, create_user


class ProjectUpdateRemediationTests(TestCase):
    def setUp(self):
        self.author = create_user('remediation-author')
        self.reviewer = create_user('remediation-reviewer')
        self.decider = create_user('remediation-decider')
        self.project = create_project(code='PRJ-REMEDIATION')
        self.project.status = Project.Status.ACTIVE
        self.project.save(update_fields=('status',))

    def decision(self, outcome='observed'):
        update = register_advance(
            self.project.pk, 'Avance observado', 'Contenido.', created_by=self.author, reported_by=self.author
        )
        publish_project_update(update.pk, self.author)
        review = create_project_update_review(update_id=update.pk, observations='Revisión.', actor=self.reviewer)
        return create_project_update_review_decision(
            review_id=review.pk, outcome=outcome, rationale='Fundamento.', actor=self.decider
        )

    def test_observed_allows_one_remediation_and_conforming_rejects(self):
        observed = self.decision()
        remediation = create_project_update_remediation(decision_id=observed.pk, response='Respuesta.', actor=self.author)
        self.assertEqual(remediation.status, ProjectUpdateRemediation.Status.DRAFT)
        with self.assertRaises(ProjectUpdateRemediationError):
            create_project_update_remediation(decision_id=observed.pk, response='Duplicado.', actor=self.author)
        with self.assertRaises(ProjectUpdateRemediationError):
            create_project_update_remediation(decision_id=self.decision('conforming').pk, response='No procede.', actor=self.author)

    def test_draft_can_change_response_and_attachments_then_submit(self):
        remediation = create_project_update_remediation(decision_id=self.decision().pk, response='Inicial.', actor=self.author)
        update_project_update_remediation(remediation_id=remediation.pk, response='Corregida.', actor=self.author)
        attachment = add_project_update_remediation_attachment(
            remediation_id=remediation.pk, title='Evidencia', file=SimpleUploadedFile('proof.pdf', b'proof'), actor=self.author
        )
        submitted = submit_project_update_remediation(remediation_id=remediation.pk, actor=self.author)
        self.assertEqual(submitted.status, ProjectUpdateRemediation.Status.SUBMITTED)
        with self.assertRaises(ProjectUpdateRemediationError):
            update_project_update_remediation(remediation_id=remediation.pk, response='Tardía.', actor=self.author)
        with self.assertRaises(ProjectUpdateRemediationError):
            delete_project_update_remediation_attachment(attachment_id=attachment.pk, actor=self.author)

    def test_resolution_requires_submitted_terminal_status_and_notes(self):
        remediation = create_project_update_remediation(decision_id=self.decision().pk, response='Respuesta.', actor=self.author)
        with self.assertRaises(ProjectUpdateRemediationError):
            resolve_project_update_remediation(remediation_id=remediation.pk, status='accepted', resolution_notes='Notas.', actor=self.reviewer)
        submit_project_update_remediation(remediation_id=remediation.pk, actor=self.author)
        with self.assertRaises(ValidationError):
            resolve_project_update_remediation(remediation_id=remediation.pk, status='accepted', resolution_notes=' ', actor=self.reviewer)
        resolved = resolve_project_update_remediation(remediation_id=remediation.pk, status='accepted', resolution_notes='Conforme.', actor=self.reviewer)
        self.assertEqual(resolved.status, ProjectUpdateRemediation.Status.ACCEPTED)
        with self.assertRaises(ProjectUpdateRemediationError):
            resolve_project_update_remediation(remediation_id=remediation.pk, status='rejected', resolution_notes='Otra.', actor=self.reviewer)

    def test_anonymous_actor_and_bulk_mutation_fail_without_partial_changes(self):
        remediation = create_project_update_remediation(decision_id=self.decision().pk, response='Respuesta.', actor=self.author)
        with self.assertRaises(ProjectUpdateRemediationError):
            submit_project_update_remediation(remediation_id=remediation.pk, actor=AnonymousUser())
        submit_project_update_remediation(remediation_id=remediation.pk, actor=self.author)
        with self.assertRaises(ProjectUpdateRemediationError):
            ProjectUpdateRemediation.objects.filter(pk=remediation.pk).update(response='Evasión.')
        remediation.refresh_from_db()
        self.assertEqual(remediation.response, 'Respuesta.')
        self.assertTrue(AuditLog.objects.filter(entity_id=str(remediation.pk)).exists())


class ProjectUpdateRemediationAttachmentPreviewTests(TestCase):
    def setUp(self):
        self.media = TemporaryDirectory()
        self.addCleanup(self.media.cleanup)
        self.override = override_settings(MEDIA_ROOT=self.media.name)
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.author = create_user('remediation-preview-author')
        self.reviewer = create_user('remediation-preview-reviewer')
        self.decider = create_user('remediation-preview-decider')
        self.project = create_project(code='PRJ-REMEDIATION-PREVIEW')
        self.project.status = Project.Status.ACTIVE
        self.project.save(update_fields=('status',))

    def decision(self, outcome='observed'):
        update = register_advance(
            self.project.pk,
            'Avance observado preview',
            'Contenido.',
            created_by=self.author,
            reported_by=self.author,
        )
        publish_project_update(update.pk, self.author)
        review = create_project_update_review(
            update_id=update.pk, observations='Revisión.', actor=self.reviewer,
        )
        return create_project_update_review_decision(
            review_id=review.pk, outcome=outcome, rationale='Fundamento.', actor=self.decider,
        )

    def create_draft_remediation(self):
        return create_project_update_remediation(
            decision_id=self.decision().pk,
            response='Respuesta en borrador.',
            actor=self.author,
        )

    def create_url(self, remediation):
        return reverse('project_update_remediation_attachment_create', args=[remediation.pk])

    def assert_single_file_input_without_multiple(self, content):
        self.assertEqual(content.count('type="file"'), 1)
        self.assertIn('name="file"', content)
        self.assertNotIn('multiple', content.split('type="file"')[1].split('>')[0])

    def assert_preview_mounts_present(self, response):
        self.assertContains(response, 'data-file-upload-preview')
        self.assertContains(response, 'class="ops-file-upload"')
        self.assertContains(response, 'data-file-upload-list')
        self.assertContains(response, 'data-file-upload-summary')

    def test_form_opts_into_single_file_upload_preview(self):
        form = ProjectUpdateRemediationAttachmentForm()
        field = form.fields['file']

        self.assertTrue(field.required)
        self.assertIsInstance(field.widget, forms.FileInput)
        self.assertNotIsInstance(field.widget, forms.ClearableFileInput)
        self.assertFalse(field.widget.allow_multiple_selected)
        self.assertEqual(field.widget.attrs.get('data-file-upload-preview'), 'true')
        self.assertNotIn('multiple', field.widget.attrs)
        rendered = str(field.widget.render('file', None))
        self.assertIn('data-file-upload-preview="true"', rendered)
        self.assertNotIn('multiple', rendered)

    def test_create_page_renders_file_upload_preview_contract(self):
        remediation = self.create_draft_remediation()
        self.client.force_login(self.author)

        response = self.client.get(self.create_url(remediation))
        content = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'enctype="multipart/form-data"')
        self.assertContains(response, 'csrfmiddlewaretoken')
        self.assertContains(response, 'name="title"')
        self.assertContains(response, 'name="file"')
        self.assertContains(response, 'type="submit"')
        self.assertContains(response, 'Cancelar')
        self.assert_preview_mounts_present(response)
        self.assert_single_file_input_without_multiple(content)
        self.assertContains(response, 'class="ops-file-upload-preview"')
        self.assertContains(response, 'class="ops-file-upload-summary"')

        preview_list_chunk = content.split('data-file-upload-list', 1)[1].split('</div>', 1)[0]
        self.assertNotIn('ops-file-upload-item', preview_list_chunk)
        self.assertNotIn('<li', preview_list_chunk)

    def test_validation_redisplay_keeps_preview_mounts_without_creating_rows(self):
        remediation = self.create_draft_remediation()
        self.client.force_login(self.author)
        audit_before = AuditLog.objects.filter(
            action=AuditLog.Action.CREATED,
            summary='Adjunto de remediación agregado.',
        ).count()

        response = self.client.post(
            self.create_url(remediation),
            data={'title': 'Sin archivo'},
        )
        content = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertFormError(response.context['form'], 'file', 'Este campo es obligatorio.')
        self.assertContains(response, 'role="alert"')
        self.assert_preview_mounts_present(response)
        self.assert_single_file_input_without_multiple(content)
        self.assertFalse(
            ProjectUpdateRemediationAttachment.objects.filter(remediation=remediation).exists()
        )
        self.assertEqual(
            AuditLog.objects.filter(
                action=AuditLog.Action.CREATED,
                summary='Adjunto de remediación agregado.',
            ).count(),
            audit_before,
        )

    def test_successful_create_persists_one_attachment_and_audit(self):
        remediation = self.create_draft_remediation()
        self.client.force_login(self.author)

        response = self.client.post(
            self.create_url(remediation),
            data={
                'title': 'Evidencia remediación',
                'file': SimpleUploadedFile(
                    'evidencia.pdf', b'evidencia-bytes', content_type='application/pdf',
                ),
            },
        )

        self.assertRedirects(
            response,
            reverse('project_update_remediation_detail', args=[remediation.pk]),
        )
        attachments = list(
            ProjectUpdateRemediationAttachment.objects.filter(remediation=remediation)
        )
        self.assertEqual(len(attachments), 1)
        attachment = attachments[0]
        self.assertEqual(attachment.title, 'Evidencia remediación')
        self.assertTrue(attachment.file.name.endswith('evidencia.pdf'))
        self.assertEqual(attachment.remediation_id, remediation.pk)
        self.assertEqual(
            AuditLog.objects.filter(
                action=AuditLog.Action.CREATED,
                summary='Adjunto de remediación agregado.',
                entity_id=str(attachment.pk),
            ).count(),
            1,
        )

        detail = self.client.get(
            reverse('project_update_remediation_detail', args=[remediation.pk]),
        )
        self.assertContains(detail, 'Evidencia remediación')
        self.assertContains(
            detail,
            reverse('project_update_remediation_attachment_download', args=[attachment.pk]),
        )
        self.assertNotContains(detail, attachment.file.url)

        create_page = self.client.get(self.create_url(remediation))
        create_content = create_page.content.decode()
        preview_list_chunk = create_content.split('data-file-upload-list', 1)[1].split('</div>', 1)[0]
        self.assertNotIn('Evidencia remediación', preview_list_chunk)
        self.assertNotIn(Path(attachment.file.name).name, preview_list_chunk)

    def test_non_draft_post_returns_403_without_creating_rows(self):
        remediation = self.create_draft_remediation()
        submit_project_update_remediation(remediation_id=remediation.pk, actor=self.author)
        self.client.force_login(self.author)
        audit_before = AuditLog.objects.filter(
            action=AuditLog.Action.CREATED,
            summary='Adjunto de remediación agregado.',
        ).count()

        response = self.client.post(
            self.create_url(remediation),
            data={
                'title': 'Tarde',
                'file': SimpleUploadedFile('late.pdf', b'late'),
            },
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(
            ProjectUpdateRemediationAttachment.objects.filter(remediation=remediation).exists()
        )
        self.assertEqual(
            AuditLog.objects.filter(
                action=AuditLog.Action.CREATED,
                summary='Adjunto de remediación agregado.',
            ).count(),
            audit_before,
        )

    def test_accepted_and_rejected_post_remain_rejected(self):
        for terminal_status, notes in (
            ('accepted', 'Aceptada.'),
            ('rejected', 'Rechazada.'),
        ):
            with self.subTest(status=terminal_status):
                remediation = self.create_draft_remediation()
                submit_project_update_remediation(
                    remediation_id=remediation.pk, actor=self.author,
                )
                resolve_project_update_remediation(
                    remediation_id=remediation.pk,
                    status=terminal_status,
                    resolution_notes=notes,
                    actor=self.reviewer,
                )
                self.client.force_login(self.author)

                response = self.client.post(
                    self.create_url(remediation),
                    data={
                        'title': 'Terminal',
                        'file': SimpleUploadedFile(f'{terminal_status}.pdf', b'x'),
                    },
                )

                self.assertEqual(response.status_code, 403)
                self.assertFalse(
                    ProjectUpdateRemediationAttachment.objects.filter(
                        remediation=remediation,
                    ).exists()
                )

    def test_user_without_add_permission_cannot_create_attachment(self):
        remediation = self.create_draft_remediation()
        limited = get_user_model().objects.create_user(
            username='no-remediation-attach', password='pass-12345',
        )
        self.client.force_login(limited)

        response = self.client.post(
            self.create_url(remediation),
            data={
                'title': 'Sin permiso',
                'file': SimpleUploadedFile('denied.pdf', b'denied'),
            },
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(
            ProjectUpdateRemediationAttachment.objects.filter(remediation=remediation).exists()
        )

    def test_authorized_download_succeeds(self):
        remediation = self.create_draft_remediation()
        attachment = add_project_update_remediation_attachment(
            remediation_id=remediation.pk,
            title='Descargable',
            file=SimpleUploadedFile(
                'downloadable.pdf', b'download-bytes', content_type='application/pdf',
            ),
            actor=self.author,
        )
        self.client.force_login(self.author)

        response = self.client.get(
            reverse('project_update_remediation_attachment_download', args=[attachment.pk]),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(b''.join(response.streaming_content), b'download-bytes')
        self.assertIn('attachment;', response['Content-Disposition'])
        self.assertIn('downloadable.pdf', response['Content-Disposition'])

    def test_user_without_view_permission_cannot_download(self):
        remediation = self.create_draft_remediation()
        attachment = add_project_update_remediation_attachment(
            remediation_id=remediation.pk,
            title='Restringido',
            file=SimpleUploadedFile('restricted.pdf', b'restricted'),
            actor=self.author,
        )
        limited = get_user_model().objects.create_user(
            username='no-remediation-download', password='pass-12345',
        )
        self.client.force_login(limited)

        response = self.client.get(
            reverse('project_update_remediation_attachment_download', args=[attachment.pk]),
        )

        self.assertEqual(response.status_code, 403)

    def test_missing_stored_file_download_returns_404(self):
        remediation = self.create_draft_remediation()
        attachment = add_project_update_remediation_attachment(
            remediation_id=remediation.pk,
            title='Ausente',
            file=SimpleUploadedFile('missing.pdf', b'missing'),
            actor=self.author,
        )
        Path(attachment.file.path).unlink()
        self.client.force_login(self.author)

        response = self.client.get(
            reverse('project_update_remediation_attachment_download', args=[attachment.pk]),
        )

        self.assertEqual(response.status_code, 404)

    def test_detail_does_not_expose_direct_storage_url(self):
        remediation = self.create_draft_remediation()
        attachment = add_project_update_remediation_attachment(
            remediation_id=remediation.pk,
            title='Privado',
            file=SimpleUploadedFile('private.pdf', b'private'),
            actor=self.author,
        )
        viewer = get_user_model().objects.create_user(
            username='remediation-viewer', password='pass-12345',
        )
        viewer.user_permissions.add(
            Permission.objects.get(codename='view_projectupdateremediation'),
            Permission.objects.get(codename='view_projectupdateremediationattachment'),
        )
        self.client.force_login(viewer)

        response = self.client.get(
            reverse('project_update_remediation_detail', args=[remediation.pk]),
        )

        self.assertContains(
            response,
            reverse('project_update_remediation_attachment_download', args=[attachment.pk]),
        )
        self.assertNotContains(response, attachment.file.url)
