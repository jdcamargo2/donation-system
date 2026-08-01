"""Reservation-aware allocation balance tests for ExpenseRequest (ER2A)."""

from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone

from apps.operations.financials import get_allocation_reserved_amount, quantize_money
from apps.operations.models import Expense, ExpenseRequest, ZERO_MONEY
from apps.operations.selectors import with_allocation_list_metrics
from apps.operations.services import (
    _validate_expense_balance,
    create_expense as create_expense_public,
    create_expense_legacy as create_expense,
    get_allocation_financial_summary,
    update_expense,
)
from apps.operations.tests.helpers import (
    TEST_DATE,
    create_allocation,
    create_approved_reserved_request,
    create_expense as create_expense_fixture,
    create_expense_request,
    create_user,
)
from apps.public_portal.selectors import get_public_transparency_summary


class ExpenseRequestBalanceTests(TestCase):
    def setUp(self):
        self.allocation = create_allocation(amount=Decimal('100.00'))
        self.requester = create_user(username='balance-requester')
        self.decider = create_user(username='balance-decider')

    def _pending(self, amount=Decimal('30.00'), **kwargs):
        return create_expense_request(
            fund_allocation=self.allocation,
            requested_by=self.requester,
            requested_amount=amount,
            **kwargs,
        )

    def _reserved(self, amount=Decimal('30.00'), **kwargs):
        kwargs.setdefault('requested_by', self.requester)
        kwargs.setdefault('decided_by', self.decider)
        return create_approved_reserved_request(
            fund_allocation=self.allocation,
            requested_amount=amount,
            **kwargs,
        )

    def test_pending_request_does_not_reduce_balance(self):
        self._pending(Decimal('40.00'))
        self.assertEqual(self.allocation.reserved_amount, ZERO_MONEY)
        self.assertEqual(self.allocation.available_balance, Decimal('100.00'))
        self.assertEqual(get_allocation_reserved_amount(self.allocation), ZERO_MONEY)

    def test_approved_reserved_request_reduces_balance(self):
        self._reserved(Decimal('40.00'))
        self.assertEqual(self.allocation.reserved_amount, Decimal('40.00'))
        self.assertEqual(self.allocation.available_balance, Decimal('60.00'))

    def test_denied_request_does_not_reduce_balance(self):
        now = timezone.now()
        create_expense_request(
            fund_allocation=self.allocation,
            requested_by=self.requester,
            requested_amount=Decimal('40.00'),
            status=ExpenseRequest.Status.DENIED,
            decided_by=self.decider,
            decided_at=now,
            decision_note='Denegación de prueba con motivo suficiente.',
        )
        self.assertEqual(self.allocation.reserved_amount, ZERO_MONEY)
        self.assertEqual(self.allocation.available_balance, Decimal('100.00'))

    def test_withdrawn_request_does_not_reduce_balance(self):
        now = timezone.now()
        create_expense_request(
            fund_allocation=self.allocation,
            requested_by=self.requester,
            requested_amount=Decimal('40.00'),
            status=ExpenseRequest.Status.WITHDRAWN,
            terminal_by=self.requester,
            terminal_at=now,
            terminal_reason='Retiro de prueba con motivo suficiente.',
        )
        self.assertEqual(self.allocation.available_balance, Decimal('100.00'))

    def test_annulled_request_does_not_reduce_balance(self):
        now = timezone.now()
        create_expense_request(
            fund_allocation=self.allocation,
            requested_by=self.requester,
            requested_amount=Decimal('40.00'),
            status=ExpenseRequest.Status.ANNULLED,
            terminal_by=self.requester,
            terminal_at=now,
            terminal_reason='Anulación de prueba con motivo suficiente.',
        )
        self.assertEqual(self.allocation.available_balance, Decimal('100.00'))

    def test_fulfilled_request_does_not_count_as_reservation(self):
        expense = create_expense_fixture(
            allocation=self.allocation,
            amount=Decimal('25.00'),
        )
        now = timezone.now()
        create_expense_request(
            fund_allocation=self.allocation,
            requested_by=self.requester,
            requested_amount=Decimal('25.00'),
            status=ExpenseRequest.Status.FULFILLED,
            decided_by=self.decider,
            decided_at=now,
            reserved_amount=Decimal('25.00'),
            reserved_at=now,
            expense=expense,
        )
        self.assertEqual(get_allocation_reserved_amount(self.allocation), ZERO_MONEY)
        self.assertEqual(self.allocation.executed_amount, Decimal('25.00'))
        self.assertEqual(self.allocation.available_balance, Decimal('75.00'))

    def test_multiple_active_reservations_sum_correctly(self):
        self._reserved(Decimal('20.00'), code='SGS-BAL-001')
        self._reserved(
            Decimal('15.00'),
            code='SGS-BAL-002',
            requested_by=create_user(username='second-requester'),
        )
        self.assertEqual(self.allocation.reserved_amount, Decimal('35.00'))
        self.assertEqual(self.allocation.available_balance, Decimal('65.00'))

    def test_executed_expense_and_reservation_both_reduce_available(self):
        create_expense_fixture(allocation=self.allocation, amount=Decimal('30.00'))
        self._reserved(Decimal('20.00'))
        self.assertEqual(self.allocation.executed_amount, Decimal('30.00'))
        self.assertEqual(self.allocation.reserved_amount, Decimal('20.00'))
        self.assertEqual(self.allocation.available_balance, Decimal('50.00'))

    def test_annotated_and_instance_property_values_match(self):
        create_expense_fixture(allocation=self.allocation, amount=Decimal('10.00'))
        self._reserved(Decimal('25.00'))
        annotated = with_allocation_list_metrics(
            type(self.allocation).objects.filter(pk=self.allocation.pk)
        ).get()
        self.allocation.refresh_from_db()
        self.assertEqual(annotated.annotated_reserved_amount, self.allocation.reserved_amount)
        self.assertEqual(annotated.annotated_executed_amount, self.allocation.executed_amount)
        self.assertEqual(annotated.annotated_available_balance, self.allocation.available_balance)
        self.assertEqual(annotated.reserved_amount, Decimal('25.00'))
        self.assertEqual(annotated.available_balance, Decimal('65.00'))

    def test_no_join_multiplication_with_multiple_expenses_and_requests(self):
        create_expense_fixture(allocation=self.allocation, amount=Decimal('10.00'), reason='E1')
        create_expense_fixture(allocation=self.allocation, amount=Decimal('15.00'), reason='E2')
        self._reserved(Decimal('20.00'), code='SGS-MULT-001')
        self._reserved(
            Decimal('5.00'),
            code='SGS-MULT-002',
            requested_by=create_user(username='multi-requester'),
        )
        annotated = with_allocation_list_metrics(
            type(self.allocation).objects.filter(pk=self.allocation.pk)
        ).get()
        self.assertEqual(annotated.annotated_executed_amount, Decimal('25.00'))
        self.assertEqual(annotated.annotated_reserved_amount, Decimal('25.00'))
        self.assertEqual(annotated.annotated_available_balance, Decimal('50.00'))

    def test_excluding_one_request_works(self):
        first = self._reserved(Decimal('20.00'), code='SGS-EXCL-001')
        self._reserved(
            Decimal('30.00'),
            code='SGS-EXCL-002',
            requested_by=create_user(username='excl-requester'),
        )
        self.assertEqual(
            get_allocation_reserved_amount(self.allocation, exclude_request_id=first.pk),
            Decimal('30.00'),
        )
        self.assertEqual(get_allocation_reserved_amount(self.allocation), Decimal('50.00'))

    def test_direct_expense_creation_cannot_consume_reserved_money(self):
        self._reserved(Decimal('70.00'))
        with self.assertRaises(Exception) as ctx:
            create_expense(
                allocation=self.allocation,
                expense_date=TEST_DATE,
                category='food',
                amount=Decimal('40.00'),
                reason='Intento sobre reserva',
                provider_or_recipient='Proveedor',
                payment_method='bank_transfer',
                description='',
                observations='',
                actor=self.requester,
                support_title='Factura',
                support_file=SimpleUploadedFile('factura.pdf', b'%PDF-1.4 soporte'),
            )
        self.assertIn('saldo disponible', str(ctx.exception))
        self.assertEqual(
            Expense.objects.exclude(status__in=Expense.non_executing_statuses()).count(),
            0,
        )
        self.assertEqual(self.allocation.available_balance, Decimal('30.00'))

    def test_public_create_expense_rejects_direct_creation(self):
        with self.assertRaisesMessage(
            Exception,
            'El gasto debe registrarse desde una solicitud de gasto aprobada.',
        ):
            create_expense_public(
                allocation=self.allocation,
                expense_date=TEST_DATE,
                category='food',
                amount=Decimal('10.00'),
                reason='Intento directo',
                provider_or_recipient='Proveedor',
                payment_method='bank_transfer',
                description='',
                observations='',
                actor=self.requester,
                support_title='Factura',
                support_file=SimpleUploadedFile('directo.pdf', b'%PDF-1.4 soporte'),
            )
        self.assertEqual(Expense.objects.count(), 0)

    def test_existing_expense_update_respects_reservations(self):
        expense = create_expense(
            allocation=self.allocation,
            expense_date=TEST_DATE,
            category='food',
            amount=Decimal('20.00'),
            reason='Gasto inicial',
            provider_or_recipient='Proveedor',
            payment_method='bank_transfer',
            description='',
            observations='',
            actor=self.requester,
            support_title='Factura',
            support_file=SimpleUploadedFile('factura.pdf', b'%PDF-1.4 soporte'),
        )
        self._reserved(Decimal('70.00'))
        with self.assertRaises(Exception) as ctx:
            update_expense(
                expense=expense,
                allocation=self.allocation,
                expense_date=expense.expense_date,
                category=expense.category,
                amount=Decimal('40.00'),
                reason=expense.reason,
                provider_or_recipient=expense.provider_or_recipient,
                payment_method=expense.payment_method,
                description='',
                observations='',
                actor=self.requester,
            )
        self.assertIn('saldo disponible', str(ctx.exception))
        expense.refresh_from_db()
        self.assertEqual(expense.amount, Decimal('20.00'))

    def test_reservation_helper_returns_exact_decimal_and_zero_fallback(self):
        self.assertEqual(get_allocation_reserved_amount(self.allocation), ZERO_MONEY)
        self.assertEqual(get_allocation_reserved_amount(self.allocation), Decimal('0.00'))
        self._reserved(Decimal('12.50'))
        total = get_allocation_reserved_amount(self.allocation)
        self.assertEqual(total, Decimal('12.50'))
        self.assertEqual(total, quantize_money('12.50'))
        self.assertIsInstance(total, Decimal)

    def test_validate_expense_balance_accepts_unused_reservation_credit(self):
        self._reserved(Decimal('40.00'))
        # reservation_credit is reserved for ER2D fulfillment; unused in production paths.
        _validate_expense_balance(
            self.allocation,
            Decimal('40.00'),
            reservation_credit=Decimal('40.00'),
        )

    def test_financial_summary_includes_reserved_amount(self):
        self._reserved(Decimal('18.00'))
        summary = get_allocation_financial_summary(self.allocation)
        self.assertEqual(summary['reserved_amount'], Decimal('18.00'))
        self.assertEqual(summary['available_amount'], Decimal('82.00'))

    def test_public_portal_does_not_expose_expense_request_rows(self):
        self._reserved(Decimal('40.00'))
        summary = get_public_transparency_summary()
        self.assertNotIn('expense_request', str(summary).lower())
        self.assertNotIn('reserved', summary)
        # Portal available_balance remains assigned - executed (no request exposure).
        self.assertEqual(
            summary['available_balance'],
            summary['total_assigned'] - summary['total_executed'],
        )
