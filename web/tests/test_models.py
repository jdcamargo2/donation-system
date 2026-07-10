from decimal import Decimal
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from apps.operations.models import AuditLog, Donation, Expense, FundAllocation, Institution, Project, SupportingDocument
from apps.operations.tests.helpers import TEST_DATE, create_allocation, create_donation, create_expense, create_institution, create_project


@override_settings(MEDIA_ROOT='/tmp/sigedon-test-media')
class ModelInvariantTests(TestCase):
    def setUp(self):
        self.donor = create_institution()
        self.project = create_project()
        self.donation = create_donation(donor=self.donor, amount=Decimal('100.00'))

    def assert_model_rejects_amount(self, instance):
        with self.assertRaises(ValidationError):
            instance.full_clean()

    def test_donation_amount_must_be_positive(self):
        donation = create_donation(code='DON-NEG', donor=self.donor, amount=Decimal('1.00'))
        donation.amount = Decimal('0.00')
        self.assert_model_rejects_amount(donation)
        donation.amount = Decimal('-1.00')
        self.assert_model_rejects_amount(donation)

    def test_allocation_amount_must_be_positive(self):
        allocation = create_allocation(donation=self.donation, project=self.project, amount=Decimal('10.00'))
        allocation.amount = Decimal('0.00')
        self.assert_model_rejects_amount(allocation)
        allocation.amount = Decimal('-1.00')
        self.assert_model_rejects_amount(allocation)

    def test_expense_amount_must_be_positive(self):
        allocation = create_allocation(donation=self.donation, project=self.project, amount=Decimal('50.00'))
        expense = create_expense(allocation=allocation, amount=Decimal('10.00'))
        expense.amount = Decimal('0.00')
        self.assert_model_rejects_amount(expense)
        expense.amount = Decimal('-1.00')
        self.assert_model_rejects_amount(expense)

    def test_donation_balance_calculates_assigned_and_available_amounts(self):
        create_allocation(donation=self.donation, project=self.project, amount=Decimal('30.00'))
        create_allocation(donation=self.donation, project=self.project, amount=Decimal('25.00'), category='health_psychosocial')

        self.assertEqual(self.donation.total_assigned, Decimal('55.00'))
        self.assertEqual(self.donation.available_balance, Decimal('45.00'))

    def test_annulled_allocation_is_excluded_from_donation_balance(self):
        create_allocation(donation=self.donation, project=self.project, amount=Decimal('30.00'))
        create_allocation(
            donation=self.donation,
            project=self.project,
            amount=Decimal('25.00'),
            status=FundAllocation.Status.ANNULLED,
        )

        self.assertEqual(self.donation.total_assigned, Decimal('30.00'))
        self.assertEqual(self.donation.available_balance, Decimal('70.00'))

    def test_allocation_cannot_exceed_donation_available_balance(self):
        create_allocation(donation=self.donation, project=self.project, amount=Decimal('80.00'))
        allocation = FundAllocation(
            donation=self.donation,
            project=self.project,
            budget_category='health_psychosocial',
            amount=Decimal('25.00'),
            allocation_date=TEST_DATE,
        )

        self.assert_model_rejects_amount(allocation)

    def test_allocation_update_excludes_itself_from_balance_calculation(self):
        allocation = create_allocation(donation=self.donation, project=self.project, amount=Decimal('80.00'))
        allocation.amount = Decimal('90.00')
        allocation.full_clean()

        allocation.amount = Decimal('101.00')
        self.assert_model_rejects_amount(allocation)

    def test_allocation_execution_and_available_balance_are_calculated(self):
        allocation = create_allocation(donation=self.donation, project=self.project, amount=Decimal('70.00'))
        create_expense(allocation=allocation, amount=Decimal('20.00'))
        create_expense(allocation=allocation, amount=Decimal('15.00'), reason='Medicine purchase')

        self.assertEqual(allocation.executed_amount, Decimal('35.00'))
        self.assertEqual(allocation.available_balance, Decimal('35.00'))

    def test_annulled_expense_is_excluded_from_allocation_execution(self):
        allocation = create_allocation(donation=self.donation, project=self.project, amount=Decimal('70.00'))
        create_expense(allocation=allocation, amount=Decimal('20.00'))
        create_expense(allocation=allocation, amount=Decimal('15.00'), status=Expense.Status.ANNULLED)

        self.assertEqual(allocation.executed_amount, Decimal('20.00'))
        self.assertEqual(allocation.available_balance, Decimal('50.00'))

    def test_expense_cannot_exceed_allocation_available_balance(self):
        allocation = create_allocation(donation=self.donation, project=self.project, amount=Decimal('60.00'))
        create_expense(allocation=allocation, amount=Decimal('50.00'))
        expense = Expense(
            allocation=allocation,
            expense_date=TEST_DATE,
            category='food',
            amount=Decimal('15.00'),
            reason='Second purchase',
            provider_or_recipient='Provider B',
        )

        self.assert_model_rejects_amount(expense)

    def test_expense_update_excludes_itself_from_balance_calculation(self):
        allocation = create_allocation(donation=self.donation, project=self.project, amount=Decimal('60.00'))
        expense = create_expense(allocation=allocation, amount=Decimal('50.00'))
        expense.amount = Decimal('55.00')
        expense.full_clean()

        expense.amount = Decimal('61.00')
        self.assert_model_rejects_amount(expense)

    def test_available_balances_never_return_negative_values(self):
        create_allocation(donation=self.donation, project=self.project, amount=Decimal('100.00'))
        allocation = create_allocation(
            donation=create_donation(code='DON-002', donor=self.donor, amount=Decimal('50.00')),
            project=self.project,
            amount=Decimal('50.00'),
        )
        create_expense(allocation=allocation, amount=Decimal('50.00'))

        self.assertEqual(self.donation.available_balance, Decimal('0.00'))
        self.assertEqual(allocation.available_balance, Decimal('0.00'))

    def test_validated_expense_support_state_is_reported(self):
        allocation = create_allocation(donation=self.donation, project=self.project, amount=Decimal('50.00'))
        expense = create_expense(allocation=allocation, amount=Decimal('10.00'), status=Expense.Status.VALIDATED)

        self.assertFalse(expense.has_required_support())
        SupportingDocument.objects.create(
            expense=expense,
            title='Receipt',
            document=SimpleUploadedFile('receipt.txt', b'receipt'),
        )
        self.assertTrue(expense.has_required_support())

    def test_core_model_relationships_are_navigable(self):
        allocation = create_allocation(donation=self.donation, project=self.project, amount=Decimal('40.00'))
        expense = create_expense(allocation=allocation, amount=Decimal('10.00'))
        document = SupportingDocument.objects.create(
            expense=expense,
            title='Receipt',
            document=SimpleUploadedFile('receipt.txt', b'receipt'),
        )
        audit = AuditLog.objects.create(
            action=AuditLog.Action.EXECUTED,
            model_name='Expense',
            entity_id=str(expense.pk),
            entity_label=str(expense),
            summary='Expense recorded.',
        )

        self.assertEqual(self.donor.donations.get(), self.donation)
        self.assertEqual(self.donation.allocations.get(pk=allocation.pk), allocation)
        self.assertEqual(self.project.allocations.get(pk=allocation.pk), allocation)
        self.assertEqual(allocation.expenses.get(), expense)
        self.assertEqual(expense.supporting_documents.get(), document)
        self.assertEqual(audit.entity_id, str(expense.pk))

    def test_project_financial_totals_are_calculated(self):
        allocation = create_allocation(donation=self.donation, project=self.project, amount=Decimal('40.00'))
        create_expense(allocation=allocation, amount=Decimal('10.00'))

        self.assertEqual(self.project.funded_amount, Decimal('40.00'))
        self.assertEqual(self.project.executed_amount, Decimal('10.00'))

    def test_project_budget_cannot_be_negative(self):
        project = Project(code='PRJ-NEG', name='Negative budget', estimated_budget=Decimal('-1.00'))

        with self.assertRaises(ValidationError) as context:
            project.full_clean()

        self.assertIn('estimated_budget', context.exception.message_dict)

    def test_project_budget_can_be_zero_when_budget_is_not_confirmed(self):
        project = Project(code='PRJ-ZERO', name='Zero budget', estimated_budget=Decimal('0.00'))

        project.full_clean()

    def test_project_end_date_cannot_be_before_start_date(self):
        project = Project(
            code='PRJ-DATE',
            name='Invalid dates',
            estimated_budget=Decimal('0.00'),
            start_date=TEST_DATE,
            end_date=TEST_DATE - timedelta(days=1),
        )

        with self.assertRaises(ValidationError) as context:
            project.full_clean()

        self.assertIn('end_date', context.exception.message_dict)
