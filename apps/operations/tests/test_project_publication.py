from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import TestCase
from django.urls import reverse

from apps.operations.models import AuditLog, Project
from apps.operations.role_services import sync_operation_roles
from apps.operations.roles import (
    ROLE_EXTERNAL_AUDITOR,
    ROLE_FIELD_OPERATOR,
    ROLE_PROJECT_COMMITTEE,
    ROLE_SIGEDON_ADMIN,
)
from apps.operations.services import (
    InvalidStateTransitionError,
    finish_project,
    publish_project,
    unpublish_project,
)
from apps.operations.tests.helpers import create_project, create_user


class ProjectPublicationServiceTests(TestCase):
    def setUp(self):
        self.actor = create_user('publication-service-actor')

    def _active_private(self, code='PRJ-PUB-PRIVATE'):
        project = create_project(code=code, name='Proyecto privado')
        self.assertEqual(project.status, Project.Status.ACTIVE)
        self.assertFalse(project.is_public)
        return project

    def _active_public(self, code='PRJ-PUB-PUBLIC'):
        project = self._active_private(code=code)
        project.is_public = True
        project.save(update_fields=('is_public', 'updated_at'))
        return project

    def test_publish_private_active_project(self):
        project = self._active_private()

        published = publish_project(project_id=project.pk, actor=self.actor)

        self.assertTrue(published.is_public)
        self.assertEqual(published.status, Project.Status.ACTIVE)
        log = AuditLog.objects.get(
            entity_id=str(project.pk),
            action=AuditLog.Action.PUBLISHED,
        )
        self.assertIn('publicado en el portal público', log.summary)

    def test_unpublish_public_active_project(self):
        project = self._active_public()

        unpublished = unpublish_project(project_id=project.pk, actor=self.actor)

        self.assertFalse(unpublished.is_public)
        self.assertEqual(unpublished.status, Project.Status.ACTIVE)
        log = AuditLog.objects.get(
            entity_id=str(project.pk),
            action=AuditLog.Action.UNPUBLISHED,
        )
        self.assertIn('retirado del portal público', log.summary)

    def test_publish_rejects_already_public_without_mutation_or_audit(self):
        project = self._active_public('PRJ-PUB-ALREADY')
        audit_count = AuditLog.objects.count()

        with self.assertRaises(InvalidStateTransitionError):
            publish_project(project_id=project.pk, actor=self.actor)

        project.refresh_from_db()
        self.assertTrue(project.is_public)
        self.assertEqual(AuditLog.objects.count(), audit_count)

    def test_unpublish_rejects_private_without_mutation_or_audit(self):
        project = self._active_private('PRJ-UNPUB-PRIVATE')
        audit_count = AuditLog.objects.count()

        with self.assertRaises(InvalidStateTransitionError):
            unpublish_project(project_id=project.pk, actor=self.actor)

        project.refresh_from_db()
        self.assertFalse(project.is_public)
        self.assertEqual(AuditLog.objects.count(), audit_count)

    def test_closed_project_cannot_be_published(self):
        project = self._active_private('PRJ-PUB-CLOSED')
        finish_project(project.pk, actor=self.actor)
        audit_count = AuditLog.objects.count()

        with self.assertRaises(InvalidStateTransitionError):
            publish_project(project_id=project.pk, actor=self.actor)

        project.refresh_from_db()
        self.assertEqual(project.status, Project.Status.CLOSED)
        self.assertFalse(project.is_public)
        self.assertEqual(AuditLog.objects.count(), audit_count)

    def test_unpublish_rejects_inconsistent_closed_public_record(self):
        project = self._active_public('PRJ-CLOSED-PUBLIC')
        Project.objects.filter(pk=project.pk).update(
            status=Project.Status.CLOSED,
            is_public=True,
        )
        audit_count = AuditLog.objects.count()

        with self.assertRaises(InvalidStateTransitionError):
            unpublish_project(project_id=project.pk, actor=self.actor)

        project.refresh_from_db()
        self.assertEqual(project.status, Project.Status.CLOSED)
        self.assertTrue(project.is_public)
        self.assertEqual(AuditLog.objects.count(), audit_count)

    def test_finish_public_project_closes_and_unpublishes_with_one_audit(self):
        project = self._active_public('PRJ-FINISH-PUBLIC')

        finished = finish_project(project.pk, actor=self.actor)

        self.assertEqual(finished.status, Project.Status.CLOSED)
        self.assertFalse(finished.is_public)
        self.assertEqual(finished.terminal_reason, 'Proyecto terminado.')
        self.assertEqual(finished.terminal_by, self.actor)
        self.assertIsNotNone(finished.terminal_at)
        logs = AuditLog.objects.filter(entity_id=str(project.pk), action=AuditLog.Action.CLOSED)
        self.assertEqual(logs.count(), 1)
        self.assertIn('retirado del portal público', logs.get().summary)
        self.assertFalse(
            AuditLog.objects.filter(
                entity_id=str(project.pk),
                action=AuditLog.Action.UNPUBLISHED,
            ).exists()
        )

    @patch('apps.operations.services.schedule_public_portal_cache_invalidation')
    def test_successful_publish_unpublish_finish_invalidate_cache(self, invalidate_mock):
        project = self._active_private('PRJ-CACHE-OK')

        with self.captureOnCommitCallbacks(execute=True):
            publish_project(project_id=project.pk, actor=self.actor)
            unpublish_project(project_id=project.pk, actor=self.actor)
            publish_project(project_id=project.pk, actor=self.actor)
            finish_project(project.pk, actor=self.actor)

        self.assertEqual(invalidate_mock.call_count, 4)

    @patch('apps.operations.services.schedule_public_portal_cache_invalidation')
    def test_rejected_operations_do_not_invalidate_cache(self, invalidate_mock):
        private = self._active_private('PRJ-CACHE-FAIL-PRIV')
        public = self._active_public('PRJ-CACHE-FAIL-PUB')

        with self.assertRaises(InvalidStateTransitionError):
            publish_project(project_id=public.pk, actor=self.actor)
        with self.assertRaises(InvalidStateTransitionError):
            unpublish_project(project_id=private.pk, actor=self.actor)

        closed = self._active_private('PRJ-CACHE-FAIL-CLOSED')
        with self.captureOnCommitCallbacks(execute=True):
            finish_project(closed.pk, actor=self.actor)
        invalidate_mock.reset_mock()
        with self.assertRaises(InvalidStateTransitionError):
            publish_project(project_id=closed.pk, actor=self.actor)

        invalidate_mock.assert_not_called()


class ProjectPublicationHttpTests(TestCase):
    def setUp(self):
        sync_operation_roles()
        self.admin = get_user_model().objects.create_user(
            username='publication-admin',
            password='pass-12345',
        )
        self.admin.groups.add(Group.objects.get(name=ROLE_SIGEDON_ADMIN))
        self.project = create_project(code='PRJ-HTTP-PUB', name='Proyecto HTTP')
        self.client.force_login(self.admin)

    def test_publish_and_unpublish_accept_post_and_reject_get(self):
        publish_url = reverse('project_publish', args=[self.project.pk])
        unpublish_url = reverse('project_unpublish', args=[self.project.pk])

        self.assertEqual(self.client.get(publish_url).status_code, 405)
        self.assertRedirects(
            self.client.post(publish_url),
            reverse('project_detail', args=[self.project.pk]),
        )
        self.project.refresh_from_db()
        self.assertTrue(self.project.is_public)

        self.assertEqual(self.client.get(unpublish_url).status_code, 405)
        self.assertRedirects(
            self.client.post(unpublish_url),
            reverse('project_detail', args=[self.project.pk]),
        )
        self.project.refresh_from_db()
        self.assertFalse(self.project.is_public)

    def test_publish_rejects_post_without_csrf(self):
        csrf_client = self.client_class(enforce_csrf_checks=True)
        csrf_client.force_login(self.admin)

        response = csrf_client.post(reverse('project_publish', args=[self.project.pk]))

        self.assertEqual(response.status_code, 403)
        self.project.refresh_from_db()
        self.assertFalse(self.project.is_public)

    def test_unauthorized_users_receive_403(self):
        limited = get_user_model().objects.create_user(
            username='publication-limited',
            password='pass-12345',
        )
        limited.user_permissions.add(
            Permission.objects.get(
                content_type__app_label='operations',
                codename='change_project',
            )
        )
        self.client.force_login(limited)

        for url_name in ('project_publish', 'project_unpublish'):
            with self.subTest(url_name=url_name):
                self.assertEqual(
                    self.client.post(reverse(url_name, args=[self.project.pk])).status_code,
                    403,
                )

    def test_anonymous_users_are_redirected_to_login(self):
        self.client.logout()
        for url_name in ('project_publish', 'project_unpublish'):
            with self.subTest(url_name=url_name):
                response = self.client.post(reverse(url_name, args=[self.project.pk]))
                self.assertEqual(response.status_code, 302)
                self.assertIn(reverse('login'), response.url)


class ProjectPublicationUiTests(TestCase):
    def setUp(self):
        sync_operation_roles()
        self.admin = get_user_model().objects.create_user(
            username='publication-ui-admin',
            password='pass-12345',
        )
        self.admin.groups.add(Group.objects.get(name=ROLE_SIGEDON_ADMIN))
        self.viewer = get_user_model().objects.create_user(
            username='publication-ui-viewer',
            password='pass-12345',
        )
        self.viewer.user_permissions.add(
            Permission.objects.get(
                content_type__app_label='operations',
                codename='view_project',
            )
        )
        self.project = create_project(code='PRJ-UI-PUB', name='Proyecto UI')

    def test_private_active_shows_publish_only_to_permission_holders(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse('project_detail', args=[self.project.pk]))

        self.assertContains(response, 'Privado')
        self.assertContains(response, 'Publicar en portal')
        self.assertContains(response, reverse('project_publish', args=[self.project.pk]))
        self.assertNotContains(response, 'Retirar del portal')
        self.assertContains(response, 'Terminar proyecto')
        self.assertNotContains(response, 'Anular proyecto')
        self.assertNotContains(response, 'aria-label="Cambiar estado del proyecto"')

        self.client.force_login(self.viewer)
        viewer_response = self.client.get(reverse('project_detail', args=[self.project.pk]))
        self.assertContains(viewer_response, 'Privado')
        self.assertNotContains(viewer_response, 'Publicar en portal')
        self.assertNotContains(viewer_response, 'Retirar del portal')

    def test_public_active_shows_unpublish_control(self):
        self.project.is_public = True
        self.project.save(update_fields=('is_public', 'updated_at'))
        self.client.force_login(self.admin)

        response = self.client.get(reverse('project_detail', args=[self.project.pk]))

        self.assertContains(response, 'Público')
        self.assertContains(response, 'Retirar del portal')
        self.assertContains(response, reverse('project_unpublish', args=[self.project.pk]))
        self.assertNotContains(response, 'Publicar en portal')

    def test_closed_project_shows_neither_publication_control(self):
        finish_project(self.project.pk, actor=self.admin)
        self.client.force_login(self.admin)

        response = self.client.get(reverse('project_detail', args=[self.project.pk]))

        self.assertContains(response, 'Privado')
        self.assertNotContains(response, 'Publicar en portal')
        self.assertNotContains(response, 'Retirar del portal')
        self.assertNotContains(response, 'Terminar proyecto')

    def test_list_shows_visibility_badges(self):
        public = create_project(code='PRJ-UI-LIST-PUB', name='Proyecto list público')
        public.is_public = True
        public.save(update_fields=('is_public', 'updated_at'))
        self.client.force_login(self.admin)

        response = self.client.get(reverse('project_list'))
        content = response.content.decode()

        self.assertContains(response, 'Privado')
        self.assertContains(response, 'Público')
        self.assertContains(response, self.project.get_status_display())
        self.assertContains(response, 'project-actions-')
        self.assertContains(
            response,
            'd-inline-flex align-items-center gap-2 flex-wrap',
        )

        # Parse list status cells only (avoid brittle whole-page substring checks).
        status_cells = []
        for row in content.split('<tr>')[1:]:
            cells = row.split('<td>')
            if len(cells) < 3:
                continue
            status_cells.append(cells[2].split('</td>', 1)[0])

        public_cell = next(cell for cell in status_cells if 'Público' in cell)
        private_cell = next(cell for cell in status_cells if 'Privado' in cell)

        for cell in (public_cell, private_cell):
            self.assertIn(
                'class="d-inline-flex align-items-center gap-2 flex-wrap"',
                cell,
            )
            self.assertIn('class="badge ops-status-badge"', cell)
            self.assertNotIn('d-block', cell)
            self.assertNotIn('mt-1', cell)

        self.assertIn('text-bg-success', public_cell)
        self.assertIn('aria-label="Visibilidad: Público"', public_cell)
        self.assertIn('text-bg-secondary', private_cell)
        self.assertIn('aria-label="Visibilidad: Privado"', private_cell)

        # Project detail visibility markup remains independent of the list layout.
        detail = self.client.get(reverse('project_detail', args=[self.project.pk]))
        detail_content = detail.content.decode()
        self.assertContains(detail, 'Privado')
        self.assertNotIn(
            'd-inline-flex align-items-center gap-2 flex-wrap',
            detail_content,
        )


class ProjectPublicationRoleTests(TestCase):
    def setUp(self):
        sync_operation_roles()

    def test_only_sigedon_admin_receives_manage_project_publication(self):
        admin = get_user_model().objects.create_user('pub-role-admin', password='pass-12345')
        admin.groups.add(Group.objects.get(name=ROLE_SIGEDON_ADMIN))
        self.assertTrue(admin.has_perm('operations.manage_project_publication'))

        for role_name in (
            ROLE_FIELD_OPERATOR,
            ROLE_EXTERNAL_AUDITOR,
            ROLE_PROJECT_COMMITTEE,
        ):
            with self.subTest(role=role_name):
                user = get_user_model().objects.create_user(
                    f'pub-role-{role_name}',
                    password='pass-12345',
                )
                user.groups.add(Group.objects.get(name=role_name))
                self.assertFalse(user.has_perm('operations.manage_project_publication'))
