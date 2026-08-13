"""Expense Request administrative annulment tests (ER2E)."""

from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase

from apps.operations.expense_request_services import (
    ExpenseRequestPermissionError,
    ExpenseRequestStateError,
    annul_expense_request,
    approve_expense_request,
    create_expense_request,
    deny_expense_request,
    fulfill_expense_request,
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
from apps.operations.services import InvalidStateTransitionError
from apps.operations.tests.helpers import TEST_DATE, create_allocation
from django.core.files.uploadedfile import SimpleUploadedFile
import tempfile
from django.test import override_settings


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class ExpenseRequestAnnulmentTests(TestCase):
    def setUp(self):
        sync_operation_roles()
        self.allocation = create_allocation(amount=Decimal('500.00'))
        self.admin = self._user('er2e-admin', ROLE_SIGEDON_ADMIN)
        self.operator = self._user('er2e-operator', ROLE_FIELD_OPERATOR)
        self.committee = self._user('er2e-committee', ROLE_PROJECT_COMMITTEE)
        self.auditor = self._user('er2e-auditor', ROLE_EXTERNAL_AUDITOR)

    def _user(self, username, role_name):
        user = get_user_model().objects.create_user(username=username, password='pass-12345')
        user.groups.add(Group.objects.get(name=role_name))
        return user

    def _pending(self, amount=Decimal('100.00')):
        return create_expense_request(
            fund_allocation=self.allocation,
            requested_amount=amount,
            purpose='Solicitud pendiente para anulación administrativa',
            requested_date=TEST_DATE,
            actor=self.operator,
        )

    def _approved(self, amount=Decimal('120.00')):
        request = self._pending(amount=amount)
        return approve_expense_request(request, actor=self.committee)

    def _events(self, request, event_type=None):
        qs = ExpenseRequestEvent.objects.filter(expense_request=request)
        if event_type is not None:
            qs = qs.filter(event_type=event_type)
        return qs

    def test_admin_annuls_pending_request(self):
        request = self._pending()
        before = self.allocation.available_balance
        annulled = annul_expense_request(
            request,
            reason='Anulación administrativa con motivo suficiente.',
            actor=self.admin,
        )
        self.allocation.refresh_from_db()
        self.assertEqual(annulled.status, ExpenseRequest.Status.ANNULLED)
        self.assertEqual(self.allocation.available_balance, before)
        self.assertEqual(
            self._events(annulled, ExpenseRequestEvent.EventType.ANNULLED).count(),
            1,
        )
        self.assertEqual(
            self._events(annulled, ExpenseRequestEvent.EventType.RESERVATION_RELEASED).count(),
            0,
        )
        self.assertEqual(
            AuditLog.objects.filter(
                entity_id=str(annulled.pk),
                action=AuditLog.Action.ANNULLED,
            ).count(),
            1,
        )

    def test_admin_annuls_approved_reserved_and_releases_balance(self):
        request = self._approved(Decimal('120.00'))
        before = self.allocation.available_balance
        self.assertEqual(before, Decimal('380.00'))
        decided_by = request.decided_by_id
        reserved_amount = request.reserved_amount
        reserved_at = request.reserved_at
        annulled = annul_expense_request(
            request,
            reason='Anulación de reserva aprobada con justificación.',
            actor=self.admin,
        )
        self.allocation.refresh_from_db()
        self.assertEqual(annulled.status, ExpenseRequest.Status.ANNULLED)
        self.assertEqual(annulled.decided_by_id, decided_by)
        self.assertEqual(annulled.reserved_amount, reserved_amount)
        self.assertEqual(annulled.reserved_at, reserved_at)
        self.assertEqual(self.allocation.reserved_amount, ZERO_MONEY)
        self.assertEqual(self.allocation.available_balance, before + Decimal('120.00'))
        self.assertEqual(
            self._events(annulled, ExpenseRequestEvent.EventType.ANNULLED).count(),
            1,
        )
        release = self._events(
            annulled, ExpenseRequestEvent.EventType.RESERVATION_RELEASED
        ).get()
        self.assertEqual(release.released_amount, Decimal('120.00'))

    def test_operator_committee_auditor_cannot_annul(self):
        request = self._pending()
        for actor in (self.operator, self.committee, self.auditor):
            with self.subTest(actor=actor.username):
                with self.assertRaises(ExpenseRequestPermissionError):
                    annul_expense_request(
                        request,
                        reason='Intento indebido de anulación administrativa.',
                        actor=actor,
                    )

    def test_reason_mandatory(self):
        request = self._pending()
        with self.assertRaises(InvalidStateTransitionError):
            annul_expense_request(request, reason='corto', actor=self.admin)

    def test_denied_withdrawn_fulfilled_annulled_cannot_annul(self):
        denied = self._pending(amount=Decimal('40.00'))
        deny_expense_request(
            denied,
            decision_note='Denegación con motivo suficientemente largo.',
            actor=self.committee,
        )
        with self.assertRaises(ExpenseRequestStateError):
            annul_expense_request(
                denied,
                reason='Intento sobre solicitud denegada inválido.',
                actor=self.admin,
            )

        withdrawn = self._pending(amount=Decimal('35.00'))
        withdraw_expense_request(
            withdrawn,
            reason='Retiro con motivo suficientemente largo.',
            actor=self.operator,
        )
        with self.assertRaises(ExpenseRequestStateError):
            annul_expense_request(
                withdrawn,
                reason='Intento sobre solicitud retirada inválido.',
                actor=self.admin,
            )

        approved = self._approved(Decimal('50.00'))
        fulfilled = fulfill_expense_request(
            approved,
            expense_date=TEST_DATE,
            amount=Decimal('50.00'),
            reason='Cumplimiento previo a anulación inválida',
            provider_or_recipient='Proveedor',
            payment_method='bank_transfer',
            description='',
            support_file=SimpleUploadedFile('f.pdf', b'%PDF soporte'),
            support_title='Factura',
            category='food',
            actor=self.admin,
        )
        with self.assertRaises(ExpenseRequestStateError):
            annul_expense_request(
                fulfilled,
                reason='Intento sobre solicitud cumplida inválido.',
                actor=self.admin,
            )

        pending = self._pending(amount=Decimal('20.00'))
        annul_expense_request(
            pending,
            reason='Primera anulación administrativa válida aquí.',
            actor=self.admin,
        )
        with self.assertRaises(ExpenseRequestStateError):
            annul_expense_request(
                pending,
                reason='Segunda anulación sobre la misma solicitud.',
                actor=self.admin,
            )
        self.assertEqual(
            self._events(pending, ExpenseRequestEvent.EventType.ANNULLED).count(),
            1,
        )

    def test_event_failure_rolls_back_annulment(self):
        request = self._approved(Decimal('80.00'))
        before = self.allocation.available_balance
        with patch(
            'apps.operations.expense_request_services.ExpenseRequestEvent.objects.create',
            side_effect=RuntimeError('event boom'),
        ):
            with self.assertRaises(RuntimeError):
                annul_expense_request(
                    request,
                    reason='Anulación que debe revertirse por fallo.',
                    actor=self.admin,
                )
        request.refresh_from_db()
        self.allocation.refresh_from_db()
        self.assertEqual(request.status, ExpenseRequest.Status.APPROVED_RESERVED)
        self.assertEqual(self.allocation.available_balance, before)
        self.assertEqual(self.allocation.reserved_amount, Decimal('80.00'))

    def test_audit_failure_rolls_back_annulment(self):
        request = self._approved(Decimal('70.00'))
        before = self.allocation.available_balance
        with patch(
            'apps.operations.expense_request_services.log_action',
            side_effect=RuntimeError('audit boom'),
        ):
            with self.assertRaises(RuntimeError):
                annul_expense_request(
                    request,
                    reason='Anulación revertida por fallo de auditoría.',
                    actor=self.admin,
                )
        request.refresh_from_db()
        self.allocation.refresh_from_db()
        self.assertEqual(request.status, ExpenseRequest.Status.APPROVED_RESERVED)
        self.assertEqual(self.allocation.available_balance, before)
        self.assertEqual(
            ExpenseRequestEvent.objects.filter(expense_request=request).filter(
                event_type__in=[
                    ExpenseRequestEvent.EventType.ANNULLED,
                    ExpenseRequestEvent.EventType.RESERVATION_RELEASED,
                ]
            ).count(),
            0,
        )
