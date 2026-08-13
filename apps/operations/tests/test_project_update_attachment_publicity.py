"""Public Project Update attachment publication and anonymous delivery (BUG-E2E-002)."""

from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse

from apps.operations.models import (
    AuditLog,
    Project,
    ProjectUpdateAttachment,
    ProjectUpdateImmutableError,
)
from apps.operations.role_services import sync_operation_roles
from apps.operations.roles import ROLE_FIELD_OPERATOR, ROLE_SIGEDON_ADMIN
from apps.operations.services import (
    add_project_update_attachment,
    publish_project_update,
    publish_project_update_attachment,
    register_advance,
    unpublish_project,
    unpublish_project_update_attachment,
)
from apps.operations.tests.helpers import create_project, create_user
from apps.public_portal.selectors import (
    get_public_project_update_detail,
    get_public_update_documents,
)


def _user_for_role(username, role_name):
    user = get_user_model().objects.create_user(username=username, password='pass-12345')
    user.groups.add(Group.objects.get(name=role_name))
    return user


class ProjectUpdateAttachmentPublicityMigrationTests(TransactionTestCase):
    migrate_from = ('operations', '0030_expense_request_event_expense_protect')
    migrate_to = ('operations', '0031_projectupdateattachment_is_public')

    def setUp(self):
        self.executor = MigrationExecutor(connection)
        self.executor.migrate([self.migrate_from])
        old_apps = self.executor.loader.project_state([self.migrate_from]).apps
        Project = old_apps.get_model('operations', 'Project')
        ProjectUpdate = old_apps.get_model('operations', 'ProjectUpdate')
        ProjectUpdateAttachment = old_apps.get_model('operations', 'ProjectUpdateAttachment')
        project = Project.objects.create(
            code='PRJ-MIG-ATT-PUB',
            name='Migración adjunto público',
            status='active',
            is_public=False,
            estimated_budget=0,
        )
        update = ProjectUpdate.objects.create(
            project=project,
            title='Avance migración',
            description='Descripción',
            status='unpublished',
        )
        ProjectUpdateAttachment.objects.create(
            project_update=update,
            title='Adjunto preexistente',
            file='project_update_attachments/legacy.pdf',
        )

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_migration_adds_is_public_default_false_without_publishing_existing(self):
        self.executor = MigrationExecutor(connection)
        self.executor.migrate([self.migrate_to])
        apps = self.executor.loader.project_state([self.migrate_to]).apps
        ProjectUpdateAttachment = apps.get_model('operations', 'ProjectUpdateAttachment')
        attachment = ProjectUpdateAttachment.objects.get(title='Adjunto preexistente')
        self.assertFalse(attachment.is_public)


class ProjectUpdateAttachmentPublicityServiceTests(TestCase):
    def setUp(self):
        self.media = TemporaryDirectory()
        self.addCleanup(self.media.cleanup)
        self.settings_override = override_settings(MEDIA_ROOT=self.media.name)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.actor = create_user('attachment-publicity-actor')
        self.project = create_project(code='PRJ-ATT-PUB')
        self.project.status = Project.Status.ACTIVE
        self.project.is_public = True
        self.project.save(update_fields=('status', 'is_public'))

    def _unpublished_with_attachment(self, title='Evidencia'):
        update = register_advance(
            project_id=self.project.pk,
            title=title,
            description='Contenido del avance.',
            created_by=self.actor,
            reported_by=self.actor,
        )
        attachment = add_project_update_attachment(
            update_id=update.pk,
            title=title,
            file=SimpleUploadedFile('evidence.pdf', b'%PDF-1.4 evidence'),
            actor=self.actor,
        )
        return update, attachment

    def test_new_attachment_defaults_to_private(self):
        _update, attachment = self._unpublished_with_attachment()
        self.assertFalse(attachment.is_public)

    def test_publisher_can_mark_public_and_unpublish_without_deleting(self):
        update, attachment = self._unpublished_with_attachment()
        publish_project_update(update.pk, self.actor)

        published = publish_project_update_attachment(
            attachment_id=attachment.pk, actor=self.actor
        )
        self.assertTrue(published.is_public)
        self.assertTrue(
            AuditLog.objects.filter(
                entity_id=str(attachment.pk),
                action=AuditLog.Action.PUBLISHED,
            ).exists()
        )

        unpublished = unpublish_project_update_attachment(
            attachment_id=attachment.pk, actor=self.actor
        )
        unpublished.refresh_from_db()
        self.assertFalse(unpublished.is_public)
        self.assertTrue(bool(unpublished.file.name))
        self.assertTrue(
            AuditLog.objects.filter(
                entity_id=str(attachment.pk),
                action=AuditLog.Action.UNPUBLISHED,
            ).exists()
        )

    def test_publicity_transition_rejected_on_closed_project(self):
        update, attachment = self._unpublished_with_attachment(title='Cerrado')
        publish_project_update(update.pk, self.actor)
        self.project.status = Project.Status.CLOSED
        self.project.save(update_fields=('status', 'updated_at'))
        audit_count = AuditLog.objects.count()

        with self.assertRaises(ProjectUpdateImmutableError):
            publish_project_update_attachment(
                attachment_id=attachment.pk, actor=self.actor
            )

        attachment.refresh_from_db()
        self.assertFalse(attachment.is_public)
        self.assertEqual(AuditLog.objects.count(), audit_count)

    def test_ordinary_save_still_blocks_published_non_publicity_mutations(self):
        update, attachment = self._unpublished_with_attachment()
        publish_project_update(update.pk, self.actor)
        publish_project_update_attachment(attachment_id=attachment.pk, actor=self.actor)
        attachment.refresh_from_db()
        attachment.title = 'Intento de edición'
        with self.assertRaises(ProjectUpdateImmutableError):
            attachment.save()


class ProjectUpdateAttachmentPublicityViewTests(TestCase):
    def setUp(self):
        sync_operation_roles()
        self.media = TemporaryDirectory()
        self.addCleanup(self.media.cleanup)
        self.settings_override = override_settings(MEDIA_ROOT=self.media.name)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.admin = _user_for_role('att-pub-admin', ROLE_SIGEDON_ADMIN)
        self.operator = _user_for_role('att-pub-operator', ROLE_FIELD_OPERATOR)
        self.project = create_project(code='PRJ-ATT-VIEW')
        self.project.status = Project.Status.ACTIVE
        self.project.is_public = True
        self.project.save(update_fields=('status', 'is_public'))
        self.update = register_advance(
            project_id=self.project.pk,
            title='Avance UI publicidad',
            description='Descripción',
            created_by=self.admin,
            reported_by=self.admin,
        )
        self.attachment = add_project_update_attachment(
            update_id=self.update.pk,
            title='Documento UI',
            file=SimpleUploadedFile('ui.pdf', b'%PDF-1.4 ui'),
            actor=self.admin,
        )
        publish_project_update(self.update.pk, self.admin)

    def test_authorized_publisher_can_publish_via_post(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse('project_update_attachment_publish', args=[self.attachment.pk])
        )
        self.assertEqual(response.status_code, 302)
        self.attachment.refresh_from_db()
        self.assertTrue(self.attachment.is_public)

    def test_operator_cannot_publish_attachment(self):
        self.client.force_login(self.operator)
        response = self.client.post(
            reverse('project_update_attachment_publish', args=[self.attachment.pk])
        )
        self.assertEqual(response.status_code, 403)
        self.attachment.refresh_from_db()
        self.assertFalse(self.attachment.is_public)

    def test_get_cannot_mutate_publicity(self):
        self.client.force_login(self.admin)
        response = self.client.get(
            reverse('project_update_attachment_publish', args=[self.attachment.pk])
        )
        self.assertEqual(response.status_code, 405)
        self.attachment.refresh_from_db()
        self.assertFalse(self.attachment.is_public)

    def test_closed_project_blocks_publicity_post(self):
        self.project.status = Project.Status.CLOSED
        self.project.save(update_fields=('status', 'updated_at'))
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse('project_update_attachment_publish', args=[self.attachment.pk])
        )
        self.assertEqual(response.status_code, 403)
        self.attachment.refresh_from_db()
        self.assertFalse(self.attachment.is_public)


class PublicUpdateAttachmentPortalTests(TestCase):
    def setUp(self):
        self.media = TemporaryDirectory()
        self.addCleanup(self.media.cleanup)
        self.settings_override = override_settings(MEDIA_ROOT=self.media.name)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.user = create_user('public-att-user')
        self.project = create_project(code='PRJ-PUB-ATT')
        self.project.status = Project.Status.ACTIVE
        self.project.is_public = True
        self.project.save(update_fields=('status', 'is_public'))
        self.update = register_advance(
            project_id=self.project.pk,
            title='Avance con documentos',
            description='Texto público del avance.',
            created_by=self.user,
            reported_by=self.user,
        )
        self.public_attachment = add_project_update_attachment(
            update_id=self.update.pk,
            title='Documento público',
            file=SimpleUploadedFile('public.pdf', b'%PDF-1.4 public-doc'),
            actor=self.user,
        )
        self.private_attachment = add_project_update_attachment(
            update_id=self.update.pk,
            title='Documento interno',
            file=SimpleUploadedFile('private.pdf', b'%PDF-1.4 private-doc'),
            actor=self.user,
        )
        publish_project_update_attachment(
            attachment_id=self.public_attachment.pk, actor=self.user
        )
        publish_project_update(self.update.pk, self.user)

    def test_public_detail_shows_only_explicit_public_attachment(self):
        response = self.client.get(
            reverse('public_portal:public_project_update_detail', args=[self.update.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Documentos del avance')
        self.assertContains(response, 'Documento público')
        self.assertNotContains(response, 'Documento interno')
        self.assertNotContains(response, '/media/')
        download_url = reverse(
            'public_portal:public_project_update_attachment_download',
            args=[self.update.pk, self.public_attachment.pk],
        )
        private_download = reverse(
            'public_portal:public_project_update_attachment_download',
            args=[self.update.pk, self.private_attachment.pk],
        )
        self.assertContains(response, download_url)
        self.assertNotContains(response, private_download)
        private_internal = reverse(
            'project_update_attachment_download',
            args=[self.project.pk, self.update.pk, self.private_attachment.pk],
        )
        self.assertNotContains(response, private_internal)

    def test_selector_omits_section_payload_when_no_public_attachments(self):
        unpublish_project_update_attachment(
            attachment_id=self.public_attachment.pk, actor=self.user
        )
        update = get_public_project_update_detail(self.update.pk)
        self.assertEqual(get_public_update_documents(update), [])
        response = self.client.get(
            reverse('public_portal:public_project_update_detail', args=[self.update.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Documentos del avance')

    def test_selector_metadata_excludes_storage_and_uploader(self):
        update = get_public_project_update_detail(self.update.pk)
        payloads = get_public_update_documents(update)
        self.assertEqual(len(payloads), 1)
        payload = payloads[0]
        self.assertEqual(payload['title'], 'Documento público')
        self.assertNotIn('uploaded_by', payload)
        self.assertNotIn('storage', payload)
        self.assertNotIn(self.public_attachment.file.name, str(payload))

    def test_anonymous_public_download_and_preview_succeed(self):
        download_url = reverse(
            'public_portal:public_project_update_attachment_download',
            args=[self.update.pk, self.public_attachment.pk],
        )
        preview_url = reverse(
            'public_portal:public_project_update_attachment_preview',
            args=[self.update.pk, self.public_attachment.pk],
        )
        download = self.client.get(download_url)
        preview = self.client.get(preview_url)
        self.assertEqual(download.status_code, 200)
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(download['X-Content-Type-Options'], 'nosniff')
        self.assertIn('attachment', download['Content-Disposition'])
        self.assertIn('inline', preview['Content-Disposition'])
        self.assertIn('public', download['Cache-Control'])

    def test_private_unpublished_and_mismatched_urls_return_404(self):
        private_url = reverse(
            'public_portal:public_project_update_attachment_download',
            args=[self.update.pk, self.private_attachment.pk],
        )
        self.assertEqual(self.client.get(private_url).status_code, 404)

        other = register_advance(
            project_id=self.project.pk,
            title='Otro avance no publicado',
            description='Privado',
            created_by=self.user,
            reported_by=self.user,
        )
        other_attachment = add_project_update_attachment(
            update_id=other.pk,
            title='Adjunto de borrador',
            file=SimpleUploadedFile('draft.pdf', b'%PDF-1.4 draft'),
            actor=self.user,
        )
        publish_project_update_attachment(
            attachment_id=other_attachment.pk, actor=self.user
        )
        unpublished_url = reverse(
            'public_portal:public_project_update_attachment_download',
            args=[other.pk, other_attachment.pk],
        )
        self.assertEqual(self.client.get(unpublished_url).status_code, 404)

        mismatched = reverse(
            'public_portal:public_project_update_attachment_download',
            args=[other.pk, self.public_attachment.pk],
        )
        self.assertEqual(self.client.get(mismatched).status_code, 404)

    def test_private_project_hides_attachment_even_when_flagged_public(self):
        unpublish_project(project_id=self.project.pk, actor=self.user)
        detail = self.client.get(
            reverse('public_portal:public_project_update_detail', args=[self.update.pk])
        )
        self.assertEqual(detail.status_code, 404)
        download_url = reverse(
            'public_portal:public_project_update_attachment_download',
            args=[self.update.pk, self.public_attachment.pk],
        )
        self.assertEqual(self.client.get(download_url).status_code, 404)

    def test_missing_storage_object_returns_404(self):
        storage = self.public_attachment.file.storage
        storage.delete(self.public_attachment.file.name)
        download_url = reverse(
            'public_portal:public_project_update_attachment_download',
            args=[self.update.pk, self.public_attachment.pk],
        )
        self.assertEqual(self.client.get(download_url).status_code, 404)

    def test_provider_error_returns_503(self):
        download_url = reverse(
            'public_portal:public_project_update_attachment_download',
            args=[self.update.pk, self.public_attachment.pk],
        )
        from apps.operations.private_files import PrivateStorageUnavailable

        with patch(
            'apps.operations.private_files._stream_private_file',
            side_effect=PrivateStorageUnavailable,
        ):
            response = self.client.get(download_url)
        self.assertEqual(response.status_code, 503)

    def test_html_not_rendered_inline(self):
        html_update = register_advance(
            project_id=self.project.pk,
            title='Avance HTML',
            description='HTML',
            created_by=self.user,
            reported_by=self.user,
        )
        html_attachment = add_project_update_attachment(
            update_id=html_update.pk,
            title='Nota HTML',
            file=SimpleUploadedFile('note.html', b'<script>alert(1)</script>'),
            actor=self.user,
        )
        publish_project_update_attachment(
            attachment_id=html_attachment.pk, actor=self.user
        )
        publish_project_update(html_update.pk, self.user)
        preview_url = reverse(
            'public_portal:public_project_update_attachment_preview',
            args=[html_update.pk, html_attachment.pk],
        )
        download_url = reverse(
            'public_portal:public_project_update_attachment_download',
            args=[html_update.pk, html_attachment.pk],
        )
        self.assertEqual(self.client.get(preview_url).status_code, 404)
        download = self.client.get(download_url)
        self.assertEqual(download.status_code, 200)
        self.assertIn('attachment', download['Content-Disposition'])
