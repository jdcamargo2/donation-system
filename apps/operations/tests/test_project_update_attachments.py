from tempfile import TemporaryDirectory

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from apps.operations.admin import ProjectUpdateAttachmentAdmin
from apps.operations.models import (
    AuditLog,
    Project,
    ProjectUpdateAttachment,
    ProjectUpdateImmutableError,
)
from apps.operations.services import (
    add_project_update_attachment,
    delete_project_update_attachment,
    publish_project_update,
    register_advance,
)
from apps.operations.tests.helpers import create_project, create_user


class ProjectUpdateAttachmentImmutabilityTests(TestCase):
    def setUp(self):
        self.media = TemporaryDirectory()
        self.addCleanup(self.media.cleanup)
        self.settings_override = override_settings(MEDIA_ROOT=self.media.name)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.user = create_user('attachment-immutable-user')
        self.other_user = get_user_model().objects.create_user(
            username='attachment-other-user', password='pass-12345'
        )
        self.project = create_project(code='PRJ-ATTACHMENT-IMMUTABLE')
        self.project.status = Project.Status.ACTIVE
        self.project.save(update_fields=('status',))

    def create_draft(self, title='Avance con adjunto'):
        # PRE: self.project is ACTIVE and self.user is authenticated.
        # POST: returns one DRAFT advance eligible for attachment mutations.
        return register_advance(
            project_id=self.project.pk,
            title=title,
            description='Evidencia operativa del avance.',
            created_by=self.user,
        )

    def add_draft_attachment(self, update, title='Soporte inicial'):
        # PRE: update is DRAFT and the upload is an in-memory test file.
        # POST: creates one attachment through the official service and its audit event.
        return add_project_update_attachment(
            update_id=update.pk,
            title=title,
            file=SimpleUploadedFile('proof.pdf', b'proof-data'),
            actor=self.user,
        )

    def create_published_attachment(self):
        update = self.create_draft()
        attachment = self.add_draft_attachment(update)
        publish_project_update(update.pk, self.user)
        attachment.refresh_from_db()
        return update, attachment

    def test_service_and_route_reject_new_attachment_for_published_update_without_audit(self):
        update, _attachment = self.create_published_attachment()
        audit_count = AuditLog.objects.count()

        with self.assertRaises(ProjectUpdateImmutableError):
            self.add_draft_attachment(update, title='Adjunto tardío')
        with self.assertRaises(ProjectUpdateImmutableError):
            ProjectUpdateAttachment.objects.create(
                project_update=update,
                title='Adjunto directo tardío',
                file=SimpleUploadedFile('direct.pdf', b'direct-data'),
                uploaded_by=self.user,
            )

        self.client.force_login(self.user)
        response = self.client.post(
            reverse('project_update_attachment_create', args=[update.pk]),
            {'title': 'Adjunto tardío', 'file': SimpleUploadedFile('late.pdf', b'late-data')},
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(update.attachments.count(), 1)
        self.assertEqual(AuditLog.objects.count(), audit_count)

    def test_published_attachment_instance_save_preserves_all_protected_fields(self):
        _update, attachment = self.create_published_attachment()
        original = (
            attachment.file.name,
            attachment.title,
            attachment.project_update_id,
            attachment.uploaded_by_id,
        )
        draft_target = self.create_draft(title='Otro borrador')
        attachment.file = SimpleUploadedFile('replacement.pdf', b'replacement-data')
        attachment.title = 'Título alterado'
        attachment.project_update = draft_target
        attachment.uploaded_by = self.other_user

        with self.assertRaises(ProjectUpdateImmutableError):
            attachment.save()

        attachment.refresh_from_db()
        self.assertEqual(
            (attachment.file.name, attachment.title, attachment.project_update_id, attachment.uploaded_by_id),
            original,
        )

    def test_published_attachment_rejects_instance_and_queryset_deletion(self):
        _update, attachment = self.create_published_attachment()

        with self.assertRaises(ProjectUpdateImmutableError):
            attachment.delete()
        with self.assertRaises(ProjectUpdateImmutableError):
            ProjectUpdateAttachment.objects.filter(pk=attachment.pk).delete()

        self.assertTrue(ProjectUpdateAttachment.objects.filter(pk=attachment.pk).exists())

    def test_published_attachment_rejects_queryset_update_without_partial_change(self):
        _update, attachment = self.create_published_attachment()

        with self.assertRaises(ProjectUpdateImmutableError):
            ProjectUpdateAttachment.objects.filter(pk=attachment.pk).update(title='Título masivo')

        attachment.refresh_from_db()
        self.assertEqual(attachment.title, 'Soporte inicial')

    def test_draft_attachment_allows_service_and_ordinary_model_mutations(self):
        update = self.create_draft()
        attachment = self.add_draft_attachment(update)
        attachment.title = 'Título corregido'
        attachment.save()

        self.assertEqual(attachment.title, 'Título corregido')
        delete_project_update_attachment(attachment_id=attachment.pk, actor=self.user)

        self.assertFalse(ProjectUpdateAttachment.objects.filter(pk=attachment.pk).exists())

    def test_admin_restricts_published_attachment_mutations_but_keeps_view_access(self):
        _update, attachment = self.create_published_attachment()
        model_admin = ProjectUpdateAttachmentAdmin(ProjectUpdateAttachment, admin.site)
        request = RequestFactory().post('/admin/')
        request.user = self.user
        foreign_key_field = model_admin.formfield_for_foreignkey(
            ProjectUpdateAttachment._meta.get_field('project_update'),
            request,
        )

        self.assertTrue(model_admin.has_view_permission(request, attachment))
        self.assertFalse(model_admin.has_change_permission(request, attachment))
        self.assertFalse(model_admin.has_delete_permission(request, attachment))
        self.assertFalse(foreign_key_field.queryset.filter(pk=attachment.project_update_id).exists())
        candidate = ProjectUpdateAttachment(
            project_update=attachment.project_update,
            title='Adjunto administrativo tardío',
            file=SimpleUploadedFile('admin.pdf', b'admin-data'),
            uploaded_by=self.user,
        )
        with self.assertRaises(ProjectUpdateImmutableError):
            model_admin.save_model(request, candidate, form=None, change=False)
        with self.assertRaises(ProjectUpdateImmutableError):
            model_admin.delete_model(request, attachment)
        with self.assertRaises(ProjectUpdateImmutableError):
            model_admin.delete_queryset(request, ProjectUpdateAttachment.objects.filter(pk=attachment.pk))

        self.assertTrue(ProjectUpdateAttachment.objects.filter(pk=attachment.pk).exists())
