"""Expense Request permission/visibility tests for ER3A UI foundation."""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from apps.operations.expense_request_services import create_expense_request
from apps.operations.role_services import sync_operation_roles
from apps.operations.roles import (
    ROLE_EXTERNAL_AUDITOR,
    ROLE_FIELD_OPERATOR,
    ROLE_PROJECT_COMMITTEE,
    ROLE_SIGEDON_ADMIN,
)
from apps.operations.selectors import (
    user_can_create_global_expense_request,
    user_has_global_expense_request_visibility,
    user_has_ownership_scoped_expense_requests,
    visible_expense_requests_for_user,
)
from apps.operations.tests.helpers import TEST_DATE, create_allocation
from apps.operations.tests.test_permissions import create_user_with_permissions


class ExpenseRequestPermissionTests(TestCase):
    def setUp(self):
        sync_operation_roles()
        self.allocation = create_allocation(amount=Decimal('300.00'))
        self.admin = self._user('er3a-perm-admin', ROLE_SIGEDON_ADMIN)
        self.operator = self._user('er3a-perm-operator', ROLE_FIELD_OPERATOR)
        self.other_operator = self._user('er3a-perm-operator-b', ROLE_FIELD_OPERATOR)
        self.committee = self._user('er3a-perm-committee', ROLE_PROJECT_COMMITTEE)
        self.auditor = self._user('er3a-perm-auditor', ROLE_EXTERNAL_AUDITOR)
        self.own_request = create_expense_request(
            fund_allocation=self.allocation,
            requested_amount=Decimal('20.00'),
            purpose='Solicitud de permisos propia',
            requested_date=TEST_DATE,
            actor=self.operator,
        )
        self.other_request = create_expense_request(
            fund_allocation=self.allocation,
            requested_amount=Decimal('22.00'),
            purpose='Solicitud de permisos ajena',
            requested_date=TEST_DATE,
            actor=self.other_operator,
        )

    def _user(self, username, role_name):
        user = get_user_model().objects.create_user(username=username, password='pass-12345')
        user.groups.add(Group.objects.get(name=role_name))
        return user

    def test_visibility_flags_by_effective_permissions(self):
        self.assertTrue(user_has_global_expense_request_visibility(self.admin))
        self.assertFalse(user_has_ownership_scoped_expense_requests(self.admin))

        self.assertTrue(user_has_global_expense_request_visibility(self.committee))
        self.assertFalse(user_has_ownership_scoped_expense_requests(self.committee))

        self.assertTrue(user_has_global_expense_request_visibility(self.auditor))
        self.assertFalse(user_has_ownership_scoped_expense_requests(self.auditor))

        self.assertFalse(user_has_global_expense_request_visibility(self.operator))
        self.assertTrue(user_has_ownership_scoped_expense_requests(self.operator))

    def test_ownership_scoped_queryset_excludes_unrelated_operator_rows(self):
        visible = set(
            visible_expense_requests_for_user(self.operator).values_list('pk', flat=True)
        )
        self.assertEqual(visible, {self.own_request.pk})
        self.assertNotIn(self.other_request.pk, visible)

    def test_unrelated_operator_detail_is_404_not_masked_200(self):
        self.client.force_login(self.other_operator)
        response = self.client.get(
            reverse('expense_request_detail', args=[self.own_request.pk])
        )
        self.assertEqual(response.status_code, 404)

    def test_direct_withdraw_without_elevated_is_ownership_scoped(self):
        user = create_user_with_permissions(
            'er3a-direct-owner',
            'view_expenserequest',
            'withdraw_expenserequest',
        )
        self.assertTrue(user_has_ownership_scoped_expense_requests(user))
        self.assertFalse(user_has_global_expense_request_visibility(user))
        visible = set(visible_expense_requests_for_user(user).values_list('pk', flat=True))
        self.assertEqual(visible, set())

    def test_direct_view_without_withdraw_sees_all(self):
        user = create_user_with_permissions('er3a-direct-auditorish', 'view_expenserequest')
        self.assertTrue(user_has_global_expense_request_visibility(user))
        visible = set(visible_expense_requests_for_user(user).values_list('pk', flat=True))
        self.assertEqual(visible, {self.own_request.pk, self.other_request.pk})

    def test_list_denied_without_view_permission(self):
        user = get_user_model().objects.create_user(
            username='er3a-perm-none',
            password='pass-12345',
        )
        self.client.force_login(user)
        response = self.client.get(reverse('expense_request_list'))
        self.assertEqual(response.status_code, 403)

    def test_global_create_permission_flag(self):
        self.assertTrue(user_can_create_global_expense_request(self.admin))
        self.assertFalse(user_can_create_global_expense_request(self.operator))
        self.assertFalse(user_can_create_global_expense_request(self.committee))
        self.assertFalse(user_can_create_global_expense_request(self.auditor))

    def test_update_and_withdraw_routes_enforce_ownership(self):
        self.client.force_login(self.other_operator)
        self.assertEqual(
            self.client.get(
                reverse('expense_request_update', args=[self.own_request.pk])
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.get(
                reverse('expense_request_withdraw', args=[self.own_request.pk])
            ).status_code,
            404,
        )

    def test_operator_with_add_cannot_open_global_create(self):
        self.client.force_login(self.operator)
        self.assertEqual(self.client.get(reverse('expense_request_create')).status_code, 403)

    def test_decision_routes_require_decide_permission(self):
        for user in (self.admin, self.operator, self.auditor):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                self.assertEqual(
                    self.client.get(
                        reverse('expense_request_approve', args=[self.own_request.pk])
                    ).status_code,
                    403,
                )
                self.assertEqual(
                    self.client.get(
                        reverse('expense_request_deny', args=[self.own_request.pk])
                    ).status_code,
                    403,
                )
        self.client.force_login(self.committee)
        self.assertEqual(
            self.client.get(
                reverse('expense_request_approve', args=[self.own_request.pk])
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.get(
                reverse('expense_request_deny', args=[self.own_request.pk])
            ).status_code,
            200,
        )

    def test_annul_routes_require_annul_permission(self):
        for user in (self.operator, self.committee, self.auditor):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                self.assertEqual(
                    self.client.get(
                        reverse('expense_request_annul', args=[self.own_request.pk])
                    ).status_code,
                    403,
                )
                self.assertEqual(
                    self.client.post(
                        reverse('expense_request_annul', args=[self.own_request.pk]),
                        {'reason': 'Anulación administrativa con motivo suficiente.'},
                    ).status_code,
                    403,
                )
        self.client.force_login(self.admin)
        self.assertEqual(
            self.client.get(
                reverse('expense_request_annul', args=[self.own_request.pk])
            ).status_code,
            200,
        )

    def test_attachment_mutation_routes_require_owner_pending_scope(self):
        create_url = reverse(
            'expense_request_attachment_create', args=[self.own_request.pk]
        )
        self.client.force_login(self.operator)
        self.assertEqual(self.client.get(create_url).status_code, 200)

        for user in (self.other_operator, self.committee, self.auditor, self.admin):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                self.assertIn(self.client.get(create_url).status_code, {403, 404})
