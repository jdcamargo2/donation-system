from pathlib import Path

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from apps.operations.models import AuditLog, Project
from apps.operations.role_services import sync_operation_roles
from apps.operations.roles import ROLE_EXTERNAL_AUDITOR, ROLE_FIELD_OPERATOR, ROLE_SIGEDON_ADMIN
from apps.operations.services import register_advance
from apps.operations.tests.helpers import create_allocation, create_donation, create_expense, create_institution, create_project


class RoleBasedUITests(TestCase):
    def setUp(self):
        sync_operation_roles()
        self.project = create_project()
        self.project.status = Project.Status.ACTIVE
        self.project.save()
        self.project_update = register_advance(
            project_id=self.project.pk,
            title='Avance visible por rol',
            description='Pendiente de revisión.',
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

    def test_field_operator_does_not_see_review_update_action(self):
        self.client.force_login(self.create_user_for_role('ui-field-review', ROLE_FIELD_OPERATOR))

        detail_response = self.client.get(reverse('project_detail', args=[self.project.pk]))
        update_response = self.client.get(reverse('project_update_detail', args=[self.project_update.pk]))

        self.assertNotContains(detail_response, 'Revisar')
        self.assertNotContains(detail_response, reverse('project_update_review', args=[self.project_update.pk]))
        self.assertNotContains(update_response, 'Revisar avance')
        self.assertNotContains(update_response, reverse('project_update_review', args=[self.project_update.pk]))

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

        self.assertNotContains(field_response, reverse('project_update_review', args=[self.project_update.pk]))
        self.assertEqual(self.client.get(reverse('project_update_review', args=[self.project_update.pk])).status_code, 403)

        auditor_user = self.create_user_for_role('ui-auditor-routes', ROLE_EXTERNAL_AUDITOR)
        self.client.force_login(auditor_user)
        auditor_response = self.client.get(reverse('expense_list'))

        self.assertNotContains(auditor_response, reverse('expense_update', args=[self.expense.pk]))
        self.assertNotContains(auditor_response, reverse('expense_delete', args=[self.expense.pk]))
        self.assertEqual(self.client.get(reverse('expense_create')).status_code, 403)
