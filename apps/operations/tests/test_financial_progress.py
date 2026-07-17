from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.operations.forms import DonationForm, FundAllocationForm
from apps.operations.models import (
    AllocationExecutionProgress,
    Donation,
    DonationAllocationProgress,
    Expense,
    FundAllocation,
)
from apps.operations.services import annul_fund_allocation
from apps.operations.tests.helpers import (
    create_allocation,
    create_donation,
    create_expense,
    create_project,
    create_user,
)


class DerivedFinancialProgressTests(TestCase):
    def setUp(self):
        self.actor = create_user()
        self.project = create_project()
        self.donation = create_donation(amount=Decimal('100.00'))

    def test_donation_progress_uses_non_annulled_allocations_without_changing_cycle(self):
        self.assertEqual(
            self.donation.allocation_progress,
            DonationAllocationProgress.UNALLOCATED,
        )
        first = create_allocation(
            donation=self.donation,
            project=self.project,
            amount=Decimal('40.00'),
        )
        self.donation.refresh_from_db()
        self.assertEqual(self.donation.status, Donation.Status.RECEIVED)
        self.assertEqual(
            self.donation.allocation_progress,
            DonationAllocationProgress.PARTIALLY_ALLOCATED,
        )
        second = create_allocation(
            donation=self.donation,
            project=self.project,
            amount=Decimal('60.00'),
            category='food_security',
        )
        self.assertEqual(
            self.donation.allocation_progress,
            DonationAllocationProgress.FULLY_ALLOCATED,
        )

        annul_fund_allocation(
            second.pk,
            actor=self.actor,
            reason='Asignación duplicada en prueba.',
        )
        self.assertEqual(
            self.donation.allocation_progress,
            DonationAllocationProgress.PARTIALLY_ALLOCATED,
        )
        first.status = FundAllocation.Status.ANNULLED
        first.save(update_fields=('status',))
        self.assertEqual(
            self.donation.allocation_progress,
            DonationAllocationProgress.UNALLOCATED,
        )

    def test_allocation_progress_uses_effective_expenses_without_changing_cycle(self):
        allocation = create_allocation(
            donation=self.donation,
            project=self.project,
            amount=Decimal('100.00'),
        )
        self.assertEqual(
            allocation.execution_progress,
            AllocationExecutionProgress.UNEXECUTED,
        )
        create_expense(allocation=allocation, amount=Decimal('40.00'))
        self.assertEqual(allocation.status, FundAllocation.Status.ACTIVE)
        self.assertEqual(
            allocation.execution_progress,
            AllocationExecutionProgress.PARTIALLY_EXECUTED,
        )
        create_expense(
            allocation=allocation,
            amount=Decimal('60.00'),
            reason='Ejecución completa',
        )
        self.assertEqual(
            allocation.execution_progress,
            AllocationExecutionProgress.FULLY_EXECUTED,
        )

    def test_cancelled_and_legacy_annulled_expenses_do_not_count(self):
        allocation = create_allocation(
            donation=self.donation,
            project=self.project,
            amount=Decimal('100.00'),
        )
        cancelled = create_expense(allocation=allocation, amount=Decimal('40.00'))
        annulled = create_expense(
            allocation=allocation,
            amount=Decimal('30.00'),
            reason='Gasto legado',
        )
        Expense.objects.filter(pk=cancelled.pk).update(status=Expense.Status.ANNULLED)
        Expense.objects.filter(pk=annulled.pk).update(status=Expense.Status.ANNULLED)
        self.assertEqual(allocation.executed_amount, Decimal('0.00'))
        self.assertEqual(
            allocation.execution_progress,
            AllocationExecutionProgress.UNEXECUTED,
        )

    def test_forms_and_templates_separate_cycle_from_progress(self):
        self.assertNotIn('status', DonationForm().fields)
        self.assertNotIn('status', FundAllocationForm().fields)
        self.client.force_login(self.actor)
        donation_response = self.client.get(
            reverse('donation_detail', args=(self.donation.pk,))
        )
        allocation = create_allocation(donation=self.donation, project=self.project)
        allocation_response = self.client.get(
            reverse('allocation_detail', args=(allocation.pk,))
        )
        self.assertContains(donation_response, self.donation.get_status_display())
        self.assertContains(donation_response, 'Sin asignar')
        self.assertContains(allocation_response, allocation.get_status_display())
        self.assertContains(allocation_response, 'Estado de ejecución: Sin ejecución')
