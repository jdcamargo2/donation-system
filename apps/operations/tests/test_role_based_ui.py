from pathlib import Path

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, override_settings
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
    create_project_update_review,
    create_project_update_review_decision,
    publish_project_update,
    register_advance,
)
from apps.operations.tests.helpers import create_allocation, create_donation, create_expense, create_institution, create_project
from apps.operations.tests.test_permissions import create_user_with_permissions


class RoleBasedUITests(TestCase):
    def setUp(self):
        sync_operation_roles()
        self.project = create_project()
        self.project.status = Project.Status.ACTIVE
        self.project.save()
        self.reporter = self.create_user_for_role('ui-update-reporter', ROLE_SIGEDON_ADMIN)
        self.project_update = register_advance(
            project_id=self.project.pk,
            title='Avance visible por rol',
            description='Pendiente de revisión.',
            reported_by=self.reporter,
        )
        self.institution = create_institution()
        self.donation = create_donation(donor=self.institution)
        self.allocation = create_allocation(donation=self.donation, project=self.project)
        self.expense = create_expense(allocation=self.allocation)
        AuditLog.objects.create(
            action=AuditLog.Action.CREATED,
            model_name='Proyecto',
            entity_id=str(self.project.pk),
            entity_label=str(self.project),
            summary='Proyecto creado.',
        )

    def create_user_for_role(self, username, role_name):
        user = get_user_model().objects.create_user(username=username, password='pass-12345')
        user.groups.add(Group.objects.get(name=role_name))
        return user

    def assert_navigation_activity(self, response, label, url, is_active):
        # PRE: response is an internal page response containing the sidebar navigation.
        # POST: verifies aria-current only when the navigation link is active.
        active_link = f'href="{url}" title="{label}" aria-current="page"'
        if is_active:
            self.assertContains(response, active_link)
        else:
            self.assertNotContains(response, active_link)

    def test_field_operator_does_not_see_create_project_action(self):
        self.client.force_login(self.create_user_for_role('ui-field-project', ROLE_FIELD_OPERATOR))

        response = self.client.get(reverse('project_list'))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, reverse('project_create'))
        self.assertNotContains(response, 'Crear proyecto')

    def test_field_operator_sees_register_update_on_project_detail(self):
        self.client.force_login(self.create_user_for_role('ui-field-update', ROLE_FIELD_OPERATOR))

        response = self.client.get(reverse('project_detail', args=[self.project.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Registrar avance')
        self.assertContains(response, reverse('project_update_create_for_project', args=[self.project.pk]))

    def test_field_operator_does_not_see_publish_update_action(self):
        self.client.force_login(self.create_user_for_role('ui-field-publish', ROLE_FIELD_OPERATOR))

        detail_response = self.client.get(reverse('project_detail', args=[self.project.pk]))
        update_response = self.client.get(reverse('project_update_detail', args=[self.project_update.pk]))

        self.assertNotContains(detail_response, 'Revisar')
        self.assertNotContains(detail_response, reverse('project_update_publish', args=[self.project_update.pk]))
        self.assertNotContains(update_response, 'Publicar avance')
        self.assertNotContains(update_response, reverse('project_update_publish', args=[self.project_update.pk]))

    def test_external_auditor_does_not_see_create_expense_action(self):
        self.client.force_login(self.create_user_for_role('ui-auditor-expense', ROLE_EXTERNAL_AUDITOR))

        response = self.client.get(reverse('expense_list'))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Nuevo gasto')
        self.assertNotContains(response, reverse('expense_create'))

    def test_external_auditor_sees_audit_navigation(self):
        self.client.force_login(self.create_user_for_role('ui-auditor-audit', ROLE_EXTERNAL_AUDITOR))

        response = self.client.get(reverse('dashboard'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Auditoría')
        self.assertContains(response, reverse('audit_log_list'))

    def test_field_operator_dashboard_focuses_on_projects_and_updates(self):
        self.client.force_login(self.create_user_for_role('ui-field-dashboard', ROLE_FIELD_OPERATOR))

        response = self.client.get(reverse('dashboard'))

        self.assertContains(response, 'Ver proyectos')
        self.assertContains(response, 'Registrar avances')
        self.assertNotContains(response, 'Crear donación')
        self.assertNotContains(response, 'Crear asignación')
        self.assertNotContains(response, 'Crear gasto')
        self.assertNotContains(response, 'Ver auditoría')

    def test_project_navigation_is_visible_only_with_project_permission(self):
        self.client.force_login(create_user_with_permissions('ui-project-viewer', 'view_project'))

        response = self.client.get(reverse('dashboard'))

        self.assertContains(response, reverse('project_list'))
        self.assertContains(response, 'title="Proyectos"')
        self.assertNotContains(response, reverse('project_update_list'))
        self.assertNotContains(response, 'title="Avances"')

    def test_project_navigation_is_hidden_without_project_permission(self):
        self.client.force_login(create_user_with_permissions('ui-update-only', 'view_projectupdate'))

        response = self.client.get(reverse('dashboard'))

        self.assertNotContains(response, reverse('project_list'))
        self.assertNotContains(response, 'title="Proyectos"')

    def test_update_navigation_is_visible_only_with_project_update_permission(self):
        self.client.force_login(create_user_with_permissions('ui-update-viewer', 'view_projectupdate'))

        response = self.client.get(reverse('dashboard'))

        self.assertContains(response, reverse('project_update_list'))
        self.assertContains(response, 'title="Avances"')
        self.assertNotContains(response, reverse('project_list'))
        self.assertNotContains(response, 'title="Proyectos"')

    def test_update_navigation_is_hidden_without_project_update_permission(self):
        self.client.force_login(create_user_with_permissions('ui-project-only', 'view_project'))

        response = self.client.get(reverse('dashboard'))

        self.assertNotContains(response, reverse('project_update_list'))
        self.assertNotContains(response, 'title="Avances"')

    def test_project_navigation_is_active_on_project_list_and_detail(self):
        self.client.force_login(self.create_user_for_role('ui-project-navigation', ROLE_FIELD_OPERATOR))

        for url in [reverse('project_list'), reverse('project_detail', args=[self.project.pk])]:
            with self.subTest(url=url):
                response = self.client.get(url)

                self.assert_navigation_activity(response, 'Proyectos', reverse('project_list'), is_active=True)
                self.assert_navigation_activity(response, 'Avances', reverse('project_update_list'), is_active=False)

    def test_update_navigation_is_active_on_update_list_and_detail(self):
        self.client.force_login(self.create_user_for_role('ui-update-navigation', ROLE_FIELD_OPERATOR))

        for url in [reverse('project_update_list'), reverse('project_update_detail', args=[self.project_update.pk])]:
            with self.subTest(url=url):
                response = self.client.get(url)

                self.assert_navigation_activity(response, 'Proyectos', reverse('project_list'), is_active=False)
                self.assert_navigation_activity(response, 'Avances', reverse('project_update_list'), is_active=True)

    def test_field_operator_sees_project_and_update_navigation(self):
        self.client.force_login(self.create_user_for_role('ui-field-navigation', ROLE_FIELD_OPERATOR))

        response = self.client.get(reverse('dashboard'))

        self.assertContains(response, reverse('project_list'))
        self.assertContains(response, 'title="Proyectos"')
        self.assertContains(response, reverse('project_update_list'))
        self.assertContains(response, 'title="Avances"')

    @override_settings(KOBO_ENABLED=True)
    def test_field_operator_does_not_see_kobo_toolbox_sidebar(self):
        self.client.force_login(self.create_user_for_role('ui-field-kobo-nav', ROLE_FIELD_OPERATOR))

        response = self.client.get(reverse('dashboard'))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'KoboToolbox')
        self.assertNotContains(response, reverse('kobo:hub'))

    @override_settings(KOBO_ENABLED=True)
    def test_external_auditor_sees_kobo_toolbox_sidebar(self):
        self.client.force_login(self.create_user_for_role('ui-auditor-kobo-nav', ROLE_EXTERNAL_AUDITOR))

        response = self.client.get(reverse('dashboard'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'KoboToolbox')
        self.assertContains(response, reverse('kobo:hub'))

    @override_settings(KOBO_ENABLED=True)
    def test_field_operator_does_not_see_project_kobo_administration_card(self):
        # Administration card (kobo_hub_project_url) is distinct from project-local
        # Kobo sections governed by operations.view_project.
        self.client.force_login(self.create_user_for_role('ui-field-kobo-card', ROLE_FIELD_OPERATOR))

        response = self.client.get(reverse('project_detail', args=[self.project.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Administrar integración')
        self.assertNotContains(response, f"{reverse('kobo:hub')}?project={self.project.pk}")
        self.assertNotContains(response, 'id="project-kobo-title"')

    @override_settings(KOBO_ENABLED=True)
    def test_external_auditor_sees_project_kobo_administration_card(self):
        self.client.force_login(self.create_user_for_role('ui-auditor-kobo-card', ROLE_EXTERNAL_AUDITOR))

        response = self.client.get(reverse('project_detail', args=[self.project.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'KoboToolbox')
        self.assertContains(response, 'Administrar integración')
        self.assertContains(response, f"{reverse('kobo:hub')}?project={self.project.pk}")

    def test_project_committee_sees_navigation_without_mutation_actions(self):
        self.client.force_login(self.create_user_for_role('ui-project-committee', ROLE_PROJECT_COMMITTEE))

        project_response = self.client.get(reverse('project_detail', args=[self.project.pk]))
        update_response = self.client.get(reverse('project_update_detail', args=[self.project_update.pk]))

        self.assertContains(project_response, reverse('project_list'))
        self.assertContains(project_response, 'title="Proyectos"')
        self.assertContains(project_response, reverse('project_update_list'))
        self.assertContains(project_response, 'title="Avances"')
        self.assertNotContains(project_response, reverse('project_update_create_for_project', args=[self.project.pk]))
        self.assertNotContains(update_response, reverse('project_update_update', args=[self.project_update.pk]))
        self.assertNotContains(update_response, reverse('project_update_delete', args=[self.project_update.pk]))
        self.assertNotContains(update_response, reverse('project_update_publish', args=[self.project_update.pk]))

    def test_committee_sees_review_action_for_published_update(self):
        publisher = self.create_user_for_role('ui-review-publisher', ROLE_SIGEDON_ADMIN)
        published_update = register_advance(
            project_id=self.project.pk,
            title='Avance publicado para Comité',
            description='Listo para revisión documental.',
            reported_by=publisher,
        )
        publish_project_update(published_update.pk, publisher)
        self.client.force_login(self.create_user_for_role('ui-committee-reviewer', ROLE_PROJECT_COMMITTEE))

        response = self.client.get(reverse('project_update_detail', args=[published_update.pk]))

        self.assertContains(response, reverse('project_update_review_create', args=[published_update.pk]))
        self.assertNotContains(response, reverse('project_update_publish', args=[published_update.pk]))

    def test_committee_sees_decision_action_for_reviewed_update(self):
        publisher = self.create_user_for_role('ui-decision-publisher', ROLE_SIGEDON_ADMIN)
        published_update = register_advance(
            project_id=self.project.pk,
            title='Avance revisado para Comité',
            description='Listo para resultado institucional.',
            reported_by=publisher,
        )
        publish_project_update(published_update.pk, publisher)
        committee = self.create_user_for_role('ui-decision-committee', ROLE_PROJECT_COMMITTEE)
        review = create_project_update_review(
            update_id=published_update.pk,
            observations='Revisión documental disponible.',
            actor=committee,
        )
        self.client.force_login(committee)

        response = self.client.get(reverse('project_update_review_detail', args=[review.pk]))

        self.assertContains(response, reverse('project_update_review_decision_create', args=[review.pk]))

    def test_review_and_decision_routes_activate_update_navigation(self):
        publisher = self.create_user_for_role('ui-review-navigation-publisher', ROLE_SIGEDON_ADMIN)
        committee = self.create_user_for_role('ui-review-navigation-committee', ROLE_PROJECT_COMMITTEE)
        unreviewed_update = register_advance(
            project_id=self.project.pk,
            title='Avance sin revisión para navegación',
            description='Debe activar el enlace Avances.',
            reported_by=publisher,
        )
        publish_project_update(unreviewed_update.pk, publisher)
        reviewable_update = register_advance(
            project_id=self.project.pk,
            title='Avance para rutas de revisión',
            description='Debe activar el enlace Avances.',
            reported_by=publisher,
        )
        publish_project_update(reviewable_update.pk, publisher)
        review = create_project_update_review(
            update_id=reviewable_update.pk,
            observations='Revisión para navegación.',
            actor=committee,
        )
        decided_update = register_advance(
            project_id=self.project.pk,
            title='Avance para detalle de resultado',
            description='Debe activar el enlace Avances.',
            reported_by=publisher,
        )
        publish_project_update(decided_update.pk, publisher)
        decided_review = create_project_update_review(
            update_id=decided_update.pk,
            observations='Revisión con resultado.',
            actor=committee,
        )
        decision = create_project_update_review_decision(
            review_id=decided_review.pk,
            outcome='conforming',
            rationale='Resultado para navegación.',
            actor=committee,
        )

        cases = [
            reverse('project_update_review_create', args=[unreviewed_update.pk]),
            reverse('project_update_review_detail', args=[review.pk]),
            reverse('project_update_review_decision_create', args=[review.pk]),
            reverse('project_update_review_decision_detail', args=[decision.pk]),
        ]
        for url in cases:
            with self.subTest(url=url):
                self.client.force_login(committee)
                response = self.client.get(url)

                self.assert_navigation_activity(response, 'Proyectos', reverse('project_list'), is_active=False)
                self.assert_navigation_activity(response, 'Avances', reverse('project_update_list'), is_active=True)

    def test_public_portal_navigation_remains_separate_from_internal_navigation(self):
        source = Path('templates/public_portal/public_base.html').read_text()

        self.assertIn("{% url 'public_portal:public_project_list' %}", source)
        self.assertIn("{% url 'public_portal:public_updates_feed' %}", source)
        self.assertNotIn("{% url 'project_update_list' %}", source)

    def test_internal_templates_do_not_import_public_portal_stylesheet(self):
        internal_sources = [Path('templates/base.html').read_text()]
        internal_sources.extend(path.read_text() for path in Path('templates/web').glob('*.html'))

        for source in internal_sources:
            with self.subTest():
                self.assertNotIn('public_portal/css/public_portal.css', source)

    def test_internal_dashboard_contains_premium_layout_classes(self):
        self.client.force_login(self.create_user_for_role('ui-admin-layout', ROLE_SIGEDON_ADMIN))

        response = self.client.get(reverse('dashboard'))

        self.assertContains(response, 'ops-topbar')
        self.assertContains(response, 'ops-page-header')
        self.assertContains(response, 'ops-action-panel')
        self.assertContains(response, 'ops-metric-grid')

    def test_admin_sees_main_actions(self):
        self.client.force_login(self.create_user_for_role('ui-admin', ROLE_SIGEDON_ADMIN))

        dashboard_response = self.client.get(reverse('dashboard'))
        project_response = self.client.get(reverse('project_list'))
        expense_response = self.client.get(reverse('expense_list'))

        self.assertContains(dashboard_response, 'Crear proyecto')
        self.assertContains(dashboard_response, 'Crear gasto')
        self.assertContains(dashboard_response, 'Ver auditoría')
        self.assertContains(project_response, reverse('project_create'))
        self.assertContains(expense_response, reverse('expense_create'))

    def test_forbidden_actions_are_hidden_even_when_routes_remain_protected(self):
        field_user = self.create_user_for_role('ui-field-routes', ROLE_FIELD_OPERATOR)
        self.client.force_login(field_user)
        field_response = self.client.get(reverse('project_detail', args=[self.project.pk]))

        self.assertNotContains(field_response, reverse('project_update_publish', args=[self.project_update.pk]))
        self.assertEqual(self.client.post(reverse('project_update_publish', args=[self.project_update.pk])).status_code, 403)

        auditor_user = self.create_user_for_role('ui-auditor-routes', ROLE_EXTERNAL_AUDITOR)
        self.client.force_login(auditor_user)
        auditor_response = self.client.get(reverse('expense_list'))

        self.assertNotContains(auditor_response, reverse('expense_update', args=[self.expense.pk]))
        self.assertNotContains(auditor_response, reverse('expense_delete', args=[self.expense.pk]))
        self.assertEqual(self.client.get(reverse('expense_create')).status_code, 403)
