from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from apps.operations.role_services import sync_operation_roles
from apps.operations.roles import (
    ROLE_EXTERNAL_AUDITOR,
    ROLE_FIELD_OPERATOR,
    ROLE_PROJECT_COMMITTEE,
    ROLE_SIGEDON_ADMIN,
)
from apps.operations.selectors import user_can_view_project_financials
from apps.operations.services import get_project_financial_summary
from apps.operations.tests.helpers import (
    create_allocation,
    create_approved_reserved_request,
    create_donation,
    create_expense,
    create_institution,
    create_project,
)
from apps.operations.tests.test_permissions import create_user_with_permissions


FUNDED_AMOUNT = Decimal('7777.77')
EXECUTED_AMOUNT = Decimal('1234.56')
RESERVED_AMOUNT = Decimal('333.33')
AVAILABLE_AMOUNT = Decimal('6209.88')  # 7777.77 − 1234.56 − 333.33
FUNDED_HTML = '7.777,77'
EXECUTED_HTML = '1.234,56'
RESERVED_HTML = '333,33'
AVAILABLE_HTML = '6.209,88'

FINANCIAL_LABELS = (
    'Fondos asignados',
    'Gastos registrados',
    'Reservado',
    'Disponible operativo',
    'Ejecución',
)


class ProjectDetailFinancialPermissionTests(TestCase):
    def setUp(self):
        sync_operation_roles()
        self.donor = create_institution(name='Donante AUTH-PROJ-FIN')
        self.project = create_project(
            code='PRJ-AUTH-FIN',
            name='Proyecto gate financiero',
        )
        self.project.location = 'Maiquetía'
        self.project.objective = 'Objetivo no financiero visible'
        self.project.description = 'Descripción operativa sin montos'
        self.project.estimated_budget = Decimal('9000.00')
        self.project.save(
            update_fields=('location', 'objective', 'description', 'estimated_budget')
        )
        donation = create_donation(
            code='DON-AUTH-FIN',
            donor=self.donor,
            amount=Decimal('9000.00'),
        )
        allocation = create_allocation(
            donation=donation,
            project=self.project,
            amount=FUNDED_AMOUNT,
        )
        create_expense(allocation=allocation, amount=EXECUTED_AMOUNT)
        create_approved_reserved_request(
            fund_allocation=allocation,
            requested_amount=RESERVED_AMOUNT,
            code='SGS-AUTH-FIN',
        )
        self.detail_url = reverse('project_detail', args=[self.project.pk])

    def _role_user(self, username, role_name):
        user = get_user_model().objects.create_user(
            username=username,
            password='pass-12345',
        )
        user.groups.add(Group.objects.get(name=role_name))
        return user

    def _assert_financial_section_visible(self, response):
        self.assertTrue(response.context['can_view_project_financials'])
        summary = response.context['project_financial_summary']
        self.assertEqual(summary['funded_amount'], FUNDED_AMOUNT)
        self.assertEqual(summary['executed_amount'], EXECUTED_AMOUNT)
        self.assertEqual(summary['reserved_amount'], RESERVED_AMOUNT)
        self.assertEqual(summary['available_amount'], AVAILABLE_AMOUNT)
        for label in FINANCIAL_LABELS:
            self.assertContains(response, label)
        self.assertContains(response, FUNDED_HTML)
        self.assertContains(response, EXECUTED_HTML)
        self.assertContains(response, RESERVED_HTML)
        self.assertContains(response, AVAILABLE_HTML)
        self.assertContains(response, 'Resumen financiero')

    def _assert_financial_section_hidden(self, response):
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['can_view_project_financials'])
        self.assertNotIn('project_financial_summary', response.context)
        self.assertNotIn('execution_percentage', response.context)
        self.assertNotIn('metrics', response.context)
        html = response.content.decode()
        for label in FINANCIAL_LABELS:
            self.assertNotIn(label, html)
        self.assertNotIn('Resumen financiero', html)
        self.assertNotIn(FUNDED_HTML, html)
        self.assertNotIn(EXECUTED_HTML, html)
        self.assertNotIn(RESERVED_HTML, html)
        self.assertNotIn(AVAILABLE_HTML, html)
        self.assertNotIn('aria-valuenow=', html)
        self.assertNotIn('ops-project-financial-summary', html)

    def _assert_non_financial_content(self, response):
        self.assertContains(response, self.project.name)
        self.assertContains(response, self.project.code)
        self.assertContains(response, self.project.location)
        self.assertContains(response, self.project.objective)
        self.assertContains(response, 'Información general')
        self.assertContains(response, 'Avances del proyecto')

    def test_helper_requires_both_financial_permissions(self):
        only_project = create_user_with_permissions(
            'gate-helper-project',
            'view_project',
        )
        allocation_only = create_user_with_permissions(
            'gate-helper-alloc',
            'view_project',
            'view_fundallocation',
        )
        expense_only = create_user_with_permissions(
            'gate-helper-expense',
            'view_project',
            'view_expense',
        )
        both = create_user_with_permissions(
            'gate-helper-both',
            'view_project',
            'view_fundallocation',
            'view_expense',
        )
        self.assertFalse(user_can_view_project_financials(only_project))
        self.assertFalse(user_can_view_project_financials(allocation_only))
        self.assertFalse(user_can_view_project_financials(expense_only))
        self.assertTrue(user_can_view_project_financials(both))

    def test_superuser_sees_financial_section(self):
        user = get_user_model().objects.create_superuser(
            username='gate-fin-super',
            password='pass-12345',
        )
        self.client.force_login(user)
        response = self.client.get(self.detail_url)
        self.assertEqual(response.status_code, 200)
        self._assert_financial_section_visible(response)
        self._assert_non_financial_content(response)

    def test_administrador_sigedon_sees_financial_section(self):
        self.client.force_login(
            self._role_user('gate-fin-admin', ROLE_SIGEDON_ADMIN)
        )
        response = self.client.get(self.detail_url)
        self.assertEqual(response.status_code, 200)
        self._assert_financial_section_visible(response)

    def test_auditor_externo_sees_financial_section(self):
        self.client.force_login(
            self._role_user('gate-fin-auditor', ROLE_EXTERNAL_AUDITOR)
        )
        response = self.client.get(self.detail_url)
        self.assertEqual(response.status_code, 200)
        self._assert_financial_section_visible(response)

    def test_operador_de_campo_hides_financial_section(self):
        self.client.force_login(
            self._role_user('gate-fin-operator', ROLE_FIELD_OPERATOR)
        )
        response = self.client.get(self.detail_url)
        self._assert_financial_section_hidden(response)
        self._assert_non_financial_content(response)

    def test_comite_de_proyectos_hides_financial_section(self):
        self.client.force_login(
            self._role_user('gate-fin-committee', ROLE_PROJECT_COMMITTEE)
        )
        response = self.client.get(self.detail_url)
        self._assert_financial_section_hidden(response)
        self._assert_non_financial_content(response)

    def test_view_project_only_hides_financial_section(self):
        self.client.force_login(
            create_user_with_permissions('gate-fin-project-only', 'view_project')
        )
        response = self.client.get(self.detail_url)
        self._assert_financial_section_hidden(response)
        self._assert_non_financial_content(response)

    def test_view_project_plus_fundallocation_hides_section(self):
        self.client.force_login(
            create_user_with_permissions(
                'gate-fin-alloc',
                'view_project',
                'view_fundallocation',
            )
        )
        response = self.client.get(self.detail_url)
        self._assert_financial_section_hidden(response)

    def test_view_project_plus_expense_hides_section(self):
        self.client.force_login(
            create_user_with_permissions(
                'gate-fin-expense',
                'view_project',
                'view_expense',
            )
        )
        response = self.client.get(self.detail_url)
        self._assert_financial_section_hidden(response)

    def test_view_project_plus_donation_only_hides_section(self):
        self.client.force_login(
            create_user_with_permissions(
                'gate-fin-donation',
                'view_project',
                'view_donation',
            )
        )
        response = self.client.get(self.detail_url)
        self._assert_financial_section_hidden(response)

    def test_both_financial_permissions_show_section(self):
        self.client.force_login(
            create_user_with_permissions(
                'gate-fin-both',
                'view_project',
                'view_fundallocation',
                'view_expense',
            )
        )
        response = self.client.get(self.detail_url)
        self.assertEqual(response.status_code, 200)
        self._assert_financial_section_visible(response)

    def test_financial_perms_without_view_project_denied(self):
        self.client.force_login(
            create_user_with_permissions(
                'gate-fin-no-project',
                'view_fundallocation',
                'view_expense',
            )
        )
        response = self.client.get(self.detail_url)
        self.assertEqual(response.status_code, 403)

    def test_unauthorized_does_not_call_financial_summary(self):
        self.client.force_login(
            create_user_with_permissions(
                'gate-fin-no-query',
                'view_project',
            )
        )
        with patch(
            'apps.operations.views.projects.get_project_financial_summary',
            wraps=get_project_financial_summary,
        ) as mocked_summary:
            response = self.client.get(self.detail_url)
        self.assertEqual(response.status_code, 200)
        mocked_summary.assert_not_called()
        self._assert_financial_section_hidden(response)

    def test_authorized_calls_financial_summary_once(self):
        self.client.force_login(
            create_user_with_permissions(
                'gate-fin-query-ok',
                'view_project',
                'view_fundallocation',
                'view_expense',
            )
        )
        with patch(
            'apps.operations.views.projects.get_project_financial_summary',
            wraps=get_project_financial_summary,
        ) as mocked_summary:
            response = self.client.get(self.detail_url)
        self.assertEqual(response.status_code, 200)
        mocked_summary.assert_called_once()
        self._assert_financial_section_visible(response)

    def test_authorized_zero_value_summary_renders(self):
        empty = create_project(code='PRJ-AUTH-ZERO', name='Sin actividad financiera')
        self.client.force_login(
            create_user_with_permissions(
                'gate-fin-zero',
                'view_project',
                'view_fundallocation',
                'view_expense',
            )
        )
        response = self.client.get(reverse('project_detail', args=[empty.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['can_view_project_financials'])
        summary = response.context['project_financial_summary']
        self.assertEqual(summary['funded_amount'], Decimal('0.00'))
        self.assertEqual(summary['executed_amount'], Decimal('0.00'))
        self.assertEqual(summary['reserved_amount'], Decimal('0.00'))
        self.assertEqual(summary['available_amount'], Decimal('0.00'))
        self.assertIsNone(summary['execution_percentage'])
        self.assertContains(response, 'Fondos asignados')
        self.assertContains(response, '0,00')
        self.assertContains(response, 'Ejecución:')
        self.assertContains(response, '—')

    def test_authorized_values_match_get_project_financial_summary(self):
        expected = get_project_financial_summary(self.project)
        self.client.force_login(
            create_user_with_permissions(
                'gate-fin-match',
                'view_project',
                'view_fundallocation',
                'view_expense',
            )
        )
        response = self.client.get(self.detail_url)
        summary = response.context['project_financial_summary']
        self.assertEqual(summary['funded_amount'], expected['funded_amount'])
        self.assertEqual(summary['executed_amount'], expected['executed_amount'])
        self.assertEqual(summary['reserved_amount'], expected['reserved_amount'])
        self.assertEqual(summary['available_amount'], expected['available_amount'])
        self.assertEqual(
            summary['execution_percentage'],
            expected['execution_percentage'],
        )
