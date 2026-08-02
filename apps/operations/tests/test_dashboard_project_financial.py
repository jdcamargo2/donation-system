from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.operations.models import (
    Expense,
    ExpenseRequest,
    FundAllocation,
    Project,
    ZERO_MONEY,
)
from apps.operations.role_services import sync_operation_roles
from apps.operations.roles import (
    ROLE_EXTERNAL_AUDITOR,
    ROLE_FIELD_OPERATOR,
    ROLE_PROJECT_COMMITTEE,
    ROLE_SIGEDON_ADMIN,
)
from apps.operations.selectors import with_project_financial_metrics
from apps.operations.services import (
    DASHBOARD_PROJECT_FINANCIAL_PREVIEW_LIMIT,
    get_dashboard_project_financial_rows,
    get_project_financial_summary,
)
from apps.operations.tests.helpers import (
    create_allocation,
    create_approved_reserved_request,
    create_donation,
    create_expense,
    create_expense_request,
    create_institution,
    create_project,
)
from apps.operations.tests.test_permissions import create_user_with_permissions


class DashboardProjectFinancialPermissionTests(TestCase):
    def setUp(self):
        sync_operation_roles()
        self.donor = create_institution(name='Donante DASH-FIN3')
        self.project = create_project(
            code='PRJ-DASH-FIN3',
            name='Catia La Mar',
        )
        donation = create_donation(
            code='DON-DASH-FIN3',
            donor=self.donor,
            amount=Decimal('200.00'),
        )
        allocation = create_allocation(
            donation=donation,
            project=self.project,
            amount=Decimal('100.00'),
        )
        create_expense(allocation=allocation, amount=Decimal('25.00'))
        create_approved_reserved_request(
            fund_allocation=allocation,
            requested_amount=Decimal('15.00'),
            code='SGS-DASH-001',
        )

    def _role_user(self, username, role_name):
        user = get_user_model().objects.create_user(
            username=username,
            password='pass-12345',
        )
        user.groups.add(Group.objects.get(name=role_name))
        return user

    def test_superuser_sees_project_financial_section(self):
        user = get_user_model().objects.create_superuser(
            username='dash-fin3-super',
            password='pass-12345',
        )
        self.client.force_login(user)

        response = self.client.get(reverse('dashboard'))

        self.assertTrue(response.context['show_project_financial_section'])
        self.assertContains(response, 'Estado financiero por proyecto')
        self.assertContains(response, 'PRJ-DASH-FIN3 · Catia La Mar')
        self.assertContains(response, 'Disponible operativo')
        detail = self.client.get(reverse('project_detail', args=[self.project.pk]))
        self.assertEqual(detail.status_code, 200)

    def test_admin_sees_project_financial_section(self):
        user = self._role_user('dash-fin3-admin', ROLE_SIGEDON_ADMIN)
        self.client.force_login(user)

        response = self.client.get(reverse('dashboard'))

        self.assertTrue(response.context['show_project_financial_section'])
        self.assertContains(response, 'Estado financiero por proyecto')
        self.assertContains(response, 'PRJ-DASH-FIN3 · Catia La Mar')
        detail = self.client.get(
            response.context['project_financial_rows'][0]['detail_url']
        )
        self.assertEqual(detail.status_code, 200)

    def test_auditor_sees_read_only_project_financial_section(self):
        user = self._role_user('dash-fin3-auditor', ROLE_EXTERNAL_AUDITOR)
        self.client.force_login(user)

        response = self.client.get(reverse('dashboard'))
        html = response.content.decode()

        self.assertTrue(response.context['show_project_financial_section'])
        self.assertContains(response, 'Estado financiero por proyecto')
        self.assertContains(response, 'PRJ-DASH-FIN3 · Catia La Mar')
        self.assertNotContains(response, 'Accesos rápidos')
        self.assertNotContains(response, 'ops-action-panel')
        self.assertNotIn('Crear gasto', html)
        self.assertNotContains(response, reverse('expense_create'))
        detail = self.client.get(
            response.context['project_financial_rows'][0]['detail_url']
        )
        self.assertEqual(detail.status_code, 200)

    def test_operator_does_not_see_project_financial_section(self):
        user = self._role_user('dash-fin3-operator', ROLE_FIELD_OPERATOR)
        self.client.force_login(user)

        response = self.client.get(reverse('dashboard'))
        html = response.content.decode()

        self.assertFalse(response.context['show_project_financial_section'])
        self.assertEqual(response.context['project_financial_rows'], [])
        self.assertNotContains(response, 'Estado financiero por proyecto')
        self.assertNotIn('PRJ-DASH-FIN3', html)
        self.assertNotIn('Catia La Mar', html)
        self.assertNotIn('100,00', html)

    def test_committee_does_not_see_project_financial_section(self):
        user = self._role_user('dash-fin3-committee', ROLE_PROJECT_COMMITTEE)
        self.client.force_login(user)

        response = self.client.get(reverse('dashboard'))
        html = response.content.decode()

        self.assertFalse(response.context['show_project_financial_section'])
        self.assertEqual(response.context['project_financial_rows'], [])
        self.assertNotContains(response, 'Estado financiero por proyecto')
        self.assertNotIn('PRJ-DASH-FIN3', html)
        self.assertNotIn('Catia La Mar', html)

    def test_partial_allocation_permission_hides_section(self):
        user = create_user_with_permissions(
            'dash-fin3-alloc-only',
            'view_fundallocation',
            'view_project',
        )
        self.client.force_login(user)

        response = self.client.get(reverse('dashboard'))

        self.assertFalse(response.context['show_project_financial_section'])
        self.assertEqual(response.context['project_financial_rows'], [])
        self.assertNotContains(response, 'Estado financiero por proyecto')
        self.assertNotContains(response, 'Disponible operativo')

    def test_partial_expense_permission_hides_section(self):
        user = create_user_with_permissions(
            'dash-fin3-expense-only',
            'view_expense',
            'view_project',
        )
        self.client.force_login(user)

        response = self.client.get(reverse('dashboard'))

        self.assertFalse(response.context['show_project_financial_section'])
        self.assertEqual(response.context['project_financial_rows'], [])
        self.assertNotContains(response, 'Estado financiero por proyecto')
        self.assertNotContains(response, 'Disponible operativo')


class DashboardProjectFinancialCorrectnessTests(TestCase):
    def setUp(self):
        self.donor = create_institution(name='Donante métricas proyecto')
        self.project = create_project(code='PRJ-FIN-METRICS', name='Métricas')
        self.donation = create_donation(
            code='DON-FIN-METRICS',
            donor=self.donor,
            amount=Decimal('500.00'),
        )

    def _annotated(self, project=None):
        target = project or self.project
        return with_project_financial_metrics(Project.objects.filter(pk=target.pk)).get()

    def test_active_and_finished_allocations_count_annulled_excluded(self):
        create_allocation(
            donation=self.donation,
            project=self.project,
            amount=Decimal('100.00'),
            status=FundAllocation.Status.ACTIVE,
        )
        create_allocation(
            donation=self.donation,
            project=self.project,
            amount=Decimal('40.00'),
            status=FundAllocation.Status.FINISHED,
            category='Finished',
        )
        create_allocation(
            donation=self.donation,
            project=self.project,
            amount=Decimal('90.00'),
            status=FundAllocation.Status.ANNULLED,
            category='Annulled',
        )

        annotated = self._annotated()

        self.assertEqual(annotated.annotated_funded_amount, Decimal('140.00'))
        self.assertEqual(annotated.annotated_executed_amount, ZERO_MONEY)
        self.assertEqual(annotated.annotated_reserved_amount, ZERO_MONEY)
        self.assertEqual(annotated.annotated_available_amount, Decimal('140.00'))

    def test_registered_expense_counts_and_annulled_excluded(self):
        allocation = create_allocation(
            donation=self.donation,
            project=self.project,
            amount=Decimal('100.00'),
        )
        create_expense(allocation=allocation, amount=Decimal('30.00'))
        annulled = create_expense(allocation=allocation, amount=Decimal('20.00'))
        Expense.objects.filter(pk=annulled.pk).update(status=Expense.Status.ANNULLED)

        annotated = self._annotated()

        self.assertEqual(annotated.annotated_executed_amount, Decimal('30.00'))
        self.assertEqual(annotated.annotated_available_amount, Decimal('70.00'))

    def test_reservation_status_matrix(self):
        allocation = create_allocation(
            donation=self.donation,
            project=self.project,
            amount=Decimal('100.00'),
        )
        requester = create_user_with_permissions(
            'fin-requester',
            'add_expenserequest',
            'withdraw_expenserequest',
        )
        decider = create_user_with_permissions('fin-decider', 'decide_expenserequest')
        create_approved_reserved_request(
            fund_allocation=allocation,
            requested_by=requester,
            decided_by=decider,
            requested_amount=Decimal('20.00'),
            code='SGS-FIN-APPR',
        )
        create_expense_request(
            fund_allocation=allocation,
            requested_by=requester,
            requested_amount=Decimal('11.00'),
            status=ExpenseRequest.Status.PENDING_DECISION,
            code='SGS-FIN-PEND',
        )
        now = timezone.now()
        fulfilled_expense = create_expense(
            allocation=allocation,
            amount=Decimal('12.00'),
            reason='Gasto cumplido de prueba',
        )
        create_expense_request(
            fund_allocation=allocation,
            requested_by=requester,
            requested_amount=Decimal('12.00'),
            status=ExpenseRequest.Status.FULFILLED,
            code='SGS-FIN-FULL',
            reserved_amount=Decimal('12.00'),
            reserved_at=now,
            decided_at=now,
            decided_by=decider,
            expense=fulfilled_expense,
        )
        create_expense_request(
            fund_allocation=allocation,
            requested_by=requester,
            requested_amount=Decimal('13.00'),
            status=ExpenseRequest.Status.DENIED,
            code='SGS-FIN-DEN',
            decided_at=now,
            decided_by=decider,
            decision_note='Denegación de prueba con motivo suficiente.',
        )
        create_expense_request(
            fund_allocation=allocation,
            requested_by=requester,
            requested_amount=Decimal('14.00'),
            status=ExpenseRequest.Status.WITHDRAWN,
            code='SGS-FIN-WITH',
            terminal_by=requester,
            terminal_at=now,
            terminal_reason='Retiro de prueba con motivo suficiente.',
        )
        create_expense_request(
            fund_allocation=allocation,
            requested_by=requester,
            requested_amount=Decimal('15.00'),
            status=ExpenseRequest.Status.ANNULLED,
            code='SGS-FIN-ANN',
            reserved_amount=Decimal('15.00'),
            reserved_at=now,
            decided_at=now,
            decided_by=decider,
            terminal_by=decider,
            terminal_at=now,
            terminal_reason='Anulación de prueba con motivo suficiente.',
        )

        annotated = self._annotated()

        self.assertEqual(annotated.annotated_reserved_amount, Decimal('20.00'))
        self.assertEqual(annotated.annotated_executed_amount, Decimal('12.00'))
        self.assertEqual(annotated.annotated_available_amount, Decimal('68.00'))

    def test_available_clamped_at_zero_and_execution_semantics(self):
        allocation = create_allocation(
            donation=self.donation,
            project=self.project,
            amount=Decimal('100.00'),
        )
        create_expense(allocation=allocation, amount=Decimal('40.00'))
        create_approved_reserved_request(
            fund_allocation=allocation,
            requested_amount=Decimal('20.00'),
            code='SGS-FIN-CLAMP',
        )
        FundAllocation.objects.filter(pk=allocation.pk).update(amount=Decimal('50.00'))

        annotated = self._annotated()
        summary = get_project_financial_summary(self.project)

        self.assertEqual(annotated.annotated_available_amount, ZERO_MONEY)
        self.assertEqual(summary['available_amount'], ZERO_MONEY)
        self.assertEqual(summary['execution_percentage'], Decimal('80.0'))
        self.assertIsInstance(summary['execution_percentage'], Decimal)

        empty = create_project(code='PRJ-FIN-EMPTY', name='Sin fondos')
        empty_summary = get_project_financial_summary(empty)
        self.assertIsNone(empty_summary['execution_percentage'])
        self.assertEqual(empty_summary['reserved_amount'], ZERO_MONEY)

    def test_dashboard_row_matches_project_detail_amounts(self):
        allocation = create_allocation(
            donation=self.donation,
            project=self.project,
            amount=Decimal('100.00'),
        )
        create_expense(allocation=allocation, amount=Decimal('35.00'))
        create_approved_reserved_request(
            fund_allocation=allocation,
            requested_amount=Decimal('15.00'),
            code='SGS-FIN-MATCH',
        )
        user = create_user_with_permissions(
            'dash-fin3-match',
            'view_fundallocation',
            'view_expense',
            'view_project',
        )

        block = get_dashboard_project_financial_rows(user=user)
        row = block['project_financial_rows'][0]
        summary = get_project_financial_summary(self.project)

        self.assertEqual(row['assigned'], summary['funded_amount'])
        self.assertEqual(row['spent'], summary['executed_amount'])
        self.assertEqual(row['reserved'], summary['reserved_amount'])
        self.assertEqual(row['available'], summary['available_amount'])
        self.assertEqual(row['execution_percentage'], summary['execution_percentage'])
        self.assertEqual(row['assigned'], Decimal('100.00'))
        self.assertEqual(row['spent'], Decimal('35.00'))
        self.assertEqual(row['reserved'], Decimal('15.00'))
        self.assertEqual(row['available'], Decimal('50.00'))
        self.assertEqual(row['execution_percentage'], Decimal('35.0'))
        self.assertEqual(row['visual_percentage'], Decimal('35.0'))

        FundAllocation.objects.filter(pk=allocation.pk).update(amount=Decimal('100.00'))
        Expense.objects.filter(allocation=allocation).update(amount=Decimal('35.00'))
        self.assertEqual(
            FundAllocation.objects.get(pk=allocation.pk).amount,
            Decimal('100.00'),
        )

    def test_visual_percentage_capped_at_100(self):
        allocation = create_allocation(
            donation=self.donation,
            project=self.project,
            amount=Decimal('50.00'),
        )
        # Legacy anomaly path: force spent above assigned via direct update after create.
        expense = create_expense(allocation=allocation, amount=Decimal('10.00'))
        Expense.objects.filter(pk=expense.pk).update(amount=Decimal('80.00'))
        user = create_user_with_permissions(
            'dash-fin3-visual',
            'view_fundallocation',
            'view_expense',
        )

        row = get_dashboard_project_financial_rows(user=user)['project_financial_rows'][0]

        self.assertEqual(row['execution_percentage'], Decimal('160.0'))
        self.assertEqual(row['visual_percentage'], Decimal('100'))


class DashboardProjectFinancialQueryTests(TestCase):
    def setUp(self):
        self.donor = create_institution(name='Donante query proyecto')
        self.user = create_user_with_permissions(
            'dash-fin3-query',
            'view_fundallocation',
            'view_expense',
            'view_project',
        )

    def _seed_projects(self, count, *, prefix):
        projects = []
        for index in range(count):
            project = create_project(
                code=f'{prefix}{index:03d}',
                name=f'Proyecto query {prefix} {index}',
            )
            donation = create_donation(
                code=f'DON-{prefix}{index:03d}',
                donor=self.donor,
                amount=Decimal('100.00'),
            )
            allocation = create_allocation(
                donation=donation,
                project=project,
                amount=Decimal('40.00'),
            )
            create_expense(allocation=allocation, amount=Decimal('10.00'))
            projects.append(project)
        return projects

    def _assert_fixed_query_budget(self, count, *, prefix):
        self._seed_projects(count, prefix=prefix)
        # Warm auth/permission caches; the project block itself must stay fixed-query.
        self.user.has_perm('operations.view_fundallocation')
        self.user.has_perm('operations.view_expense')
        with CaptureQueriesContext(connection) as captured:
            block = get_dashboard_project_financial_rows(user=self.user)
        self.assertLessEqual(len(captured), 2)
        self.assertTrue(block['show_project_financial_section'])
        expected_rows = min(count, DASHBOARD_PROJECT_FINANCIAL_PREVIEW_LIMIT)
        self.assertEqual(len(block['project_financial_rows']), expected_rows)
        self.assertEqual(
            block['show_all_projects_link'],
            count > DASHBOARD_PROJECT_FINANCIAL_PREVIEW_LIMIT,
        )
        sql = ' '.join(item['sql'] for item in captured.captured_queries).lower()
        self.assertIn('annotated_funded_amount', sql)
        self.assertIn('annotated_reserved_amount', sql)
        # No per-project model-property aggregate loops.
        self.assertEqual(sql.count('from "operations_project"'), 1)

    def test_fixed_query_budget_for_one_project(self):
        self._assert_fixed_query_budget(1, prefix='Q1')

    def test_fixed_query_budget_for_five_projects(self):
        self._assert_fixed_query_budget(5, prefix='Q5')

    def test_fixed_query_budget_for_fifteen_projects(self):
        self._assert_fixed_query_budget(15, prefix='Q15')

    def test_preview_limit_and_view_all_at_eleven(self):
        self._seed_projects(11, prefix='PV')
        block = get_dashboard_project_financial_rows(user=self.user)

        self.assertEqual(len(block['project_financial_rows']), 10)
        self.assertTrue(block['show_all_projects_link'])
        self.assertEqual(block['all_projects_url'], reverse('project_list'))

    def test_no_per_row_detail_url_queries(self):
        self._seed_projects(5, prefix='URL')
        self.user.has_perm('operations.view_fundallocation')
        self.user.has_perm('operations.view_expense')
        with CaptureQueriesContext(connection) as captured:
            block = get_dashboard_project_financial_rows(user=self.user)
            urls = [row['detail_url'] for row in block['project_financial_rows']]
        self.assertEqual(len(urls), 5)
        self.assertLessEqual(len(captured), 2)


class DashboardProjectFinancialUITests(TestCase):
    def setUp(self):
        self.donor = create_institution(name='Donante UI proyecto')
        self.project = create_project(code='PRJ-UI-FIN', name='UI financiera')
        donation = create_donation(
            code='DON-UI-FIN',
            donor=self.donor,
            amount=Decimal('200.00'),
        )
        allocation = create_allocation(
            donation=donation,
            project=self.project,
            amount=Decimal('100.00'),
        )
        create_expense(allocation=allocation, amount=Decimal('64.00'))
        self.user = create_user_with_permissions(
            'dash-fin3-ui',
            'view_donation',
            'view_fundallocation',
            'view_expense',
            'view_project',
            'fulfill_expenserequest',
            'view_expenserequest',
        )
        self.user.user_permissions.add(
            *Permission.objects.filter(
                content_type__app_label='operations',
                codename__in={
                    'view_donation',
                    'view_fundallocation',
                    'view_expense',
                    'view_project',
                    'fulfill_expenserequest',
                    'view_expenserequest',
                },
            )
        )
        self.client.force_login(self.user)

    def test_section_markup_columns_formatting_and_placement(self):
        create_approved_reserved_request(
            fund_allocation=FundAllocation.objects.get(project=self.project),
            requested_amount=Decimal('10.00'),
            code='SGS-UI-RES',
        )
        response = self.client.get(reverse('dashboard'))
        html = response.content.decode()

        self.assertContains(response, 'Estado financiero por proyecto')
        self.assertContains(response, 'Fondos recibidos')
        self.assertContains(response, 'Solicitudes que requieren atención')
        self.assertContains(response, 'Actividad reciente')
        queues_pos = html.find('Solicitudes que requieren atención')
        projects_pos = html.find('Estado financiero por proyecto')
        activity_pos = html.find('Actividad reciente')
        self.assertLess(queues_pos, projects_pos)
        self.assertLess(projects_pos, activity_pos)

        self.assertContains(response, '<th scope="col">Proyecto</th>', html=True)
        self.assertContains(response, '<th scope="col">Fondos asignados</th>', html=True)
        self.assertContains(response, '<th scope="col">Gastos registrados</th>', html=True)
        self.assertContains(response, '<th scope="col">Reservado</th>', html=True)
        self.assertContains(response, '<th scope="col">Disponible operativo</th>', html=True)
        self.assertContains(response, '<th scope="col">Ejecución</th>', html=True)
        self.assertContains(response, 'PRJ-UI-FIN · UI financiera')
        self.assertContains(response, '100,00 USD')
        self.assertContains(response, '64,00 USD')
        self.assertContains(response, '10,00 USD')
        self.assertContains(response, '26,00 USD')
        self.assertContains(response, '64 %')
        self.assertContains(response, 'role="progressbar"')
        self.assertContains(response, reverse('project_detail', args=[self.project.pk]))
        self.assertContains(response, 'Activo')
        self.assertNotContains(response, 'Top 5')
        self.assertNotContains(response, 'Top 10')
        self.assertNotContains(response, 'ranking')
        self.assertNotContains(response, 'overflow-y')
        self.assertNotContains(response, 'Accesos rápidos')
        self.assertNotContains(response, 'ops-action-panel')

    def test_empty_authorized_state_and_zero_activity_row(self):
        empty = create_project(code='PRJ-UI-ZERO', name='Sin actividad')
        response = self.client.get(reverse('dashboard'))
        rows = response.context['project_financial_rows']
        zero_row = next(row for row in rows if row['project_id'] == empty.pk)

        self.assertTrue(response.context['show_project_financial_section'])
        self.assertEqual(zero_row['assigned'], ZERO_MONEY)
        self.assertEqual(zero_row['spent'], ZERO_MONEY)
        self.assertEqual(zero_row['reserved'], ZERO_MONEY)
        self.assertIsNone(zero_row['execution_percentage'])
        self.assertContains(response, 'PRJ-UI-ZERO · Sin actividad')
        self.assertContains(response, '—')

    def test_view_all_link_only_when_more_than_preview(self):
        for index in range(10):
            create_project(code=f'PRJ-MORE-{index:02d}', name=f'Extra {index}')
        response = self.client.get(reverse('dashboard'))

        self.assertEqual(len(response.context['project_financial_rows']), 10)
        self.assertTrue(response.context['show_all_projects_link'])
        self.assertContains(response, 'Ver todos los proyectos')
        self.assertContains(response, reverse('project_list'))

    def test_project_detail_reservation_aware_labels(self):
        allocation = FundAllocation.objects.get(project=self.project)
        create_approved_reserved_request(
            fund_allocation=allocation,
            requested_amount=Decimal('10.00'),
            code='SGS-UI-DET',
        )
        response = self.client.get(reverse('project_detail', args=[self.project.pk]))

        self.assertContains(response, 'Fondos asignados')
        self.assertContains(response, 'Gastos registrados')
        self.assertContains(response, 'Reservado')
        self.assertContains(response, 'Disponible operativo')
        self.assertContains(response, 'Ejecución:')
        self.assertNotContains(response, '>Disponible<')
        summary = response.context['project_financial_summary']
        self.assertEqual(summary['funded_amount'], Decimal('100.00'))
        self.assertEqual(summary['executed_amount'], Decimal('64.00'))
        self.assertEqual(summary['reserved_amount'], Decimal('10.00'))
        self.assertEqual(summary['available_amount'], Decimal('26.00'))


class DashboardProjectFinancialEmptyStateTests(TestCase):
    def test_authorized_user_with_no_projects_sees_empty_message(self):
        user = create_user_with_permissions(
            'dash-fin3-no-projects',
            'view_fundallocation',
            'view_expense',
        )
        self.client.force_login(user)

        response = self.client.get(reverse('dashboard'))

        self.assertTrue(response.context['show_project_financial_section'])
        self.assertEqual(response.context['project_financial_rows'], [])
        self.assertContains(response, 'Estado financiero por proyecto')
        self.assertContains(response, 'No hay proyectos registrados.')
        self.assertFalse(response.context['show_all_projects_link'])
