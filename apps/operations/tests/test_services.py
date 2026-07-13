from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission

from apps.operations.models import Donation, Expense, FundAllocation, Institution, Project, ZERO_MONEY
from apps.operations.services import (
    create_expense as create_expense_service,
    create_fund_allocation,
    get_allocation_financial_summary,
    get_dashboard_metrics,
    get_donation_financial_summary,
    get_project_financial_summary,
    sum_money,
    update_expense as update_expense_service,
    update_fund_allocation,
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

    def allocation_service_data(self, donation, project, amount):
        return {
            'donation': donation,
            'project': project,
            'budget_category': 'health_psychosocial',
            'amount': amount,
            'responsible_person': '',
            'allocation_date': TEST_DATE,
            'status': FundAllocation.Status.ACTIVE,
            'notes': '',
        }

    def expense_service_data(self, allocation, amount):
        return {
            'allocation': allocation,
            'expense_date': TEST_DATE,
            'category': 'food',
            'amount': amount,
            'reason': 'Compra mediante servicio',
            'provider_or_recipient': 'Proveedor A',
            'payment_method': 'bank_transfer',
            'description': '',
            'observations': '',
            'support_file': SimpleUploadedFile('servicio.pdf', b'%PDF soporte'),
        }

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
        user = get_user_model().objects.create_user(
            username='dashboard-service-user',
            password='pass-12345',
        )
        user.user_permissions.add(
            *Permission.objects.filter(
                content_type__app_label='operations',
                codename__in={
                    'view_donation',
                    'view_fundallocation',
                    'view_expense',
                    'view_auditlog',
                },
            )
        )

        metrics = get_dashboard_metrics(user=user)

        self.assertEqual(metrics['total_donations'], ZERO_MONEY)
        self.assertEqual(metrics['total_assigned'], ZERO_MONEY)
        self.assertEqual(metrics['total_executed'], ZERO_MONEY)
        self.assertEqual(metrics['available_balance'], ZERO_MONEY)
        self.assertIn('recent_donations', metrics)
        self.assertIn('recent_expenses', metrics)
        self.assertIn('recent_audit_logs', metrics)

    def test_get_dashboard_metrics_hides_data_without_permissions(self):
        user = get_user_model().objects.create_user(
            username='dashboard-restricted-user',
            password='pass-12345',
        )

        metrics = get_dashboard_metrics(user=user)

        self.assertIsNone(metrics['total_donations'])
        self.assertIsNone(metrics['total_assigned'])
        self.assertIsNone(metrics['total_executed'])
        self.assertIsNone(metrics['available_balance'])
        self.assertFalse(metrics['recent_donations'].exists())
        self.assertFalse(metrics['recent_expenses'].exists())
        self.assertFalse(metrics['recent_audit_logs'].exists())

    def test_create_fund_allocation_rejects_over_allocation(self):
        donation = self.create_donation(amount=Decimal('100.00'))
        project = self.create_project()
        self.create_allocation(donation=donation, project=project, amount=Decimal('80.00'))

        with self.assertRaisesMessage(ValidationError, 'excede el saldo disponible'):
            create_fund_allocation(**self.allocation_service_data(donation, project, Decimal('20.01')))

        self.assertEqual(donation.allocations.count(), 1)

    def test_create_fund_allocation_rejects_non_usd_donation(self):
        donation = self.create_donation(amount=Decimal('100.00'))
        donation.currency = 'EUR'
        donation.save(update_fields=['currency'])
        project = self.create_project()

        with self.assertRaisesMessage(ValidationError, 'solo permite operaciones financieras en USD'):
            create_fund_allocation(**self.allocation_service_data(donation, project, Decimal('20.00')))

        self.assertFalse(donation.allocations.exists())

    def test_update_fund_allocation_excludes_its_previous_amount(self):
        donation = self.create_donation(amount=Decimal('100.00'))
        project = self.create_project()
        allocation = self.create_allocation(donation=donation, project=project, amount=Decimal('60.00'))

        updated = update_fund_allocation(
            allocation=allocation,
            **self.allocation_service_data(donation, project, Decimal('100.00')),
        )

        self.assertEqual(updated.amount, Decimal('100.00'))
        self.assertEqual(donation.available_balance, ZERO_MONEY)

    def test_update_fund_allocation_rechecks_balance_when_donation_changes(self):
        original_donation = self.create_donation(code='DON-ORIGINAL', amount=Decimal('100.00'))
        target_donation = self.create_donation(code='DON-TARGET', amount=Decimal('50.00'))
        project = self.create_project()
        allocation = self.create_allocation(donation=original_donation, project=project, amount=Decimal('60.00'))
        self.create_allocation(donation=target_donation, project=project, amount=Decimal('40.00'))

        with self.assertRaisesMessage(ValidationError, 'excede el saldo disponible'):
            update_fund_allocation(
                allocation=allocation,
                **self.allocation_service_data(target_donation, project, Decimal('20.00')),
            )

        allocation.refresh_from_db()
        self.assertEqual(allocation.donation, original_donation)
        self.assertEqual(allocation.amount, Decimal('60.00'))

    def test_update_fund_allocation_cannot_drop_below_executed_amount(self):
        allocation = self.create_allocation(amount=Decimal('60.00'))
        self.create_expense(allocation=allocation, amount=Decimal('40.00'))

        with self.assertRaisesMessage(ValidationError, 'no puede ser menor al monto ya ejecutado'):
            update_fund_allocation(
                allocation=allocation,
                **self.allocation_service_data(allocation.donation, allocation.project, Decimal('39.99')),
            )

        allocation.refresh_from_db()
        self.assertEqual(allocation.amount, Decimal('60.00'))

    def test_create_expense_rejects_over_execution(self):
        allocation = self.create_allocation(amount=Decimal('60.00'))
        self.create_expense(allocation=allocation, amount=Decimal('20.00'))

        with self.assertRaisesMessage(ValidationError, 'excede el saldo disponible'):
            create_expense_service(**self.expense_service_data(allocation, Decimal('40.01')))

        self.assertEqual(allocation.expenses.count(), 1)

    def test_create_expense_service_rejects_non_usd_currency(self):
        allocation = self.create_allocation(amount=Decimal('60.00'))
        service_data = self.expense_service_data(allocation, Decimal('20.00'))
        service_data['currency'] = 'EUR'

        with self.assertRaisesMessage(ValidationError, 'solo permite operaciones financieras en USD'):
            create_expense_service(**service_data)

        self.assertFalse(allocation.expenses.exists())

    def test_update_expense_excludes_its_previous_amount(self):
        allocation = self.create_allocation(amount=Decimal('60.00'))
        expense = self.create_expense(allocation=allocation, amount=Decimal('20.00'))

        updated = update_expense_service(
            expense=expense,
            **self.expense_service_data(allocation, Decimal('60.00')),
        )

        self.assertEqual(updated.amount, Decimal('60.00'))
        self.assertEqual(allocation.available_balance, ZERO_MONEY)

    def test_update_expense_service_rejects_non_usd_currency(self):
        allocation = self.create_allocation(amount=Decimal('60.00'))
        expense = self.create_expense(allocation=allocation, amount=Decimal('20.00'))
        service_data = self.expense_service_data(allocation, Decimal('25.00'))
        service_data['currency'] = 'EUR'

        with self.assertRaisesMessage(ValidationError, 'solo permite operaciones financieras en USD'):
            update_expense_service(expense=expense, **service_data)

        expense.refresh_from_db()
        self.assertEqual(expense.amount, Decimal('20.00'))
        self.assertEqual(expense.currency, 'USD')

    def test_update_expense_rechecks_balance_when_allocation_changes(self):
        original_allocation = self.create_allocation(amount=Decimal('70.00'))
        original_expense = self.create_expense(allocation=original_allocation, amount=Decimal('30.00'))
        target_donation = self.create_donation(code='DON-TARGET-EXP', amount=Decimal('100.00'))
        target_project = self.create_project(code='PRJ-TARGET-EXP')
        target_allocation = self.create_allocation(
            donation=target_donation,
            project=target_project,
            amount=Decimal('50.00'),
        )
        self.create_expense(allocation=target_allocation, amount=Decimal('45.00'))

        with self.assertRaisesMessage(ValidationError, 'excede el saldo disponible'):
            update_expense_service(
                expense=original_expense,
                **self.expense_service_data(target_allocation, Decimal('10.00')),
            )

        original_expense.refresh_from_db()
        self.assertEqual(original_expense.allocation, original_allocation)
        self.assertEqual(original_expense.amount, Decimal('30.00'))
