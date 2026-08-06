"""CLEANUP-ALLOCATION-TRANSITION: generic allocation status route is retired."""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import NoReverseMatch, reverse

from apps.operations.models import AuditLog, Donation, FundAllocation
from apps.operations.services import (
    InvalidStateTransitionError,
    annul_fund_allocation,
    finish_fund_allocation,
)
from apps.operations.tests.helpers import (
    TEST_DATE,
    create_allocation,
    create_donation,
    create_expense_request,
    create_user,
)


class AllocationTransitionCleanupTests(TestCase):
    def setUp(self):
        self.actor = create_user(username='alloc-cleanup-actor')
        self.allocation = create_allocation()

    def test_generic_allocation_status_transition_cannot_be_reversed(self):
        with self.assertRaises(NoReverseMatch):
            reverse(
                'allocation_status_transition',
                args=[self.allocation.pk, FundAllocation.Status.FINISHED],
            )

    def test_old_generic_allocation_status_path_returns_404(self):
        self.client.force_login(self.actor)
        response = self.client.post(
            f'/allocations/{self.allocation.pk}/status/{FundAllocation.Status.FINISHED}/'
        )
        self.assertEqual(response.status_code, 404)
        self.allocation.refresh_from_db()
        self.assertEqual(self.allocation.status, FundAllocation.Status.ACTIVE)

    def test_finish_and_annul_routes_remain_reversible(self):
        self.assertEqual(
            reverse('allocation_finish', kwargs={'pk': self.allocation.pk}),
            f'/panel/allocations/{self.allocation.pk}/finish/',
        )
        self.assertEqual(
            reverse('allocation_annul', kwargs={'pk': self.allocation.pk}),
            f'/panel/allocations/{self.allocation.pk}/annul/',
        )

    def test_eligible_active_allocation_can_finish(self):
        finished = finish_fund_allocation(self.allocation.pk, actor=self.actor)
        self.assertEqual(finished.status, FundAllocation.Status.FINISHED)
        self.assertEqual(
            AuditLog.objects.filter(
                entity_id=str(self.allocation.pk),
                action=AuditLog.Action.CLOSED,
            ).count(),
            1,
        )

    def test_open_expense_request_blocks_finish(self):
        create_expense_request(fund_allocation=self.allocation)
        with self.assertRaises(InvalidStateTransitionError):
            finish_fund_allocation(self.allocation.pk, actor=self.actor)
        self.allocation.refresh_from_db()
        self.assertEqual(self.allocation.status, FundAllocation.Status.ACTIVE)

    def test_valid_allocation_can_annul(self):
        annulled = annul_fund_allocation(
            self.allocation.pk,
            actor=self.actor,
            reason='Motivo válido de anulación para limpieza.',
        )
        self.assertEqual(annulled.status, FundAllocation.Status.ANNULLED)
        self.assertEqual(
            AuditLog.objects.filter(
                entity_id=str(self.allocation.pk),
                action=AuditLog.Action.ANNULLED,
            ).count(),
            1,
        )

    def test_terminal_allocation_cannot_finish_or_annul_again(self):
        finish_fund_allocation(self.allocation.pk, actor=self.actor)
        with self.assertRaises(InvalidStateTransitionError):
            finish_fund_allocation(self.allocation.pk, actor=self.actor)
        with self.assertRaises(InvalidStateTransitionError):
            annul_fund_allocation(
                self.allocation.pk,
                actor=self.actor,
                reason='Segundo intento sobre asignación terminal.',
            )

    def test_unauthorized_user_cannot_invoke_finish_or_annul(self):
        stranger = get_user_model().objects.create_user(
            username='alloc-cleanup-stranger',
            password='pass-12345',
        )
        self.client.force_login(stranger)
        finish_response = self.client.post(
            reverse('allocation_finish', args=[self.allocation.pk])
        )
        annul_response = self.client.post(
            reverse('allocation_annul', args=[self.allocation.pk]),
            data={'reason': 'Motivo sin permiso suficiente.'},
        )
        self.assertEqual(finish_response.status_code, 403)
        self.assertEqual(annul_response.status_code, 403)
        self.allocation.refresh_from_db()
        self.assertEqual(self.allocation.status, FundAllocation.Status.ACTIVE)

    def test_donation_status_transition_remains_functional(self):
        donation = create_donation(
            code='DON-ALLOC-CLEANUP',
            amount=Decimal('50.00'),
            status=Donation.Status.REGISTERED,
        )
        donation.received_date = TEST_DATE
        donation.objective = 'Donación para regresión de transición'
        donation.save(update_fields=('received_date', 'objective'))
        self.client.force_login(self.actor)
        response = self.client.post(
            reverse(
                'donation_status_transition',
                args=[donation.pk, Donation.Status.RECEIVED],
            )
        )
        self.assertEqual(response.status_code, 302)
        donation.refresh_from_db()
        self.assertEqual(donation.status, Donation.Status.RECEIVED)

    def test_detail_ui_uses_dedicated_terminal_actions_only(self):
        self.client.force_login(self.actor)
        response = self.client.get(
            reverse('allocation_detail', args=[self.allocation.pk])
        )
        self.assertContains(response, reverse('allocation_finish', args=[self.allocation.pk]))
        self.assertContains(response, reverse('allocation_annul', args=[self.allocation.pk]))
        self.assertContains(response, 'Finalizar asignación')
        self.assertContains(response, 'Anular asignación')
        self.assertNotContains(response, f'/allocations/{self.allocation.pk}/status/')
        self.assertNotContains(response, 'Cambiar estado')
