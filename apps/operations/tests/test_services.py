from datetime import date
from decimal import Decimal

from django.test import TestCase

from apps.operations.models import Donation, Expense, FundAllocation, Institution, Project, ZERO_MONEY
from apps.operations.services import (
    get_allocation_financial_summary,
    get_dashboard_metrics,
    get_donation_financial_summary,
    get_project_financial_summary,
    sum_money,
)


TEST_DATE = date(2026, 7, 9)


class OperationServiceTests(TestCase):
    def create_institution(self, name='Donante de prueba'):
        return Institution.objects.create(
            name=name,
            institution_type='foundation',
            role=Institution.Role.DONOR,
            country='VE',
        )

    def create_project(self, code='PRJ-SVC-001', name='Proyecto de prueba'):
        return Project.objects.create(code=code, name=name, estimated_budget=Decimal('1000.00'))

    def create_donation(self, code='DON-SVC-001', amount=Decimal('100.00')):
        return Donation.objects.create(
            code=code,
            donor=self.create_institution(),
            amount=amount,
            currency='USD',
            objective='Atención operativa',
            status=Donation.Status.RECEIVED,
        )

    def create_allocation(self, donation=None, project=None, amount=Decimal('60.00')):
        return FundAllocation.objects.create(
            donation=donation or self.create_donation(),
            project=project or self.create_project(),
            budget_category='health_psychosocial',
            amount=amount,
            allocation_date=TEST_DATE,
            status=FundAllocation.Status.ACTIVE,
        )

    def create_expense(self, allocation=None, amount=Decimal('20.00')):
        return Expense.objects.create(
            allocation=allocation or self.create_allocation(),
            expense_date=TEST_DATE,
            category='food',
            amount=amount,
            currency='USD',
            reason='Compra operativa',
            provider_or_recipient='Proveedor A',
            payment_method='bank_transfer',
            status=Expense.Status.REGISTERED,
        )

    def test_sum_money_returns_zero_for_empty_queryset(self):
        self.assertEqual(sum_money(Donation.objects.none(), 'amount'), ZERO_MONEY)

    def test_sum_money_adds_existing_amounts(self):
        self.create_donation(code='DON-SVC-001', amount=Decimal('25.50'))
        self.create_donation(code='DON-SVC-002', amount=Decimal('74.50'))

        self.assertEqual(sum_money(Donation.objects.all(), 'amount'), Decimal('100.00'))

    def test_get_donation_financial_summary_calculates_balances(self):
        donation = self.create_donation(amount=Decimal('120.00'))
        self.create_allocation(donation=donation, amount=Decimal('45.00'))

        summary = get_donation_financial_summary(donation)

        self.assertEqual(summary['total_amount'], Decimal('120.00'))
        self.assertEqual(summary['assigned_amount'], Decimal('45.00'))
        self.assertEqual(summary['available_amount'], Decimal('75.00'))

    def test_get_allocation_financial_summary_calculates_balances(self):
        allocation = self.create_allocation(amount=Decimal('90.00'))
        self.create_expense(allocation=allocation, amount=Decimal('30.00'))

        summary = get_allocation_financial_summary(allocation)

        self.assertEqual(summary['allocated_amount'], Decimal('90.00'))
        self.assertEqual(summary['executed_amount'], Decimal('30.00'))
        self.assertEqual(summary['available_amount'], Decimal('60.00'))

    def test_get_project_financial_summary_calculates_balances(self):
        project = self.create_project()
        donation = self.create_donation(amount=Decimal('150.00'))
        allocation = self.create_allocation(donation=donation, project=project, amount=Decimal('100.00'))
        self.create_expense(allocation=allocation, amount=Decimal('40.00'))

        summary = get_project_financial_summary(project)

        self.assertEqual(summary['funded_amount'], Decimal('100.00'))
        self.assertEqual(summary['executed_amount'], Decimal('40.00'))
        self.assertEqual(summary['available_amount'], Decimal('60.00'))

    def test_get_dashboard_metrics_returns_expected_keys_with_empty_database(self):
        metrics = get_dashboard_metrics()

        self.assertEqual(metrics['total_donations'], ZERO_MONEY)
        self.assertEqual(metrics['total_assigned'], ZERO_MONEY)
        self.assertEqual(metrics['total_executed'], ZERO_MONEY)
        self.assertEqual(metrics['available_balance'], ZERO_MONEY)
        self.assertIn('recent_donations', metrics)
        self.assertIn('recent_expenses', metrics)
        self.assertIn('recent_audit_logs', metrics)
