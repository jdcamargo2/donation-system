"""TRANSVERSAL-1A: block closing financially open scopes."""

from decimal import Decimal
from pathlib import Path
from queue import Queue
from threading import Barrier, Thread
from unittest import skipUnless

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection, connections
from django.test import TestCase, TransactionTestCase
from django.urls import reverse

from apps.operations.expense_request_services import approve_expense_request
from apps.operations.models import (
    AuditLog,
    ExpenseRequest,
    ExpenseRequestEvent,
    FundAllocation,
    OperationalCodeSequence,
    Project,
    OPERATIONAL_CODE_PREFIXES,
)
from apps.operations.role_services import sync_operation_roles
from apps.operations.roles import ROLE_PROJECT_COMMITTEE, ROLE_SIGEDON_ADMIN
from apps.operations.selectors import (
    allocation_has_open_financial_work,
    project_has_open_financial_work,
)
from apps.operations.services import (
    InvalidStateTransitionError,
    annul_fund_allocation,
    create_fund_allocation,
    finish_fund_allocation,
    finish_project,
)
from apps.operations.tests.helpers import (
    TEST_DATE,
    create_allocation,
    create_approved_reserved_request,
    create_donation,
    create_expense_request,
    create_project,
    create_user,
)


POSTGRESQL_LOCKING_REQUIRED = 'Requires PostgreSQL row-level locking'
THREAD_TIMEOUT_SECONDS = 15
BARRIER_TIMEOUT_SECONDS = 10
VALID_ANNUL_REASON = 'Asignación anulada por cierre de alcance financiero documentado.'


class FinancialScopeClosureServiceTests(TestCase):
    def setUp(self):
        self.actor = create_user(username='scope-closure-actor')

    def test_open_financial_statuses_are_authoritative(self):
        self.assertEqual(
            ExpenseRequest.open_financial_statuses(),
            (
                ExpenseRequest.Status.PENDING_DECISION,
                ExpenseRequest.Status.APPROVED_RESERVED,
            ),
        )

    def test_finish_allocation_without_requests_succeeds(self):
        allocation = create_allocation()

        finished = finish_fund_allocation(allocation.pk, actor=self.actor)

        self.assertEqual(finished.status, FundAllocation.Status.FINISHED)
        self.assertEqual(finished.terminal_by, self.actor)
        self.assertEqual(finished.terminal_reason, 'Asignación finalizada.')
        self.assertEqual(
            AuditLog.objects.filter(
                action=AuditLog.Action.CLOSED,
                entity_id=str(allocation.pk),
                model_name='Asignación de fondos',
            ).count(),
            1,
        )
        self.assertFalse(
            ExpenseRequestEvent.objects.filter(
                expense_request__fund_allocation_id=allocation.pk
            ).exists()
        )

    def test_finish_allocation_allows_terminal_request_history(self):
        for index, status in enumerate(
            (
                ExpenseRequest.Status.DENIED,
                ExpenseRequest.Status.WITHDRAWN,
                ExpenseRequest.Status.ANNULLED,
            )
        ):
            with self.subTest(status=status):
                allocation = create_allocation(
                    donation=create_donation(code=f'DON-HIST-{index}'),
                    project=create_project(code=f'PRJ-HIST-{index}'),
                )
                extra = {}
                if status == ExpenseRequest.Status.ANNULLED:
                    extra = {
                        'terminal_reason': VALID_ANNUL_REASON,
                        'terminal_by': self.actor,
                        'terminal_at': allocation.created_at,
                    }
                elif status == ExpenseRequest.Status.DENIED:
                    extra = {
                        'decided_by': self.actor,
                        'decided_at': allocation.created_at,
                        'decision_note': 'Denegada en prueba.',
                    }
                else:
                    extra = {
                        'terminal_reason': 'Retiro documentado de solicitud.',
                        'terminal_by': self.actor,
                        'terminal_at': allocation.created_at,
                    }
                create_expense_request(
                    fund_allocation=allocation,
                    requested_by=self.actor,
                    status=status,
                    **extra,
                )
                finished = finish_fund_allocation(allocation.pk, actor=self.actor)
                self.assertEqual(finished.status, FundAllocation.Status.FINISHED)

    def test_finish_allocation_blocks_pending_decision(self):
        allocation = create_allocation()
        create_expense_request(fund_allocation=allocation, requested_by=self.actor)

        with self.assertRaises(InvalidStateTransitionError) as raised:
            finish_fund_allocation(allocation.pk, actor=self.actor)

        self.assertIn('pendientes de decisión', raised.exception.messages[0])
        allocation.refresh_from_db()
        self.assertEqual(allocation.status, FundAllocation.Status.ACTIVE)
        self.assertFalse(
            AuditLog.objects.filter(
                action=AuditLog.Action.CLOSED,
                entity_id=str(allocation.pk),
            ).exists()
        )

    def test_finish_allocation_blocks_approved_reserved(self):
        allocation = create_allocation()
        create_approved_reserved_request(
            fund_allocation=allocation,
            requested_by=self.actor,
            decided_by=self.actor,
        )

        with self.assertRaises(InvalidStateTransitionError) as raised:
            finish_fund_allocation(allocation.pk, actor=self.actor)

        self.assertIn('pendientes de registrar gasto', raised.exception.messages[0])
        allocation.refresh_from_db()
        self.assertEqual(allocation.status, FundAllocation.Status.ACTIVE)
        self.assertTrue(allocation_has_open_financial_work(allocation))

    def test_finish_allocation_blocks_mixed_open_requests(self):
        allocation = create_allocation()
        create_expense_request(fund_allocation=allocation, requested_by=self.actor)
        create_approved_reserved_request(
            fund_allocation=allocation,
            requested_by=self.actor,
            decided_by=self.actor,
            requested_amount=Decimal('10.00'),
        )

        with self.assertRaises(InvalidStateTransitionError) as raised:
            finish_fund_allocation(allocation.pk, actor=self.actor)

        self.assertIn('pendientes o reservas activas', raised.exception.messages[0])

    def test_finished_and_annulled_cannot_finish_again(self):
        allocation = create_allocation()
        finish_fund_allocation(allocation.pk, actor=self.actor)
        with self.assertRaises(InvalidStateTransitionError):
            finish_fund_allocation(allocation.pk, actor=self.actor)

        other = create_allocation(
            donation=create_donation(code='DON-ANNUL-FINISH'),
            project=create_project(code='PRJ-ANNUL-FINISH'),
        )
        annul_fund_allocation(other.pk, actor=self.actor, reason=VALID_ANNUL_REASON)
        with self.assertRaises(InvalidStateTransitionError):
            finish_fund_allocation(other.pk, actor=self.actor)

    def test_finish_project_without_allocations_succeeds(self):
        project = create_project(code='PRJ-EMPTY-CLOSE')

        closed = finish_project(project.pk, actor=self.actor)

        self.assertEqual(closed.status, Project.Status.CLOSED)
        self.assertEqual(
            AuditLog.objects.filter(
                action=AuditLog.Action.CLOSED,
                entity_id=str(project.pk),
            ).count(),
            1,
        )

    def test_finish_project_blocks_active_allocation(self):
        project = create_project(code='PRJ-ACTIVE-ALLOC')
        create_allocation(project=project)

        with self.assertRaises(InvalidStateTransitionError) as raised:
            finish_project(project.pk, actor=self.actor)

        self.assertIn('asignaciones activas', raised.exception.messages[0])
        project.refresh_from_db()
        self.assertEqual(project.status, Project.Status.ACTIVE)
        self.assertFalse(
            AuditLog.objects.filter(
                action=AuditLog.Action.CLOSED,
                entity_id=str(project.pk),
            ).exists()
        )

    def test_finish_project_allows_finished_or_annulled_allocations(self):
        finished_project = create_project(code='PRJ-ONLY-FINISHED')
        finished_allocation = create_allocation(
            donation=create_donation(code='DON-ONLY-FINISHED'),
            project=finished_project,
        )
        finish_fund_allocation(finished_allocation.pk, actor=self.actor)
        closed = finish_project(finished_project.pk, actor=self.actor)
        self.assertEqual(closed.status, Project.Status.CLOSED)

        annulled_project = create_project(code='PRJ-ONLY-ANNULLED')
        annulled_allocation = create_allocation(
            donation=create_donation(code='DON-ONLY-ANNULLED'),
            project=annulled_project,
        )
        annul_fund_allocation(
            annulled_allocation.pk,
            actor=self.actor,
            reason=VALID_ANNUL_REASON,
        )
        closed_annulled = finish_project(annulled_project.pk, actor=self.actor)
        self.assertEqual(closed_annulled.status, Project.Status.CLOSED)

    def test_finish_project_blocks_open_request_even_if_allocation_finished_in_db(self):
        # Guard path for open requests under the project scope.
        project = create_project(code='PRJ-OPEN-REQ')
        allocation = create_allocation(
            donation=create_donation(code='DON-OPEN-REQ'),
            project=project,
        )
        create_expense_request(fund_allocation=allocation, requested_by=self.actor)
        self.assertTrue(project_has_open_financial_work(project))

        with self.assertRaises(InvalidStateTransitionError) as raised:
            finish_project(project.pk, actor=self.actor)
        self.assertIn('asignaciones activas', raised.exception.messages[0])

    def test_finish_project_allows_historical_terminal_requests(self):
        project = create_project(code='PRJ-TERM-REQ')
        allocation = create_allocation(
            donation=create_donation(code='DON-TERM-REQ'),
            project=project,
        )
        create_expense_request(
            fund_allocation=allocation,
            requested_by=self.actor,
            status=ExpenseRequest.Status.DENIED,
            decided_by=self.actor,
            decided_at=allocation.created_at,
            decision_note='Denegada históricamente.',
        )
        finish_fund_allocation(allocation.pk, actor=self.actor)

        closed = finish_project(project.pk, actor=self.actor)
        self.assertEqual(closed.status, Project.Status.CLOSED)


class FinancialScopeClosureViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username='scope-closure-view',
            password='pass-12345',
        )
        self.client.force_login(self.user)

    def test_direct_post_cannot_finish_allocation_with_pending_decision(self):
        allocation = create_allocation()
        create_expense_request(fund_allocation=allocation, requested_by=self.user)
        url = reverse('allocation_finish', args=[allocation.pk])

        response = self.client.post(url)

        self.assertEqual(response.status_code, 200)
        allocation.refresh_from_db()
        self.assertEqual(allocation.status, FundAllocation.Status.ACTIVE)
        self.assertFalse(
            AuditLog.objects.filter(
                action=AuditLog.Action.CLOSED,
                entity_id=str(allocation.pk),
            ).exists()
        )

    def test_direct_post_cannot_finish_allocation_with_approved_reserved(self):
        allocation = create_allocation()
        create_approved_reserved_request(
            fund_allocation=allocation,
            requested_by=self.user,
            decided_by=self.user,
        )

        response = self.client.post(reverse('allocation_finish', args=[allocation.pk]))

        self.assertEqual(response.status_code, 200)
        allocation.refresh_from_db()
        self.assertEqual(allocation.status, FundAllocation.Status.ACTIVE)

    def test_direct_post_finishes_allocation_when_clean(self):
        allocation = create_allocation()
        create_expense_request(
            fund_allocation=allocation,
            requested_by=self.user,
            status=ExpenseRequest.Status.WITHDRAWN,
            terminal_reason='Retiro documentado de solicitud.',
            terminal_by=self.user,
            terminal_at=allocation.created_at,
        )

        response = self.client.post(reverse('allocation_finish', args=[allocation.pk]))

        self.assertRedirects(response, reverse('allocation_detail', args=[allocation.pk]))
        allocation.refresh_from_db()
        self.assertEqual(allocation.status, FundAllocation.Status.FINISHED)
        self.assertEqual(
            AuditLog.objects.filter(
                action=AuditLog.Action.CLOSED,
                entity_id=str(allocation.pk),
            ).count(),
            1,
        )

    def test_direct_post_cannot_close_project_with_active_allocation(self):
        project = create_project(code='PRJ-POST-ACTIVE')
        create_allocation(project=project)

        response = self.client.post(reverse('project_finish', args=[project.pk]))

        self.assertEqual(response.status_code, 200)
        project.refresh_from_db()
        self.assertEqual(project.status, Project.Status.ACTIVE)
        self.assertFalse(
            AuditLog.objects.filter(
                action=AuditLog.Action.CLOSED,
                entity_id=str(project.pk),
            ).exists()
        )

    def test_direct_post_closes_project_when_allocations_resolved(self):
        project = create_project(code='PRJ-POST-OK')
        allocation = create_allocation(
            donation=create_donation(code='DON-POST-OK'),
            project=project,
        )
        finish_fund_allocation(allocation.pk, actor=self.user)

        response = self.client.post(reverse('project_finish', args=[project.pk]))

        self.assertRedirects(response, reverse('project_detail', args=[project.pk]))
        project.refresh_from_db()
        self.assertEqual(project.status, Project.Status.CLOSED)
        self.assertEqual(
            AuditLog.objects.filter(
                action=AuditLog.Action.CLOSED,
                entity_id=str(project.pk),
            ).count(),
            1,
        )

    def test_allocation_detail_shows_finish_when_valid(self):
        allocation = create_allocation()
        response = self.client.get(reverse('allocation_detail', args=[allocation.pk]))

        self.assertTrue(response.context['can_finish'])
        self.assertFalse(response.context['show_finish_guidance'])
        self.assertContains(response, 'Finalizar asignación')
        self.assertContains(response, reverse('allocation_finish', args=[allocation.pk]))
        self.assertNotIn(
            'status_transitions',
            Path('templates/web/allocation_detail.html').read_text(encoding='utf-8'),
        )

    def test_allocation_detail_hides_finish_and_shows_guidance_when_blocked(self):
        allocation = create_allocation()
        create_expense_request(fund_allocation=allocation, requested_by=self.user)
        response = self.client.get(reverse('allocation_detail', args=[allocation.pk]))

        self.assertFalse(response.context['can_finish'])
        self.assertTrue(response.context['show_finish_guidance'])
        self.assertNotContains(response, reverse('allocation_finish', args=[allocation.pk]))
        self.assertContains(
            response,
            'Para finalizar la asignación, resuelve las solicitudes de gasto pendientes',
        )

    def test_project_detail_hides_finish_and_shows_guidance_with_active_allocation(self):
        project = create_project(code='PRJ-UI-BLOCK')
        create_allocation(project=project)
        response = self.client.get(reverse('project_detail', args=[project.pk]))

        self.assertFalse(response.context['can_finish'])
        self.assertTrue(response.context['show_finish_guidance'])
        self.assertNotContains(response, 'Terminar proyecto')
        self.assertContains(
            response,
            'Para cerrar el proyecto, finaliza o anula sus asignaciones',
        )

    def test_project_detail_shows_finish_when_scope_is_clear(self):
        project = create_project(code='PRJ-UI-OK')
        response = self.client.get(reverse('project_detail', args=[project.pk]))

        self.assertTrue(response.context['can_finish'])
        self.assertContains(response, 'Terminar proyecto')

    def test_unauthorized_user_sees_no_finish_actions(self):
        allocation = create_allocation()
        project = allocation.project
        viewer = get_user_model().objects.create_user(
            username='scope-viewer',
            password='pass-12345',
        )
        viewer.user_permissions.add(
            Permission.objects.get(codename='view_fundallocation'),
            Permission.objects.get(codename='view_project'),
        )
        self.client.force_login(viewer)

        allocation_response = self.client.get(
            reverse('allocation_detail', args=[allocation.pk])
        )
        project_response = self.client.get(reverse('project_detail', args=[project.pk]))

        self.assertFalse(allocation_response.context['can_finish'])
        self.assertFalse(allocation_response.context['show_finish_guidance'])
        self.assertNotContains(allocation_response, 'Finalizar asignación')
        self.assertFalse(project_response.context['can_finish'])
        self.assertFalse(project_response.context['show_finish_guidance'])
        self.assertNotContains(project_response, 'Terminar proyecto')

    def test_superuser_still_blocked_by_domain_guard_on_post(self):
        allocation = create_allocation()
        create_approved_reserved_request(
            fund_allocation=allocation,
            requested_by=self.user,
            decided_by=self.user,
        )
        response = self.client.post(reverse('allocation_finish', args=[allocation.pk]))
        self.assertEqual(response.status_code, 200)
        allocation.refresh_from_db()
        self.assertEqual(allocation.status, FundAllocation.Status.ACTIVE)


@skipUnless(connection.vendor == 'postgresql', POSTGRESQL_LOCKING_REQUIRED)
class FinancialScopeClosureConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        super().setUp()
        OperationalCodeSequence.objects.bulk_create(
            [
                OperationalCodeSequence(namespace=namespace, prefix=prefix, next_value=1)
                for namespace, prefix in OPERATIONAL_CODE_PREFIXES.items()
            ],
            ignore_conflicts=True,
        )
        sync_operation_roles()
        self.admin = self._user('scope-admin', ROLE_SIGEDON_ADMIN)
        self.committee = self._user('scope-committee', ROLE_PROJECT_COMMITTEE)

    def _user(self, username, role_name):
        user = get_user_model().objects.create_user(username=username, password='pass-12345')
        user.groups.add(Group.objects.get(name=role_name))
        return user

    def run_concurrently(self, operations):
        barrier = Barrier(len(operations))
        results = Queue()

        def run_operation(operation):
            close_old_connections()
            try:
                barrier.wait(timeout=BARRIER_TIMEOUT_SECONDS)
                value = operation()
                results.put(('success', value))
            except BaseException as exc:
                results.put(('error', exc))
            finally:
                connections.close_all()

        threads = [Thread(target=run_operation, args=(operation,)) for operation in operations]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=THREAD_TIMEOUT_SECONDS)
        alive = [thread.name for thread in threads if thread.is_alive()]
        self.assertFalse(alive, f'Concurrent workers timed out: {alive}')
        return [results.get_nowait() for _ in threads]

    def assert_one_success_one_domain_error(self, results):
        self.assertEqual([outcome for outcome, _ in results].count('success'), 1, results)
        errors = [value for outcome, value in results if outcome == 'error']
        self.assertEqual(len(errors), 1, results)
        self.assertIsInstance(errors[0], ValidationError)

    def test_finish_allocation_vs_approval_race(self):
        allocation = create_allocation(
            donation=create_donation(amount=Decimal('100.00')),
            amount=Decimal('60.00'),
        )
        pending = create_expense_request(
            fund_allocation=allocation,
            requested_by=self.admin,
            requested_amount=Decimal('20.00'),
        )
        allocation_id = allocation.pk
        request_id = pending.pk
        admin = self.admin
        committee = self.committee

        def finish():
            return finish_fund_allocation(allocation_id, actor=admin)

        def approve():
            return approve_expense_request(
                ExpenseRequest.objects.get(pk=request_id),
                actor=committee,
            )

        results = self.run_concurrently([finish, approve])
        self.assert_one_success_one_domain_error(results)

        allocation.refresh_from_db()
        pending.refresh_from_db()
        finished_and_reserved = (
            allocation.status == FundAllocation.Status.FINISHED
            and pending.status == ExpenseRequest.Status.APPROVED_RESERVED
        )
        self.assertFalse(finished_and_reserved)

    def test_finish_project_vs_approval_race(self):
        project = create_project(code='PRJ-RACE-CLOSE')
        allocation = create_allocation(
            donation=create_donation(code='DON-RACE-CLOSE', amount=Decimal('100.00')),
            project=project,
            amount=Decimal('60.00'),
        )
        # Project close requires no ACTIVE allocations; finish allocation first path is
        # not the race under test. Race: close with finished allocation vs approving under
        # a still-ACTIVE allocation before close resolves children.
        pending = create_expense_request(
            fund_allocation=allocation,
            requested_by=self.admin,
            requested_amount=Decimal('15.00'),
        )
        project_id = project.pk
        request_id = pending.pk
        admin = self.admin
        committee = self.committee

        def close_project():
            return finish_project(project_id, actor=admin)

        def approve():
            return approve_expense_request(
                ExpenseRequest.objects.get(pk=request_id),
                actor=committee,
            )

        results = self.run_concurrently([close_project, approve])
        # Close must fail while allocation is ACTIVE; approval may succeed.
        outcomes = {outcome for outcome, _ in results}
        self.assertIn('error', outcomes)
        project.refresh_from_db()
        pending.refresh_from_db()
        impossible = (
            project.status == Project.Status.CLOSED
            and pending.status == ExpenseRequest.Status.APPROVED_RESERVED
        )
        self.assertFalse(impossible)
        if project.status == Project.Status.CLOSED:
            self.assertNotEqual(pending.status, ExpenseRequest.Status.APPROVED_RESERVED)

    def test_finish_project_vs_allocation_creation_race(self):
        project = create_project(code='PRJ-RACE-ALLOC')
        donation = create_donation(code='DON-RACE-ALLOC', amount=Decimal('100.00'))
        project_id = project.pk
        donation_id = donation.pk
        admin = self.admin

        def close_project():
            return finish_project(project_id, actor=admin)

        def create_allocation_op():
            return create_fund_allocation(
                donation=type(donation).objects.get(pk=donation_id),
                project=Project.objects.get(pk=project_id),
                budget_category='health_psychosocial',
                amount=Decimal('25.00'),
                responsible_person='',
                allocation_date=TEST_DATE,
                status=FundAllocation.Status.ACTIVE,
                notes='',
            )

        results = self.run_concurrently([close_project, create_allocation_op])
        self.assert_one_success_one_domain_error(results)

        project.refresh_from_db()
        active_on_closed = (
            project.status == Project.Status.CLOSED
            and FundAllocation.objects.filter(
                project_id=project.pk,
                status=FundAllocation.Status.ACTIVE,
            ).exists()
        )
        self.assertFalse(active_on_closed)
