"""Expense Request committee decision UI (ER4A)."""

from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from apps.operations.expense_request_services import (
    approve_expense_request,
    create_expense_request,
    deny_expense_request,
    withdraw_expense_request,
)
from apps.operations.forms import ExpenseRequestApproveForm
from apps.operations.models import AuditLog, ExpenseRequest, ExpenseRequestEvent
from apps.operations.role_services import sync_operation_roles
from apps.operations.roles import (
    ROLE_EXTERNAL_AUDITOR,
    ROLE_FIELD_OPERATOR,
    ROLE_PROJECT_COMMITTEE,
    ROLE_SIGEDON_ADMIN,
)
from apps.operations.tests.helpers import TEST_DATE, create_allocation
from apps.operations.tests.test_permissions import create_user_with_permissions


class ExpenseRequestCommitteeUITests(TestCase):
    def setUp(self):
        sync_operation_roles()
        self.allocation = create_allocation(amount=Decimal('200.00'))
        self.admin = self._user('er4a-admin', ROLE_SIGEDON_ADMIN)
        self.operator = self._user('er4a-operator', ROLE_FIELD_OPERATOR)
        self.committee = self._user('er4a-committee', ROLE_PROJECT_COMMITTEE)
        self.auditor = self._user('er4a-auditor', ROLE_EXTERNAL_AUDITOR)
        self.request_obj = create_expense_request(
            fund_allocation=self.allocation,
            requested_amount=Decimal('50.00'),
            purpose='Solicitud comité de prueba',
            requested_date=TEST_DATE,
            actor=self.operator,
        )

    def _user(self, username, role_name):
        user = get_user_model().objects.create_user(username=username, password='pass-12345')
        user.groups.add(Group.objects.get(name=role_name))
        return user

    def _create(self, *, amount=Decimal('25.00'), actor=None, purpose='Otra solicitud'):
        return create_expense_request(
            fund_allocation=self.allocation,
            requested_amount=amount,
            purpose=purpose,
            requested_date=TEST_DATE,
            actor=actor or self.operator,
        )

    def _events(self, request, event_type=None):
        qs = ExpenseRequestEvent.objects.filter(expense_request=request)
        if event_type is not None:
            qs = qs.filter(event_type=event_type)
        return qs

    def _audits(self, request):
        return AuditLog.objects.filter(entity_id=str(request.pk))

    def _approve_url(self, pk=None):
        return reverse('expense_request_approve', args=[pk or self.request_obj.pk])

    def _deny_url(self, pk=None):
        return reverse('expense_request_deny', args=[pk or self.request_obj.pk])

    def _detail_url(self, pk=None):
        return reverse('expense_request_detail', args=[pk or self.request_obj.pk])

    # --- Approval route ---

    def test_committee_get_approve_200_for_pending(self):
        self.client.force_login(self.committee)
        response = self.client.get(self._approve_url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.request_obj.code)
        self.assertContains(response, self.allocation.project.name)
        self.assertContains(response, self.allocation.get_budget_category_display())
        self.assertContains(response, 'USD 50,00')
        self.assertContains(response, 'USD 200,00')
        self.assertContains(response, 'se reservarán USD 50,00')
        self.assertContains(response, 'Aprobar y reservar fondos')
        self.assertNotContains(response, self.allocation.donation.donor.name)
        self.assertIsInstance(response.context['form'], ExpenseRequestApproveForm)
        self.assertEqual(response.context['approval_requested_amount'], Decimal('50.00'))
        self.assertEqual(response.context['approval_available_balance'], Decimal('200.00'))
        self.assertEqual(response.context['approval_balance_after'], Decimal('150.00'))
        self.assertFalse(response.context['approval_balance_insufficient'])
        self.request_obj.refresh_from_db()
        self.assertEqual(self.request_obj.status, ExpenseRequest.Status.PENDING_DECISION)

    def test_committee_post_approve_succeeds_with_reservation(self):
        self.client.force_login(self.committee)
        response = self.client.post(
            self._approve_url(),
            {'decision_note': 'Observación opcional del Comité.'},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], self._detail_url())
        self.request_obj.refresh_from_db()
        self.assertEqual(self.request_obj.status, ExpenseRequest.Status.APPROVED_RESERVED)
        self.assertEqual(self.request_obj.reserved_amount, Decimal('50.00'))
        self.assertEqual(self.request_obj.decision_note, 'Observación opcional del Comité.')
        self.assertEqual(self.request_obj.decided_by_id, self.committee.pk)
        self.allocation.refresh_from_db()
        self.assertEqual(self.allocation.available_balance, Decimal('150.00'))
        self.assertEqual(
            self._events(self.request_obj, ExpenseRequestEvent.EventType.APPROVED).count(),
            1,
        )
        self.assertEqual(
            self._events(
                self.request_obj, ExpenseRequestEvent.EventType.RESERVATION_CREATED
            ).count(),
            1,
        )
        self.assertEqual(
            self._audits(self.request_obj).filter(action=AuditLog.Action.VALIDATED).count(),
            1,
        )

    def test_approve_roles_without_decide_are_denied(self):
        for user in (self.admin, self.operator, self.auditor):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                self.assertEqual(self.client.get(self._approve_url()).status_code, 403)
                self.assertEqual(
                    self.client.post(self._approve_url(), {'decision_note': ''}).status_code,
                    403,
                )
        self.request_obj.refresh_from_db()
        self.assertEqual(self.request_obj.status, ExpenseRequest.Status.PENDING_DECISION)
        self.assertIsNone(self.request_obj.reserved_amount)

    def test_anonymous_approve_redirects_to_login(self):
        response = self.client.get(self._approve_url())
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response['Location'])

    def test_non_pending_approve_get_is_404(self):
        approved = approve_expense_request(self.request_obj, actor=self.committee)
        self.client.force_login(self.committee)
        self.assertEqual(self.client.get(self._approve_url(approved.pk)).status_code, 404)

    def test_duplicate_approval_post_is_non_mutating(self):
        approve_expense_request(self.request_obj, actor=self.committee)
        approved_count = self._events(
            self.request_obj, ExpenseRequestEvent.EventType.APPROVED
        ).count()
        reservation_count = self._events(
            self.request_obj, ExpenseRequestEvent.EventType.RESERVATION_CREATED
        ).count()
        audit_count = self._audits(self.request_obj).filter(
            action=AuditLog.Action.VALIDATED
        ).count()
        self.client.force_login(self.committee)
        response = self.client.post(self._approve_url(), {'decision_note': 'Duplicado'})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            self._events(self.request_obj, ExpenseRequestEvent.EventType.APPROVED).count(),
            approved_count,
        )
        self.assertEqual(
            self._events(
                self.request_obj, ExpenseRequestEvent.EventType.RESERVATION_CREATED
            ).count(),
            reservation_count,
        )
        self.assertEqual(
            self._audits(self.request_obj).filter(action=AuditLog.Action.VALIDATED).count(),
            audit_count,
        )

    def test_insufficient_balance_returns_form_error_without_writes(self):
        oversized = self._create(amount=Decimal('250.00'), purpose='Excede saldo')
        before_balance = self.allocation.available_balance
        self.client.force_login(self.committee)
        get_response = self.client.get(self._approve_url(oversized.pk))
        self.assertEqual(get_response.status_code, 200)
        self.assertTrue(get_response.context['approval_balance_insufficient'])
        self.assertContains(
            get_response,
            'El saldo disponible actual no permite aprobar esta solicitud.',
        )
        self.assertNotContains(get_response, 'Aprobar y reservar fondos')

        post_response = self.client.post(
            self._approve_url(oversized.pk),
            {'decision_note': 'Intento con saldo insuficiente'},
        )
        self.assertEqual(post_response.status_code, 200)
        self.assertContains(
            post_response,
            'El monto solicitado excede el saldo disponible de la asignación.',
        )
        oversized.refresh_from_db()
        self.assertEqual(oversized.status, ExpenseRequest.Status.PENDING_DECISION)
        self.assertIsNone(oversized.reserved_amount)
        self.assertEqual(
            self._events(oversized, ExpenseRequestEvent.EventType.APPROVED).count(),
            0,
        )
        self.assertEqual(
            self._audits(oversized).filter(action=AuditLog.Action.VALIDATED).count(),
            0,
        )
        self.allocation.refresh_from_db()
        self.assertEqual(self.allocation.available_balance, before_balance)

        detail = self.client.get(self._detail_url(oversized.pk))
        self.assertTrue(detail.context['can_deny_expense_request'])
        self.assertContains(detail, reverse('expense_request_deny', args=[oversized.pk]))

    def test_stale_state_after_concurrent_decision_on_approve(self):
        self.client.force_login(self.committee)
        with patch(
            'apps.operations.views.expense_requests.approve_expense_request',
            side_effect=ValidationError(
                {'status': 'La solicitud ya fue decidida y no admite esta acción.'}
            ),
        ):
            response = self.client.post(
                self._approve_url(),
                {'decision_note': 'Carrera concurrente'},
            )
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            'La solicitud ya fue decidida y no admite esta acción.',
        )
        self.request_obj.refresh_from_db()
        self.assertEqual(self.request_obj.status, ExpenseRequest.Status.PENDING_DECISION)

    def test_approval_event_failure_rolls_back_via_form_error(self):
        self.client.force_login(self.committee)
        with patch(
            'apps.operations.expense_request_services.ExpenseRequestEvent.objects.create',
            side_effect=RuntimeError('approve event boom'),
        ):
            response = self.client.post(
                self._approve_url(),
                {'decision_note': 'Fallo de evento'},
            )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'No se pudo completar la acción')
        self.request_obj.refresh_from_db()
        self.assertEqual(self.request_obj.status, ExpenseRequest.Status.PENDING_DECISION)
        self.assertIsNone(self.request_obj.reserved_amount)
        self.allocation.refresh_from_db()
        self.assertEqual(self.allocation.available_balance, Decimal('200.00'))

    def test_approval_audit_failure_rolls_back_via_form_error(self):
        self.client.force_login(self.committee)
        with patch(
            'apps.operations.expense_request_services.log_action',
            side_effect=RuntimeError('approve audit boom'),
        ):
            response = self.client.post(
                self._approve_url(),
                {'decision_note': 'Fallo de auditoría'},
            )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'No se pudo completar la acción')
        self.request_obj.refresh_from_db()
        self.assertEqual(self.request_obj.status, ExpenseRequest.Status.PENDING_DECISION)
        self.assertIsNone(self.request_obj.reserved_amount)
        self.assertEqual(
            self._events(self.request_obj, ExpenseRequestEvent.EventType.APPROVED).count(),
            0,
        )

    def test_forged_approve_post_fields_are_ignored(self):
        self.client.force_login(self.committee)
        response = self.client.post(
            self._approve_url(),
            {
                'decision_note': 'Nota legítima',
                'requested_amount': '1.00',
                'fund_allocation': '9999',
                'status': 'denied',
                'actor': str(self.admin.pk),
                'reserved_amount': '1.00',
            },
        )
        self.assertEqual(response.status_code, 302)
        self.request_obj.refresh_from_db()
        self.assertEqual(self.request_obj.status, ExpenseRequest.Status.APPROVED_RESERVED)
        self.assertEqual(self.request_obj.reserved_amount, Decimal('50.00'))
        self.assertEqual(self.request_obj.decided_by_id, self.committee.pk)
        self.assertEqual(self.request_obj.decision_note, 'Nota legítima')
        self.allocation.refresh_from_db()
        self.assertEqual(self.allocation.available_balance, Decimal('150.00'))

    # --- Denial route ---

    def test_committee_get_deny_200(self):
        self.client.force_login(self.committee)
        response = self.client.get(self._deny_url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Denegar solicitud de gasto')
        self.assertContains(
            response,
            'La solicitud quedará cerrada y no podrá registrarse un gasto a partir de ella.',
        )
        self.assertContains(response, 'Denegar solicitud')

    def test_committee_post_deny_requires_reason_and_succeeds(self):
        before_balance = self.allocation.available_balance
        self.client.force_login(self.committee)
        empty = self.client.post(self._deny_url(), {'reason': ''})
        self.assertEqual(empty.status_code, 200)
        self.request_obj.refresh_from_db()
        self.assertEqual(self.request_obj.status, ExpenseRequest.Status.PENDING_DECISION)

        short = self.client.post(self._deny_url(), {'reason': 'corto'})
        self.assertEqual(short.status_code, 200)
        self.request_obj.refresh_from_db()
        self.assertEqual(self.request_obj.status, ExpenseRequest.Status.PENDING_DECISION)

        response = self.client.post(
            self._deny_url(),
            {'reason': 'Motivo suficiente para denegar la solicitud.'},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], self._detail_url())
        self.request_obj.refresh_from_db()
        self.assertEqual(self.request_obj.status, ExpenseRequest.Status.DENIED)
        self.assertEqual(
            self.request_obj.decision_note,
            'Motivo suficiente para denegar la solicitud.',
        )
        self.assertIsNone(self.request_obj.reserved_amount)
        self.allocation.refresh_from_db()
        self.assertEqual(self.allocation.available_balance, before_balance)
        self.assertEqual(
            self._events(self.request_obj, ExpenseRequestEvent.EventType.DENIED).count(),
            1,
        )
        self.assertEqual(
            self._audits(self.request_obj).filter(action=AuditLog.Action.REJECTED).count(),
            1,
        )

    def test_deny_roles_without_decide_are_denied(self):
        for user in (self.admin, self.operator, self.auditor):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                self.assertEqual(self.client.get(self._deny_url()).status_code, 403)
                self.assertEqual(
                    self.client.post(
                        self._deny_url(),
                        {'reason': 'Motivo suficiente para denegar la solicitud.'},
                    ).status_code,
                    403,
                )
        self.request_obj.refresh_from_db()
        self.assertEqual(self.request_obj.status, ExpenseRequest.Status.PENDING_DECISION)

    def test_duplicate_deny_and_non_pending_states_rejected(self):
        deny_expense_request(
            self.request_obj,
            decision_note='Motivo suficiente para denegar la solicitud.',
            actor=self.committee,
        )
        self.client.force_login(self.committee)
        response = self.client.post(
            self._deny_url(),
            {'reason': 'Segundo intento de denegación válido.'},
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            self._events(self.request_obj, ExpenseRequestEvent.EventType.DENIED).count(),
            1,
        )

        approved = self._create(amount=Decimal('10.00'), purpose='Aprobada no denegable')
        approve_expense_request(approved, actor=self.committee)
        self.assertEqual(self.client.get(self._deny_url(approved.pk)).status_code, 404)
        self.assertEqual(
            self.client.post(
                self._deny_url(approved.pk),
                {'reason': 'Intento sobre solicitud aprobada.'},
            ).status_code,
            404,
        )

        withdrawn = self._create(amount=Decimal('10.00'), purpose='Retirada no denegable')
        withdraw_expense_request(
            withdrawn,
            reason='Motivo suficiente para retirar la solicitud.',
            actor=self.operator,
        )
        self.assertEqual(self.client.get(self._deny_url(withdrawn.pk)).status_code, 404)

    def test_deny_failure_rolls_back_via_form_error(self):
        self.client.force_login(self.committee)
        with patch(
            'apps.operations.expense_request_services.ExpenseRequestEvent.objects.create',
            side_effect=RuntimeError('deny event boom'),
        ):
            response = self.client.post(
                self._deny_url(),
                {'reason': 'Motivo suficiente para denegar la solicitud.'},
            )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'No se pudo completar la acción')
        self.request_obj.refresh_from_db()
        self.assertEqual(self.request_obj.status, ExpenseRequest.Status.PENDING_DECISION)
        self.assertEqual(
            self._events(self.request_obj, ExpenseRequestEvent.EventType.DENIED).count(),
            0,
        )

    # --- Visibility / permissions ---

    def test_detail_decision_visibility_matrix(self):
        cases = (
            (self.committee, True),
            (self.admin, False),
            (self.operator, False),
            (self.auditor, False),
        )
        for user, expected in cases:
            with self.subTest(user=user.username, expected=expected):
                self.client.force_login(user)
                response = self.client.get(self._detail_url())
                self.assertEqual(
                    response.context['can_approve_expense_request'],
                    expected,
                )
                self.assertEqual(
                    response.context['can_deny_expense_request'],
                    expected,
                )
                if expected:
                    self.assertContains(response, 'Aprobar')
                    self.assertContains(response, 'Denegar')
                else:
                    html = response.content.decode()
                    self.assertNotIn(
                        reverse('expense_request_approve', args=[self.request_obj.pk]),
                        html,
                    )
                    self.assertNotIn(
                        reverse('expense_request_deny', args=[self.request_obj.pk]),
                        html,
                    )

        approved = approve_expense_request(self.request_obj, actor=self.committee)
        for user in (self.committee, self.admin, self.operator, self.auditor):
            with self.subTest(non_pending=user.username):
                self.client.force_login(user)
                response = self.client.get(self._detail_url(approved.pk))
                self.assertFalse(response.context['can_approve_expense_request'])
                self.assertFalse(response.context['can_deny_expense_request'])

    def test_direct_perm_user_without_decide_cannot_post(self):
        viewer = create_user_with_permissions(
            'er4a-viewer-only',
            'view_expenserequest',
        )
        self.client.force_login(viewer)
        self.assertEqual(self.client.get(self._approve_url()).status_code, 403)
        self.assertEqual(
            self.client.post(self._approve_url(), {'decision_note': ''}).status_code,
            403,
        )
        self.assertEqual(self.client.get(self._deny_url()).status_code, 403)

    def test_approve_form_has_no_amount_or_allocation_fields(self):
        form = ExpenseRequestApproveForm()
        self.assertIn('decision_note', form.fields)
        self.assertNotIn('requested_amount', form.fields)
        self.assertNotIn('fund_allocation', form.fields)
        self.assertNotIn('status', form.fields)
        self.assertNotIn('actor', form.fields)
        self.assertNotIn('reserved_amount', form.fields)
