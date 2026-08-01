"""Expense Request administrative annulment UI (ER4B)."""

from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
import tempfile

from apps.operations.expense_request_services import (
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
from apps.operations.tests.helpers import TEST_DATE, create_allocation
from apps.operations.tests.test_permissions import create_user_with_permissions


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class ExpenseRequestAdminAnnulUITests(TestCase):
    def setUp(self):
        sync_operation_roles()
        self.allocation = create_allocation(amount=Decimal('200.00'))
        self.admin = self._user('er4b-admin', ROLE_SIGEDON_ADMIN)
        self.operator = self._user('er4b-operator', ROLE_FIELD_OPERATOR)
        self.committee = self._user('er4b-committee', ROLE_PROJECT_COMMITTEE)
        self.auditor = self._user('er4b-auditor', ROLE_EXTERNAL_AUDITOR)
        self.request_obj = create_expense_request(
            fund_allocation=self.allocation,
            requested_amount=Decimal('50.00'),
            purpose='Solicitud anulación administrativa de prueba',
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

    def _approve(self, request=None):
        return approve_expense_request(request or self.request_obj, actor=self.committee)

    def _events(self, request, event_type=None):
        qs = ExpenseRequestEvent.objects.filter(expense_request=request)
        if event_type is not None:
            qs = qs.filter(event_type=event_type)
        return qs

    def _audits(self, request):
        return AuditLog.objects.filter(entity_id=str(request.pk))

    def _annul_url(self, pk=None):
        return reverse('expense_request_annul', args=[pk or self.request_obj.pk])

    def _detail_url(self, pk=None):
        return reverse('expense_request_detail', args=[pk or self.request_obj.pk])

    def _valid_reason(self):
        return 'Anulación administrativa con motivo suficiente.'

    # --- Route and permission ---

    def test_admin_get_annul_pending_200(self):
        self.client.force_login(self.admin)
        response = self.client.get(self._annul_url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.request_obj.code)
        self.assertContains(response, self.allocation.project.name)
        self.assertContains(response, self.allocation.get_budget_category_display())
        self.assertContains(response, 'Pendiente de decisión')
        self.assertContains(response, 'USD 50,00')
        self.assertContains(response, 'Anular solicitud de gasto')
        self.assertContains(response, 'Anular solicitud')
        self.assertContains(response, 'no podrá ser evaluada por el Comité')
        self.assertContains(response, 'no afecta el saldo de la asignación')
        self.assertNotContains(response, self.allocation.donation.donor.name)
        self.assertNotContains(response, self.allocation.donation.code)
        self.request_obj.refresh_from_db()
        self.assertEqual(self.request_obj.status, ExpenseRequest.Status.PENDING_DECISION)

    def test_admin_post_annul_pending_succeeds(self):
        before = self.allocation.available_balance
        self.client.force_login(self.admin)
        response = self.client.post(
            self._annul_url(),
            {'reason': self._valid_reason()},
            follow=True,
        )
        self.assertEqual(response.redirect_chain[0][1], 302)
        self.assertEqual(response.redirect_chain[0][0], self._detail_url())
        self.request_obj.refresh_from_db()
        self.allocation.refresh_from_db()
        self.assertEqual(self.request_obj.status, ExpenseRequest.Status.ANNULLED)
        self.assertEqual(self.request_obj.terminal_reason, self._valid_reason())
        self.assertEqual(self.request_obj.terminal_by_id, self.admin.pk)
        self.assertIsNotNone(self.request_obj.terminal_at)
        self.assertIsNone(self.request_obj.reserved_amount)
        self.assertEqual(self.allocation.available_balance, before)
        self.assertEqual(
            self._events(self.request_obj, ExpenseRequestEvent.EventType.ANNULLED).count(),
            1,
        )
        self.assertEqual(
            self._events(
                self.request_obj, ExpenseRequestEvent.EventType.RESERVATION_RELEASED
            ).count(),
            0,
        )
        self.assertEqual(
            self._audits(self.request_obj).filter(action=AuditLog.Action.ANNULLED).count(),
            1,
        )
        self.assertContains(response, 'Solicitud de gasto anulada.')

    def test_admin_get_annul_approved_reserved_200(self):
        approved = self._approve()
        self.client.force_login(self.admin)
        response = self.client.get(self._annul_url(approved.pk))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, approved.code)
        self.assertContains(response, self.allocation.project.name)
        self.assertContains(response, self.allocation.get_budget_category_display())
        self.assertContains(response, 'Aprobada · Fondos reservados')
        self.assertContains(response, 'USD 50,00')
        self.assertContains(response, 'se liberará la reserva de')
        self.assertContains(response, 'volverá a estar disponible en la asignación')
        self.assertTrue(response.context['annul_is_reserved'])
        self.assertEqual(response.context['annul_reserved_amount'], Decimal('50.00'))
        self.assertNotContains(response, self.allocation.donation.donor.name)
        self.assertNotContains(response, self.allocation.donation.code)

    def test_admin_post_annul_approved_reserved_releases_reservation(self):
        approved = self._approve()
        decided_by = approved.decided_by_id
        decided_at = approved.decided_at
        decision_note = approved.decision_note
        reserved_amount = approved.reserved_amount
        reserved_at = approved.reserved_at
        before = self.allocation.available_balance
        self.assertEqual(before, Decimal('150.00'))

        self.client.force_login(self.admin)
        response = self.client.post(
            self._annul_url(approved.pk),
            {'reason': 'Anulación de reserva aprobada con justificación.'},
            follow=True,
        )
        self.assertEqual(response.redirect_chain[0][1], 302)
        self.assertEqual(response.redirect_chain[0][0], self._detail_url(approved.pk))
        approved.refresh_from_db()
        self.allocation.refresh_from_db()
        self.assertEqual(approved.status, ExpenseRequest.Status.ANNULLED)
        self.assertEqual(approved.decided_by_id, decided_by)
        self.assertEqual(approved.decided_at, decided_at)
        self.assertEqual(approved.decision_note, decision_note)
        self.assertEqual(approved.reserved_amount, reserved_amount)
        self.assertEqual(approved.reserved_at, reserved_at)
        self.assertEqual(self.allocation.available_balance, before + Decimal('50.00'))
        self.assertEqual(self.allocation.reserved_amount, ZERO_MONEY)
        self.assertEqual(
            self._events(approved, ExpenseRequestEvent.EventType.ANNULLED).count(),
            1,
        )
        release = self._events(
            approved, ExpenseRequestEvent.EventType.RESERVATION_RELEASED
        ).get()
        self.assertEqual(release.released_amount, Decimal('50.00'))
        self.assertEqual(release.allocation_balance_before, Decimal('150.00'))
        self.assertEqual(release.allocation_balance_after, Decimal('200.00'))
        self.assertEqual(
            self._audits(approved).filter(action=AuditLog.Action.ANNULLED).count(),
            1,
        )
        self.assertContains(response, 'Solicitud anulada. La reserva fue liberada.')

    def test_roles_without_annul_are_denied(self):
        for user in (self.operator, self.committee, self.auditor):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                self.assertEqual(self.client.get(self._annul_url()).status_code, 403)
                self.assertEqual(
                    self.client.post(
                        self._annul_url(),
                        {'reason': self._valid_reason()},
                    ).status_code,
                    403,
                )
        self.request_obj.refresh_from_db()
        self.assertEqual(self.request_obj.status, ExpenseRequest.Status.PENDING_DECISION)

    def test_anonymous_annul_redirects_to_login(self):
        response = self.client.get(self._annul_url())
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response['Location'])

    def test_direct_permission_user_can_annul(self):
        direct = create_user_with_permissions(
            'er4b-direct-annul',
            'view_expenserequest',
            'annul_expenserequest',
        )
        self.client.force_login(direct)
        self.assertEqual(self.client.get(self._annul_url()).status_code, 200)
        response = self.client.post(self._annul_url(), {'reason': self._valid_reason()})
        self.assertEqual(response.status_code, 302)
        self.request_obj.refresh_from_db()
        self.assertEqual(self.request_obj.status, ExpenseRequest.Status.ANNULLED)
        self.assertEqual(self.request_obj.terminal_by_id, direct.pk)

    # --- Invalid / stale states ---

    def test_non_annullable_statuses_get_404(self):
        denied = self._create(amount=Decimal('15.00'), purpose='Para denegar')
        deny_expense_request(
            denied,
            decision_note='Denegación con motivo suficientemente largo.',
            actor=self.committee,
        )
        withdrawn = self._create(amount=Decimal('16.00'), purpose='Para retirar')
        withdraw_expense_request(
            withdrawn,
            reason='Retiro con motivo suficientemente largo.',
            actor=self.operator,
        )
        approved = self._create(amount=Decimal('17.00'), purpose='Para cumplir')
        approved = approve_expense_request(approved, actor=self.committee)
        fulfilled = fulfill_expense_request(
            approved,
            expense_date=TEST_DATE,
            amount=Decimal('17.00'),
            reason='Cumplimiento previo a anulación UI',
            provider_or_recipient='Proveedor',
            payment_method='bank_transfer',
            description='',
            support_file=SimpleUploadedFile('f.pdf', b'%PDF soporte'),
            support_title='Factura',
            category='food',
            actor=self.admin,
        )
        already = self._create(amount=Decimal('18.00'), purpose='Ya anulada')
        self.client.force_login(self.admin)
        self.client.post(
            self._annul_url(already.pk),
            {'reason': self._valid_reason()},
        )
        already.refresh_from_db()

        before_balance = self.allocation.available_balance
        for request in (denied, withdrawn, fulfilled, already):
            with self.subTest(status=request.status, pk=request.pk):
                events_before = self._events(request).count()
                audits_before = self._audits(request).count()
                self.assertEqual(
                    self.client.get(self._annul_url(request.pk)).status_code,
                    404,
                )
                self.assertEqual(
                    self.client.post(
                        self._annul_url(request.pk),
                        {'reason': self._valid_reason()},
                    ).status_code,
                    404,
                )
                self.assertEqual(self._events(request).count(), events_before)
                self.assertEqual(self._audits(request).count(), audits_before)
        self.allocation.refresh_from_db()
        self.assertEqual(self.allocation.available_balance, before_balance)

    def test_duplicate_annul_post_is_non_mutating(self):
        self.client.force_login(self.admin)
        self.client.post(self._annul_url(), {'reason': self._valid_reason()})
        annulled_count = self._events(
            self.request_obj, ExpenseRequestEvent.EventType.ANNULLED
        ).count()
        audit_count = self._audits(self.request_obj).filter(
            action=AuditLog.Action.ANNULLED
        ).count()
        before = self.allocation.available_balance
        response = self.client.post(self._annul_url(), {'reason': self._valid_reason()})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            self._events(self.request_obj, ExpenseRequestEvent.EventType.ANNULLED).count(),
            annulled_count,
        )
        self.assertEqual(
            self._audits(self.request_obj).filter(action=AuditLog.Action.ANNULLED).count(),
            audit_count,
        )
        self.allocation.refresh_from_db()
        self.assertEqual(self.allocation.available_balance, before)

    # --- Reason validation and forged POST ---

    def test_empty_whitespace_and_short_reason_rejected(self):
        before = self.allocation.available_balance
        self.client.force_login(self.admin)
        for reason in ('', '   ', 'corto'):
            with self.subTest(reason=repr(reason)):
                response = self.client.post(self._annul_url(), {'reason': reason})
                self.assertEqual(response.status_code, 200)
                self.request_obj.refresh_from_db()
                self.assertEqual(
                    self.request_obj.status,
                    ExpenseRequest.Status.PENDING_DECISION,
                )
                self.assertEqual(
                    self._events(
                        self.request_obj, ExpenseRequestEvent.EventType.ANNULLED
                    ).count(),
                    0,
                )
        self.allocation.refresh_from_db()
        self.assertEqual(self.allocation.available_balance, before)

    def test_forged_post_fields_ignored(self):
        approved = self._approve()
        before = self.allocation.available_balance
        self.client.force_login(self.admin)
        response = self.client.post(
            self._annul_url(approved.pk),
            {
                'reason': self._valid_reason(),
                'terminal_by': self.operator.pk,
                'status': ExpenseRequest.Status.FULFILLED,
                'reserved_amount': '999.00',
            },
        )
        self.assertEqual(response.status_code, 302)
        approved.refresh_from_db()
        self.allocation.refresh_from_db()
        self.assertEqual(approved.status, ExpenseRequest.Status.ANNULLED)
        self.assertEqual(approved.terminal_by_id, self.admin.pk)
        self.assertEqual(approved.reserved_amount, Decimal('50.00'))
        self.assertEqual(self.allocation.available_balance, before + Decimal('50.00'))

    # --- Atomic rollback via UI ---

    def test_event_failure_rolls_back_reserved_annulment(self):
        approved = self._approve()
        before = self.allocation.available_balance
        self.client.force_login(self.admin)
        with patch(
            'apps.operations.expense_request_services.ExpenseRequestEvent.objects.create',
            side_effect=RuntimeError('annul event boom'),
        ):
            response = self.client.post(
                self._annul_url(approved.pk),
                {'reason': self._valid_reason()},
            )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'No se pudo completar la acción')
        approved.refresh_from_db()
        self.allocation.refresh_from_db()
        self.assertEqual(approved.status, ExpenseRequest.Status.APPROVED_RESERVED)
        self.assertFalse((approved.terminal_reason or '').strip())
        self.assertIsNone(approved.terminal_by_id)
        self.assertIsNone(approved.terminal_at)
        self.assertEqual(self.allocation.available_balance, before)
        self.assertEqual(self.allocation.reserved_amount, Decimal('50.00'))
        self.assertEqual(
            self._events(approved, ExpenseRequestEvent.EventType.ANNULLED).count(),
            0,
        )
        self.assertEqual(
            self._events(
                approved, ExpenseRequestEvent.EventType.RESERVATION_RELEASED
            ).count(),
            0,
        )
        self.assertEqual(
            self._audits(approved).filter(action=AuditLog.Action.ANNULLED).count(),
            0,
        )

    def test_audit_failure_rolls_back_pending_annulment(self):
        before = self.allocation.available_balance
        self.client.force_login(self.admin)
        with patch(
            'apps.operations.expense_request_services.log_action',
            side_effect=RuntimeError('annul audit boom'),
        ):
            response = self.client.post(
                self._annul_url(),
                {'reason': self._valid_reason()},
            )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'No se pudo completar la acción')
        self.request_obj.refresh_from_db()
        self.allocation.refresh_from_db()
        self.assertEqual(self.request_obj.status, ExpenseRequest.Status.PENDING_DECISION)
        self.assertFalse((self.request_obj.terminal_reason or '').strip())
        self.assertEqual(self.allocation.available_balance, before)
        self.assertEqual(
            self._events(self.request_obj, ExpenseRequestEvent.EventType.ANNULLED).count(),
            0,
        )
        self.assertEqual(
            self._audits(self.request_obj).filter(action=AuditLog.Action.ANNULLED).count(),
            0,
        )

    def test_final_save_failure_rolls_back_reserved_annulment(self):
        approved = self._approve()
        before = self.allocation.available_balance
        self.client.force_login(self.admin)
        with patch(
            'apps.operations.models.ExpenseRequest.save',
            side_effect=RuntimeError('annul save boom'),
        ):
            response = self.client.post(
                self._annul_url(approved.pk),
                {'reason': self._valid_reason()},
            )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'No se pudo completar la acción')
        approved.refresh_from_db()
        self.allocation.refresh_from_db()
        self.assertEqual(approved.status, ExpenseRequest.Status.APPROVED_RESERVED)
        self.assertEqual(self.allocation.available_balance, before)
        self.assertEqual(
            self._events(approved, ExpenseRequestEvent.EventType.ANNULLED).count(),
            0,
        )
        self.assertEqual(
            self._events(
                approved, ExpenseRequestEvent.EventType.RESERVATION_RELEASED
            ).count(),
            0,
        )

    # --- Detail action visibility ---

    def test_detail_annul_visibility_matrix_pending(self):
        self.client.force_login(self.admin)
        admin_detail = self.client.get(self._detail_url())
        self.assertTrue(admin_detail.context['can_annul_expense_request'])
        self.assertFalse(admin_detail.context['can_approve_expense_request'])
        self.assertFalse(admin_detail.context['can_deny_expense_request'])
        self.assertContains(admin_detail, 'Anular solicitud')
        self.assertContains(admin_detail, self._annul_url())
        self.assertNotContains(admin_detail, 'Aprobar')
        self.assertNotContains(admin_detail, 'Denegar')

        for user in (self.operator, self.committee, self.auditor):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                response = self.client.get(self._detail_url())
                self.assertFalse(response.context['can_annul_expense_request'])
                html = response.content.decode()
                self.assertNotIn('Anular solicitud', html)
                self.assertNotIn('expense_request_annul', html)

        self.client.force_login(self.operator)
        owner = self.client.get(self._detail_url())
        self.assertTrue(owner.context['can_edit_expense_request'])
        self.assertTrue(owner.context['can_withdraw_expense_request'])
        self.assertContains(owner, 'Editar')
        self.assertContains(owner, 'Retirar')

        self.client.force_login(self.committee)
        committee = self.client.get(self._detail_url())
        self.assertTrue(committee.context['can_approve_expense_request'])
        self.assertTrue(committee.context['can_deny_expense_request'])
        self.assertContains(committee, 'Aprobar')
        self.assertContains(committee, 'Denegar')

    def test_detail_annul_visibility_approved_reserved(self):
        approved = self._approve()
        self.client.force_login(self.admin)
        admin_detail = self.client.get(self._detail_url(approved.pk))
        self.assertTrue(admin_detail.context['can_annul_expense_request'])
        self.assertContains(admin_detail, 'Anular solicitud')
        self.assertContains(admin_detail, self._annul_url(approved.pk))
        self.assertFalse(admin_detail.context['can_approve_expense_request'])
        self.assertFalse(admin_detail.context['can_deny_expense_request'])

        for user in (self.committee, self.auditor, self.operator):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                response = self.client.get(self._detail_url(approved.pk))
                self.assertFalse(response.context['can_annul_expense_request'])
                self.assertNotContains(response, 'Anular solicitud')

    def test_detail_annul_hidden_for_terminal_statuses(self):
        denied = self._create(amount=Decimal('12.00'), purpose='Terminal denegada')
        deny_expense_request(
            denied,
            decision_note='Denegación con motivo suficientemente largo.',
            actor=self.committee,
        )
        self.client.force_login(self.admin)
        response = self.client.get(self._detail_url(denied.pk))
        self.assertFalse(response.context['can_annul_expense_request'])
        self.assertNotContains(response, 'Anular solicitud')
