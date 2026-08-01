"""Expense Request fulfillment tests (ER2D)."""

import tempfile
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from apps.operations.expense_request_services import (
    ExpenseRequestAlreadyFulfilledError,
    ExpenseRequestAmountError,
    ExpenseRequestPermissionError,
    ExpenseRequestStateError,
    approve_expense_request,
    create_expense_request,
    deny_expense_request,
    fulfill_expense_request,
    withdraw_expense_request,
)
from apps.operations.models import (
    AuditLog,
    Expense,
    ExpenseRequest,
    ExpenseRequestEvent,
    SupportingDocument,
    ZERO_MONEY,
)
from apps.operations.role_services import sync_operation_roles
from apps.operations.roles import (
    ROLE_EXTERNAL_AUDITOR,
    ROLE_FIELD_OPERATOR,
    ROLE_PROJECT_COMMITTEE,
    ROLE_SIGEDON_ADMIN,
)
from apps.operations.services import annul_expense, create_expense as create_expense_public
from apps.operations.tests.helpers import TEST_DATE, create_allocation


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class ExpenseRequestFulfillmentTests(TestCase):
    def setUp(self):
        sync_operation_roles()
        self.allocation = create_allocation(amount=Decimal('1500.00'))
        self.admin = self._user('er2d-admin', ROLE_SIGEDON_ADMIN)
        self.operator = self._user('er2d-operator', ROLE_FIELD_OPERATOR)
        self.committee = self._user('er2d-committee', ROLE_PROJECT_COMMITTEE)
        self.auditor = self._user('er2d-auditor', ROLE_EXTERNAL_AUDITOR)

    def _user(self, username, role_name):
        user = get_user_model().objects.create_user(username=username, password='pass-12345')
        user.groups.add(Group.objects.get(name=role_name))
        return user

    def _support(self, name='factura.pdf'):
        return SimpleUploadedFile(name, b'%PDF-1.4 soporte cumplimiento')

    def _approved(self, amount=Decimal('1500.00'), actor=None):
        request = create_expense_request(
            fund_allocation=self.allocation,
            requested_amount=amount,
            purpose='Compra operativa para cumplimiento de solicitud',
            requested_date=TEST_DATE,
            actor=actor or self.operator,
        )
        return approve_expense_request(request, actor=self.committee)

    def _fulfill(self, request, amount=None, actor=None, **kwargs):
        reserved = request.reserved_amount
        return fulfill_expense_request(
            request,
            expense_date=kwargs.pop('expense_date', TEST_DATE),
            amount=amount if amount is not None else reserved,
            reason=kwargs.pop('reason', 'Pago final autorizado de la solicitud'),
            provider_or_recipient=kwargs.pop('provider_or_recipient', 'Proveedor final'),
            payment_method=kwargs.pop('payment_method', 'bank_transfer'),
            description=kwargs.pop('description', 'Descripción del gasto final'),
            support_file=kwargs.pop('support_file', self._support()),
            support_title=kwargs.pop('support_title', 'Factura final'),
            category=kwargs.pop('category', 'materials'),
            support_notes=kwargs.pop('support_notes', ''),
            observations=kwargs.pop('observations', ''),
            actor=actor or self.admin,
            **kwargs,
        )

    def _events(self, request, event_type=None):
        qs = ExpenseRequestEvent.objects.filter(expense_request=request)
        if event_type is not None:
            qs = qs.filter(event_type=event_type)
        return qs

    def test_admin_fulfills_exact_amount(self):
        request = self._approved(Decimal('1500.00'))
        before = self.allocation.available_balance
        self.assertEqual(before, ZERO_MONEY)
        fulfilled = self._fulfill(request, amount=Decimal('1500.00'))
        self.allocation.refresh_from_db()
        self.assertEqual(fulfilled.status, ExpenseRequest.Status.FULFILLED)
        self.assertIsNotNone(fulfilled.expense_id)
        self.assertEqual(fulfilled.expense.amount, Decimal('1500.00'))
        self.assertEqual(fulfilled.reserved_amount, Decimal('1500.00'))
        self.assertEqual(self.allocation.reserved_amount, ZERO_MONEY)
        self.assertEqual(self.allocation.executed_amount, Decimal('1500.00'))
        self.assertEqual(self.allocation.available_balance, before)
        self.assertEqual(
            self._events(fulfilled, ExpenseRequestEvent.EventType.EXPENSE_REGISTERED).count(),
            1,
        )
        self.assertEqual(
            self._events(fulfilled, ExpenseRequestEvent.EventType.RESERVATION_CONSUMED).count(),
            1,
        )
        self.assertEqual(
            self._events(
                fulfilled, ExpenseRequestEvent.EventType.UNUSED_RESERVATION_RELEASED
            ).count(),
            0,
        )

    def test_admin_fulfills_lower_amount_releases_difference(self):
        request = self._approved(Decimal('1500.00'))
        before = self.allocation.available_balance
        fulfilled = self._fulfill(request, amount=Decimal('1200.00'))
        self.allocation.refresh_from_db()
        self.assertEqual(fulfilled.expense.amount, Decimal('1200.00'))
        self.assertEqual(self.allocation.executed_amount, Decimal('1200.00'))
        self.assertEqual(self.allocation.reserved_amount, ZERO_MONEY)
        self.assertEqual(self.allocation.available_balance, before + Decimal('300.00'))
        release = self._events(
            fulfilled, ExpenseRequestEvent.EventType.UNUSED_RESERVATION_RELEASED
        ).get()
        self.assertEqual(release.released_amount, Decimal('300.00'))

    def test_operator_committee_auditor_cannot_fulfill(self):
        request = self._approved(Decimal('100.00'))
        for actor in (self.operator, self.committee, self.auditor):
            with self.subTest(actor=actor.username):
                with self.assertRaises(ExpenseRequestPermissionError):
                    self._fulfill(request, amount=Decimal('100.00'), actor=actor)

    def test_rejects_non_approved_states(self):
        pending = create_expense_request(
            fund_allocation=self.allocation,
            requested_amount=Decimal('50.00'),
            purpose='Pendiente sin aprobación todavía vigente',
            requested_date=TEST_DATE,
            actor=self.operator,
        )
        with self.assertRaises(ExpenseRequestStateError):
            self._fulfill(pending, amount=Decimal('50.00'))

        denied = create_expense_request(
            fund_allocation=self.allocation,
            requested_amount=Decimal('40.00'),
            purpose='Solicitud que será denegada por el comité',
            requested_date=TEST_DATE,
            actor=self.operator,
        )
        deny_expense_request(
            denied,
            decision_note='Denegación justificada con motivo suficiente.',
            actor=self.committee,
        )
        with self.assertRaises(ExpenseRequestStateError):
            self._fulfill(denied, amount=Decimal('40.00'))

        withdrawn = create_expense_request(
            fund_allocation=self.allocation,
            requested_amount=Decimal('30.00'),
            purpose='Solicitud que será retirada por el solicitante',
            requested_date=TEST_DATE,
            actor=self.operator,
        )
        withdraw_expense_request(
            withdrawn,
            reason='Retiro voluntario con motivo suficiente.',
            actor=self.operator,
        )
        with self.assertRaises(ExpenseRequestStateError):
            self._fulfill(withdrawn, amount=Decimal('30.00'))

    def test_rejects_already_fulfilled(self):
        request = self._approved(Decimal('80.00'))
        self._fulfill(request, amount=Decimal('80.00'))
        with self.assertRaises(ExpenseRequestAlreadyFulfilledError):
            self._fulfill(request, amount=Decimal('80.00'))

    def test_rejects_zero_negative_and_above_reserved(self):
        request = self._approved(Decimal('100.00'))
        for amount in (Decimal('0.00'), Decimal('-5.00')):
            with self.subTest(amount=amount):
                with self.assertRaises(ExpenseRequestAmountError):
                    self._fulfill(request, amount=amount)
        with self.assertRaises(ExpenseRequestAmountError):
            self._fulfill(request, amount=Decimal('100.01'))
        request.refresh_from_db()
        self.assertEqual(request.status, ExpenseRequest.Status.APPROVED_RESERVED)
        self.assertIsNone(request.expense_id)

    def test_support_file_mandatory(self):
        request = self._approved(Decimal('60.00'))
        with self.assertRaises(ValidationError):
            fulfill_expense_request(
                request,
                expense_date=TEST_DATE,
                amount=Decimal('60.00'),
                reason='Sin soporte documental adjunto',
                provider_or_recipient='Proveedor',
                payment_method='bank_transfer',
                description='',
                support_file=None,
                support_title='',
                category='food',
                actor=self.admin,
            )

    def test_expense_fields_and_link(self):
        request = self._approved(Decimal('75.00'))
        fulfilled = self._fulfill(
            request,
            amount=Decimal('70.00'),
            reason='Motivo copiado al gasto final',
            provider_or_recipient='Destinatario final',
            payment_method='cash',
            description='Detalle del gasto',
            category='logistics',
            support_title='Soporte X',
            support_notes='Nota de soporte',
        )
        expense = fulfilled.expense
        self.assertEqual(expense.allocation_id, request.fund_allocation_id)
        self.assertEqual(expense.amount, Decimal('70.00'))
        self.assertEqual(expense.reason, 'Motivo copiado al gasto final')
        self.assertEqual(expense.provider_or_recipient, 'Destinatario final')
        self.assertEqual(expense.payment_method, 'cash')
        self.assertEqual(expense.category, 'logistics')
        self.assertRegex(expense.code, r'^GAS-\d{6,}$')
        self.assertEqual(expense.source_expense_request.pk, fulfilled.pk)
        self.assertEqual(SupportingDocument.objects.filter(expense=expense).count(), 1)
        doc = expense.supporting_documents.get()
        self.assertEqual(doc.title, 'Soporte X')
        self.assertEqual(doc.notes, 'Nota de soporte')

    def test_audit_trail_summarizes_fulfillment(self):
        request = self._approved(Decimal('1500.00'))
        fulfilled = self._fulfill(request, amount=Decimal('1200.00'))
        from django.utils.text import capfirst

        expense_audits = AuditLog.objects.filter(
            action=AuditLog.Action.EXECUTED,
            model_name=capfirst(Expense._meta.verbose_name),
            entity_id=str(fulfilled.expense_id),
        )
        self.assertEqual(expense_audits.count(), 1)
        request_audits = AuditLog.objects.filter(
            action=AuditLog.Action.EXECUTED,
            model_name=capfirst(ExpenseRequest._meta.verbose_name),
            entity_id=str(fulfilled.pk),
        )
        self.assertEqual(request_audits.count(), 1)
        summary = request_audits.get().summary
        self.assertIn(fulfilled.code, summary)
        self.assertIn(fulfilled.expense.code, summary)
        self.assertIn('1200.00', summary)
        self.assertIn('300.00', summary)

    def test_public_create_expense_rejected_while_fulfillment_works(self):
        request = self._approved(Decimal('50.00'))
        with self.assertRaisesMessage(
            ValidationError,
            'El gasto debe registrarse desde una solicitud de gasto aprobada.',
        ):
            create_expense_public(
                allocation=self.allocation,
                expense_date=TEST_DATE,
                category='food',
                amount=Decimal('50.00'),
                reason='Bypass',
                provider_or_recipient='X',
                payment_method='cash',
                description='',
                observations='',
                support_file=self._support('bypass.pdf'),
            )
        fulfilled = self._fulfill(request, amount=Decimal('50.00'))
        self.assertEqual(fulfilled.status, ExpenseRequest.Status.FULFILLED)

    def test_event_failure_rolls_back_fulfillment(self):
        request = self._approved(Decimal('90.00'))
        before_balance = self.allocation.available_balance
        with patch(
            'apps.operations.expense_request_services.ExpenseRequestEvent.objects.create',
            side_effect=RuntimeError('event boom'),
        ):
            with self.assertRaises(RuntimeError):
                self._fulfill(request, amount=Decimal('90.00'))
        request.refresh_from_db()
        self.allocation.refresh_from_db()
        self.assertEqual(request.status, ExpenseRequest.Status.APPROVED_RESERVED)
        self.assertIsNone(request.expense_id)
        self.assertEqual(Expense.objects.count(), 0)
        self.assertEqual(SupportingDocument.objects.count(), 0)
        self.assertEqual(self.allocation.available_balance, before_balance)
        self.assertEqual(self.allocation.reserved_amount, Decimal('90.00'))

    def test_expense_save_failure_rolls_back(self):
        request = self._approved(Decimal('55.00'))
        with patch(
            'apps.operations.services.Expense.save',
            side_effect=RuntimeError('expense boom'),
        ):
            with self.assertRaises(RuntimeError):
                self._fulfill(request, amount=Decimal('55.00'))
        request.refresh_from_db()
        self.assertEqual(request.status, ExpenseRequest.Status.APPROVED_RESERVED)
        self.assertIsNone(request.expense_id)
        self.assertEqual(Expense.objects.count(), 0)

    def test_request_final_save_failure_rolls_back(self):
        request = self._approved(Decimal('45.00'))
        original_save = ExpenseRequest.save

        def boom(self, *args, **kwargs):
            if self.status == ExpenseRequest.Status.FULFILLED:
                raise RuntimeError('request save boom')
            return original_save(self, *args, **kwargs)

        with patch.object(ExpenseRequest, 'save', boom):
            with self.assertRaises(RuntimeError):
                self._fulfill(request, amount=Decimal('45.00'))
        request.refresh_from_db()
        self.assertEqual(request.status, ExpenseRequest.Status.APPROVED_RESERVED)
        self.assertIsNone(request.expense_id)
        self.assertEqual(Expense.objects.count(), 0)

    def test_audit_failure_rolls_back_fulfillment(self):
        request = self._approved(Decimal('35.00'))
        with patch(
            'apps.operations.expense_request_services.log_action',
            side_effect=RuntimeError('audit boom'),
        ):
            with self.assertRaises(RuntimeError):
                self._fulfill(request, amount=Decimal('35.00'))
        request.refresh_from_db()
        self.assertEqual(request.status, ExpenseRequest.Status.APPROVED_RESERVED)
        self.assertIsNone(request.expense_id)
        self.assertEqual(Expense.objects.count(), 0)

    def test_linked_expense_annulment_keeps_request_fulfilled(self):
        request = self._approved(Decimal('200.00'))
        fulfilled = self._fulfill(request, amount=Decimal('180.00'))
        expense = fulfilled.expense
        before = self.allocation.available_balance
        annul_expense(
            expense.pk,
            actor=self.admin,
            reason='Anulación del gasto enlazado con motivo válido.',
        )
        fulfilled.refresh_from_db()
        expense.refresh_from_db()
        self.allocation.refresh_from_db()
        self.assertEqual(fulfilled.status, ExpenseRequest.Status.FULFILLED)
        self.assertEqual(expense.status, Expense.Status.ANNULLED)
        self.assertEqual(self.allocation.reserved_amount, ZERO_MONEY)
        self.assertEqual(self.allocation.available_balance, before + Decimal('180.00'))
        self.assertEqual(
            self._events(
                fulfilled, ExpenseRequestEvent.EventType.LINKED_EXPENSE_ANNULLED
            ).count(),
            1,
        )
        self.assertEqual(
            AuditLog.objects.filter(
                entity_id=str(expense.pk),
                action=AuditLog.Action.EXPENSE_CANCELLED,
            ).count(),
            1,
        )
        self.assertEqual(
            AuditLog.objects.filter(
                entity_id=str(fulfilled.pk),
                action=AuditLog.Action.EXPENSE_CANCELLED,
            ).count(),
            1,
        )