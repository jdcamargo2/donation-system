from decimal import Decimal

from django.db import IntegrityError, transaction
from django.test import TestCase

from apps.operations.models import Donation, Expense, FundAllocation, Project
from apps.operations.tests.helpers import (
    TEST_DATE,
    create_allocation,
    create_donation,
    create_institution,
    create_project,
)


class MonetaryRowConstraintTests(TestCase):
    def assert_integrity_error(self, create_record):
        """
        PRE: create_record is a zero-argument ORM write expected to violate one row constraint.
        POST: asserts IntegrityError inside a savepoint so the surrounding TestCase remains usable.
        """
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                create_record()

    def create_donation_with_amount(self, amount):
        """
        PRE: amount is a Decimal proposed for a new donation.
        POST: attempts a direct ORM insert and returns the created donation on success.
        """
        return Donation.objects.create(
            code=f'DON-CONSTRAINT-{str(amount).replace("-", "N")}',
            donor=create_institution(name=f'Donante {amount}'),
            amount=amount,
            currency='USD',
            objective='Prueba de constraint',
            status=Donation.Status.RECEIVED,
        )

    def create_allocation_with_amount(self, amount):
        """
        PRE: amount is a Decimal proposed for a new allocation.
        POST: attempts a direct ORM insert and returns the allocation on success.
        """
        return FundAllocation.objects.create(
            donation=create_donation(code=f'DON-ALLOC-{str(amount).replace("-", "N")}'),
            project=create_project(code=f'PRJ-ALLOC-{str(amount).replace("-", "N")}'),
            budget_category='health_psychosocial',
            amount=amount,
            allocation_date=TEST_DATE,
            status=FundAllocation.Status.ACTIVE,
        )

    def create_expense_with_amount(self, amount):
        """
        PRE: amount is a Decimal proposed for a new expense.
        POST: attempts a direct ORM insert and returns the expense on success.
        """
        return Expense.objects.create(
            allocation=create_allocation(),
            expense_date=TEST_DATE,
            category='food',
            amount=amount,
            currency='USD',
            reason='Prueba de constraint',
            provider_or_recipient='Proveedor',
            payment_method='bank_transfer',
            status=Expense.Status.REGISTERED,
        )

    def test_donation_rejects_zero_and_negative_amounts(self):
        for amount in (Decimal('0.00'), Decimal('-1.00')):
            with self.subTest(amount=amount):
                self.assert_integrity_error(
                    lambda amount=amount: self.create_donation_with_amount(amount)
                )

    def test_allocation_rejects_zero_and_negative_amounts(self):
        for amount in (Decimal('0.00'), Decimal('-1.00')):
            with self.subTest(amount=amount):
                self.assert_integrity_error(
                    lambda amount=amount: self.create_allocation_with_amount(amount)
                )

    def test_expense_rejects_zero_and_negative_amounts(self):
        for amount in (Decimal('0.00'), Decimal('-1.00')):
            with self.subTest(amount=amount):
                self.assert_integrity_error(
                    lambda amount=amount: self.create_expense_with_amount(amount)
                )

    def test_project_rejects_negative_budget(self):
        self.assert_integrity_error(
            lambda: Project.objects.create(
                code='PRJ-NEGATIVE-BUDGET',
                name='Presupuesto inválido',
                estimated_budget=Decimal('-0.01'),
            )
        )

    def test_project_accepts_zero_budget(self):
        project = Project.objects.create(
            code='PRJ-ZERO-BUDGET',
            name='Presupuesto cero',
            estimated_budget=Decimal('0.00'),
        )

        self.assertEqual(project.estimated_budget, Decimal('0.00'))

    def test_positive_amounts_remain_valid(self):
        donation = self.create_donation_with_amount(Decimal('10.00'))
        allocation = self.create_allocation_with_amount(Decimal('5.00'))
        expense = self.create_expense_with_amount(Decimal('1.00'))

        self.assertEqual(donation.amount, Decimal('10.00'))
        self.assertEqual(allocation.amount, Decimal('5.00'))
        self.assertEqual(expense.amount, Decimal('1.00'))
