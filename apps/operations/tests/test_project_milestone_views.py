import json

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.messages import get_messages
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.operations.forms import ProjectMilestoneForm
from apps.operations.models import AuditLog, Project, ProjectMilestone
from apps.operations.tests.helpers import create_project


MILESTONE_PERMISSION_CODENAMES = (
    'view_project',
    'add_projectmilestone',
    'change_projectmilestone',
    'complete_projectmilestone',
    'delete_projectmilestone',
    'reorder_projectmilestone',
)


class ProjectMilestoneViewTests(TestCase):
    def setUp(self):
        self.project = create_project()
        self.other_project = create_project(code='PRJ-MILESTONE-HTTP-OTHER')
        self.user = self.create_user_with_permissions(
            'milestone-http-user', *MILESTONE_PERMISSION_CODENAMES
        )
        self.client.force_login(self.user)
        self.first = ProjectMilestone.objects.create(
            project=self.project,
            title='Primer hito',
            description='Descripción inicial',
            position=1,
        )
        self.second = ProjectMilestone.objects.create(
            project=self.project,
            title='Segundo hito',
            position=2,
            is_completed=True,
            completed_at=timezone.now(),
            completed_by=self.user,
        )

    def create_user_with_permissions(self, username, *codenames):
        """
        PRE: codenames identify existing operations permissions required by one HTTP scenario.
        POST: returns an authenticated-capable user holding exactly those requested permissions.
        """
        user = get_user_model().objects.create_user(username=username, password='pass-12345')
        user.user_permissions.add(
            *Permission.objects.filter(
                content_type__app_label='operations',
                codename__in=codenames,
            )
        )
        return user

    def milestone_url(self, name, milestone=None):
        return reverse(name, args=[(milestone or self.first).pk])

    def project_redirect_url(self, project=None):
        return f'{reverse("project_detail", args=[(project or self.project).pk])}#project-milestones'

    def response_messages(self, response):
        return [str(message) for message in get_messages(response.wsgi_request)]

    def test_form_exposes_only_descriptive_fields(self):
        self.assertEqual(list(ProjectMilestoneForm().fields), ['title', 'description'])

    def test_add_get_renders_form_for_authorized_project_reader(self):
        response = self.client.get(
            reverse('project_milestone_add', args=[self.project.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'web/project_milestone_form.html')
        self.assertEqual(response.context['project'], self.project)
        self.assertEqual(list(response.context['form'].fields), ['title', 'description'])

    def test_add_post_uses_url_project_and_ignores_protected_fields(self):
        response = self.client.post(
            reverse('project_milestone_add', args=[self.project.pk]),
            {
                'title': '  Hito creado por HTTP  ',
                'description': 'Descripción web',
                'project': self.other_project.pk,
                'position': 99,
                'is_completed': 'on',
                'completed_at': timezone.now().isoformat(),
                'completed_by': self.user.pk,
                'created_by': self.user.pk,
            },
        )

        created = ProjectMilestone.objects.get(title='Hito creado por HTTP')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], self.project_redirect_url())
        self.assertEqual(created.project, self.project)
        self.assertEqual(created.position, 3)
        self.assertFalse(created.is_completed)
        self.assertIsNone(created.completed_at)
        self.assertIsNone(created.completed_by)
        self.assertEqual(created.created_by, self.user)
        self.assertIn('Hito creado.', self.response_messages(response))

    def test_add_invalid_post_preserves_form_without_creating(self):
        before_count = self.project.milestones.count()

        response = self.client.post(
            reverse('project_milestone_add', args=[self.project.pk]),
            {'title': '   ', 'description': 'Inválida'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.project.milestones.count(), before_count)
        self.assertTrue(response.context['form'].errors)

    def test_add_for_missing_project_returns_404(self):
        response = self.client.get(reverse('project_milestone_add', args=[999999]))

        self.assertEqual(response.status_code, 404)

    def test_add_terminal_project_redisplays_domain_error(self):
        self.project.status = Project.Status.CLOSED
        self.project.save(update_fields=('status', 'updated_at'))

        response = self.client.post(
            reverse('project_milestone_add', args=[self.project.pk]),
            {'title': 'No permitido', 'description': ''},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'cerrados o anulados no admiten cambios')
        self.assertFalse(self.project.milestones.filter(title='No permitido').exists())

    def test_edit_get_prepopulates_title_and_description(self):
        response = self.client.get(self.milestone_url('project_milestone_edit'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['form'].initial['title'], self.first.title)
        self.assertEqual(response.context['form'].initial['description'], self.first.description)

    def test_edit_post_changes_content_but_not_project_position_or_completion(self):
        response = self.client.post(
            self.milestone_url('project_milestone_edit'),
            {
                'title': '  Primer hito editado  ',
                'description': 'Descripción editada',
                'project': self.other_project.pk,
                'position': 50,
                'is_completed': 'on',
                'completed_at': timezone.now().isoformat(),
                'completed_by': self.user.pk,
            },
        )

        self.first.refresh_from_db()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], self.project_redirect_url())
        self.assertEqual(self.first.title, 'Primer hito editado')
        self.assertEqual(self.first.description, 'Descripción editada')
        self.assertEqual(self.first.project, self.project)
        self.assertEqual(self.first.position, 1)
        self.assertFalse(self.first.is_completed)
        self.assertIsNone(self.first.completed_at)
        self.assertIsNone(self.first.completed_by)

    def test_edit_no_op_does_not_duplicate_audit_or_claim_update(self):
        response = self.client.post(
            self.milestone_url('project_milestone_edit'),
            {'title': self.first.title, 'description': self.first.description},
        )

        self.assertFalse(
            AuditLog.objects.filter(
                entity_id=str(self.first.pk), action=AuditLog.Action.UPDATED
            ).exists()
        )
        self.assertIn('No se realizaron cambios', ' '.join(self.response_messages(response)))

    def test_edit_terminal_project_redisplays_domain_error(self):
        self.project.status = Project.Status.CLOSED
        self.project.save(update_fields=('status', 'updated_at'))

        response = self.client.post(
            self.milestone_url('project_milestone_edit'),
            {'title': 'Intento', 'description': ''},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'cerrados no admiten cambios')
        self.first.refresh_from_db()
        self.assertEqual(self.first.title, 'Primer hito')

    def test_complete_is_post_only_redirects_and_is_idempotent(self):
        url = self.milestone_url('project_milestone_complete')

        get_response = self.client.get(url)
        first_response = self.client.post(url)
        second_response = self.client.post(url)

        self.first.refresh_from_db()
        self.assertEqual(get_response.status_code, 405)
        self.assertEqual(
            self.client.get(url, HTTP_HX_REQUEST='true').status_code,
            405,
        )
        self.assertEqual(first_response.status_code, 302)
        self.assertEqual(first_response['Location'], self.project_redirect_url())
        self.assertTrue(self.first.is_completed)
        self.assertEqual(
            AuditLog.objects.filter(
                entity_id=str(self.first.pk), action=AuditLog.Action.COMPLETED
            ).count(),
            1,
        )
        self.assertIn('ya estaba completado', ' '.join(self.response_messages(second_response)))

    def test_complete_terminal_project_returns_403_without_mutation(self):
        self.project.status = Project.Status.CLOSED
        self.project.save(update_fields=('status', 'updated_at'))

        response = self.client.post(self.milestone_url('project_milestone_complete'))

        self.assertEqual(response.status_code, 403)
        self.assertContains(
            response,
            'cerrados o anulados no admiten cambios',
            status_code=403,
        )
        self.first.refresh_from_db()
        self.assertFalse(self.first.is_completed)

    def test_htmx_complete_returns_only_updated_partial_and_toast_header(self):
        url = self.milestone_url('project_milestone_complete')

        response = self.client.post(url, HTTP_HX_REQUEST='true')
        repeated_response = self.client.post(url, HTTP_HX_REQUEST='true')

        self.first.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(
            response,
            'web/includes/project_milestone_item_response.html',
        )
        self.assertNotContains(response, 'Información del proyecto')
        self.assertNotContains(response, 'project_milestones.js')
        self.assertNotContains(response, 'htmx.min.js')
        self.assertNotContains(response, 'id="project-milestones"')
        self.assertNotContains(response, 'id="project-milestone-list"')
        self.assertContains(response, f'id="milestone-{self.first.pk}"', count=1)
        self.assertContains(response, 'id="milestone-progress-summary"', count=1)
        self.assertContains(response, 'id="milestone-progress-bar"', count=1)
        self.assertContains(response, 'hx-swap-oob="outerHTML"', count=2)
        self.assertContains(response, '2 de 2 completados')
        self.assertContains(response, 'aria-valuenow="100"')
        self.assertContains(response, 'style="width: 100%"')
        self.assertTrue(self.first.is_completed)
        trigger = json.loads(response.headers['HX-Trigger'])
        self.assertEqual(
            trigger['milestoneToast'],
            {'type': 'success', 'message': 'Hito completado.'},
        )
        repeated_trigger = json.loads(repeated_response.headers['HX-Trigger'])
        self.assertEqual(repeated_trigger['milestoneToast']['type'], 'info')
        self.assertEqual(
            AuditLog.objects.filter(
                entity_id=str(self.first.pk), action=AuditLog.Action.COMPLETED
            ).count(),
            1,
        )

    def test_reopen_get_confirms_without_mutating_and_post_reopens(self):
        url = self.milestone_url('project_milestone_reopen', self.second)

        get_response = self.client.get(url)
        self.second.refresh_from_db()
        self.assertEqual(get_response.status_code, 200)
        self.assertContains(get_response, 'volverá a estado pendiente')
        self.assertTrue(self.second.is_completed)

        post_response = self.client.post(url)
        self.second.refresh_from_db()
        self.assertEqual(post_response.status_code, 302)
        self.assertEqual(post_response['Location'], self.project_redirect_url())
        self.assertFalse(self.second.is_completed)
        self.assertIsNone(self.second.completed_at)
        self.assertIsNone(self.second.completed_by)

    def test_reopen_terminal_project_redisplays_domain_error(self):
        self.project.status = Project.Status.CLOSED
        self.project.save(update_fields=('status', 'updated_at'))

        response = self.client.post(
            self.milestone_url('project_milestone_reopen', self.second)
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'cerrados o anulados no admiten cambios')
        self.second.refresh_from_db()
        self.assertTrue(self.second.is_completed)

    def test_htmx_reopen_returns_partial_with_recalculated_progress(self):
        response = self.client.post(
            self.milestone_url('project_milestone_reopen', self.second),
            HTTP_HX_REQUEST='true',
        )

        self.second.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(
            response,
            'web/includes/project_milestone_item_response.html',
        )
        self.assertContains(response, f'id="milestone-{self.second.pk}"', count=1)
        self.assertContains(response, 'id="milestone-progress-summary"', count=1)
        self.assertContains(response, 'id="milestone-progress-bar"', count=1)
        self.assertNotContains(response, 'id="project-milestone-list"')
        self.assertContains(response, '0 de 2 completados')
        self.assertContains(response, 'aria-valuenow="0"')
        self.assertContains(response, 'style="width: 0%"')
        self.assertFalse(self.second.is_completed)
        trigger = json.loads(response.headers['HX-Trigger'])
        self.assertEqual(trigger['milestoneToast']['message'], 'Hito reabierto.')

    def test_delete_get_confirms_without_deleting_and_post_deletes_and_reindexes(self):
        url = self.milestone_url('project_milestone_delete')

        get_response = self.client.get(url)
        self.assertEqual(get_response.status_code, 200)
        self.assertContains(get_response, 'Esta acción no se puede deshacer')
        self.assertTrue(ProjectMilestone.objects.filter(pk=self.first.pk).exists())

        post_response = self.client.post(url)
        self.second.refresh_from_db()
        self.assertEqual(post_response.status_code, 302)
        self.assertEqual(post_response['Location'], self.project_redirect_url())
        self.assertFalse(ProjectMilestone.objects.filter(pk=self.first.pk).exists())
        self.assertEqual(self.second.position, 1)

    def test_delete_terminal_project_redisplays_domain_error(self):
        self.project.status = Project.Status.CLOSED
        self.project.save(update_fields=('status', 'updated_at'))

        response = self.client.post(self.milestone_url('project_milestone_delete'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'cerrados no admiten cambios')
        self.assertTrue(ProjectMilestone.objects.filter(pk=self.first.pk).exists())

    def test_htmx_delete_returns_partial_without_deleted_milestone(self):
        deleted_title = self.first.title

        response = self.client.post(
            self.milestone_url('project_milestone_delete'),
            HTTP_HX_REQUEST='true',
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(
            response,
            'web/includes/project_milestone_list_response.html',
        )
        self.assertNotContains(response, deleted_title)
        self.assertNotContains(response, 'id="project-milestones"')
        self.assertContains(response, 'id="project-milestone-list"', count=1)
        self.assertContains(response, 'id="milestone-progress-summary"', count=1)
        self.assertContains(response, 'id="milestone-progress-bar"', count=1)
        self.assertContains(response, 'hx-swap-oob="outerHTML"', count=2)
        self.assertContains(response, '1 de 1 completados')
        self.assertFalse(ProjectMilestone.objects.filter(pk=self.first.pk).exists())
        trigger = json.loads(response.headers['HX-Trigger'])
        self.assertEqual(trigger['milestoneToast']['message'], 'Hito eliminado.')

    def test_move_endpoints_are_post_only_move_and_keep_boundary_idempotent(self):
        move_down_url = self.milestone_url('project_milestone_move_down')
        move_up_url = self.milestone_url('project_milestone_move_up')

        self.assertEqual(self.client.get(move_down_url).status_code, 405)
        self.assertEqual(self.client.get(move_up_url).status_code, 405)
        self.assertEqual(
            self.client.get(move_down_url, HTTP_HX_REQUEST='true').status_code,
            405,
        )
        self.assertEqual(
            self.client.get(move_up_url, HTTP_HX_REQUEST='true').status_code,
            405,
        )
        down_response = self.client.post(move_down_url)
        self.first.refresh_from_db()
        self.second.refresh_from_db()
        self.assertEqual(down_response.status_code, 302)
        self.assertEqual((self.second.position, self.first.position), (1, 2))

        up_response = self.client.post(move_up_url)
        self.first.refresh_from_db()
        self.second.refresh_from_db()
        self.assertEqual(up_response.status_code, 302)
        self.assertEqual((self.first.position, self.second.position), (1, 2))

        boundary_response = self.client.post(move_up_url)
        self.assertIn('límite', ' '.join(self.response_messages(boundary_response)))
        last_boundary_response = self.client.post(
            self.milestone_url('project_milestone_move_down', self.second)
        )
        self.assertIn('límite', ' '.join(self.response_messages(last_boundary_response)))
        self.assertEqual(
            AuditLog.objects.filter(action=AuditLog.Action.REORDERED).count(),
            2,
        )

    def test_move_terminal_project_returns_403_without_reordering(self):
        self.project.status = Project.Status.CLOSED
        self.project.save(update_fields=('status', 'updated_at'))

        response = self.client.post(self.milestone_url('project_milestone_move_down'))

        self.assertEqual(response.status_code, 403)
        self.first.refresh_from_db()
        self.second.refresh_from_db()
        self.assertEqual((self.first.position, self.second.position), (1, 2))

    def test_htmx_move_returns_partial_in_new_order(self):
        response = self.client.post(
            self.milestone_url('project_milestone_move_down'),
            HTTP_HX_REQUEST='true',
        )

        self.first.refresh_from_db()
        self.second.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(
            response,
            'web/includes/project_milestone_list_response.html',
        )
        self.assertContains(response, 'id="project-milestone-list"', count=1)
        self.assertNotContains(response, 'id="project-milestones"')
        self.assertNotContains(response, 'id="milestone-progress-summary"')
        self.assertNotContains(response, 'id="milestone-progress-bar"')
        self.assertNotContains(response, 'hx-swap-oob')
        self.assertEqual(
            [item.pk for item in response.context['project_milestones']],
            [self.second.pk, self.first.pk],
        )
        self.assertEqual((self.second.position, self.first.position), (1, 2))
        trigger = json.loads(response.headers['HX-Trigger'])
        self.assertEqual(
            trigger['milestoneToast']['message'],
            'Orden de hitos actualizado.',
        )

    def test_htmx_domain_permission_and_missing_errors_keep_real_statuses(self):
        self.project.status = Project.Status.CLOSED
        self.project.save(update_fields=('status', 'updated_at'))
        domain_response = self.client.post(
            self.milestone_url('project_milestone_complete'),
            HTTP_HX_REQUEST='true',
        )
        self.assertEqual(domain_response.status_code, 403)
        self.assertContains(
            domain_response,
            'cerrados no admiten cambios',
            status_code=403,
        )

        self.project.status = Project.Status.ACTIVE
        self.project.save(update_fields=('status', 'updated_at'))
        limited_user = self.create_user_with_permissions(
            'milestone-htmx-view-only',
            'view_project',
        )
        self.client.force_login(limited_user)
        permission_response = self.client.post(
            self.milestone_url('project_milestone_complete'),
            HTTP_HX_REQUEST='true',
        )
        self.assertEqual(permission_response.status_code, 403)

        self.client.force_login(self.user)
        missing_response = self.client.post(
            reverse('project_milestone_complete', args=[999999]),
            HTTP_HX_REQUEST='true',
        )
        self.assertEqual(missing_response.status_code, 404)

    def test_each_endpoint_requires_its_exact_action_permission(self):
        user = self.create_user_with_permissions('milestone-view-only', 'view_project')
        self.client.force_login(user)
        requests = (
            ('get', reverse('project_milestone_add', args=[self.project.pk])),
            ('get', self.milestone_url('project_milestone_edit')),
            ('post', self.milestone_url('project_milestone_complete')),
            ('get', self.milestone_url('project_milestone_reopen', self.second)),
            ('get', self.milestone_url('project_milestone_delete')),
            ('post', self.milestone_url('project_milestone_move_up')),
            ('post', self.milestone_url('project_milestone_move_down')),
        )

        for method, url in requests:
            with self.subTest(url=url):
                self.assertEqual(getattr(self.client, method)(url).status_code, 403)

    def test_action_permission_without_project_view_permission_is_insufficient(self):
        user = self.create_user_with_permissions(
            'milestone-actions-no-project-view',
            'add_projectmilestone',
            'change_projectmilestone',
            'complete_projectmilestone',
            'delete_projectmilestone',
            'reorder_projectmilestone',
        )
        self.client.force_login(user)

        self.assertEqual(
            self.client.get(
                reverse('project_milestone_add', args=[self.project.pk])
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.post(self.milestone_url('project_milestone_complete')).status_code,
            403,
        )

    def test_anonymous_user_is_redirected_to_login(self):
        self.client.logout()

        response = self.client.get(
            reverse('project_milestone_add', args=[self.project.pk])
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('login'), response['Location'])

    def test_missing_milestone_returns_404_for_authorized_user(self):
        response = self.client.get(reverse('project_milestone_edit', args=[999999]))

        self.assertEqual(response.status_code, 404)

    def test_csrf_is_required_for_every_mutating_endpoint(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.user)
        requests = (
            (
                reverse('project_milestone_add', args=[self.project.pk]),
                {'title': 'Sin CSRF'},
            ),
            (
                self.milestone_url('project_milestone_edit'),
                {'title': 'Sin CSRF', 'description': ''},
            ),
            (self.milestone_url('project_milestone_complete'), {}),
            (self.milestone_url('project_milestone_reopen', self.second), {}),
            (self.milestone_url('project_milestone_delete'), {}),
            (self.milestone_url('project_milestone_move_up', self.second), {}),
            (self.milestone_url('project_milestone_move_down'), {}),
        )

        for url, data in requests:
            with self.subTest(url=url):
                self.assertEqual(csrf_client.post(url, data).status_code, 403)
                self.assertEqual(
                    csrf_client.post(
                        url,
                        data,
                        HTTP_HX_REQUEST='true',
                    ).status_code,
                    403,
                )

    def test_htmx_post_accepts_real_form_csrf_token(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.user)
        detail_response = csrf_client.get(
            reverse('project_detail', args=[self.project.pk])
        )
        csrf_token = csrf_client.cookies['csrftoken'].value

        response = csrf_client.post(
            self.milestone_url('project_milestone_complete'),
            {'csrfmiddlewaretoken': csrf_token},
            HTTP_HX_REQUEST='true',
        )

        self.assertEqual(detail_response.status_code, 200)
        self.assertEqual(response.status_code, 200)
        self.first.refresh_from_db()
        self.assertTrue(self.first.is_completed)
