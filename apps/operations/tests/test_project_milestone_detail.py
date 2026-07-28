from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import connection
from django.templatetags.static import static
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.operations.models import Project, ProjectMilestone
from apps.operations.tests.helpers import create_project


MILESTONE_ACTION_PERMISSIONS = (
    'add_projectmilestone',
    'change_projectmilestone',
    'complete_projectmilestone',
    'delete_projectmilestone',
    'reorder_projectmilestone',
)


class ProjectMilestoneDetailTests(TestCase):
    def setUp(self):
        self.project = create_project(code='PRJ-MILESTONE-DETAIL')
        self.viewer = self.create_user_with_permissions('milestone-detail-viewer')
        self.client.force_login(self.viewer)

    def create_user_with_permissions(self, username, *codenames):
        """
        PRE: codenames identify optional operations permissions for one UI scenario.
        POST: returns a user that can view projects plus exactly the requested milestone actions.
        """
        user = get_user_model().objects.create_user(
            username=username,
            password='pass-12345',
        )
        requested = {'view_project', *codenames}
        user.user_permissions.add(
            *Permission.objects.filter(
                content_type__app_label='operations',
                codename__in=requested,
            )
        )
        return user

    def create_milestone(
        self,
        *,
        project=None,
        title,
        position,
        completed=False,
        completed_by=None,
    ):
        """
        PRE: project is persisted and position is free within it.
        POST: returns a coherent persisted milestone suitable for detail rendering.
        """
        return ProjectMilestone.objects.create(
            project=project or self.project,
            title=title,
            description=f'Descripción de {title}',
            position=position,
            is_completed=completed,
            completed_at=timezone.now() if completed else None,
            completed_by=completed_by if completed else None,
            created_by=self.viewer,
        )

    def detail_response(self, project=None):
        return self.client.get(
            reverse('project_detail', args=[(project or self.project).pk])
        )

    def test_empty_detail_has_explicit_state_without_zero_progressbar(self):
        response = self.detail_response()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['project_milestones'], [])
        self.assertEqual(response.context['milestone_progress'].total, 0)
        self.assertContains(response, 'Hitos del proyecto')
        self.assertContains(response, 'Sin hitos definidos', count=2)
        self.assertNotContains(response, 'role="progressbar"')
        self.assertNotContains(response, '0 %')
        self.assertNotContains(response, 'En progreso')

    def test_partial_progress_is_ordered_and_accessible(self):
        completer = get_user_model().objects.create_user(username='detail-completer')
        first = self.create_milestone(title='Primero por posición', position=1)

        zero_response = self.detail_response()
        self.assertContains(zero_response, '0 de 1 completados')
        self.assertContains(zero_response, 'aria-valuenow="0"')
        self.assertContains(zero_response, 'style="width: 0%"')

        second = self.create_milestone(
            title='Segundo por posición',
            position=2,
            completed=True,
            completed_by=completer,
        )

        response = self.detail_response()

        self.assertEqual(
            [milestone.pk for milestone in response.context['project_milestones']],
            [first.pk, second.pk],
        )
        self.assertContains(response, '1 de 2 completados')
        self.assertContains(response, '50 %')
        self.assertContains(response, 'En progreso')
        self.assertContains(response, 'role="progressbar"')
        self.assertContains(response, 'aria-valuemin="0"')
        self.assertContains(response, 'aria-valuemax="100"')
        self.assertContains(response, 'aria-valuenow="50"')
        self.assertContains(response, 'style="width: 50%"')
        self.assertContains(response, 'aria-label="Progreso de hitos del proyecto"')
        self.assertContains(response, 'type="checkbox"')
        self.assertContains(response, 'disabled')
        self.assertContains(response, completer.get_username())

    def test_complete_progress_and_new_milestone_are_derived_without_changing_project(self):
        self.create_milestone(title='Completado A', position=1, completed=True)
        self.create_milestone(title='Completado B', position=2, completed=True)

        completed_response = self.detail_response()
        self.assertContains(completed_response, '2 de 2 completados')
        self.assertContains(completed_response, '100 %')
        self.assertContains(completed_response, 'aria-valuenow="100"')
        self.assertContains(completed_response, 'style="width: 100%"')
        self.assertContains(completed_response, '<strong>Completado</strong>', html=True)
        self.assertNotContains(completed_response, 'Finalizado')

        original_status = self.project.status
        self.create_milestone(title='Nuevo pendiente', position=3)
        reduced_response = self.detail_response()
        self.project.refresh_from_db()

        self.assertContains(reduced_response, '2 de 3 completados')
        self.assertContains(reduced_response, '67 %')
        self.assertContains(reduced_response, 'En progreso')
        self.assertEqual(self.project.status, original_status)

    def test_add_action_requires_its_permission(self):
        add_url = reverse('project_milestone_add', args=[self.project.pk])

        self.assertNotContains(self.detail_response(), add_url)
        manager = self.create_user_with_permissions(
            'milestone-detail-adder',
            'add_projectmilestone',
        )
        self.client.force_login(manager)

        response = self.detail_response()
        self.assertContains(response, add_url)
        self.assertContains(response, 'Nuevo hito')

    def test_each_action_is_rendered_only_for_its_specific_permission(self):
        pending = self.create_milestone(title='Pendiente', position=1)
        completed = self.create_milestone(title='Completado', position=2, completed=True)
        expectations = {
            'change_projectmilestone': (
                reverse('project_milestone_edit', args=[pending.pk]),
                reverse('project_milestone_edit', args=[completed.pk]),
            ),
            'complete_projectmilestone': (
                reverse('project_milestone_complete', args=[pending.pk]),
                reverse('project_milestone_reopen', args=[completed.pk]),
            ),
            'delete_projectmilestone': (
                reverse('project_milestone_delete', args=[pending.pk]),
                reverse('project_milestone_delete', args=[completed.pk]),
            ),
            'reorder_projectmilestone': (
                reverse('project_milestone_move_down', args=[pending.pk]),
                reverse('project_milestone_move_up', args=[completed.pk]),
            ),
        }

        for codename, visible_urls in expectations.items():
            with self.subTest(codename=codename):
                user = self.create_user_with_permissions(
                    f'milestone-detail-{codename}',
                    codename,
                )
                self.client.force_login(user)
                response = self.detail_response()

                for action_codename, action_urls in expectations.items():
                    for url in action_urls:
                        if action_codename == codename:
                            self.assertContains(response, url)
                        else:
                            self.assertNotContains(response, url)

    def test_complete_and_move_use_post_forms_with_csrf_and_boundaries(self):
        manager = self.create_user_with_permissions(
            'milestone-detail-post-actions',
            'complete_projectmilestone',
            'reorder_projectmilestone',
        )
        self.client.force_login(manager)
        first = self.create_milestone(title='Primero', position=1)
        last = self.create_milestone(title='Último', position=2)

        response = self.detail_response()
        content = response.content.decode()

        complete_url = reverse('project_milestone_complete', args=[first.pk])
        first_up_url = reverse('project_milestone_move_up', args=[first.pk])
        first_down_url = reverse('project_milestone_move_down', args=[first.pk])
        last_up_url = reverse('project_milestone_move_up', args=[last.pk])
        last_down_url = reverse('project_milestone_move_down', args=[last.pk])
        self.assertIn(f'action="{complete_url}"', content)
        self.assertIn(f'hx-post="{complete_url}"', content)
        self.assertIn('class="ops-milestone-check-form"', content)
        self.assertIn('class="ops-milestone-check-button"', content)
        self.assertIn('role="checkbox"', content)
        self.assertIn('aria-checked="false"', content)
        self.assertIn('aria-label="Completar hito: Primero"', content)
        self.assertIn(f'action="{first_down_url}"', content)
        self.assertIn(f'hx-post="{first_down_url}"', content)
        self.assertIn(f'action="{last_up_url}"', content)
        self.assertIn(f'hx-post="{last_up_url}"', content)
        self.assertNotIn(f'href="{complete_url}"', content)
        self.assertNotIn(first_up_url, content)
        self.assertNotIn(last_down_url, content)
        self.assertGreaterEqual(content.count('name="csrfmiddlewaretoken"'), 3)

    def test_reopen_and_delete_are_confirmation_links(self):
        manager = self.create_user_with_permissions(
            'milestone-detail-confirmations',
            'complete_projectmilestone',
            'delete_projectmilestone',
        )
        self.client.force_login(manager)
        milestone = self.create_milestone(title='Histórico', position=1, completed=True)

        response = self.detail_response()

        self.assertContains(
            response,
            f'href="{reverse("project_milestone_reopen", args=[milestone.pk])}"',
        )
        self.assertContains(
            response,
            f'href="{reverse("project_milestone_delete", args=[milestone.pk])}"',
        )

    def test_progressive_enhancement_exposes_fetch_and_sweetalert_hooks_with_fallbacks(self):
        manager = self.create_user_with_permissions(
            'milestone-detail-enhancement',
            'complete_projectmilestone',
            'delete_projectmilestone',
            'reorder_projectmilestone',
        )
        self.client.force_login(manager)
        pending = self.create_milestone(title='Pendiente mejorado', position=1)
        completed = self.create_milestone(title='Completado mejorado', position=2, completed=True)

        response = self.detail_response()

        self.assertContains(response, static('web/js/project_milestones.js'))
        self.assertContains(response, static('vendor/htmx/htmx.min.js'))
        self.assertContains(response, 'data-milestone-panel')
        self.assertContains(response, 'data-milestone-action="complete"')
        self.assertContains(response, 'data-milestone-action="move"')
        self.assertContains(response, 'data-confirm-kind="reopen"')
        self.assertContains(response, 'data-confirm-kind="delete"')
        self.assertContains(response, f'hx-target="#milestone-{pending.pk}"')
        self.assertContains(response, 'hx-target="#project-milestone-list"')
        self.assertContains(response, 'hx-swap="outerHTML swap:0ms settle:0ms"')
        self.assertContains(response, 'hx-disabled-elt="find button"')
        self.assertContains(response, 'class="htmx-indicator ops-milestone-loading"')
        self.assertContains(
            response,
            'Este hito volverá a estado pendiente. El progreso general del proyecto será recalculado.',
        )
        self.assertContains(
            response,
            'Eliminar este hito recalculará el progreso del proyecto. Esta acción no se puede deshacer.',
        )
        delete_url = reverse('project_milestone_delete', args=[completed.pk])
        complete_url = reverse('project_milestone_complete', args=[pending.pk])
        self.assertContains(response, f'href="{delete_url}"')
        self.assertContains(response, f'hx-post="{delete_url}"')
        self.assertContains(response, f'action="{complete_url}"')
        self.assertContains(response, f'hx-post="{complete_url}"')
        self.assertContains(response, 'name="csrfmiddlewaretoken"')
        self.assertContains(response, 'class="bi bi-three-dots"')
        self.assertContains(
            response,
            'aria-label="Más acciones para Completado mejorado"',
        )
        self.assertNotRegex(response.content.decode(), r'>\s*Más\s*</button>')
        self.assertNotContains(response, 'ops-milestone-status')
        self.assertNotContains(response, '<ol', html=False)
        self.assertContains(response, '<ul class="ops-milestone-list">')

    def test_milestone_javascript_delegates_transport_and_swapping_to_htmx(self):
        script = (
            Path(settings.BASE_DIR) / 'static/web/js/project_milestones.js'
        ).read_text(encoding='utf-8')

        self.assertNotIn('fetch(', script)
        self.assertNotIn('DOMParser', script)
        self.assertNotIn('replaceWith', script)
        self.assertIn('htmx:confirm', script)
        self.assertIn('htmx:afterSwap', script)
        self.assertIn('htmx:responseError', script)
        self.assertIn('htmx:sendError', script)
        self.assertIn('milestoneToast', script)
        self.assertNotIn('scrollIntoView', script)

    def test_milestone_fragments_have_unique_ids_and_compact_css_has_no_gradients(self):
        manager = self.create_user_with_permissions(
            'milestone-detail-compact-ui',
            *MILESTONE_ACTION_PERMISSIONS,
        )
        self.client.force_login(manager)
        milestone = self.create_milestone(title='Fila compacta', position=1)

        response = self.detail_response()
        content = response.content.decode()
        for fragment_id in (
            'project-milestones',
            'milestone-progress-summary',
            'milestone-progress-bar',
            'project-milestone-list',
            f'milestone-{milestone.pk}',
        ):
            self.assertEqual(content.count(f'id="{fragment_id}"'), 1)

        stylesheet = (
            Path(settings.BASE_DIR) / 'static/web/css/sigedon.css'
        ).read_text(encoding='utf-8')
        milestone_css = stylesheet.split('.ops-milestones {', 1)[1].split(
            '.ops-form-card {', 1
        )[0]
        self.assertNotIn('gradient(', milestone_css)
        self.assertNotIn('min-height', milestone_css)
        self.assertNotIn('border-left', milestone_css)
        self.assertIn('list-style: none', milestone_css)
        self.assertIn('max-width: none', milestone_css)
        self.assertIn('width: 100%', milestone_css)

    def test_view_only_user_sees_information_without_mutation_actions(self):
        milestone = self.create_milestone(title='Solo lectura', position=1)

        response = self.detail_response()

        self.assertContains(response, milestone.title)
        self.assertContains(response, 'type="checkbox"')
        self.assertContains(response, 'disabled')
        self.assertNotContains(response, 'ops-milestone-check-button')
        for route_name in (
            'project_milestone_edit',
            'project_milestone_complete',
            'project_milestone_reopen',
            'project_milestone_delete',
            'project_milestone_move_up',
            'project_milestone_move_down',
        ):
            self.assertNotContains(response, reverse(route_name, args=[milestone.pk]))

    def test_terminal_projects_show_history_and_hide_all_actions(self):
        manager = self.create_user_with_permissions(
            'milestone-detail-terminal-manager',
            *MILESTONE_ACTION_PERMISSIONS,
        )
        self.client.force_login(manager)
        milestone = self.create_milestone(title='Hito histórico', position=1)
        terminal_cases = (
            (
                Project.Status.CLOSED,
                'Este proyecto está cerrado. Sus hitos se conservan como registro histórico.',
            ),
        )

        for status, notice in terminal_cases:
            with self.subTest(status=status):
                self.project.status = status
                self.project.save(update_fields=('status', 'updated_at'))
                response = self.detail_response()

                self.assertContains(response, milestone.title)
                self.assertContains(response, notice)
                self.assertNotContains(
                    response,
                    reverse('project_milestone_add', args=[self.project.pk]),
                )
                for route_name in (
                    'project_milestone_edit',
                    'project_milestone_complete',
                    'project_milestone_reopen',
                    'project_milestone_delete',
                    'project_milestone_move_up',
                    'project_milestone_move_down',
                ):
                    self.assertNotContains(
                        response,
                        reverse(route_name, args=[milestone.pk]),
                    )

    def test_deleted_completer_keeps_date_and_gets_historical_label(self):
        completed_at = timezone.now().replace(microsecond=0)
        deleted_user = get_user_model().objects.create_user(username='deleted-completer')
        milestone = ProjectMilestone.objects.create(
            project=self.project,
            title='Completado históricamente',
            position=1,
            is_completed=True,
            completed_at=completed_at,
            completed_by=deleted_user,
        )
        deleted_user.delete()
        milestone.refresh_from_db()

        response = self.detail_response()

        self.assertContains(response, 'Usuario eliminado')
        self.assertContains(response, completed_at.strftime('%d/%m/%Y %H:%M'))
        self.assertTrue(milestone.is_completed)
        self.assertIsNone(milestone.completed_by)

    def test_milestone_query_count_is_stable_and_users_are_joined(self):
        one_project = create_project(code='PRJ-MILESTONE-QUERY-ONE')
        many_project = create_project(code='PRJ-MILESTONE-QUERY-MANY')
        completer = get_user_model().objects.create_user(username='query-completer')
        self.create_milestone(
            project=one_project,
            title='Único',
            position=1,
            completed=True,
            completed_by=completer,
        )
        for position in range(1, 11):
            self.create_milestone(
                project=many_project,
                title=f'Hito {position}',
                position=position,
                completed=position % 2 == 0,
                completed_by=completer,
            )

        with CaptureQueriesContext(connection) as one_queries:
            one_response = self.detail_response(one_project)
        with CaptureQueriesContext(connection) as many_queries:
            many_response = self.detail_response(many_project)

        self.assertEqual(one_response.status_code, 200)
        self.assertEqual(many_response.status_code, 200)
        self.assertEqual(len(one_queries), len(many_queries))
        milestone_queries = [
            query['sql']
            for query in many_queries.captured_queries
            if 'operations_projectmilestone' in query['sql'].lower()
        ]
        self.assertEqual(len(milestone_queries), 1)
        self.assertGreaterEqual(milestone_queries[0].lower().count('auth_user'), 2)

    def test_htmx_partial_query_count_is_stable_with_one_and_many_milestones(self):
        manager = self.create_user_with_permissions(
            'milestone-partial-query-manager',
            'reorder_projectmilestone',
        )
        self.client.force_login(manager)
        one_project = create_project(code='PRJ-MILESTONE-PARTIAL-ONE')
        many_project = create_project(code='PRJ-MILESTONE-PARTIAL-MANY')
        one_milestone = self.create_milestone(
            project=one_project,
            title='Único parcial',
            position=1,
        )
        many_milestones = [
            self.create_milestone(
                project=many_project,
                title=f'Parcial {position}',
                position=position,
            )
            for position in range(1, 11)
        ]

        with CaptureQueriesContext(connection) as one_queries:
            one_response = self.client.post(
                reverse('project_milestone_move_up', args=[one_milestone.pk]),
                HTTP_HX_REQUEST='true',
            )
        with CaptureQueriesContext(connection) as many_queries:
            many_response = self.client.post(
                reverse('project_milestone_move_up', args=[many_milestones[0].pk]),
                HTTP_HX_REQUEST='true',
            )

        self.assertEqual(one_response.status_code, 200)
        self.assertEqual(many_response.status_code, 200)
        self.assertEqual(len(one_queries), len(many_queries))
        joined_partial_queries = [
            query['sql'].lower()
            for query in many_queries.captured_queries
            if 'operations_projectmilestone' in query['sql'].lower()
            and query['sql'].lower().count('auth_user') >= 2
        ]
        self.assertEqual(len(joined_partial_queries), 1)

    @override_settings(KOBO_ENABLED=True)
    def test_kobo_template_inheritance_renders_milestone_block_once(self):
        self.create_milestone(title='Visible con Kobo', position=1)

        response = self.detail_response()

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'operations/project_detail.html')
        self.assertContains(response, 'id="project-milestones"', count=1)
        self.assertContains(response, 'Hitos del proyecto', count=1)
