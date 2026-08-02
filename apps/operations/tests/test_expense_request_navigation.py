"""FLOW-ER-NAV: Expense Request entry points and allocation traceability."""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.operations.expense_request_services import create_expense_request
from apps.operations.models import ExpenseRequest, FundAllocation, Project
from apps.operations.role_services import sync_operation_roles
from apps.operations.roles import (
    ROLE_EXTERNAL_AUDITOR,
    ROLE_FIELD_OPERATOR,
    ROLE_PROJECT_COMMITTEE,
    ROLE_SIGEDON_ADMIN,
)
from apps.operations.selectors import (
    expense_request_allocation_choices,
    visible_expense_requests_for_allocation,
)
from apps.operations.tests.helpers import (
    TEST_DATE,
    create_allocation,
    create_donation,
    create_expense,
    create_expense_request as create_expense_request_row,
    create_project,
)


class ExpenseRequestNavigationTests(TestCase):
    def setUp(self):
        sync_operation_roles()
        self.project = create_project(code='PRJ-ER-NAV', name='Proyecto ER-NAV')
        self.other_project = create_project(
            code='PRJ-ER-NAV-OTHER',
            name='Otro proyecto ER-NAV',
        )
        self.donation = create_donation(code='DON-ER-NAV', amount=Decimal('1000.00'))
        self.allocation = create_allocation(
            donation=self.donation,
            project=self.project,
            amount=Decimal('200.00'),
            category='health_psychosocial',
        )
        self.other_allocation = create_allocation(
            donation=self.donation,
            project=self.other_project,
            amount=Decimal('150.00'),
            category='training_entrepreneurship',
        )
        self.admin = self._user('er-nav-admin', ROLE_SIGEDON_ADMIN)
        self.operator = self._user('er-nav-operator', ROLE_FIELD_OPERATOR)
        self.other_operator = self._user('er-nav-operator-b', ROLE_FIELD_OPERATOR)
        self.committee = self._user('er-nav-committee', ROLE_PROJECT_COMMITTEE)
        self.auditor = self._user('er-nav-auditor', ROLE_EXTERNAL_AUDITOR)
        self.view_fundallocation = Permission.objects.get(
            content_type__app_label='operations',
            codename='view_fundallocation',
        )

    def _user(self, username, role_name):
        user = get_user_model().objects.create_user(
            username=username,
            password='pass-12345',
        )
        user.groups.add(Group.objects.get(name=role_name))
        return user

    def _grant_allocation_view(self, user):
        """Operators/Committee lack view_fundallocation; grant only for detail UI tests."""
        user.user_permissions.add(self.view_fundallocation)
        return get_user_model().objects.get(pk=user.pk)

    def _create_url(self, project=None):
        return reverse(
            'expense_request_create_for_project',
            args=[(project or self.project).pk],
        )

    def _create_payload(self, allocation, *, amount='25,00', purpose='Solicitud ER-NAV'):
        return {
            'fund_allocation': str(allocation.pk),
            'requested_amount': amount,
            'purpose': purpose,
            'requested_date': TEST_DATE.isoformat(),
        }

    def _create_via_service(self, actor, allocation=None, *, purpose='Solicitud propia ER-NAV'):
        return create_expense_request(
            fund_allocation=allocation or self.allocation,
            requested_amount=Decimal('25.00'),
            purpose=purpose,
            requested_date=TEST_DATE,
            actor=actor,
        )

    # --- FLOW-ER-NAV-10: manipulated parameter ---

    def test_valid_allocation_query_parameter_preselects(self):
        self.client.force_login(self.operator)
        response = self.client.get(
            f'{self._create_url()}?allocation={self.allocation.pk}'
        )
        self.assertEqual(response.status_code, 200)
        form = response.context['form']
        self.assertEqual(form.initial.get('fund_allocation'), self.allocation.pk)
        self.assertContains(response, f'value="{self.allocation.pk}"')
        self.assertContains(response, 'selected')

    def test_allocation_from_another_project_is_not_preselected(self):
        self.client.force_login(self.operator)
        response = self.client.get(
            f'{self._create_url()}?allocation={self.other_allocation.pk}'
        )
        self.assertEqual(response.status_code, 200)
        form = response.context['form']
        self.assertNotEqual(form.initial.get('fund_allocation'), self.other_allocation.pk)
        self.assertNotContains(response, f'value="{self.other_allocation.pk}"')
        self.assertFalse(
            form.fields['fund_allocation'].queryset.filter(pk=self.other_allocation.pk).exists()
        )

    def test_finished_allocation_is_not_preselected(self):
        finished = create_allocation(
            donation=create_donation(code='DON-ER-NAV-FIN', amount=Decimal('80.00')),
            project=self.project,
            amount=Decimal('40.00'),
            category='infrastructure_supply',
            status=FundAllocation.Status.FINISHED,
        )
        self.client.force_login(self.operator)
        response = self.client.get(f'{self._create_url()}?allocation={finished.pk}')
        self.assertEqual(response.status_code, 200)
        form = response.context['form']
        self.assertNotEqual(form.initial.get('fund_allocation'), finished.pk)
        self.assertNotContains(response, f'value="{finished.pk}"')

    def test_annulled_allocation_is_not_preselected(self):
        annulled = create_allocation(
            donation=create_donation(code='DON-ER-NAV-ANL', amount=Decimal('80.00')),
            project=self.project,
            amount=Decimal('40.00'),
            category='communication_networks',
            status=FundAllocation.Status.ANNULLED,
        )
        self.client.force_login(self.operator)
        response = self.client.get(f'{self._create_url()}?allocation={annulled.pk}')
        self.assertEqual(response.status_code, 200)
        form = response.context['form']
        self.assertNotEqual(form.initial.get('fund_allocation'), annulled.pk)
        self.assertNotContains(response, f'value="{annulled.pk}"')

    def test_allocation_on_closed_project_is_not_preselected(self):
        closed_project = create_project(code='PRJ-ER-NAV-CLOSED', name='Cerrado ER-NAV')
        closed_allocation = create_allocation(
            donation=create_donation(code='DON-ER-NAV-CLOSED', amount=Decimal('90.00')),
            project=closed_project,
            amount=Decimal('50.00'),
            category='institutional_relations',
        )
        Project.objects.filter(pk=closed_project.pk).update(status=Project.Status.CLOSED)
        self.client.force_login(self.operator)
        closed_create = self.client.get(self._create_url(closed_project))
        self.assertEqual(closed_create.status_code, 404)
        response = self.client.get(
            f'{self._create_url()}?allocation={closed_allocation.pk}'
        )
        self.assertEqual(response.status_code, 200)
        form = response.context['form']
        self.assertNotEqual(form.initial.get('fund_allocation'), closed_allocation.pk)
        self.assertNotContains(response, f'value="{closed_allocation.pk}"')
        self.assertFalse(
            expense_request_allocation_choices(project=self.project)
            .filter(pk=closed_allocation.pk)
            .exists()
        )

    def test_nonexistent_allocation_parameter_does_not_reveal_existence(self):
        self.client.force_login(self.operator)
        missing_pk = 9_999_991
        response = self.client.get(f'{self._create_url()}?allocation={missing_pk}')
        self.assertEqual(response.status_code, 200)
        form = response.context['form']
        self.assertNotEqual(form.initial.get('fund_allocation'), missing_pk)
        self.assertNotContains(response, str(missing_pk))
        self.assertNotContains(response, 'no existe')
        self.assertNotContains(response, 'no encontrada')

    def test_zero_balance_allocation_is_not_preselected(self):
        depleted = create_allocation(
            donation=create_donation(code='DON-ER-NAV-ZERO', amount=Decimal('60.00')),
            project=self.project,
            amount=Decimal('30.00'),
            category='infrastructure_supply',
        )
        create_expense(allocation=depleted, amount=Decimal('30.00'), reason='Consume todo')
        self.assertFalse(
            expense_request_allocation_choices(project=self.project)
            .filter(pk=depleted.pk)
            .exists()
        )
        self.client.force_login(self.operator)
        response = self.client.get(f'{self._create_url()}?allocation={depleted.pk}')
        form = response.context['form']
        self.assertNotEqual(form.initial.get('fund_allocation'), depleted.pk)
        self.assertNotContains(response, f'value="{depleted.pk}"')

    def test_crafted_post_with_ineligible_allocation_is_rejected(self):
        finished = create_allocation(
            donation=create_donation(code='DON-ER-NAV-POST-FIN', amount=Decimal('70.00')),
            project=self.project,
            amount=Decimal('35.00'),
            category='communication_networks',
            status=FundAllocation.Status.FINISHED,
        )
        before = ExpenseRequest.objects.count()
        self.client.force_login(self.operator)
        response = self.client.post(
            self._create_url(),
            self._create_payload(finished, purpose='POST inelegible ER-NAV'),
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn('fund_allocation', response.context['form'].errors)
        self.assertEqual(ExpenseRequest.objects.count(), before)
        self.assertNotContains(response, f'value="{finished.pk}"')

    def test_valid_post_still_creates_through_service(self):
        self.client.force_login(self.operator)
        response = self.client.post(
            f'{self._create_url()}?allocation={self.allocation.pk}',
            self._create_payload(self.allocation, purpose='POST válido ER-NAV'),
        )
        created = ExpenseRequest.objects.get(purpose='POST válido ER-NAV')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response['Location'],
            reverse('expense_request_detail', args=[created.pk]),
        )
        self.assertEqual(created.fund_allocation_id, self.allocation.pk)
        self.assertEqual(created.requested_by_id, self.operator.pk)

    def test_hidden_allocation_does_not_appear_in_option_markup(self):
        hidden = create_allocation(
            donation=create_donation(code='DON-ER-NAV-HIDDEN', amount=Decimal('55.00')),
            project=self.project,
            amount=Decimal('25.00'),
            category='institutional_relations',
            status=FundAllocation.Status.ANNULLED,
        )
        self.client.force_login(self.operator)
        response = self.client.get(self._create_url())
        self.assertContains(response, f'value="{self.allocation.pk}"')
        self.assertContains(response, 'Salud y apoyo psicosocial')
        self.assertNotContains(response, f'value="{hidden.pk}"')
        self.assertNotContains(response, 'Relaciones institucionales')
        self.assertNotContains(response, f'value="{self.other_allocation.pk}"')
        self.assertNotContains(response, 'DON-ER-NAV-HIDDEN')

    # --- FLOW-ER-NAV-11: project CTA ---

    def test_project_cta_shown_when_eligible_allocation_exists(self):
        url = reverse('project_detail', args=[self.project.pk])
        self.client.force_login(self.operator)
        response = self.client.get(url)
        self.assertTrue(response.context['can_create_expense_request'])
        self.assertContains(response, 'Solicitar gasto')
        self.assertContains(response, self._create_url())
        self.assertFalse(response.context['show_expense_request_allocation_guidance'])

    def test_project_cta_hidden_with_guidance_when_no_allocations(self):
        empty_project = create_project(code='PRJ-ER-NAV-EMPTY', name='Sin asignaciones')
        url = reverse('project_detail', args=[empty_project.pk])
        self.client.force_login(self.operator)
        response = self.client.get(url)
        self.assertFalse(response.context['can_create_expense_request'])
        self.assertTrue(response.context['show_expense_request_allocation_guidance'])
        self.assertNotContains(response, 'Solicitar gasto')
        self.assertContains(
            response,
            'No hay asignaciones disponibles para registrar una solicitud de gasto.',
        )
        self.assertFalse(response.context['show_expense_request_admin_allocation_guidance'])
        self.assertNotContains(
            response,
            'Verifica que el proyecto tenga una asignación activa y disponible.',
        )

    def test_project_cta_admin_gets_secondary_guidance_when_empty(self):
        empty_project = create_project(code='PRJ-ER-NAV-EMPTY-ADM', name='Vacío admin')
        self.client.force_login(self.admin)
        response = self.client.get(reverse('project_detail', args=[empty_project.pk]))
        self.assertTrue(response.context['show_expense_request_allocation_guidance'])
        self.assertTrue(response.context['show_expense_request_admin_allocation_guidance'])
        self.assertContains(
            response,
            'Verifica que el proyecto tenga una asignación activa y disponible.',
        )

    def test_project_cta_hidden_when_only_finished_allocation(self):
        project = create_project(code='PRJ-ER-NAV-FIN-ONLY', name='Solo finalizada')
        create_allocation(
            donation=create_donation(code='DON-ER-NAV-FIN-ONLY', amount=Decimal('40.00')),
            project=project,
            amount=Decimal('20.00'),
            status=FundAllocation.Status.FINISHED,
        )
        self.client.force_login(self.operator)
        response = self.client.get(reverse('project_detail', args=[project.pk]))
        self.assertFalse(response.context['can_create_expense_request'])
        self.assertNotContains(response, 'Solicitar gasto')
        self.assertContains(
            response,
            'No hay asignaciones disponibles para registrar una solicitud de gasto.',
        )

    def test_project_cta_hidden_when_only_annulled_allocation(self):
        project = create_project(code='PRJ-ER-NAV-ANL-ONLY', name='Solo anulada')
        create_allocation(
            donation=create_donation(code='DON-ER-NAV-ANL-ONLY', amount=Decimal('40.00')),
            project=project,
            amount=Decimal('20.00'),
            status=FundAllocation.Status.ANNULLED,
        )
        self.client.force_login(self.operator)
        response = self.client.get(reverse('project_detail', args=[project.pk]))
        self.assertFalse(response.context['can_create_expense_request'])
        self.assertNotContains(response, 'Solicitar gasto')

    def test_project_cta_hidden_when_allocation_has_no_availability(self):
        project = create_project(code='PRJ-ER-NAV-DEPLETED', name='Sin disponible')
        allocation = create_allocation(
            donation=create_donation(code='DON-ER-NAV-DEPLETED', amount=Decimal('40.00')),
            project=project,
            amount=Decimal('20.00'),
        )
        create_expense(allocation=allocation, amount=Decimal('20.00'), reason='Agotada')
        self.client.force_login(self.operator)
        response = self.client.get(reverse('project_detail', args=[project.pk]))
        self.assertFalse(response.context['can_create_expense_request'])
        self.assertNotContains(response, 'Solicitar gasto')

    def test_ineligible_foreign_allocation_does_not_enable_project_cta(self):
        """Another project's eligible allocation must not enable this project's CTA."""
        project = create_project(code='PRJ-ER-NAV-NO-ELIG', name='Sin elegibles propias')
        create_allocation(
            donation=create_donation(code='DON-ER-NAV-NO-ELIG', amount=Decimal('40.00')),
            project=project,
            amount=Decimal('20.00'),
            status=FundAllocation.Status.FINISHED,
        )
        self.assertTrue(
            expense_request_allocation_choices(project=self.other_project).exists()
        )
        self.client.force_login(self.operator)
        response = self.client.get(reverse('project_detail', args=[project.pk]))
        self.assertFalse(response.context['can_create_expense_request'])
        self.assertNotContains(response, 'Solicitar gasto')

    def test_user_without_create_permission_sees_neither_cta_nor_guidance(self):
        url = reverse('project_detail', args=[self.project.pk])
        for user in (self.committee, self.auditor):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                response = self.client.get(url)
                self.assertFalse(response.context['can_create_expense_request'])
                self.assertFalse(response.context['show_expense_request_allocation_guidance'])
                self.assertNotContains(response, 'Solicitar gasto')
                self.assertNotContains(
                    response,
                    'No hay asignaciones disponibles para registrar una solicitud de gasto.',
                )

    def test_service_still_rejects_crafted_request_independently(self):
        finished = create_allocation(
            donation=create_donation(code='DON-ER-NAV-SVC', amount=Decimal('40.00')),
            project=self.project,
            amount=Decimal('20.00'),
            status=FundAllocation.Status.FINISHED,
        )
        with self.assertRaises(ValidationError):
            create_expense_request(
                fund_allocation=finished,
                requested_amount=Decimal('10.00'),
                purpose='Bypass servicio',
                requested_date=TEST_DATE,
                actor=self.operator,
            )

    # --- FLOW-ER-NAV-12: allocation detail ---

    def test_allocation_cta_includes_allocation_query_parameter(self):
        operator = self._grant_allocation_view(self.operator)
        self.client.force_login(operator)
        response = self.client.get(reverse('allocation_detail', args=[self.allocation.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['can_create_expense_request'])
        self.assertContains(
            response,
            f'{self._create_url()}?allocation={self.allocation.pk}',
        )

    def test_allocation_cta_link_opens_form_with_selected_allocation(self):
        self.client.force_login(self.operator)
        response = self.client.get(
            f'{self._create_url()}?allocation={self.allocation.pk}'
        )
        self.assertEqual(
            response.context['form'].initial.get('fund_allocation'),
            self.allocation.pk,
        )

    def test_allocation_cta_hidden_for_finished_allocation(self):
        finished = create_allocation(
            donation=create_donation(code='DON-ER-NAV-CTA-FIN', amount=Decimal('40.00')),
            project=self.project,
            amount=Decimal('20.00'),
            status=FundAllocation.Status.FINISHED,
        )
        operator = self._grant_allocation_view(self.operator)
        self.client.force_login(operator)
        response = self.client.get(reverse('allocation_detail', args=[finished.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['can_create_expense_request'])
        self.assertNotContains(response, 'Solicitar gasto')

    def test_linked_requests_section_renders_for_operator_own_request(self):
        own = self._create_via_service(
            self.operator,
            purpose='Propósito visible operador ER-NAV',
        )
        foreign = self._create_via_service(
            self.other_operator,
            purpose='Propósito ajeno oculto ER-NAV',
        )
        operator = self._grant_allocation_view(self.operator)
        self.client.force_login(operator)
        response = self.client.get(reverse('allocation_detail', args=[self.allocation.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['can_view_expense_requests'])
        self.assertContains(response, 'Solicitudes de gasto vinculadas')
        self.assertContains(response, own.code)
        self.assertContains(response, 'Propósito visible operador ER-NAV')
        self.assertContains(response, own.get_status_display())
        self.assertContains(response, reverse('expense_request_detail', args=[own.pk]))
        self.assertNotContains(response, foreign.code)
        self.assertNotContains(response, 'Propósito ajeno oculto ER-NAV')
        self.assertNotContains(response, 'pending_decision')
        self.assertNotContains(response, 'Aprobar')
        self.assertNotContains(response, 'Denegar')
        self.assertNotContains(response, 'Retirar')

    def test_admin_sees_all_linked_requests(self):
        first = self._create_via_service(self.operator, purpose='Admin ve A ER-NAV')
        second = self._create_via_service(self.other_operator, purpose='Admin ve B ER-NAV')
        self.client.force_login(self.admin)
        response = self.client.get(reverse('allocation_detail', args=[self.allocation.pk]))
        self.assertContains(response, first.code)
        self.assertContains(response, second.code)

    def test_committee_and_auditor_see_linked_requests_read_only(self):
        request_obj = self._create_via_service(
            self.operator,
            purpose='Visible comité/auditor ER-NAV',
        )
        committee = self._grant_allocation_view(self.committee)
        for user in (committee, self.auditor):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                response = self.client.get(
                    reverse('allocation_detail', args=[self.allocation.pk])
                )
                self.assertEqual(response.status_code, 200)
                self.assertFalse(response.context['can_create_expense_request'])
                self.assertNotContains(response, 'Solicitar gasto')
                self.assertContains(response, request_obj.code)
                self.assertContains(response, 'Visible comité/auditor ER-NAV')
                self.assertNotContains(response, 'Aprobar')
                self.assertNotContains(response, 'Denegar')

    def test_linked_requests_empty_state(self):
        operator = self._grant_allocation_view(self.operator)
        self.client.force_login(operator)
        response = self.client.get(reverse('allocation_detail', args=[self.allocation.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            'No hay solicitudes de gasto vinculadas a esta asignación.',
        )

    def test_view_fundallocation_without_view_expenserequest_does_not_leak_rows(self):
        own = self._create_via_service(self.operator, purpose='No filtrar por asignación sola')
        viewer = get_user_model().objects.create_user(
            username='er-nav-alloc-only',
            password='pass-12345',
        )
        viewer.user_permissions.add(self.view_fundallocation)
        self.client.force_login(viewer)
        response = self.client.get(reverse('allocation_detail', args=[self.allocation.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['can_view_expense_requests'])
        self.assertNotContains(response, 'Solicitudes de gasto vinculadas')
        self.assertNotContains(response, own.code)
        self.assertNotContains(response, 'No filtrar por asignación sola')

    def test_linked_request_detail_link_returns_200_for_same_user(self):
        own = self._create_via_service(self.operator)
        operator = self._grant_allocation_view(self.operator)
        self.client.force_login(operator)
        detail = self.client.get(reverse('allocation_detail', args=[self.allocation.pk]))
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, reverse('expense_request_detail', args=[own.pk]))
        response = self.client.get(reverse('expense_request_detail', args=[own.pk]))
        self.assertEqual(response.status_code, 200)

    def test_visible_expense_requests_for_allocation_starts_from_user_scope(self):
        own = self._create_via_service(self.operator, purpose='Selector propio')
        self._create_via_service(self.other_operator, purpose='Selector ajeno')
        visible = list(
            visible_expense_requests_for_allocation(
                user=self.operator,
                allocation=self.allocation,
            )
        )
        self.assertEqual([row.pk for row in visible], [own.pk])

    def test_form_empty_state_when_no_eligible_allocations(self):
        empty_project = create_project(code='PRJ-ER-NAV-FORM-EMPTY', name='Form vacío')
        self.client.force_login(self.operator)
        response = self.client.get(self._create_url(empty_project))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['has_eligible_allocations'])
        self.assertContains(
            response,
            'No hay asignaciones disponibles para registrar esta solicitud.',
        )
        self.assertNotContains(response, 'Registrar solicitud')
        self.assertContains(response, 'Volver al proyecto')
        self.assertContains(
            response,
            reverse('project_detail', args=[empty_project.pk]),
        )

    def test_linked_requests_do_not_add_queries_per_row(self):
        one_request_allocation = create_allocation(
            donation=create_donation(code='DON-ER-NAV-Q1', amount=Decimal('100.00')),
            project=create_project(code='PRJ-ER-NAV-Q1'),
            amount=Decimal('50.00'),
        )
        three_request_allocation = create_allocation(
            donation=create_donation(code='DON-ER-NAV-Q3', amount=Decimal('100.00')),
            project=create_project(code='PRJ-ER-NAV-Q3'),
            amount=Decimal('50.00'),
        )
        create_expense_request_row(
            fund_allocation=one_request_allocation,
            requested_by=self.admin,
            purpose='Una solicitud query',
            requested_amount=Decimal('5.00'),
            code='SGS-ERNAV-ONE',
        )
        for index in range(3):
            create_expense_request_row(
                fund_allocation=three_request_allocation,
                requested_by=self.admin,
                purpose=f'Solicitud query {index}',
                requested_amount=Decimal('5.00'),
                code=f'SGS-ERNAV-{index:04d}',
            )
        self.client.force_login(self.admin)
        with CaptureQueriesContext(connection) as one_queries:
            one_response = self.client.get(
                reverse('allocation_detail', args=[one_request_allocation.pk])
            )
        with CaptureQueriesContext(connection) as three_queries:
            three_response = self.client.get(
                reverse('allocation_detail', args=[three_request_allocation.pk])
            )
        self.assertEqual(one_response.status_code, 200)
        self.assertEqual(three_response.status_code, 200)
        self.assertEqual(len(one_queries), len(three_queries))
        self.assertEqual(len(three_response.context['recent_allocation_expense_requests']), 3)
