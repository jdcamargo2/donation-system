"""Expense Request requester workflow UI tests (ER3B)."""

from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from apps.operations.expense_request_services import (
    approve_expense_request,
    create_expense_request,
    withdraw_expense_request,
)
from apps.operations.models import AuditLog, ExpenseRequest, ExpenseRequestEvent
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


class ExpenseRequestRequesterUITests(TestCase):
    def setUp(self):
        sync_operation_roles()
        self.project = create_project(code='PRJ-ER3B-UI', name='Proyecto ER3B')
        self.other_project = create_project(code='PRJ-ER3B-OTHER', name='Otro proyecto')
        donation = create_donation(code='DON-ER3B-UI', amount=Decimal('1000.00'))
        self.allocation = create_allocation(
            donation=donation,
            project=self.project,
            amount=Decimal('200.00'),
        )
        self.other_allocation = create_allocation(
            donation=donation,
            project=self.other_project,
            amount=Decimal('150.00'),
            category='training_entrepreneurship',
        )
        self.admin = self._user('er3b-ui-admin', ROLE_SIGEDON_ADMIN)
        self.operator = self._user('er3b-ui-operator', ROLE_FIELD_OPERATOR)
        self.other_operator = self._user('er3b-ui-operator-b', ROLE_FIELD_OPERATOR)
        self.committee = self._user('er3b-ui-committee', ROLE_PROJECT_COMMITTEE)
        self.auditor = self._user('er3b-ui-auditor', ROLE_EXTERNAL_AUDITOR)

    def _user(self, username, role_name):
        user = get_user_model().objects.create_user(username=username, password='pass-12345')
        user.groups.add(Group.objects.get(name=role_name))
        return user

    def _create_payload(self, allocation, *, amount='25,00', purpose='Solicitud ER3B UI'):
        return {
            'fund_allocation': str(allocation.pk),
            'requested_amount': amount,
            'purpose': purpose,
            'requested_date': TEST_DATE.isoformat(),
        }

    def _create_via_service(self, actor, allocation=None, amount=Decimal('25.00')):
        return create_expense_request(
            fund_allocation=allocation or self.allocation,
            requested_amount=amount,
            purpose='Solicitud propia ER3B',
            requested_date=TEST_DATE,
            actor=actor,
        )

    # --- Create ---

    def test_admin_global_create_get_200(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse('expense_request_create'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Nueva solicitud de gasto')
        self.assertContains(response, 'Comité de proyectos')

    def test_admin_global_create_post_success(self):
        self.client.force_login(self.admin)
        balance_before = self.allocation.available_balance
        response = self.client.post(
            reverse('expense_request_create'),
            self._create_payload(self.allocation),
        )
        created = ExpenseRequest.objects.get(purpose='Solicitud ER3B UI')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response['Location'],
            reverse('expense_request_detail', args=[created.pk]),
        )
        self.assertEqual(created.requested_by_id, self.admin.pk)
        self.assertEqual(created.status, ExpenseRequest.Status.PENDING_DECISION)
        self.assertRegex(created.code, r'^SGS-\d+')
        self.assertIsNone(created.reserved_amount)
        self.allocation.refresh_from_db()
        self.assertEqual(self.allocation.available_balance, balance_before)
        self.assertTrue(
            ExpenseRequestEvent.objects.filter(
                expense_request=created,
                event_type=ExpenseRequestEvent.EventType.CREATED,
            ).exists()
        )
        self.assertTrue(
            AuditLog.objects.filter(
                entity_id=str(created.pk),
                action=AuditLog.Action.CREATED,
            ).exists()
        )

    def test_operator_global_create_forbidden(self):
        self.client.force_login(self.operator)
        get_response = self.client.get(reverse('expense_request_create'))
        self.assertEqual(get_response.status_code, 403)
        post_response = self.client.post(
            reverse('expense_request_create'),
            self._create_payload(self.allocation),
        )
        self.assertEqual(post_response.status_code, 403)
        self.assertFalse(
            ExpenseRequest.objects.filter(purpose='Solicitud ER3B UI').exists()
        )

    def test_operator_project_create_get_and_post_success(self):
        self.client.force_login(self.operator)
        url = reverse(
            'expense_request_create_for_project',
            args=[self.project.pk],
        )
        get_response = self.client.get(url)
        self.assertEqual(get_response.status_code, 200)
        self.assertContains(get_response, self.project.code)
        self.assertContains(get_response, 'Registrar solicitud')

        balance_before = self.allocation.available_balance
        post_response = self.client.post(url, self._create_payload(self.allocation))
        created = ExpenseRequest.objects.get(purpose='Solicitud ER3B UI')
        self.assertEqual(post_response.status_code, 302)
        self.assertEqual(created.requested_by_id, self.operator.pk)
        self.assertEqual(created.status, ExpenseRequest.Status.PENDING_DECISION)
        self.allocation.refresh_from_db()
        self.assertEqual(self.allocation.available_balance, balance_before)

    def test_admin_project_create_success(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse('expense_request_create_for_project', args=[self.project.pk]),
            self._create_payload(self.allocation, purpose='Admin desde proyecto'),
        )
        created = ExpenseRequest.objects.get(purpose='Admin desde proyecto')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(created.requested_by_id, self.admin.pk)

    def test_committee_and_auditor_create_forbidden(self):
        for user in (self.committee, self.auditor):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                self.assertEqual(
                    self.client.get(reverse('expense_request_create')).status_code,
                    403,
                )
                self.assertEqual(
                    self.client.get(
                        reverse(
                            'expense_request_create_for_project',
                            args=[self.project.pk],
                        )
                    ).status_code,
                    403,
                )

    def test_anonymous_create_redirects_to_login(self):
        response = self.client.get(reverse('expense_request_create'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response['Location'])

    def test_create_amount_may_exceed_balance(self):
        self.client.force_login(self.operator)
        response = self.client.post(
            reverse('expense_request_create_for_project', args=[self.project.pk]),
            self._create_payload(self.allocation, amount='500,00', purpose='Exceso saldo'),
        )
        created = ExpenseRequest.objects.get(purpose='Exceso saldo')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(created.requested_amount, Decimal('500.00'))

    def test_forged_other_project_allocation_rejected_on_project_create(self):
        self.client.force_login(self.operator)
        before = ExpenseRequest.objects.count()
        response = self.client.post(
            reverse('expense_request_create_for_project', args=[self.project.pk]),
            self._create_payload(self.other_allocation, purpose='Forged alloc'),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(ExpenseRequest.objects.count(), before)
        self.assertFormError(
            response.context['form'],
            'fund_allocation',
            'La asignación seleccionada no está disponible para esta solicitud.',
        )

    def test_forged_requester_status_code_ignored_on_create(self):
        self.client.force_login(self.operator)
        payload = self._create_payload(self.allocation, purpose='Forged immutable')
        payload.update(
            {
                'requested_by': str(self.admin.pk),
                'status': ExpenseRequest.Status.APPROVED_RESERVED,
                'code': 'SGS-HACKED',
            }
        )
        response = self.client.post(
            reverse('expense_request_create_for_project', args=[self.project.pk]),
            payload,
        )
        created = ExpenseRequest.objects.get(purpose='Forged immutable')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(created.requested_by_id, self.operator.pk)
        self.assertEqual(created.status, ExpenseRequest.Status.PENDING_DECISION)
        self.assertNotEqual(created.code, 'SGS-HACKED')
        self.assertRegex(created.code, r'^SGS-\d+')

    @patch(
        'apps.operations.views.expense_requests.create_expense_request',
        side_effect=Exception('boom'),
    )
    def test_service_failure_leaves_no_partial_row(self, _mock):
        self.client.force_login(self.operator)
        before = ExpenseRequest.objects.count()
        with self.assertRaises(Exception):
            self.client.post(
                reverse('expense_request_create_for_project', args=[self.project.pk]),
                self._create_payload(self.allocation, purpose='Fallo servicio'),
            )
        self.assertEqual(ExpenseRequest.objects.count(), before)

    def test_service_validation_error_returns_form_errors(self):
        from django.core.exceptions import ValidationError

        self.client.force_login(self.operator)
        before = ExpenseRequest.objects.count()
        with patch(
            'apps.operations.views.expense_requests.create_expense_request',
            side_effect=ValidationError({'purpose': 'Propósito inválido de servicio'}),
        ):
            response = self.client.post(
                reverse('expense_request_create_for_project', args=[self.project.pk]),
                self._create_payload(self.allocation, purpose='Validación servicio'),
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(ExpenseRequest.objects.count(), before)
        self.assertFormError(
            response.context['form'],
            'purpose',
            'Propósito inválido de servicio',
        )

    # --- Update ---

    def test_admin_and_operator_edit_own_pending(self):
        cases = (
            (self.admin, 'Admin actualizada'),
            (self.operator, 'Operador actualizada'),
        )
        for actor, purpose in cases:
            with self.subTest(actor=actor.username):
                request_obj = self._create_via_service(actor)
                self.client.force_login(actor)
                response = self.client.post(
                    reverse('expense_request_update', args=[request_obj.pk]),
                    self._create_payload(
                        self.allocation,
                        amount='40,00',
                        purpose=purpose,
                    ),
                )
                request_obj.refresh_from_db()
                self.assertEqual(response.status_code, 302)
                self.assertEqual(request_obj.purpose, purpose)
                self.assertEqual(request_obj.requested_amount, Decimal('40.00'))
                self.assertTrue(
                    ExpenseRequestEvent.objects.filter(
                        expense_request=request_obj,
                        event_type=ExpenseRequestEvent.EventType.UPDATED,
                    ).exists()
                )

    def test_update_get_shows_initial_values(self):
        request_obj = self._create_via_service(self.operator, amount=Decimal('33.50'))
        self.client.force_login(self.operator)
        response = self.client.get(reverse('expense_request_update', args=[request_obj.pk]))
        self.assertEqual(response.status_code, 200)
        form = response.context['form']
        self.assertEqual(form.initial['fund_allocation'], request_obj.fund_allocation_id)
        self.assertEqual(form.initial['requested_amount'], request_obj.requested_amount)
        self.assertEqual(form.initial['purpose'], request_obj.purpose)
        self.assertEqual(form.initial['requested_date'], request_obj.requested_date)

    def test_cannot_edit_foreign_request(self):
        admin_request = self._create_via_service(self.admin)
        operator_request = self._create_via_service(self.operator)

        self.client.force_login(self.admin)
        self.assertEqual(
            self.client.get(
                reverse('expense_request_update', args=[operator_request.pk])
            ).status_code,
            404,
        )

        self.client.force_login(self.operator)
        self.assertEqual(
            self.client.get(
                reverse('expense_request_update', args=[admin_request.pk])
            ).status_code,
            404,
        )

        self.client.force_login(self.other_operator)
        self.assertEqual(
            self.client.get(
                reverse('expense_request_update', args=[operator_request.pk])
            ).status_code,
            404,
        )

    def test_committee_auditor_update_denied(self):
        request_obj = self._create_via_service(self.operator)
        for user in (self.committee, self.auditor):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                self.assertEqual(
                    self.client.get(
                        reverse('expense_request_update', args=[request_obj.pk])
                    ).status_code,
                    403,
                )

    def test_non_pending_cannot_edit(self):
        request_obj = self._create_via_service(self.operator)
        approve_expense_request(request_obj, actor=self.committee)
        self.client.force_login(self.operator)
        self.assertEqual(
            self.client.get(
                reverse('expense_request_update', args=[request_obj.pk])
            ).status_code,
            404,
        )

    def test_operator_update_keeps_project_scoped_allocations(self):
        request_obj = self._create_via_service(self.operator)
        self.client.force_login(self.operator)
        get_response = self.client.get(
            reverse('expense_request_update', args=[request_obj.pk])
        )
        choice_pks = set(
            get_response.context['form']
            .fields['fund_allocation']
            .queryset.values_list('pk', flat=True)
        )
        self.assertIn(self.allocation.pk, choice_pks)
        self.assertNotIn(self.other_allocation.pk, choice_pks)

        response = self.client.post(
            reverse('expense_request_update', args=[request_obj.pk]),
            self._create_payload(self.other_allocation, purpose='Cambio proyecto'),
        )
        self.assertEqual(response.status_code, 200)
        request_obj.refresh_from_db()
        self.assertEqual(request_obj.fund_allocation_id, self.allocation.pk)

    def test_admin_update_allows_global_allocations(self):
        request_obj = self._create_via_service(self.admin)
        self.client.force_login(self.admin)
        get_response = self.client.get(
            reverse('expense_request_update', args=[request_obj.pk])
        )
        choice_pks = set(
            get_response.context['form']
            .fields['fund_allocation']
            .queryset.values_list('pk', flat=True)
        )
        self.assertIn(self.allocation.pk, choice_pks)
        self.assertIn(self.other_allocation.pk, choice_pks)

        response = self.client.post(
            reverse('expense_request_update', args=[request_obj.pk]),
            self._create_payload(self.other_allocation, purpose='Admin cambia proyecto'),
        )
        request_obj.refresh_from_db()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(request_obj.fund_allocation_id, self.other_allocation.pk)

    # --- Withdraw ---

    def test_owner_can_withdraw_own_pending(self):
        for actor in (self.admin, self.operator):
            with self.subTest(actor=actor.username):
                request_obj = self._create_via_service(actor)
                self.client.force_login(actor)
                response = self.client.post(
                    reverse('expense_request_withdraw', args=[request_obj.pk]),
                    {'reason': 'Motivo de retiro suficiente'},
                )
                request_obj.refresh_from_db()
                self.assertEqual(response.status_code, 302)
                self.assertEqual(request_obj.status, ExpenseRequest.Status.WITHDRAWN)
                self.assertEqual(request_obj.terminal_reason, 'Motivo de retiro suficiente')
                self.assertEqual(request_obj.terminal_by_id, actor.pk)
                self.assertEqual(
                    ExpenseRequestEvent.objects.filter(
                        expense_request=request_obj,
                        event_type=ExpenseRequestEvent.EventType.WITHDRAWN,
                    ).count(),
                    1,
                )

    def test_cannot_withdraw_foreign_request(self):
        operator_request = self._create_via_service(self.operator)
        admin_request = self._create_via_service(self.admin)

        self.client.force_login(self.admin)
        self.assertEqual(
            self.client.get(
                reverse('expense_request_withdraw', args=[operator_request.pk])
            ).status_code,
            404,
        )
        self.client.force_login(self.operator)
        self.assertEqual(
            self.client.get(
                reverse('expense_request_withdraw', args=[admin_request.pk])
            ).status_code,
            404,
        )

    def test_withdraw_requires_reason(self):
        request_obj = self._create_via_service(self.operator)
        self.client.force_login(self.operator)
        response = self.client.post(
            reverse('expense_request_withdraw', args=[request_obj.pk]),
            {'reason': ''},
        )
        request_obj.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(request_obj.status, ExpenseRequest.Status.PENDING_DECISION)

    def test_duplicate_withdraw_rejected(self):
        request_obj = self._create_via_service(self.operator)
        withdraw_expense_request(
            request_obj,
            reason='Primer retiro válido',
            actor=self.operator,
        )
        self.client.force_login(self.operator)
        self.assertEqual(
            self.client.get(
                reverse('expense_request_withdraw', args=[request_obj.pk])
            ).status_code,
            404,
        )

    def test_non_pending_withdraw_is_404(self):
        request_obj = self._create_via_service(self.operator)
        approve_expense_request(request_obj, actor=self.committee)
        self.client.force_login(self.operator)
        self.assertEqual(
            self.client.get(
                reverse('expense_request_withdraw', args=[request_obj.pk])
            ).status_code,
            404,
        )

    # --- UI visibility ---

    def test_list_cta_visibility(self):
        self.client.force_login(self.admin)
        admin_list = self.client.get(reverse('expense_request_list'))
        self.assertContains(admin_list, 'Nueva solicitud')
        self.assertContains(admin_list, reverse('expense_request_create'))

        self.client.force_login(self.operator)
        operator_list = self.client.get(reverse('expense_request_list'))
        self.assertNotContains(operator_list, 'Nueva solicitud')
        self.assertNotContains(operator_list, reverse('expense_request_create'))
        self.assertContains(
            operator_list,
            'Cree una solicitud desde el detalle de un proyecto.',
        )

    def test_project_detail_solicitar_gasto_visibility(self):
        url = reverse('project_detail', args=[self.project.pk])
        for user, expected in (
            (self.admin, True),
            (self.operator, True),
            (self.committee, False),
            (self.auditor, False),
        ):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                response = self.client.get(url)
                if expected:
                    self.assertContains(response, 'Solicitar gasto')
                    self.assertContains(
                        response,
                        reverse(
                            'expense_request_create_for_project',
                            args=[self.project.pk],
                        ),
                    )
                else:
                    self.assertNotContains(response, 'Solicitar gasto')

    def test_detail_action_visibility_for_owner_and_non_owner(self):
        own = self._create_via_service(self.operator)
        foreign = self._create_via_service(self.admin)

        self.client.force_login(self.operator)
        own_detail = self.client.get(reverse('expense_request_detail', args=[own.pk]))
        self.assertContains(own_detail, 'Editar')
        self.assertContains(own_detail, 'Retirar')
        self.assertContains(own_detail, reverse('expense_request_update', args=[own.pk]))
        self.assertContains(own_detail, reverse('expense_request_withdraw', args=[own.pk]))

        self.client.force_login(self.admin)
        foreign_as_admin = self.client.get(
            reverse('expense_request_detail', args=[own.pk])
        )
        self.assertNotContains(foreign_as_admin, reverse('expense_request_update', args=[own.pk]))
        self.assertNotContains(
            foreign_as_admin,
            reverse('expense_request_withdraw', args=[own.pk]),
        )
        # Admin may still see "Editar" project links elsewhere; ensure requester actions absent.
        self.assertFalse(foreign_as_admin.context['can_edit_expense_request'])
        self.assertFalse(foreign_as_admin.context['can_withdraw_expense_request'])

        approved = approve_expense_request(foreign, actor=self.committee)
        self.client.force_login(self.admin)
        non_pending = self.client.get(
            reverse('expense_request_detail', args=[approved.pk])
        )
        self.assertFalse(non_pending.context['can_edit_expense_request'])
        self.assertFalse(non_pending.context['can_withdraw_expense_request'])

    def test_no_decision_fulfill_annul_actions_yet(self):
        request_obj = self._create_via_service(self.operator)
        for user in (self.admin, self.operator, self.committee, self.auditor):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                response = self.client.get(
                    reverse('expense_request_detail', args=[request_obj.pk])
                )
                html = response.content.decode()
                for label in (
                    'Aprobar',
                    'Denegar',
                    'Anular solicitud',
                    'Registrar gasto',
                    'Agregar adjunto',
                    'Eliminar adjunto',
                ):
                    self.assertNotIn(label, html)
                self.assertNotIn('expense_request_approve', html)
                self.assertNotIn('expense_request_fulfill', html)
                self.assertNotIn('expense_request_annul', html)
