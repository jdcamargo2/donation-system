from decimal import Decimal
from unittest import skipUnless

from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from apps.operations.choices import OPERATING_CURRENCY
from apps.operations.models import ExpenseRequest, OperationalCodeSequence
from apps.operations.tests.helpers import (
    TEST_DATE,
    create_allocation,
    create_expense,
    create_expense_request,
    create_user,
)


VALID_TERMINAL_REASON = 'Solicitud retirada por una causa operativa documentada.'
VALID_DECISION_NOTE = 'Denegada por insuficiencia documentada de justificación.'


class ExpenseRequestModelTests(TestCase):
    def setUp(self):
        OperationalCodeSequence.objects.update_or_create(
            namespace='expense_request',
            defaults={'prefix': 'SGS', 'next_value': 1},
        )
        self.user = create_user(username='er-model-actor')
        self.allocation = create_allocation()

    def test_default_status_is_pending_decision(self):
        request = create_expense_request(
            fund_allocation=self.allocation,
            requested_by=self.user,
        )

        self.assertEqual(request.status, ExpenseRequest.Status.PENDING_DECISION)
        self.assertTrue(request.is_pending_decision)
        self.assertFalse(request.is_terminal)
        self.assertFalse(request.has_active_reservation)

    def test_spanish_status_labels(self):
        expected = {
            ExpenseRequest.Status.PENDING_DECISION: 'Pendiente de decisión',
            ExpenseRequest.Status.APPROVED_RESERVED: 'Aprobada · Fondos reservados',
            ExpenseRequest.Status.DENIED: 'Denegada',
            ExpenseRequest.Status.WITHDRAWN: 'Retirada',
            ExpenseRequest.Status.FULFILLED: 'Gasto registrado',
            ExpenseRequest.Status.ANNULLED: 'Anulada',
        }
        for value, label in expected.items():
            with self.subTest(status=value):
                self.assertEqual(dict(ExpenseRequest.Status.choices)[value], label)

    def test_str_uses_code_and_status_label(self):
        request = create_expense_request(
            fund_allocation=self.allocation,
            requested_by=self.user,
        )

        self.assertEqual(str(request), f'{request.code} · Pendiente de decisión')

    def test_currency_property_returns_operating_currency(self):
        request = create_expense_request(
            fund_allocation=self.allocation,
            requested_by=self.user,
        )

        self.assertEqual(request.currency, OPERATING_CURRENCY)
        self.assertNotIn(
            'currency',
            {field.name for field in ExpenseRequest._meta.local_fields},
        )

    def test_positive_requested_amount_is_required_by_clean(self):
        request = ExpenseRequest(
            fund_allocation=self.allocation,
            requested_by=self.user,
            requested_amount=Decimal('0.00'),
            purpose='Monto inválido',
            requested_date=TEST_DATE,
        )

        with self.assertRaises(ValidationError) as raised:
            request.clean()

        self.assertIn('requested_amount', raised.exception.message_dict)

    def test_code_is_immutable_after_persistence(self):
        request = create_expense_request(
            fund_allocation=self.allocation,
            requested_by=self.user,
        )
        original = request.code
        request.code = 'SGS-999999'

        with self.assertRaises(ValidationError) as raised:
            request.save()

        self.assertIn('code', raised.exception.message_dict)
        request.refresh_from_db()
        self.assertEqual(request.code, original)

    def test_fund_allocation_deletion_is_protected(self):
        create_expense_request(
            fund_allocation=self.allocation,
            requested_by=self.user,
        )

        with self.assertRaises(Exception):
            self.allocation.delete()

    def test_one_to_one_expense_uniqueness(self):
        expense = create_expense(allocation=self.allocation, amount=Decimal('10.00'))
        decided_at = timezone.now()
        create_expense_request(
            fund_allocation=self.allocation,
            requested_by=self.user,
            status=ExpenseRequest.Status.FULFILLED,
            decided_by=self.user,
            decided_at=decided_at,
            reserved_amount=Decimal('10.00'),
            reserved_at=decided_at,
            expense=expense,
            decision_note='',
        )
        second = ExpenseRequest(
            fund_allocation=self.allocation,
            requested_by=self.user,
            requested_amount=Decimal('10.00'),
            purpose='Segundo enlace',
            requested_date=TEST_DATE,
            status=ExpenseRequest.Status.FULFILLED,
            decided_by=self.user,
            decided_at=decided_at,
            reserved_amount=Decimal('10.00'),
            reserved_at=decided_at,
            expense=expense,
        )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                second.save()

    def test_clean_rejects_fulfilled_without_expense(self):
        request = ExpenseRequest(
            fund_allocation=self.allocation,
            requested_by=self.user,
            requested_amount=Decimal('10.00'),
            purpose='Sin gasto',
            requested_date=TEST_DATE,
            status=ExpenseRequest.Status.FULFILLED,
            decided_by=self.user,
            decided_at=timezone.now(),
            reserved_amount=Decimal('10.00'),
            reserved_at=timezone.now(),
        )

        with self.assertRaises(ValidationError) as raised:
            request.clean()

        self.assertIn('expense', raised.exception.message_dict)

    def test_clean_rejects_expense_link_outside_fulfilled(self):
        expense = create_expense(allocation=self.allocation, amount=Decimal('5.00'))
        request = ExpenseRequest(
            fund_allocation=self.allocation,
            requested_by=self.user,
            requested_amount=Decimal('5.00'),
            purpose='Gasto prematuro',
            requested_date=TEST_DATE,
            status=ExpenseRequest.Status.PENDING_DECISION,
            expense=expense,
        )

        with self.assertRaises(ValidationError) as raised:
            request.clean()

        self.assertTrue(
            'expense' in raised.exception.message_dict
            or 'status' in raised.exception.message_dict
        )

    def test_clean_requires_denial_note(self):
        request = ExpenseRequest(
            fund_allocation=self.allocation,
            requested_by=self.user,
            requested_amount=Decimal('5.00'),
            purpose='Denegación sin nota',
            requested_date=TEST_DATE,
            status=ExpenseRequest.Status.DENIED,
            decided_by=self.user,
            decided_at=timezone.now(),
            decision_note='   ',
        )

        with self.assertRaises(ValidationError) as raised:
            request.clean()

        self.assertIn('decision_note', raised.exception.message_dict)

    def test_clean_requires_terminal_reason(self):
        request = ExpenseRequest(
            fund_allocation=self.allocation,
            requested_by=self.user,
            requested_amount=Decimal('5.00'),
            purpose='Retiro sin motivo',
            requested_date=TEST_DATE,
            status=ExpenseRequest.Status.WITHDRAWN,
            terminal_by=self.user,
            terminal_at=timezone.now(),
            terminal_reason='',
        )

        with self.assertRaises(ValidationError) as raised:
            request.clean()

        self.assertIn('terminal_reason', raised.exception.message_dict)

    def test_helpers_for_approved_reserved(self):
        decided_at = timezone.now()
        request = create_expense_request(
            fund_allocation=self.allocation,
            requested_by=self.user,
            status=ExpenseRequest.Status.APPROVED_RESERVED,
            decided_by=self.user,
            decided_at=decided_at,
            reserved_amount=Decimal('15.00'),
            reserved_at=decided_at,
        )

        self.assertTrue(request.has_active_reservation)
        self.assertFalse(request.is_terminal)
        self.assertFalse(request.is_pending_decision)


@skipUnless(connection.vendor == 'postgresql', 'Requires PostgreSQL check constraints.')
class ExpenseRequestConstraintTests(TransactionTestCase):
    def setUp(self):
        OperationalCodeSequence.objects.update_or_create(
            namespace='expense_request',
            defaults={'prefix': 'SGS', 'next_value': 1},
        )
        OperationalCodeSequence.objects.update_or_create(
            namespace='fund_allocation',
            defaults={'prefix': 'ASG', 'next_value': 1},
        )
        OperationalCodeSequence.objects.update_or_create(
            namespace='donation',
            defaults={'prefix': 'DON', 'next_value': 1},
        )
        OperationalCodeSequence.objects.update_or_create(
            namespace='project',
            defaults={'prefix': 'PRJ', 'next_value': 1},
        )
        OperationalCodeSequence.objects.update_or_create(
            namespace='expense',
            defaults={'prefix': 'GAS', 'next_value': 1},
        )
        self.user = create_user(username='er-constraint-actor')
        self.allocation = create_allocation()

    def test_requested_amount_must_be_positive(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ExpenseRequest.objects.create(
                    fund_allocation=self.allocation,
                    requested_by=self.user,
                    requested_amount=Decimal('0.00'),
                    purpose='Monto cero',
                    requested_date=TEST_DATE,
                )

    def test_fulfilled_requires_expense_at_database(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ExpenseRequest.objects.create(
                    fund_allocation=self.allocation,
                    requested_by=self.user,
                    requested_amount=Decimal('10.00'),
                    purpose='Cumplida sin gasto',
                    requested_date=TEST_DATE,
                    status=ExpenseRequest.Status.FULFILLED,
                    decided_by=self.user,
                    decided_at=timezone.now(),
                    reserved_amount=Decimal('10.00'),
                    reserved_at=timezone.now(),
                )

    def test_denied_requires_decision_note_at_database(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ExpenseRequest.objects.create(
                    fund_allocation=self.allocation,
                    requested_by=self.user,
                    requested_amount=Decimal('10.00'),
                    purpose='Denegada sin nota',
                    requested_date=TEST_DATE,
                    status=ExpenseRequest.Status.DENIED,
                    decided_by=self.user,
                    decided_at=timezone.now(),
                    decision_note='',
                )
