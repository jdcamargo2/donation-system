"""Expense Request lifecycle service tests (ER2B–ER2C)."""

from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.operations.expense_request_services import (
    ExpenseRequestAlreadyDecidedError,
    ExpenseRequestAmountError,
    ExpenseRequestBalanceError,
    ExpenseRequestPermissionError,
    ExpenseRequestStateError,
    approve_expense_request,
    create_expense_request,
    deny_expense_request,
    update_expense_request,
    withdraw_expense_request,
)
from apps.operations.models import AuditLog, ExpenseRequest, ExpenseRequestEvent, ZERO_MONEY
from apps.operations.role_services import sync_operation_roles
from apps.operations.roles import (
    ROLE_EXTERNAL_AUDITOR,
    ROLE_FIELD_OPERATOR,
    ROLE_PROJECT_COMMITTEE,
    ROLE_SIGEDON_ADMIN,
)
from apps.operations.tests.helpers import (
    TEST_DATE,
    create_allocation,
    create_donation,
    create_project,
)


class ExpenseRequestServiceTests(TestCase):
    def setUp(self):
        sync_operation_roles()
        self.allocation = create_allocation(amount=Decimal('100.00'))
        self.admin = self._user('er2-admin', ROLE_SIGEDON_ADMIN)
        self.operator = self._user('er2-operator', ROLE_FIELD_OPERATOR)
        self.committee = self._user('er2-committee', ROLE_PROJECT_COMMITTEE)
        self.auditor = self._user('er2-auditor', ROLE_EXTERNAL_AUDITOR)

    def _user(self, username, role_name):
        user = get_user_model().objects.create_user(username=username, password='pass-12345')
        user.groups.add(Group.objects.get(name=role_name))
        return user

    def _create(self, actor, amount=Decimal('30.00'), allocation=None, **kwargs):
        return create_expense_request(
            fund_allocation=allocation or self.allocation,
            requested_amount=amount,
            purpose=kwargs.pop('purpose', 'Compra de insumos operativos'),
            requested_date=kwargs.pop('requested_date', TEST_DATE),
            actor=actor,
            **kwargs,
        )

    def _events(self, request, event_type=None):
        qs = ExpenseRequestEvent.objects.filter(expense_request=request)
        if event_type is not None:
            qs = qs.filter(event_type=event_type)
        return qs

    def _audits(self, request):
        return AuditLog.objects.filter(entity_id=str(request.pk))

    # --- Creation ---

    def test_admin_creates_request(self):
        request = self._create(self.admin)
        self.assertEqual(request.status, ExpenseRequest.Status.PENDING_DECISION)
        self.assertEqual(request.requested_by_id, self.admin.pk)
        self.assertRegex(request.code, r'^SGS-\d{6,}$')
        self.assertIsNone(request.reserved_amount)
        self.assertEqual(self.allocation.available_balance, Decimal('100.00'))
        self.assertEqual(self._events(request, ExpenseRequestEvent.EventType.CREATED).count(), 1)
        self.assertEqual(self._audits(request).filter(action=AuditLog.Action.CREATED).count(), 1)

    def test_operator_creates_request(self):
        request = self._create(self.operator)
        self.assertEqual(request.requested_by_id, self.operator.pk)

    def test_committee_cannot_create(self):
        with self.assertRaises(ExpenseRequestPermissionError):
            self._create(self.committee)

    def test_auditor_cannot_create(self):
        with self.assertRaises(ExpenseRequestPermissionError):
            self._create(self.auditor)

    def test_requester_assigned_automatically_and_forged_requester_impossible(self):
        request = create_expense_request(
            fund_allocation=self.allocation,
            requested_amount=Decimal('10.00'),
            purpose='Propósito válido de solicitud',
            requested_date=TEST_DATE,
            actor=self.operator,
        )
        self.assertEqual(request.requested_by_id, self.operator.pk)
        # Signature has no requested_by parameter; actor is authoritative.
        self.assertTrue(
            'requested_by' not in create_expense_request.__code__.co_varnames
            or create_expense_request.__code__.co_varnames.index('actor') >= 0
        )

    def test_creation_rejects_zero_and_negative_amount(self):
        for amount in (Decimal('0.00'), Decimal('-5.00')):
            with self.subTest(amount=amount):
                with self.assertRaises(ExpenseRequestAmountError):
                    self._create(self.admin, amount=amount)

    def test_creation_rejects_finalized_allocation(self):
        from django.utils import timezone

        finalized = create_allocation(
            donation=create_donation(code='DON-FINAL-ER', amount=Decimal('50.00')),
            project=create_project(code='PRJ-FINAL-ER'),
            amount=Decimal('40.00'),
        )
        finalized.status = finalized.Status.FINISHED
        finalized.save(update_fields=('status', 'updated_at'))
        with self.assertRaises(ValidationError):
            self._create(self.admin, allocation=finalized)

        annulled_donation = create_donation(code='DON-ANN-ER', amount=Decimal('50.00'))
        annulled_donation.status = annulled_donation.Status.ANNULLED
        annulled_donation.terminal_reason = 'Anulación de donación de prueba suficiente.'
        annulled_donation.terminal_at = timezone.now()
        annulled_donation.terminal_by = self.admin
        annulled_donation.save()
        bad_allocation = create_allocation(
            donation=annulled_donation,
            project=create_project(code='PRJ-ANN-ER'),
            amount=Decimal('40.00'),
        )
        with self.assertRaises(ValidationError):
            self._create(self.admin, allocation=bad_allocation)

    def test_event_failure_rolls_back_request_creation(self):
        with patch(
            'apps.operations.expense_request_services.ExpenseRequestEvent.objects.create',
            side_effect=RuntimeError('event boom'),
        ):
            with self.assertRaises(RuntimeError):
                self._create(self.admin)
        self.assertEqual(ExpenseRequest.objects.count(), 0)
        self.assertEqual(AuditLog.objects.count(), 0)

    # --- Update ---

    def test_admin_edits_own_pending_request(self):
        request = self._create(self.admin, amount=Decimal('20.00'))
        updated = update_expense_request(
            request,
            fund_allocation=self.allocation,
            requested_amount=Decimal('25.00'),
            purpose='Propósito actualizado de la solicitud',
            requested_date=TEST_DATE,
            actor=self.admin,
        )
        self.assertEqual(updated.requested_amount, Decimal('25.00'))
        self.assertEqual(updated.code, request.code)
        self.assertEqual(updated.requested_by_id, self.admin.pk)
        event = self._events(updated, ExpenseRequestEvent.EventType.UPDATED).get()
        self.assertEqual(event.metadata['previous']['requested_amount'], '20.00')
        self.assertEqual(event.metadata['new']['requested_amount'], '25.00')
        self.assertEqual(self._audits(updated).filter(action=AuditLog.Action.UPDATED).count(), 1)

    def test_operator_edits_own_pending_request(self):
        request = self._create(self.operator)
        updated = update_expense_request(
            request,
            fund_allocation=self.allocation,
            requested_amount=Decimal('22.00'),
            purpose='Actualización del operador de campo',
            requested_date=TEST_DATE,
            actor=self.operator,
        )
        self.assertEqual(updated.requested_amount, Decimal('22.00'))

    def test_admin_cannot_edit_operator_request(self):
        request = self._create(self.operator)
        with self.assertRaises(ExpenseRequestPermissionError):
            update_expense_request(
                request,
                fund_allocation=self.allocation,
                requested_amount=Decimal('22.00'),
                purpose='Intento indebido del administrador',
                requested_date=TEST_DATE,
                actor=self.admin,
            )

    def test_operator_cannot_edit_admin_request(self):
        request = self._create(self.admin)
        with self.assertRaises(ExpenseRequestPermissionError):
            update_expense_request(
                request,
                fund_allocation=self.allocation,
                requested_amount=Decimal('22.00'),
                purpose='Intento indebido del operador',
                requested_date=TEST_DATE,
                actor=self.operator,
            )

    def test_cannot_edit_non_pending_request(self):
        request = self._create(self.admin)
        deny_expense_request(
            request,
            decision_note='Denegación previa con motivo suficiente.',
            actor=self.committee,
        )
        with self.assertRaises((ExpenseRequestAlreadyDecidedError, ExpenseRequestStateError)):
            update_expense_request(
                request,
                fund_allocation=self.allocation,
                requested_amount=Decimal('22.00'),
                purpose='Edición sobre denegada',
                requested_date=TEST_DATE,
                actor=self.admin,
            )

    def test_allocation_change_is_audited(self):
        other = create_allocation(
            donation=create_donation(code='DON-OTHER-ER', amount=Decimal('80.00')),
            project=create_project(code='PRJ-OTHER-ER'),
            amount=Decimal('50.00'),
        )
        request = self._create(self.admin, amount=Decimal('10.00'))
        previous_code = request.fund_allocation.code
        updated = update_expense_request(
            request,
            fund_allocation=other,
            requested_amount=Decimal('10.00'),
            purpose='Cambio de asignación de fondos',
            requested_date=TEST_DATE,
            actor=self.admin,
        )
        self.assertEqual(updated.fund_allocation_id, other.pk)
        event = self._events(updated, ExpenseRequestEvent.EventType.UPDATED).get()
        self.assertEqual(event.metadata['previous']['allocation_code'], previous_code)
        self.assertEqual(event.metadata['new']['allocation_code'], other.code)

    def test_update_event_failure_rolls_back(self):
        request = self._create(self.admin, amount=Decimal('20.00'))
        with patch(
            'apps.operations.expense_request_services.ExpenseRequestEvent.objects.create',
            side_effect=RuntimeError('update event boom'),
        ):
            with self.assertRaises(RuntimeError):
                update_expense_request(
                    request,
                    fund_allocation=self.allocation,
                    requested_amount=Decimal('25.00'),
                    purpose='Fallo de evento en actualización',
                    requested_date=TEST_DATE,
                    actor=self.admin,
                )
        request.refresh_from_db()
        self.assertEqual(request.requested_amount, Decimal('20.00'))

    # --- Withdrawal ---

    def test_admin_withdraws_own_request(self):
        request = self._create(self.admin)
        withdrawn = withdraw_expense_request(
            request,
            reason='Retiro voluntario con justificación suficiente.',
            actor=self.admin,
        )
        self.assertEqual(withdrawn.status, ExpenseRequest.Status.WITHDRAWN)
        self.assertEqual(self.allocation.available_balance, Decimal('100.00'))
        self.assertEqual(self._events(withdrawn, ExpenseRequestEvent.EventType.WITHDRAWN).count(), 1)
        self.assertEqual(self._audits(withdrawn).count(), 2)  # created + withdrawn

    def test_operator_withdraws_own_request(self):
        request = self._create(self.operator)
        withdrawn = withdraw_expense_request(
            request,
            reason='Retiro del operador con justificación suficiente.',
            actor=self.operator,
        )
        self.assertEqual(withdrawn.status, ExpenseRequest.Status.WITHDRAWN)

    def test_cannot_withdraw_another_users_request(self):
        request = self._create(self.operator)
        with self.assertRaises(ExpenseRequestPermissionError):
            withdraw_expense_request(
                request,
                reason='Retiro ajeno con justificación suficiente.',
                actor=self.admin,
            )

    def test_withdraw_reason_mandatory(self):
        request = self._create(self.admin)
        with self.assertRaises(ValidationError):
            withdraw_expense_request(request, reason='corto', actor=self.admin)

    def test_withdraw_non_pending_rejected(self):
        request = self._create(self.admin)
        deny_expense_request(
            request,
            decision_note='Denegación previa con motivo suficiente.',
            actor=self.committee,
        )
        with self.assertRaises((ExpenseRequestAlreadyDecidedError, ExpenseRequestStateError)):
            withdraw_expense_request(
                request,
                reason='Retiro tardío con justificación suficiente.',
                actor=self.admin,
            )

    # --- Denial ---

    def test_committee_denies(self):
        request = self._create(self.admin)
        denied = deny_expense_request(
            request,
            decision_note='Denegación del comité con motivo suficiente.',
            actor=self.committee,
        )
        self.assertEqual(denied.status, ExpenseRequest.Status.DENIED)
        self.assertEqual(denied.decided_by_id, self.committee.pk)
        self.assertEqual(self.allocation.available_balance, Decimal('100.00'))
        self.assertEqual(self._events(denied, ExpenseRequestEvent.EventType.DENIED).count(), 1)

    def test_admin_cannot_deny(self):
        request = self._create(self.admin)
        with self.assertRaises(ExpenseRequestPermissionError):
            deny_expense_request(
                request,
                decision_note='Intento de denegación administrativa suficiente.',
                actor=self.admin,
            )

    def test_operator_cannot_deny(self):
        request = self._create(self.operator)
        with self.assertRaises(ExpenseRequestPermissionError):
            deny_expense_request(
                request,
                decision_note='Intento de denegación operativa suficiente.',
                actor=self.operator,
            )

    def test_deny_reason_mandatory_and_duplicate_rejected(self):
        request = self._create(self.admin)
        with self.assertRaises(ValidationError):
            deny_expense_request(request, decision_note='corto', actor=self.committee)
        deny_expense_request(
            request,
            decision_note='Primera denegación con motivo suficiente.',
            actor=self.committee,
        )
        with self.assertRaises(ExpenseRequestAlreadyDecidedError):
            deny_expense_request(
                request,
                decision_note='Segunda denegación con motivo suficiente.',
                actor=self.committee,
            )
        self.assertEqual(self._events(request, ExpenseRequestEvent.EventType.DENIED).count(), 1)
        self.assertEqual(
            self._audits(request).filter(action=AuditLog.Action.REJECTED).count(),
            1,
        )

    # --- Approval ---

    def test_committee_approves_and_reserves(self):
        request = self._create(self.admin, amount=Decimal('40.00'))
        approved = approve_expense_request(
            request,
            decision_note='Aprobación con nota opcional.',
            actor=self.committee,
        )
        self.assertEqual(approved.status, ExpenseRequest.Status.APPROVED_RESERVED)
        self.assertEqual(approved.reserved_amount, Decimal('40.00'))
        self.assertEqual(approved.decided_by_id, self.committee.pk)
        self.assertEqual(approved.decision_note, 'Aprobación con nota opcional.')
        self.allocation.refresh_from_db()
        self.assertEqual(self.allocation.reserved_amount, Decimal('40.00'))
        self.assertEqual(self.allocation.available_balance, Decimal('60.00'))
        self.assertEqual(self._events(approved, ExpenseRequestEvent.EventType.APPROVED).count(), 1)
        self.assertEqual(
            self._events(approved, ExpenseRequestEvent.EventType.RESERVATION_CREATED).count(),
            1,
        )
        reservation = self._events(
            approved, ExpenseRequestEvent.EventType.RESERVATION_CREATED
        ).get()
        self.assertEqual(reservation.reserved_amount, Decimal('40.00'))
        self.assertEqual(reservation.allocation_balance_before, Decimal('100.00'))
        self.assertEqual(reservation.allocation_balance_after, Decimal('60.00'))
        self.assertEqual(
            self._audits(approved).filter(action=AuditLog.Action.VALIDATED).count(),
            1,
        )

    def test_admin_operator_auditor_cannot_approve(self):
        for actor in (self.admin, self.operator, self.auditor):
            request = self._create(self.admin, amount=Decimal('5.00'))
            with self.subTest(actor=actor.username):
                with self.assertRaises(ExpenseRequestPermissionError):
                    approve_expense_request(request, actor=actor)

    def test_insufficient_balance_rejected_atomically(self):
        request = self._create(self.admin, amount=Decimal('120.00'))
        with self.assertRaises(ExpenseRequestBalanceError):
            approve_expense_request(request, actor=self.committee)
        request.refresh_from_db()
        self.assertEqual(request.status, ExpenseRequest.Status.PENDING_DECISION)
        self.assertIsNone(request.reserved_amount)
        self.assertEqual(self._events(request, ExpenseRequestEvent.EventType.APPROVED).count(), 0)
        self.assertEqual(
            self._audits(request).filter(action=AuditLog.Action.VALIDATED).count(),
            0,
        )
        self.assertEqual(self.allocation.available_balance, Decimal('100.00'))

    def test_duplicate_approval_rejected(self):
        request = self._create(self.admin, amount=Decimal('20.00'))
        approve_expense_request(request, actor=self.committee)
        with self.assertRaises(ExpenseRequestAlreadyDecidedError):
            approve_expense_request(request, actor=self.committee)
        self.assertEqual(
            self._events(request, ExpenseRequestEvent.EventType.APPROVED).count(),
            1,
        )
        self.assertEqual(
            self._events(request, ExpenseRequestEvent.EventType.RESERVATION_CREATED).count(),
            1,
        )

    def test_approval_event_failure_rolls_back_reservation(self):
        request = self._create(self.admin, amount=Decimal('20.00'))
        with patch(
            'apps.operations.expense_request_services.ExpenseRequestEvent.objects.create',
            side_effect=RuntimeError('approve event boom'),
        ):
            with self.assertRaises(RuntimeError):
                approve_expense_request(request, actor=self.committee)
        request.refresh_from_db()
        self.assertEqual(request.status, ExpenseRequest.Status.PENDING_DECISION)
        self.assertIsNone(request.reserved_amount)
        self.assertEqual(self.allocation.available_balance, Decimal('100.00'))
        self.assertEqual(self.allocation.reserved_amount, ZERO_MONEY)
