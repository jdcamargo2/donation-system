"""Expense Request fulfillment UI and direct Expense creation retirement (ER5)."""

import tempfile
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.operations.expense_request_services import (
    annul_expense_request,
    approve_expense_request,
    create_expense_request,
    deny_expense_request,
    fulfill_expense_request,
    withdraw_expense_request,
)
from apps.operations.forms import ExpenseRequestFulfillmentForm
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
from apps.operations.services import create_expense as create_expense_public
from apps.operations.tests.helpers import TEST_DATE, create_allocation
from apps.operations.tests.test_permissions import create_user_with_permissions


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class ExpenseRequestFulfillmentUITests(TestCase):
    def setUp(self):
        sync_operation_roles()
        self.allocation = create_allocation(amount=Decimal('200.00'))
        self.admin = self._user('er5-admin', ROLE_SIGEDON_ADMIN)
        self.operator = self._user('er5-operator', ROLE_FIELD_OPERATOR)
        self.committee = self._user('er5-committee', ROLE_PROJECT_COMMITTEE)
        self.auditor = self._user('er5-auditor', ROLE_EXTERNAL_AUDITOR)
        self.request_obj = create_expense_request(
            fund_allocation=self.allocation,
            requested_amount=Decimal('50.00'),
            purpose='Solicitud de cumplimiento UI',
            requested_date=TEST_DATE,
            actor=self.operator,
        )

    def _user(self, username, role_name):
        user = get_user_model().objects.create_user(username=username, password='pass-12345')
        user.groups.add(Group.objects.get(name=role_name))
        return user

    def _approve(self, request=None, amount=None):
        target = request or self.request_obj
        if amount is not None and target.requested_amount != amount:
            target = create_expense_request(
                fund_allocation=self.allocation,
                requested_amount=amount,
                purpose='Otra solicitud de cumplimiento',
                requested_date=TEST_DATE,
                actor=self.operator,
            )
        return approve_expense_request(target, actor=self.committee)

    def _support(self, name='factura-er5.pdf'):
        return SimpleUploadedFile(name, b'%PDF-1.4 soporte er5', content_type='application/pdf')

    def _fulfill_url(self, pk=None):
        return reverse('expense_request_fulfill', args=[pk or self.request_obj.pk])

    def _detail_url(self, pk=None):
        return reverse('expense_request_detail', args=[pk or self.request_obj.pk])

    def _post_data(self, *, amount='50.00', include_file=True, **overrides):
        data = {
            'expense_date': TEST_DATE.isoformat(),
            'amount': amount,
            'category': 'materials',
            'reason': 'Pago final autorizado desde UI',
            'provider_or_recipient': 'Proveedor final UI',
            'payment_method': 'bank_transfer',
            'description': 'Descripción del gasto final',
            'observations': '',
            'support_title': 'Factura final',
            'support_notes': '',
        }
        data.update(overrides)
        if include_file and 'support_file' not in data:
            data['support_file'] = self._support()
        return data

    def _events(self, request, event_type=None):
        qs = ExpenseRequestEvent.objects.filter(expense_request=request)
        if event_type is not None:
            qs = qs.filter(event_type=event_type)
        return qs

    def _audits(self, request):
        return AuditLog.objects.filter(entity_id=str(request.pk))

    # --- Route and permission ---

    def test_admin_get_approved_reserved_200(self):
        approved = self._approve()
        self.client.force_login(self.admin)
        response = self.client.get(self._fulfill_url(approved.pk))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, approved.code)
        self.assertContains(response, self.allocation.project.name)
        self.assertContains(response, self.allocation.get_budget_category_display())
        self.assertContains(response, 'USD 50,00')
        self.assertContains(response, 'Máximo según la reserva')
        self.assertContains(response, 'Registrar gasto')
        self.assertContains(response, 'Volver a la solicitud')
        self.assertContains(
            response,
            'El gasto se registrará contra la reserva de esta solicitud',
        )
        self.assertContains(response, 'enctype="multipart/form-data"')
        self.assertContains(response, 'data-file-upload-preview')
        self.assertNotContains(response, self.allocation.donation.donor.name)
        self.assertNotContains(response, self.allocation.donation.code)
        approved.refresh_from_db()
        self.assertEqual(approved.status, ExpenseRequest.Status.APPROVED_RESERVED)

    def test_admin_post_exact_amount_succeeds(self):
        approved = self._approve()
        before = self.allocation.available_balance
        expenses_before = Expense.objects.count()
        docs_before = SupportingDocument.objects.count()
        self.client.force_login(self.admin)
        response = self.client.post(
            self._fulfill_url(approved.pk),
            self._post_data(amount='50.00'),
            follow=True,
        )
        self.assertEqual(response.redirect_chain[0][1], 302)
        self.assertEqual(response.redirect_chain[0][0], self._detail_url(approved.pk))
        approved.refresh_from_db()
        self.allocation.refresh_from_db()
        self.assertEqual(approved.status, ExpenseRequest.Status.FULFILLED)
        self.assertIsNotNone(approved.expense_id)
        self.assertEqual(approved.expense.amount, Decimal('50.00'))
        self.assertEqual(Expense.objects.count(), expenses_before + 1)
        self.assertEqual(SupportingDocument.objects.count(), docs_before + 1)
        self.assertEqual(self.allocation.available_balance, before)
        self.assertEqual(self.allocation.executed_amount, Decimal('50.00'))
        self.assertEqual(self.allocation.reserved_amount, ZERO_MONEY)
        self.assertEqual(
            self._events(approved, ExpenseRequestEvent.EventType.EXPENSE_REGISTERED).count(),
            1,
        )
        self.assertEqual(
            self._events(approved, ExpenseRequestEvent.EventType.RESERVATION_CONSUMED).count(),
            1,
        )
        self.assertEqual(
            self._events(
                approved, ExpenseRequestEvent.EventType.UNUSED_RESERVATION_RELEASED
            ).count(),
            0,
        )
        self.assertEqual(
            self._audits(approved).filter(action=AuditLog.Action.EXECUTED).count(),
            1,
        )
        self.assertContains(response, 'Gasto registrado desde la solicitud.')
        self.assertNotContains(response, 'La reserva no utilizada fue liberada.')
        self.assertContains(response, approved.expense.code)
        self.assertTrue(response.context['financial_summary']['has_linked_expense'])
        self.assertFalse(response.context['can_fulfill_expense_request'])

    def test_admin_post_partial_amount_releases_unused_reservation(self):
        approved = self._approve()
        before = self.allocation.available_balance
        self.client.force_login(self.admin)
        response = self.client.post(
            self._fulfill_url(approved.pk),
            self._post_data(amount='30.00'),
            follow=True,
        )
        self.assertEqual(response.redirect_chain[0][1], 302)
        approved.refresh_from_db()
        self.allocation.refresh_from_db()
        self.assertEqual(approved.status, ExpenseRequest.Status.FULFILLED)
        self.assertEqual(approved.expense.amount, Decimal('30.00'))
        self.assertEqual(approved.reserved_amount, Decimal('50.00'))
        self.assertEqual(self.allocation.available_balance, before + Decimal('20.00'))
        self.assertEqual(self.allocation.executed_amount, Decimal('30.00'))
        self.assertEqual(
            self._events(
                approved, ExpenseRequestEvent.EventType.UNUSED_RESERVATION_RELEASED
            ).count(),
            1,
        )
        self.assertContains(
            response,
            'Gasto registrado desde la solicitud. La reserva no utilizada fue liberada.',
        )
        financial = response.context['financial_summary']
        self.assertEqual(financial['requested_amount'], Decimal('50.00'))
        self.assertEqual(financial['reserved_amount'], Decimal('50.00'))
        self.assertEqual(financial['executed_amount'], Decimal('30.00'))
        self.assertEqual(financial['released_amount'], Decimal('20.00'))
        self.assertTrue(financial['show_released'])

    def test_operator_committee_auditor_forbidden(self):
        approved = self._approve()
        url = self._fulfill_url(approved.pk)
        for user in (self.operator, self.committee, self.auditor):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                self.assertEqual(self.client.get(url).status_code, 403)
                self.assertEqual(
                    self.client.post(url, self._post_data()).status_code,
                    403,
                )
                approved.refresh_from_db()
                self.assertEqual(approved.status, ExpenseRequest.Status.APPROVED_RESERVED)
                self.assertIsNone(approved.expense_id)

    def test_anonymous_redirects_to_login(self):
        approved = self._approve()
        response = self.client.get(self._fulfill_url(approved.pk))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response['Location'])

    def test_non_fulfillable_statuses_return_404(self):
        pending = self.request_obj
        denied = deny_expense_request(
            create_expense_request(
                fund_allocation=self.allocation,
                requested_amount=Decimal('10.00'),
                purpose='Para denegar',
                requested_date=TEST_DATE,
                actor=self.operator,
            ),
            decision_note='Denegación con motivo suficiente para la prueba.',
            actor=self.committee,
        )
        withdrawn = withdraw_expense_request(
            create_expense_request(
                fund_allocation=self.allocation,
                requested_amount=Decimal('11.00'),
                purpose='Para retirar',
                requested_date=TEST_DATE,
                actor=self.operator,
            ),
            reason='Retiro con motivo suficiente para la prueba.',
            actor=self.operator,
        )
        approved = self._approve(
            create_expense_request(
                fund_allocation=self.allocation,
                requested_amount=Decimal('12.00'),
                purpose='Para cumplir',
                requested_date=TEST_DATE,
                actor=self.operator,
            )
        )
        fulfilled = fulfill_expense_request(
            approved,
            expense_date=TEST_DATE,
            amount=Decimal('12.00'),
            reason='Cumplimiento previo',
            provider_or_recipient='Proveedor',
            payment_method='bank_transfer',
            description='',
            support_file=self._support('prev.pdf'),
            support_title='Factura',
            category='food',
            actor=self.admin,
        )
        annulled = annul_expense_request(
            create_expense_request(
                fund_allocation=self.allocation,
                requested_amount=Decimal('13.00'),
                purpose='Para anular',
                requested_date=TEST_DATE,
                actor=self.operator,
            ),
            reason='Anulación con motivo suficiente para la prueba.',
            actor=self.admin,
        )

        self.client.force_login(self.admin)
        for request in (pending, denied, withdrawn, fulfilled, annulled):
            with self.subTest(status=request.status, pk=request.pk):
                self.assertEqual(self.client.get(self._fulfill_url(request.pk)).status_code, 404)
                self.assertEqual(
                    self.client.post(
                        self._fulfill_url(request.pk),
                        self._post_data(amount='1.00'),
                    ).status_code,
                    404,
                )

    def test_direct_permission_user_can_fulfill(self):
        approved = self._approve()
        user = create_user_with_permissions(
            'er5-direct-fulfill',
            'view_expenserequest',
            'fulfill_expenserequest',
            'view_expense',
            'view_fundallocation',
            'view_project',
        )
        self.client.force_login(user)
        response = self.client.get(self._fulfill_url(approved.pk))
        self.assertEqual(response.status_code, 200)
        post = self.client.post(
            self._fulfill_url(approved.pk),
            self._post_data(amount='50.00'),
            follow=True,
        )
        self.assertEqual(post.redirect_chain[0][1], 302)
        approved.refresh_from_db()
        self.assertEqual(approved.status, ExpenseRequest.Status.FULFILLED)
        self.assertEqual(approved.expense.amount, Decimal('50.00'))

    # --- Validation ---

    def test_validation_rejects_invalid_amounts_and_missing_support(self):
        approved = self._approve()
        before = self.allocation.available_balance
        expenses_before = Expense.objects.count()
        self.client.force_login(self.admin)
        cases = [
            {'amount': '0.00', 'include_file': True},
            {'amount': '-5.00', 'include_file': True},
            {'amount': '50.01', 'include_file': True},
            {'amount': '50.00', 'include_file': False},
            {'amount': 'abc', 'include_file': True},
            {'amount': '50.00', 'include_file': True, 'category': 'not-a-category'},
            {'amount': '50.00', 'include_file': True, 'expense_date': '31/02/2020'},
        ]
        for case in cases:
            with self.subTest(case=case):
                include_file = case.pop('include_file')
                response = self.client.post(
                    self._fulfill_url(approved.pk),
                    self._post_data(include_file=include_file, **case),
                )
                self.assertEqual(response.status_code, 200)
                approved.refresh_from_db()
                self.allocation.refresh_from_db()
                self.assertEqual(approved.status, ExpenseRequest.Status.APPROVED_RESERVED)
                self.assertIsNone(approved.expense_id)
                self.assertEqual(Expense.objects.count(), expenses_before)
                self.assertEqual(self.allocation.available_balance, before)

    def test_forged_identity_fields_are_ignored(self):
        approved = self._approve()
        before = self.allocation.available_balance
        self.client.force_login(self.admin)
        response = self.client.post(
            self._fulfill_url(approved.pk),
            self._post_data(
                amount='50.00',
                fund_allocation=999999,
                allocation=999999,
                reserved_amount='1.00',
                status=ExpenseRequest.Status.PENDING_DECISION,
                code='REQ-FORGED',
                expense_code='GAS-FORGED',
                requested_by=self.admin.pk,
                actor=self.operator.pk,
            ),
            follow=True,
        )
        self.assertEqual(response.redirect_chain[0][1], 302)
        approved.refresh_from_db()
        self.allocation.refresh_from_db()
        self.assertEqual(approved.status, ExpenseRequest.Status.FULFILLED)
        self.assertEqual(approved.expense.allocation_id, self.allocation.pk)
        self.assertNotEqual(approved.expense.code, 'GAS-FORGED')
        self.assertNotEqual(approved.code, 'REQ-FORGED')
        self.assertEqual(self.allocation.executed_amount, Decimal('50.00'))
        self.assertEqual(self.allocation.available_balance, before)

    # --- Rollback ---

    def test_event_failure_rolls_back_fulfillment(self):
        approved = self._approve()
        before = self.allocation.available_balance
        expenses_before = Expense.objects.count()
        docs_before = SupportingDocument.objects.count()
        self.client.force_login(self.admin)
        with patch(
            'apps.operations.expense_request_services.ExpenseRequestEvent.objects.create',
            side_effect=RuntimeError('fulfill event boom'),
        ):
            response = self.client.post(
                self._fulfill_url(approved.pk),
                self._post_data(amount='50.00'),
            )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'No se pudo completar la acción')
        approved.refresh_from_db()
        self.allocation.refresh_from_db()
        self.assertEqual(approved.status, ExpenseRequest.Status.APPROVED_RESERVED)
        self.assertIsNone(approved.expense_id)
        self.assertEqual(Expense.objects.count(), expenses_before)
        self.assertEqual(SupportingDocument.objects.count(), docs_before)
        self.assertEqual(self.allocation.available_balance, before)
        self.assertEqual(self.allocation.reserved_amount, Decimal('50.00'))

    def test_audit_failure_rolls_back_fulfillment(self):
        approved = self._approve()
        before = self.allocation.available_balance
        self.client.force_login(self.admin)
        with patch(
            'apps.operations.expense_request_services.log_action',
            side_effect=RuntimeError('fulfill audit boom'),
        ):
            response = self.client.post(
                self._fulfill_url(approved.pk),
                self._post_data(amount='50.00'),
            )
        self.assertEqual(response.status_code, 200)
        approved.refresh_from_db()
        self.assertEqual(approved.status, ExpenseRequest.Status.APPROVED_RESERVED)
        self.assertIsNone(approved.expense_id)
        self.allocation.refresh_from_db()
        self.assertEqual(self.allocation.available_balance, before)

    def test_expense_creation_failure_rolls_back(self):
        approved = self._approve()
        before = self.allocation.available_balance
        self.client.force_login(self.admin)
        with patch(
            'apps.operations.expense_request_services._create_expense_locked',
            side_effect=RuntimeError('expense boom'),
        ):
            response = self.client.post(
                self._fulfill_url(approved.pk),
                self._post_data(amount='50.00'),
            )
        self.assertEqual(response.status_code, 200)
        approved.refresh_from_db()
        self.assertEqual(approved.status, ExpenseRequest.Status.APPROVED_RESERVED)
        self.assertIsNone(approved.expense_id)
        self.allocation.refresh_from_db()
        self.assertEqual(self.allocation.available_balance, before)

    def test_stale_second_fulfill_returns_404(self):
        approved = self._approve()
        self.client.force_login(self.admin)
        first = self.client.post(
            self._fulfill_url(approved.pk),
            self._post_data(amount='50.00'),
            follow=True,
        )
        self.assertEqual(first.redirect_chain[0][1], 302)
        expenses_after = Expense.objects.count()
        second = self.client.post(
            self._fulfill_url(approved.pk),
            self._post_data(amount='50.00'),
        )
        self.assertEqual(second.status_code, 404)
        self.assertEqual(Expense.objects.count(), expenses_after)

    # --- Detail visibility ---

    def test_detail_fulfill_action_visibility_by_role_and_status(self):
        pending = self.request_obj
        approved = self._approve(
            create_expense_request(
                fund_allocation=self.allocation,
                requested_amount=Decimal('20.00'),
                purpose='Visibilidad cumplimiento',
                requested_date=TEST_DATE,
                actor=self.operator,
            )
        )
        fulfilled = fulfill_expense_request(
            self._approve(
                create_expense_request(
                    fund_allocation=self.allocation,
                    requested_amount=Decimal('15.00'),
                    purpose='Ya cumplida',
                    requested_date=TEST_DATE,
                    actor=self.operator,
                )
            ),
            expense_date=TEST_DATE,
            amount=Decimal('15.00'),
            reason='Cumplida',
            provider_or_recipient='Proveedor',
            payment_method='cash',
            description='',
            support_file=self._support('done.pdf'),
            support_title='Soporte',
            category='food',
            actor=self.admin,
        )

        self.client.force_login(self.admin)
        pending_detail = self.client.get(self._detail_url(pending.pk))
        self.assertFalse(pending_detail.context['can_fulfill_expense_request'])
        self.assertNotContains(pending_detail, 'Registrar gasto')
        self.assertNotIn('expense_request_fulfill', pending_detail.content.decode())
        self.assertTrue(pending_detail.context['can_annul_expense_request'])

        approved_detail = self.client.get(self._detail_url(approved.pk))
        self.assertTrue(approved_detail.context['can_fulfill_expense_request'])
        self.assertContains(approved_detail, 'Registrar gasto')
        self.assertContains(
            approved_detail,
            reverse('expense_request_fulfill', args=[approved.pk]),
        )
        self.assertTrue(approved_detail.context['can_annul_expense_request'])
        self.assertContains(approved_detail, 'Anular solicitud')

        fulfilled_detail = self.client.get(self._detail_url(fulfilled.pk))
        self.assertFalse(fulfilled_detail.context['can_fulfill_expense_request'])
        self.assertFalse(fulfilled_detail.context['can_annul_expense_request'])
        self.assertNotContains(fulfilled_detail, 'Registrar gasto')
        self.assertContains(fulfilled_detail, fulfilled.expense.code)

        for user in (self.operator, self.committee, self.auditor):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                response = self.client.get(self._detail_url(approved.pk))
                self.assertFalse(response.context['can_fulfill_expense_request'])
                html = response.content.decode()
                self.assertNotIn('Registrar gasto', html)
                self.assertNotIn('expense_request_fulfill', html)

    # --- Direct Expense UI retirement ---

    def test_expense_list_has_no_nuevo_gasto(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse('expense_list'))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Nuevo gasto')
        self.assertNotContains(response, reverse('expense_create'))
        self.assertContains(response, 'Ver solicitudes de gasto')
        self.assertContains(response, reverse('expense_request_list'))

    def test_dashboard_retires_crear_gasto(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse('dashboard'))
        self.assertNotContains(response, 'Crear gasto')
        self.assertNotContains(response, reverse('expense_create'))
        self.assertContains(response, 'Ver solicitudes')
        self.assertContains(response, 'Pendientes de registrar gasto')
        self.assertContains(response, 'status=approved_reserved')

    def test_allocation_detail_points_to_request_creation(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse('allocation_detail', args=[self.allocation.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Nuevo gasto')
        self.assertNotContains(response, reverse('expense_create'))
        self.assertTrue(response.context['can_create_expense_request'])
        self.assertContains(response, 'Solicitar gasto')
        self.assertContains(
            response,
            reverse(
                'expense_request_create_for_project',
                args=[self.allocation.project.pk],
            ),
        )

    def test_direct_expense_create_get_and_post_create_nothing(self):
        expenses_before = Expense.objects.count()
        docs_before = SupportingDocument.objects.count()
        self.client.force_login(self.admin)
        get_response = self.client.get(reverse('expense_create'))
        self.assertEqual(get_response.status_code, 302)
        self.assertIn(reverse('expense_request_list'), get_response['Location'])
        self.assertIn('status=approved_reserved', get_response['Location'])

        post_response = self.client.post(
            reverse('expense_create'),
            {
                'allocation': self.allocation.pk,
                'expense_date': TEST_DATE.isoformat(),
                'category': 'food',
                'amount': '10.00',
                'reason': 'Intento directo',
                'provider_or_recipient': 'Proveedor',
                'payment_method': 'cash',
                'support_file': self._support('direct.pdf'),
            },
        )
        self.assertEqual(post_response.status_code, 302)
        self.assertEqual(Expense.objects.count(), expenses_before)
        self.assertEqual(SupportingDocument.objects.count(), docs_before)

        follow = self.client.get(get_response['Location'], follow=True)
        self.assertContains(
            follow,
            'El gasto debe registrarse desde una solicitud de gasto aprobada.',
        )

    def test_public_create_expense_service_remains_rejected(self):
        with self.assertRaisesMessage(
            Exception,
            'El gasto debe registrarse desde una solicitud de gasto aprobada.',
        ):
            create_expense_public(
                allocation=self.allocation,
                expense_date=TEST_DATE,
                category='food',
                amount=Decimal('10.00'),
                reason='Directo',
                provider_or_recipient='Proveedor',
                payment_method='cash',
                description='',
                observations='',
                actor=self.admin,
                support_title='x',
                support_file=self._support(),
            )

    def test_expense_list_and_detail_remain_accessible(self):
        approved = self._approve()
        fulfilled = fulfill_expense_request(
            approved,
            expense_date=TEST_DATE,
            amount=Decimal('50.00'),
            reason='Acceso histórico',
            provider_or_recipient='Proveedor',
            payment_method='bank_transfer',
            description='',
            support_file=self._support('hist.pdf'),
            support_title='Hist',
            category='materials',
            actor=self.admin,
        )
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse('expense_list')).status_code, 200)
        self.assertEqual(
            self.client.get(reverse('expense_detail', args=[fulfilled.expense.pk])).status_code,
            200,
        )


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class ExpenseRequestFulfillmentFormTests(TestCase):
    def test_form_requires_support_and_caps_amount(self):
        form = ExpenseRequestFulfillmentForm(
            data={
                'expense_date': TEST_DATE.isoformat(),
                'amount': '60.00',
                'category': 'food',
                'reason': 'Motivo',
                'provider_or_recipient': 'Proveedor',
                'payment_method': 'cash',
                'description': '',
                'observations': '',
                'support_title': '',
                'support_notes': '',
            },
            reserved_amount=Decimal('50.00'),
        )
        self.assertFalse(form.is_valid())
        self.assertIn('amount', form.errors)
        self.assertIn('support_file', form.errors)
        self.assertNotIn('allocation', form.fields)
        self.assertNotIn('status', form.fields)
