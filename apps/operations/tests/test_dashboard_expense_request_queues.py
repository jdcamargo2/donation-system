"""DASH-FIN2: permission-scoped Expense Request queues on the financial dashboard."""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import TestCase
from django.urls import reverse

from apps.operations.expense_request_services import (
    annul_expense_request,
    approve_expense_request,
    create_expense_request,
    deny_expense_request,
    fulfill_expense_request,
    withdraw_expense_request,
)
from apps.operations.models import ExpenseRequest
from apps.operations.role_services import sync_operation_roles
from apps.operations.roles import (
    ROLE_EXTERNAL_AUDITOR,
    ROLE_FIELD_OPERATOR,
    ROLE_PROJECT_COMMITTEE,
    ROLE_SIGEDON_ADMIN,
)
from apps.operations.services import (
    DASHBOARD_EXPENSE_REQUEST_QUEUE_PREVIEW_LIMIT,
    get_dashboard_expense_request_queues,
    get_dashboard_metrics,
)
from apps.operations.tests.helpers import (
    TEST_DATE,
    create_allocation,
    create_support_upload,
)
from apps.operations.tests.test_permissions import create_user_with_permissions


class DashboardExpenseRequestQueueTests(TestCase):
    def setUp(self):
        sync_operation_roles()
        self.allocation = create_allocation(amount=Decimal('500.00'))
        self.admin = self._role_user('dash-fin2-admin', ROLE_SIGEDON_ADMIN)
        self.operator = self._role_user('dash-fin2-operator', ROLE_FIELD_OPERATOR)
        self.other_operator = self._role_user(
            'dash-fin2-other-operator',
            ROLE_FIELD_OPERATOR,
        )
        self.committee = self._role_user('dash-fin2-committee', ROLE_PROJECT_COMMITTEE)
        self.auditor = self._role_user('dash-fin2-auditor', ROLE_EXTERNAL_AUDITOR)
        self.superuser = get_user_model().objects.create_superuser(
            username='dash-fin2-super',
            email='dash-fin2-super@example.com',
            password='pass-12345',
        )

    def _role_user(self, username, role_name):
        user = get_user_model().objects.create_user(
            username=username,
            password='pass-12345',
        )
        user.groups.add(Group.objects.get(name=role_name))
        return user

    def _pending(self, *, actor=None, amount=Decimal('25.00'), purpose='Pendiente DASH'):
        return create_expense_request(
            fund_allocation=self.allocation,
            requested_amount=amount,
            purpose=purpose,
            requested_date=TEST_DATE,
            actor=actor or self.operator,
        )

    def _approved(self, *, actor=None, amount=Decimal('30.00'), purpose='Aprobada DASH'):
        pending = self._pending(actor=actor, amount=amount, purpose=purpose)
        return approve_expense_request(pending, actor=self.committee)

    def _queue_map(self, user):
        return {
            queue['key']: queue
            for queue in get_dashboard_expense_request_queues(user=user)
        }

    def test_superuser_sees_fulfillment_and_decision_queues_with_actions(self):
        pending = self._pending(purpose='Pendiente super')
        approved = self._approved(purpose='Aprobada super')
        queues = self._queue_map(self.superuser)

        self.assertIn('fulfillment', queues)
        self.assertIn('decision', queues)
        self.assertNotIn('personal', queues)
        self.assertNotIn('tracking', queues)

        fulfillment_codes = {item['code'] for item in queues['fulfillment']['items']}
        decision_codes = {item['code'] for item in queues['decision']['items']}
        self.assertEqual(fulfillment_codes & decision_codes, set())
        self.assertIn(approved.code, fulfillment_codes)
        self.assertIn(pending.code, decision_codes)

        fulfill_item = queues['fulfillment']['items'][0]
        self.assertEqual(fulfill_item['action_label'], 'Registrar gasto')
        self.assertEqual(
            fulfill_item['action_url'],
            reverse('expense_request_fulfill', args=[approved.pk]),
        )
        decide_item = next(
            item for item in queues['decision']['items'] if item['code'] == pending.code
        )
        self.assertEqual(decide_item['action_label'], 'Revisar solicitud')
        self.assertEqual(
            decide_item['action_url'],
            reverse('expense_request_detail', args=[pending.pk]),
        )

        self.client.force_login(self.superuser)
        for url in (fulfill_item['action_url'], decide_item['action_url']):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_admin_sees_fulfillment_without_decision(self):
        self._pending()
        approved = self._approved()
        queues = self._queue_map(self.admin)

        self.assertIn('fulfillment', queues)
        self.assertNotIn('decision', queues)
        self.assertEqual(queues['fulfillment']['total_count'], 1)
        self.assertEqual(queues['fulfillment']['items'][0]['code'], approved.code)
        self.assertEqual(
            queues['fulfillment']['items'][0]['action_label'],
            'Registrar gasto',
        )

    def test_admin_sees_decision_when_decide_permission_granted(self):
        user = create_user_with_permissions(
            'dash-fin2-admin-both',
            'view_expenserequest',
            'fulfill_expenserequest',
            'decide_expenserequest',
        )
        self._pending()
        self._approved()
        keys = list(self._queue_map(user))
        self.assertEqual(keys, ['fulfillment', 'decision'])

    def test_committee_sees_only_decision_queue(self):
        pending = self._pending()
        approved = self._approved()
        queues = self._queue_map(self.committee)

        self.assertEqual(list(queues), ['decision'])
        codes = {item['code'] for item in queues['decision']['items']}
        self.assertIn(pending.code, codes)
        self.assertNotIn(approved.code, codes)
        self.assertEqual(
            queues['decision']['items'][0]['action_label'],
            'Revisar solicitud',
        )

        self.client.force_login(self.committee)
        response = self.client.get(reverse('dashboard'))
        self.assertContains(response, 'Solicitudes pendientes de decisión')
        self.assertContains(response, 'Revisar solicitud')
        self.assertNotContains(response, 'Aprobadas pendientes de registrar gasto')
        self.assertNotContains(response, approved.code)
        self.assertNotContains(response, 'Registrar gasto')

    def test_operator_sees_only_own_active_requests(self):
        own_pending = self._pending(actor=self.operator, purpose='Propia pendiente')
        own_approved = self._approved(actor=self.operator, purpose='Propia aprobada')
        other = self._pending(actor=self.other_operator, purpose='Ajena')
        queues = self._queue_map(self.operator)

        self.assertEqual(list(queues), ['personal'])
        codes = {item['code'] for item in queues['personal']['items']}
        self.assertEqual(codes, {own_pending.code, own_approved.code})
        self.assertNotIn(other.code, codes)
        own_by_code = {
            own_pending.code: own_pending,
            own_approved.code: own_approved,
        }
        for item in queues['personal']['items']:
            self.assertEqual(item['action_label'], 'Ver solicitud')
            expected_url = reverse(
                'expense_request_detail',
                args=[own_by_code[item['code']].pk],
            )
            self.assertEqual(item['action_url'], expected_url)
            self.assertTrue(
                item['action_url'].startswith(reverse('expense_request_list')),
            )
            self.assertEqual(item['action_style'], 'outline')

        self.client.force_login(self.operator)
        response = self.client.get(reverse('dashboard'))
        html = response.content.decode()
        self.assertContains(response, 'Mis solicitudes activas')
        self.assertContains(response, own_pending.code)
        self.assertNotContains(response, other.code)
        self.assertNotContains(response, 'Aprobadas pendientes de registrar gasto')
        self.assertNotContains(response, 'Solicitudes pendientes de decisión')
        self.assertNotContains(response, 'Registrar gasto')
        self.assertNotContains(response, 'Revisar solicitud')
        self.assertNotIn('pending_decision', html)
        self.assertNotIn('approved_reserved', html)

    def test_auditor_sees_read_only_tracking_without_mutation_labels(self):
        pending = self._pending()
        approved = self._approved()
        queues = self._queue_map(self.auditor)

        self.assertEqual(list(queues), ['tracking'])
        codes = {item['code'] for item in queues['tracking']['items']}
        self.assertEqual(codes, {pending.code, approved.code})
        for item in queues['tracking']['items']:
            self.assertEqual(item['action_label'], 'Ver solicitud')
            self.assertEqual(item['action_style'], 'outline')

        self.client.force_login(self.auditor)
        response = self.client.get(reverse('dashboard'))
        self.assertContains(response, 'Solicitudes de gasto en seguimiento')
        self.assertContains(response, 'Ver solicitud')
        self.assertNotContains(response, 'Registrar gasto')
        self.assertNotContains(response, 'Revisar solicitud')
        self.assertNotContains(response, 'Aprobadas pendientes de registrar gasto')
        self.assertNotContains(response, 'Solicitudes pendientes de decisión')

    def test_auditor_without_view_permission_omits_section(self):
        user = create_user_with_permissions('dash-fin2-auditor-no-er', 'view_donation')
        self._pending()
        metrics = get_dashboard_metrics(user=user)
        self.assertEqual(metrics['expense_request_queues'], [])
        self.client.force_login(user)
        response = self.client.get(reverse('dashboard'))
        self.assertNotContains(response, 'Solicitudes que requieren atención')
        self.assertNotContains(response, 'Solicitudes de gasto en seguimiento')
        self.assertNotContains(response, 'Mis solicitudes activas')

    def test_partial_permissions_drive_queues_not_role_label(self):
        fulfill_only = create_user_with_permissions(
            'dash-fin2-fulfill-only',
            'view_expenserequest',
            'fulfill_expenserequest',
        )
        decide_only = create_user_with_permissions(
            'dash-fin2-decide-only',
            'view_expenserequest',
            'decide_expenserequest',
        )
        pending = self._pending()
        approved = self._approved()

        fulfill_queues = self._queue_map(fulfill_only)
        decide_queues = self._queue_map(decide_only)
        self.assertEqual(list(fulfill_queues), ['fulfillment'])
        self.assertEqual(list(decide_queues), ['decision'])
        self.assertEqual(fulfill_queues['fulfillment']['items'][0]['code'], approved.code)
        self.assertEqual(decide_queues['decision']['items'][0]['code'], pending.code)

        self.client.force_login(fulfill_only)
        fulfill_response = self.client.get(reverse('dashboard'))
        self.assertContains(fulfill_response, approved.code)
        self.assertNotContains(fulfill_response, pending.code)
        action_url = fulfill_queues['fulfillment']['items'][0]['action_url']
        self.assertEqual(self.client.get(action_url).status_code, 200)

        self.client.force_login(decide_only)
        decide_response = self.client.get(reverse('dashboard'))
        self.assertContains(decide_response, pending.code)
        self.assertNotContains(decide_response, approved.code)
        decide_url = decide_queues['decision']['items'][0]['action_url']
        self.assertEqual(self.client.get(decide_url).status_code, 200)

    def test_workflow_status_membership_and_fulfillment_exclusion(self):
        pending = self._pending(purpose='Status pending')
        approved = self._approved(purpose='Status approved')
        fulfilled_source = self._approved(
            actor=self.other_operator,
            amount=Decimal('12.00'),
            purpose='Status fulfilled',
        )
        fulfill_expense_request(
            fulfilled_source,
            expense_date=TEST_DATE,
            amount=Decimal('12.00'),
            reason='Cumplimiento DASH',
            provider_or_recipient='Proveedor',
            payment_method='bank_transfer',
            description='Gasto desde cola',
            support_file=create_support_upload(),
            support_title='Factura DASH',
            category='food',
            actor=self.admin,
        )
        denied = deny_expense_request(
            self._pending(actor=self.other_operator, purpose='Denegada DASH'),
            decision_note='Fuera de alcance',
            actor=self.committee,
        )
        withdrawn = withdraw_expense_request(
            self._pending(actor=self.operator, purpose='Retirada DASH'),
            reason='Ya no se requiere',
            actor=self.operator,
        )
        annulled = annul_expense_request(
            self._pending(actor=self.other_operator, purpose='Anulada DASH'),
            reason='Anulación de prueba DASH-FIN2',
            actor=self.admin,
        )

        queues = self._queue_map(self.superuser)
        fulfillment_codes = {item['code'] for item in queues['fulfillment']['items']}
        decision_codes = {item['code'] for item in queues['decision']['items']}
        self.assertIn(approved.code, fulfillment_codes)
        self.assertNotIn(fulfilled_source.code, fulfillment_codes)
        self.assertIn(pending.code, decision_codes)
        for code in (
            fulfilled_source.code,
            denied.code,
            withdrawn.code,
            annulled.code,
        ):
            self.assertNotIn(code, fulfillment_codes)
            self.assertNotIn(code, decision_codes)

        amount = queues['fulfillment']['items'][0]['amount']
        self.assertIsInstance(amount, Decimal)
        self.assertEqual(amount, approved.requested_amount)
        self.assertEqual(
            queues['decision']['items'][0]['status_label'],
            pending.get_status_display(),
        )

    def test_preview_limit_preserves_accurate_count_and_view_all(self):
        for index in range(DASHBOARD_EXPENSE_REQUEST_QUEUE_PREVIEW_LIMIT + 2):
            self._pending(
                actor=self.operator if index % 2 == 0 else self.other_operator,
                amount=Decimal('10.00') + Decimal(index),
                purpose=f'Pendiente cola {index}',
            )
        queues = self._queue_map(self.committee)
        decision = queues['decision']
        self.assertEqual(
            decision['displayed_count'],
            DASHBOARD_EXPENSE_REQUEST_QUEUE_PREVIEW_LIMIT,
        )
        self.assertEqual(
            decision['total_count'],
            DASHBOARD_EXPENSE_REQUEST_QUEUE_PREVIEW_LIMIT + 2,
        )
        self.assertTrue(decision['show_view_all'])
        self.assertIn('status=pending_decision', decision['list_url'])

        self.client.force_login(self.committee)
        response = self.client.get(reverse('dashboard'))
        self.assertContains(response, 'Ver todas')
        self.assertContains(response, str(decision['total_count']))

    def test_positive_empty_state_for_authorized_actionable_queues(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse('dashboard'))
        self.assertContains(response, 'Solicitudes que requieren atención')
        self.assertContains(
            response,
            'No hay solicitudes de gasto que requieran tu atención en este momento.',
        )
        self.assertNotContains(response, 'Aprobadas pendientes de registrar gasto')

    def test_dashboard_placement_between_ratios_and_recent_activity(self):
        self._approved()
        self.admin.user_permissions.add(
            *Permission.objects.filter(
                content_type__app_label='operations',
                codename__in={
                    'view_donation',
                    'view_fundallocation',
                    'view_expense',
                    'view_auditlog',
                },
            )
        )
        # Admin already has financial view via role; ensure section order.
        self.client.force_login(self.admin)
        response = self.client.get(reverse('dashboard'))
        html = response.content.decode()
        ratios_pos = html.find('Ejecución financiera')
        queues_pos = html.find('Solicitudes que requieren atención')
        projects_pos = html.find('Estado financiero por proyecto')
        activity_pos = html.find('Actividad reciente')
        self.assertNotEqual(queues_pos, -1)
        self.assertLess(ratios_pos, queues_pos)
        self.assertLess(queues_pos, projects_pos)
        self.assertLess(projects_pos, activity_pos)
        self.assertNotContains(response, 'Accesos rápidos')
        self.assertNotContains(response, 'ops-action-panel')
        self.assertEqual(html.count('<h1'), 1)
        self.assertContains(response, 'ops-expense-request-queue-row')
        self.assertContains(response, 'list-unstyled')
        self.assertContains(response, 'Registrar gasto')
        # Raw status codes must not appear in row badges.
        self.assertNotRegex(
            html,
            r'ops-status-badge">pending_decision|ops-status-badge">approved_reserved',
        )

    def test_fulfillment_ordering_oldest_reservation_first(self):
        older = self._approved(amount=Decimal('11.00'), purpose='Más antigua')
        newer = self._approved(
            actor=self.other_operator,
            amount=Decimal('12.00'),
            purpose='Más reciente',
        )
        ExpenseRequest.objects.filter(pk=older.pk).update(
            reserved_at=older.reserved_at.replace(year=2025),
        )
        ExpenseRequest.objects.filter(pk=newer.pk).update(
            reserved_at=newer.reserved_at.replace(year=2026),
        )
        items = self._queue_map(self.admin)['fulfillment']['items']
        self.assertEqual([item['code'] for item in items], [older.code, newer.code])
