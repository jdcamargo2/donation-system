"""Expense Request list/detail view tests (ER3A)."""

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.operations.expense_request_services import (
    annul_expense_request,
    approve_expense_request,
    create_expense_request,
    fulfill_expense_request,
)
from apps.operations.models import ExpenseRequest, ZERO_MONEY
from apps.operations.pagination import DEFAULT_PAGE_SIZE
from apps.operations.role_services import sync_operation_roles
from apps.operations.roles import (
    ROLE_EXTERNAL_AUDITOR,
    ROLE_FIELD_OPERATOR,
    ROLE_PROJECT_COMMITTEE,
    ROLE_SIGEDON_ADMIN,
)
from apps.operations.selectors import (
    get_expense_request_financial_display,
    visible_expense_requests_for_user,
)
from apps.operations.services import annul_expense
from apps.operations.tests.helpers import TEST_DATE, create_allocation
from apps.operations.tests.test_permissions import create_user_with_permissions
from django.core.files.uploadedfile import SimpleUploadedFile
import tempfile


def _support_file(name='soporte.pdf'):
    return SimpleUploadedFile(name, b'%PDF-1.4', content_type='application/pdf')


def _fulfill(request, *, amount, actor, purpose_reason='Ejecución'):
    return fulfill_expense_request(
        request,
        expense_date=TEST_DATE,
        amount=amount,
        reason=purpose_reason,
        provider_or_recipient='Proveedor',
        payment_method='bank_transfer',
        description='Descripción del gasto',
        support_file=_support_file(),
        support_title='Soporte',
        category='food',
        actor=actor,
    )


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class ExpenseRequestViewTests(TestCase):
    def setUp(self):
        sync_operation_roles()
        self.allocation = create_allocation(amount=Decimal('500.00'))
        self.admin = self._user('er3a-admin', ROLE_SIGEDON_ADMIN)
        self.operator = self._user('er3a-operator', ROLE_FIELD_OPERATOR)
        self.other_operator = self._user('er3a-operator-other', ROLE_FIELD_OPERATOR)
        self.committee = self._user('er3a-committee', ROLE_PROJECT_COMMITTEE)
        self.auditor = self._user('er3a-auditor', ROLE_EXTERNAL_AUDITOR)
        self.own_request = create_expense_request(
            fund_allocation=self.allocation,
            requested_amount=Decimal('40.00'),
            purpose='Compra de insumos propios',
            requested_date=TEST_DATE,
            actor=self.operator,
        )
        self.other_request = create_expense_request(
            fund_allocation=self.allocation,
            requested_amount=Decimal('55.00'),
            purpose='Compra de insumos ajenos',
            requested_date=date(2026, 7, 9),
            actor=self.other_operator,
        )

    def _user(self, username, role_name):
        user = get_user_model().objects.create_user(username=username, password='pass-12345')
        user.groups.add(Group.objects.get(name=role_name))
        return user

    def test_admin_list_sees_all_requests(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse('expense_request_list'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.own_request.code)
        self.assertContains(response, self.other_request.code)

    def test_committee_list_sees_all_and_defaults_to_pending(self):
        self.client.force_login(self.committee)
        response = self.client.get(reverse('expense_request_list'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('status=pending_decision', response['Location'])
        follow = self.client.get(response['Location'])
        self.assertEqual(follow.status_code, 200)
        self.assertContains(follow, self.own_request.code)
        self.assertContains(follow, 'value="pending_decision" selected')

    def test_committee_explicit_status_overrides_default(self):
        approved = approve_expense_request(self.own_request, actor=self.committee)
        self.client.force_login(self.committee)
        response = self.client.get(
            reverse('expense_request_list'),
            {'status': ExpenseRequest.Status.APPROVED_RESERVED},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, approved.code)
        self.assertNotContains(response, self.other_request.code)

    def test_committee_todos_status_shows_all_without_redirect_loop(self):
        self.client.force_login(self.committee)
        response = self.client.get(reverse('expense_request_list'), {'status': ''})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.own_request.code)
        self.assertContains(response, self.other_request.code)

    def test_auditor_list_sees_all_requests(self):
        self.client.force_login(self.auditor)
        response = self.client.get(reverse('expense_request_list'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.own_request.code)
        self.assertContains(response, self.other_request.code)

    def test_operator_list_sees_only_own_requests(self):
        self.client.force_login(self.operator)
        response = self.client.get(reverse('expense_request_list'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.own_request.code)
        self.assertNotContains(response, self.other_request.code)

    def test_superuser_list_sees_all_requests(self):
        superuser = get_user_model().objects.create_superuser(
            username='er3a-super',
            password='pass-12345',
        )
        self.client.force_login(superuser)
        response = self.client.get(reverse('expense_request_list'), follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.own_request.code)
        self.assertContains(response, self.other_request.code)

    def test_direct_view_permission_user_sees_all_like_auditor(self):
        viewer = create_user_with_permissions('er3a-direct-view', 'view_expenserequest')
        self.client.force_login(viewer)
        response = self.client.get(reverse('expense_request_list'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.own_request.code)
        self.assertContains(response, self.other_request.code)

    def test_list_query_count_stays_reasonable(self):
        for index in range(5):
            create_expense_request(
                fund_allocation=self.allocation,
                requested_amount=Decimal('10.00'),
                purpose=f'Solicitud listado {index}',
                requested_date=TEST_DATE,
                actor=self.operator,
            )
        self.client.force_login(self.admin)
        with CaptureQueriesContext(connection) as captured:
            response = self.client.get(reverse('expense_request_list'))
        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(captured), 25)

    def test_search_by_code_purpose_project_and_expense(self):
        approved = approve_expense_request(self.own_request, actor=self.committee)
        fulfilled = _fulfill(
            approved,
            amount=Decimal('40.00'),
            actor=self.admin,
            purpose_reason='Ejecución de solicitud',
        )
        self.client.force_login(self.admin)
        for query, expected_code in (
            (fulfilled.code, fulfilled.code),
            ('insumos propios', fulfilled.code),
            (self.allocation.project.code, fulfilled.code),
            (fulfilled.expense.code, fulfilled.code),
        ):
            response = self.client.get(reverse('expense_request_list'), {'q': query})
            self.assertContains(response, expected_code)

    def test_status_and_date_filters(self):
        self.client.force_login(self.admin)
        response = self.client.get(
            reverse('expense_request_list'),
            {
                'status': ExpenseRequest.Status.PENDING_DECISION,
                'date_from': '2026-07-08',
                'date_to': '2026-07-08',
            },
        )
        self.assertContains(response, self.own_request.code)
        self.assertNotContains(response, self.other_request.code)

    def test_project_and_requester_filters_for_global_viewers(self):
        self.client.force_login(self.admin)
        response = self.client.get(
            reverse('expense_request_list'),
            {
                'project': str(self.allocation.project_id),
                'requester': str(self.operator.pk),
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.own_request.code)
        self.assertNotContains(response, self.other_request.code)
        self.assertContains(response, 'id="filter-requester"')
        self.assertContains(response, 'id="filter-category"')

    def test_operator_does_not_see_requester_filter(self):
        self.client.force_login(self.operator)
        response = self.client.get(reverse('expense_request_list'))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'id="filter-requester"')
        self.assertNotContains(response, 'id="filter-category"')

    def test_pagination_preserves_filters(self):
        for index in range(DEFAULT_PAGE_SIZE + 1):
            create_expense_request(
                fund_allocation=self.allocation,
                requested_amount=Decimal('5.00'),
                purpose=f'Paginación {index}',
                requested_date=TEST_DATE,
                actor=self.operator,
            )
        self.client.force_login(self.admin)
        response = self.client.get(
            reverse('expense_request_list'),
            {
                'status': ExpenseRequest.Status.PENDING_DECISION,
                'q': 'Paginación',
                'page': '2',
            },
        )
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn('status=pending_decision', html)
        self.assertIn('q=Paginaci', html)

    def test_empty_state_messages(self):
        lonely = self._user('er3a-lonely', ROLE_FIELD_OPERATOR)
        self.client.force_login(lonely)
        empty = self.client.get(reverse('expense_request_list'))
        self.assertContains(empty, 'No hay solicitudes de gasto registradas.')
        self.client.force_login(self.admin)
        filtered = self.client.get(
            reverse('expense_request_list'),
            {'q': 'inexistente-xyz'},
        )
        self.assertContains(
            filtered,
            'No se encontraron solicitudes con los filtros seleccionados.',
        )

    def test_detail_access_matrix(self):
        cases = (
            (self.admin, 200),
            (self.committee, 200),
            (self.auditor, 200),
            (self.operator, 200),
            (self.other_operator, 404),
        )
        for user, status_code in cases:
            with self.subTest(user=user.username, status=status_code):
                self.client.force_login(user)
                response = self.client.get(
                    reverse('expense_request_detail', args=[self.own_request.pk])
                )
                self.assertEqual(response.status_code, status_code)

    def test_detail_without_permission_is_403(self):
        user = get_user_model().objects.create_user(
            username='er3a-no-view',
            password='pass-12345',
        )
        self.client.force_login(user)
        response = self.client.get(
            reverse('expense_request_detail', args=[self.own_request.pk])
        )
        self.assertEqual(response.status_code, 403)

    def test_anonymous_redirects_to_login(self):
        response = self.client.get(reverse('expense_request_list'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response['Location'])

    def test_detail_related_links_are_permission_aware(self):
        approved = approve_expense_request(self.own_request, actor=self.committee)
        fulfilled = _fulfill(
            approved,
            amount=Decimal('40.00'),
            actor=self.admin,
            purpose_reason='Gasto desde solicitud',
        )
        self.client.force_login(self.operator)
        response = self.client.get(
            reverse('expense_request_detail', args=[fulfilled.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse('project_detail', args=[self.allocation.project.pk]))
        self.assertNotContains(
            response,
            reverse('allocation_detail', args=[self.allocation.pk]),
        )
        self.assertNotContains(
            response,
            reverse('expense_detail', args=[fulfilled.expense.pk]),
        )
        self.assertNotContains(response, self.allocation.donation.code)
        self.assertNotContains(response, 'Donación')

        self.client.force_login(self.auditor)
        auditor_response = self.client.get(
            reverse('expense_request_detail', args=[fulfilled.pk])
        )
        self.assertContains(
            auditor_response,
            reverse('allocation_detail', args=[self.allocation.pk]),
        )
        self.assertContains(
            auditor_response,
            reverse('expense_detail', args=[fulfilled.expense.pk]),
        )

    def test_financial_summary_scenarios(self):
        pending = get_expense_request_financial_display(self.own_request)
        self.assertEqual(pending['requested_amount'], Decimal('40.00'))
        self.assertEqual(pending['reserved_amount'], ZERO_MONEY)
        self.assertFalse(pending['show_reserved'])
        self.assertFalse(pending['show_executed'])
        self.assertFalse(pending['show_released'])

        approved = approve_expense_request(self.own_request, actor=self.committee)
        reserved = get_expense_request_financial_display(
            ExpenseRequest.objects.select_related('expense').get(pk=approved.pk)
        )
        self.assertTrue(reserved['has_active_reservation'])
        self.assertEqual(reserved['reserved_amount'], Decimal('40.00'))
        self.assertTrue(reserved['show_reserved'])

        exact = _fulfill(approved, amount=Decimal('40.00'), actor=self.admin, purpose_reason='Exacto')
        exact_display = get_expense_request_financial_display(
            ExpenseRequest.objects.select_related('expense').get(pk=exact.pk)
        )
        self.assertEqual(exact_display['executed_amount'], Decimal('40.00'))
        self.assertEqual(exact_display['released_amount'], ZERO_MONEY)
        self.assertFalse(exact_display['show_released'])

        partial_source = create_expense_request(
            fund_allocation=self.allocation,
            requested_amount=Decimal('50.00'),
            purpose='Parcial',
            requested_date=TEST_DATE,
            actor=self.operator,
        )
        partial_approved = approve_expense_request(partial_source, actor=self.committee)
        partial = _fulfill(
            partial_approved,
            amount=Decimal('30.00'),
            actor=self.admin,
            purpose_reason='Parcial',
        )
        partial_display = get_expense_request_financial_display(
            ExpenseRequest.objects.select_related('expense').get(pk=partial.pk)
        )
        self.assertEqual(partial_display['released_amount'], Decimal('20.00'))
        self.assertTrue(partial_display['show_released'])

        to_annul = create_expense_request(
            fund_allocation=self.allocation,
            requested_amount=Decimal('25.00'),
            purpose='Anular aprobada',
            requested_date=TEST_DATE,
            actor=self.operator,
        )
        approved_for_annul = approve_expense_request(to_annul, actor=self.committee)
        annulled = annul_expense_request(
            approved_for_annul,
            reason='Anulación administrativa con motivo suficiente.',
            actor=self.admin,
        )
        annulled_display = get_expense_request_financial_display(
            ExpenseRequest.objects.prefetch_related('events').get(pk=annulled.pk)
        )
        self.assertEqual(annulled_display['released_amount'], Decimal('25.00'))
        self.assertTrue(annulled_display['show_released'])

        linked_source = create_expense_request(
            fund_allocation=self.allocation,
            requested_amount=Decimal('35.00'),
            purpose='Gasto enlazado anulado',
            requested_date=TEST_DATE,
            actor=self.operator,
        )
        linked_approved = approve_expense_request(linked_source, actor=self.committee)
        linked_fulfilled = _fulfill(
            linked_approved,
            amount=Decimal('35.00'),
            actor=self.admin,
            purpose_reason='Luego anulado',
        )
        annul_expense(
            linked_fulfilled.expense.pk,
            actor=self.admin,
            reason='Anulación del gasto enlazado con motivo suficiente.',
        )
        linked_display = get_expense_request_financial_display(
            ExpenseRequest.objects.select_related('expense').get(pk=linked_fulfilled.pk)
        )
        self.assertTrue(linked_display['has_linked_expense'])
        self.assertTrue(linked_display['linked_expense_is_annulled'])
        self.assertEqual(linked_display['executed_amount'], Decimal('35.00'))

    def test_selector_ownership_policy(self):
        own_ids = set(
            visible_expense_requests_for_user(self.operator).values_list('pk', flat=True)
        )
        self.assertEqual(own_ids, {self.own_request.pk})
        all_ids = set(
            visible_expense_requests_for_user(self.auditor).values_list('pk', flat=True)
        )
        self.assertEqual(all_ids, {self.own_request.pk, self.other_request.pk})

    def test_detail_exposes_decision_flags_for_committee_only(self):
        self.client.force_login(self.committee)
        committee = self.client.get(
            reverse('expense_request_detail', args=[self.own_request.pk])
        )
        self.assertTrue(committee.context['can_approve_expense_request'])
        self.assertTrue(committee.context['can_deny_expense_request'])
        self.assertFalse(committee.context['can_annul_expense_request'])
        self.assertEqual(committee.context['approval_requested_amount'], Decimal('40.00'))

        self.client.force_login(self.admin)
        admin = self.client.get(
            reverse('expense_request_detail', args=[self.own_request.pk])
        )
        self.assertFalse(admin.context['can_approve_expense_request'])
        self.assertFalse(admin.context['can_deny_expense_request'])
        self.assertTrue(admin.context['can_annul_expense_request'])
        self.assertNotIn('approval_requested_amount', admin.context)

    def test_detail_exposes_annul_flag_for_approved_reserved_admin_only(self):
        approved = approve_expense_request(self.own_request, actor=self.committee)
        self.client.force_login(self.admin)
        admin = self.client.get(
            reverse('expense_request_detail', args=[approved.pk])
        )
        self.assertTrue(admin.context['can_annul_expense_request'])
        self.assertFalse(admin.context['can_approve_expense_request'])

        self.client.force_login(self.committee)
        committee = self.client.get(
            reverse('expense_request_detail', args=[approved.pk])
        )
        self.assertFalse(committee.context['can_annul_expense_request'])
        self.assertFalse(committee.context['can_approve_expense_request'])
