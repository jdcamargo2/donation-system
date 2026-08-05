"""BUG-E2E-004: Expense Request list CTA and eligible-project chooser entry."""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from apps.operations.models import FundAllocation, Project
from apps.operations.role_services import sync_operation_roles
from apps.operations.roles import (
    ROLE_EXTERNAL_AUDITOR,
    ROLE_FIELD_OPERATOR,
    ROLE_PROJECT_COMMITTEE,
    ROLE_SIGEDON_ADMIN,
)
from apps.operations.selectors import eligible_projects_for_expense_request_creation
from apps.operations.tests.helpers import (
    create_allocation,
    create_donation,
    create_expense,
    create_project,
)


class ExpenseRequestCreationEntryTests(TestCase):
    def setUp(self):
        sync_operation_roles()
        self.project = create_project(code='PRJ-E2E004', name='Proyecto E2E-004')
        self.other_project = create_project(
            code='PRJ-E2E004-OTHER',
            name='Otro proyecto E2E-004',
        )
        donation = create_donation(code='DON-E2E004', amount=Decimal('1000.00'))
        self.allocation = create_allocation(
            donation=donation,
            project=self.project,
            amount=Decimal('200.00'),
        )
        create_allocation(
            donation=donation,
            project=self.other_project,
            amount=Decimal('150.00'),
            category='training_entrepreneurship',
        )
        self.admin = self._user('e2e004-admin', ROLE_SIGEDON_ADMIN)
        self.operator = self._user('e2e004-operator', ROLE_FIELD_OPERATOR)
        self.committee = self._user('e2e004-committee', ROLE_PROJECT_COMMITTEE)
        self.auditor = self._user('e2e004-auditor', ROLE_EXTERNAL_AUDITOR)
        self.list_url = reverse('expense_request_list')
        self.chooser_url = reverse('expense_request_create_choose_project')
        self.global_create_url = reverse('expense_request_create')
        self.create_for_project_url = reverse(
            'expense_request_create_for_project',
            args=[self.project.pk],
        )

    def _user(self, username, role_name):
        user = get_user_model().objects.create_user(
            username=username,
            password='pass-12345',
        )
        user.groups.add(Group.objects.get(name=role_name))
        return user

    # --- List CTA ---

    def test_operator_sees_nueva_solicitud_de_gasto_cta(self):
        self.client.force_login(self.operator)
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['show_expense_request_create_cta'])
        self.assertTrue(response.context['can_create_expense_request'])
        self.assertFalse(response.context['can_create_global_expense_request'])
        self.assertContains(response, 'Nueva solicitud de gasto')
        self.assertContains(response, self.chooser_url)
        self.assertNotContains(response, f'href="{self.global_create_url}"')
        self.assertNotIn('role_name', response.context)
        self.assertNotIn('functional_role', response.context)

    def test_admin_cta_keeps_global_create_route(self):
        self.client.force_login(self.admin)
        response = self.client.get(self.list_url)
        self.assertTrue(response.context['show_expense_request_create_cta'])
        self.assertTrue(response.context['can_create_global_expense_request'])
        self.assertContains(response, 'Nueva solicitud de gasto')
        self.assertContains(response, self.global_create_url)
        self.assertNotContains(response, self.chooser_url)

    def test_auditor_and_committee_do_not_see_create_cta(self):
        for user in (self.auditor, self.committee):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                response = self.client.get(self.list_url)
                if response.status_code == 302:
                    response = self.client.get(response['Location'])
                self.assertEqual(response.status_code, 200)
                self.assertFalse(response.context['show_expense_request_create_cta'])
                self.assertFalse(response.context['can_create_expense_request'])
                self.assertNotContains(response, 'Nueva solicitud de gasto')
                self.assertNotContains(response, self.chooser_url)
                self.assertNotContains(response, self.global_create_url)

    def test_anonymous_list_redirects_to_login(self):
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response['Location'])

    def test_list_search_and_filters_remain_intact(self):
        self.client.force_login(self.operator)
        response = self.client.get(self.list_url, {'q': 'sin-coincidencias-e2e004'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Nueva solicitud de gasto')
        self.assertContains(
            response,
            'No se encontraron solicitudes con los filtros seleccionados.',
        )

    # --- Chooser entry ---

    def test_operator_opens_chooser_with_eligible_project(self):
        self.client.force_login(self.operator)
        response = self.client.get(self.chooser_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            'Seleccione el proyecto para el cual desea registrar la solicitud.',
        )
        self.assertContains(response, self.project.code)
        self.assertContains(response, self.project.name)
        self.assertContains(response, self.create_for_project_url)
        self.assertContains(response, 'Continuar')

    def test_closed_project_absent_from_chooser(self):
        closed = create_project(code='PRJ-E2E004-CLOSED', name='Cerrado E2E-004')
        create_allocation(
            donation=create_donation(code='DON-E2E004-CLOSED', amount=Decimal('80.00')),
            project=closed,
            amount=Decimal('40.00'),
        )
        Project.objects.filter(pk=closed.pk).update(status=Project.Status.CLOSED)
        self.client.force_login(self.operator)
        response = self.client.get(self.chooser_url)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, closed.code)
        self.assertNotContains(response, closed.name)
        self.assertNotContains(
            response,
            reverse('expense_request_create_for_project', args=[closed.pk]),
        )
        self.assertFalse(
            eligible_projects_for_expense_request_creation()
            .filter(pk=closed.pk)
            .exists()
        )

    def test_project_without_eligible_allocation_absent(self):
        empty = create_project(code='PRJ-E2E004-EMPTY', name='Sin elegibles')
        depleted = create_project(code='PRJ-E2E004-DEP', name='Agotado')
        depleted_alloc = create_allocation(
            donation=create_donation(code='DON-E2E004-DEP', amount=Decimal('50.00')),
            project=depleted,
            amount=Decimal('25.00'),
        )
        create_expense(allocation=depleted_alloc, amount=Decimal('25.00'), reason='Agota')
        finished_only = create_project(code='PRJ-E2E004-FIN', name='Solo finalizada')
        create_allocation(
            donation=create_donation(code='DON-E2E004-FIN', amount=Decimal('50.00')),
            project=finished_only,
            amount=Decimal('25.00'),
            status=FundAllocation.Status.FINISHED,
        )
        self.client.force_login(self.operator)
        response = self.client.get(self.chooser_url)
        for project in (empty, depleted, finished_only):
            with self.subTest(project=project.code):
                self.assertNotContains(response, project.code)
                self.assertNotContains(response, project.name)

    def test_chooser_empty_state_spanish(self):
        Project.objects.all().update(status=Project.Status.CLOSED)
        self.client.force_login(self.operator)
        response = self.client.get(self.chooser_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            'No hay proyectos disponibles para crear una solicitud de gasto.',
        )
        self.assertNotContains(response, 'Continuar')

    def test_selected_project_opens_existing_create_form(self):
        self.client.force_login(self.operator)
        chooser = self.client.get(self.chooser_url)
        self.assertContains(chooser, self.create_for_project_url)
        response = self.client.get(self.create_for_project_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['project'].pk, self.project.pk)
        self.assertTrue(response.context['has_eligible_allocations'])
        self.assertContains(response, 'Registrar solicitud')

    def test_direct_closed_project_create_url_remains_404(self):
        closed = create_project(code='PRJ-E2E004-CLOSED-URL', name='Cerrado URL')
        create_allocation(
            donation=create_donation(code='DON-E2E004-CLOSED-URL', amount=Decimal('70.00')),
            project=closed,
            amount=Decimal('35.00'),
        )
        Project.objects.filter(pk=closed.pk).update(status=Project.Status.CLOSED)
        self.client.force_login(self.operator)
        response = self.client.get(
            reverse('expense_request_create_for_project', args=[closed.pk])
        )
        self.assertEqual(response.status_code, 404)

    def test_unauthorized_roles_denied_on_chooser(self):
        for user in (self.committee, self.auditor):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                self.assertEqual(self.client.get(self.chooser_url).status_code, 403)

    def test_anonymous_chooser_redirects_to_login(self):
        response = self.client.get(self.chooser_url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response['Location'])

    def test_chooser_get_does_not_create_expense_request(self):
        from apps.operations.models import ExpenseRequest

        before = ExpenseRequest.objects.count()
        self.client.force_login(self.operator)
        self.assertEqual(self.client.get(self.chooser_url).status_code, 200)
        self.assertEqual(ExpenseRequest.objects.count(), before)

    def test_project_detail_cta_remains_functional(self):
        self.client.force_login(self.operator)
        response = self.client.get(reverse('project_detail', args=[self.project.pk]))
        self.assertTrue(response.context['can_create_expense_request'])
        self.assertContains(response, 'Solicitar gasto')
        self.assertContains(response, self.create_for_project_url)
