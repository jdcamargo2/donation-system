"""PostgreSQL concurrency tests for ExpenseRequest approval and reservation (ER2C)."""

from decimal import Decimal
from queue import Queue
from threading import Barrier, Thread
from unittest import skipUnless

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import close_old_connections, connection, connections
from django.test import TransactionTestCase

from apps.operations.expense_request_services import (
    ExpenseRequestAlreadyDecidedError,
    ExpenseRequestBalanceError,
    ExpenseRequestStateError,
    approve_expense_request,
    create_expense_request,
    update_expense_request,
)
from apps.operations.models import (
    AuditLog,
    Expense,
    ExpenseRequest,
    ExpenseRequestEvent,
    OperationalCodeSequence,
    OPERATIONAL_CODE_PREFIXES,
    ZERO_MONEY,
)
from apps.operations.role_services import sync_operation_roles
from apps.operations.roles import ROLE_FIELD_OPERATOR, ROLE_PROJECT_COMMITTEE, ROLE_SIGEDON_ADMIN
from apps.operations.services import create_expense
from apps.operations.tests.helpers import TEST_DATE, create_allocation, create_donation, create_project


POSTGRESQL_LOCKING_REQUIRED = 'Requires PostgreSQL row-level locking'
THREAD_TIMEOUT_SECONDS = 15
BARRIER_TIMEOUT_SECONDS = 10


@skipUnless(connection.vendor == 'postgresql', POSTGRESQL_LOCKING_REQUIRED)
class ExpenseRequestConcurrencyTests(TransactionTestCase):
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
        self.admin = self._user('er2c-admin', ROLE_SIGEDON_ADMIN)
        self.operator = self._user('er2c-operator', ROLE_FIELD_OPERATOR)
        self.committee_a = self._user('er2c-committee-a', ROLE_PROJECT_COMMITTEE)
        self.committee_b = self._user('er2c-committee-b', ROLE_PROJECT_COMMITTEE)

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

    def assert_one_success_one_domain_error(self, results, error_types):
        self.assertEqual([outcome for outcome, _ in results].count('success'), 1, results)
        errors = [value for outcome, value in results if outcome == 'error']
        self.assertEqual(len(errors), 1, results)
        self.assertIsInstance(errors[0], error_types)

    def test_two_approvals_against_shared_insufficient_balance(self):
        allocation = create_allocation(amount=Decimal('1000.00'))
        request_a = create_expense_request(
            fund_allocation=allocation,
            requested_amount=Decimal('700.00'),
            purpose='Solicitud concurrente A de fondos',
            requested_date=TEST_DATE,
            actor=self.admin,
        )
        request_b = create_expense_request(
            fund_allocation=allocation,
            requested_amount=Decimal('700.00'),
            purpose='Solicitud concurrente B de fondos',
            requested_date=TEST_DATE,
            actor=self.operator,
        )

        def approve(request_id, actor_id):
            actor = get_user_model().objects.get(pk=actor_id)
            request = ExpenseRequest.objects.get(pk=request_id)
            return approve_expense_request(request, actor=actor).pk

        results = self.run_concurrently(
            [
                lambda: approve(request_a.pk, self.committee_a.pk),
                lambda: approve(request_b.pk, self.committee_b.pk),
            ]
        )

        self.assert_one_success_one_domain_error(
            results,
            (ExpenseRequestBalanceError, ValidationError),
        )
        approved = ExpenseRequest.objects.filter(
            status=ExpenseRequest.Status.APPROVED_RESERVED
        )
        pending = ExpenseRequest.objects.filter(
            status=ExpenseRequest.Status.PENDING_DECISION
        )
        self.assertEqual(approved.count(), 1)
        self.assertEqual(pending.count(), 1)
        self.assertEqual(approved.get().reserved_amount, Decimal('700.00'))
        allocation.refresh_from_db()
        self.assertEqual(allocation.reserved_amount, Decimal('700.00'))
        self.assertEqual(allocation.available_balance, Decimal('300.00'))
        self.assertEqual(
            ExpenseRequestEvent.objects.filter(
                event_type=ExpenseRequestEvent.EventType.APPROVED
            ).count(),
            1,
        )
        self.assertEqual(
            ExpenseRequestEvent.objects.filter(
                event_type=ExpenseRequestEvent.EventType.RESERVATION_CREATED
            ).count(),
            1,
        )
        self.assertEqual(
            AuditLog.objects.filter(action=AuditLog.Action.VALIDATED).count(),
            1,
        )

    def test_duplicate_approval_of_same_request(self):
        allocation = create_allocation(amount=Decimal('500.00'))
        request = create_expense_request(
            fund_allocation=allocation,
            requested_amount=Decimal('200.00'),
            purpose='Solicitud para aprobación duplicada',
            requested_date=TEST_DATE,
            actor=self.admin,
        )

        def approve(actor_id):
            actor = get_user_model().objects.get(pk=actor_id)
            return approve_expense_request(
                ExpenseRequest.objects.get(pk=request.pk),
                actor=actor,
            ).pk

        results = self.run_concurrently(
            [
                lambda: approve(self.committee_a.pk),
                lambda: approve(self.committee_b.pk),
            ]
        )

        self.assert_one_success_one_domain_error(
            results,
            (
                ExpenseRequestAlreadyDecidedError,
                ExpenseRequestStateError,
                ValidationError,
            ),
        )
        request.refresh_from_db()
        self.assertEqual(request.status, ExpenseRequest.Status.APPROVED_RESERVED)
        self.assertEqual(request.reserved_amount, Decimal('200.00'))
        self.assertEqual(
            ExpenseRequestEvent.objects.filter(
                expense_request=request,
                event_type=ExpenseRequestEvent.EventType.APPROVED,
            ).count(),
            1,
        )
        self.assertEqual(
            ExpenseRequestEvent.objects.filter(
                expense_request=request,
                event_type=ExpenseRequestEvent.EventType.RESERVATION_CREATED,
            ).count(),
            1,
        )

    def test_approval_vs_direct_expense_creation(self):
        allocation = create_allocation(amount=Decimal('100.00'))
        request = create_expense_request(
            fund_allocation=allocation,
            requested_amount=Decimal('70.00'),
            purpose='Solicitud vs gasto directo concurrente',
            requested_date=TEST_DATE,
            actor=self.admin,
        )

        def approve():
            actor = get_user_model().objects.get(pk=self.committee_a.pk)
            return approve_expense_request(
                ExpenseRequest.objects.get(pk=request.pk),
                actor=actor,
            ).pk

        def spend():
            return create_expense(
                allocation=type(allocation).objects.get(pk=allocation.pk),
                expense_date=TEST_DATE,
                category='food',
                amount=Decimal('70.00'),
                reason='Gasto directo concurrente',
                provider_or_recipient='Proveedor',
                payment_method='bank_transfer',
                description='',
                observations='',
                support_file=SimpleUploadedFile('concurrent.pdf', b'%PDF soporte'),
            ).pk

        results = self.run_concurrently([approve, spend])
        successes = [value for outcome, value in results if outcome == 'success']
        errors = [value for outcome, value in results if outcome == 'error']
        self.assertEqual(len(successes), 1, results)
        self.assertEqual(len(errors), 1, results)
        self.assertIsInstance(errors[0], (ExpenseRequestBalanceError, ValidationError))

        allocation.refresh_from_db()
        from django.db.models import Sum

        executed_total = (
            Expense.objects.filter(allocation=allocation)
            .exclude(status__in=Expense.non_executing_statuses())
            .aggregate(total=Sum('amount'))['total']
            or ZERO_MONEY
        )
        reserved_total = allocation.reserved_amount
        self.assertLessEqual(executed_total + reserved_total, allocation.amount)
        self.assertEqual(executed_total + reserved_total, Decimal('70.00'))

    def test_update_vs_approval_serializes_on_request_row(self):
        allocation = create_allocation(amount=Decimal('100.00'))
        request = create_expense_request(
            fund_allocation=allocation,
            requested_amount=Decimal('40.00'),
            purpose='Solicitud original pendiente de decisión',
            requested_date=TEST_DATE,
            actor=self.admin,
        )

        def update():
            actor = get_user_model().objects.get(pk=self.admin.pk)
            return update_expense_request(
                ExpenseRequest.objects.get(pk=request.pk),
                fund_allocation=allocation,
                requested_amount=Decimal('55.00'),
                purpose='Solicitud actualizada concurrentemente aquí',
                requested_date=TEST_DATE,
                actor=actor,
            ).requested_amount

        def approve():
            actor = get_user_model().objects.get(pk=self.committee_a.pk)
            approved = approve_expense_request(
                ExpenseRequest.objects.get(pk=request.pk),
                actor=actor,
            )
            return approved.requested_amount

        results = self.run_concurrently([update, approve])
        # Both may succeed if update commits first then approval uses new amount,
        # or approval commits first then update fails state check.
        outcomes = [outcome for outcome, _ in results]
        self.assertIn('success', outcomes)
        request.refresh_from_db()
        if request.status == ExpenseRequest.Status.APPROVED_RESERVED:
            self.assertEqual(request.reserved_amount, request.requested_amount)
            self.assertIn(request.requested_amount, {Decimal('40.00'), Decimal('55.00')})
            # Update either applied before approval or was rejected after approval.
            errors = [value for outcome, value in results if outcome == 'error']
            if errors:
                self.assertIsInstance(
                    errors[0],
                    (
                        ExpenseRequestAlreadyDecidedError,
                        ExpenseRequestStateError,
                        ValidationError,
                    ),
                )
            self.assertEqual(
                ExpenseRequestEvent.objects.filter(
                    expense_request=request,
                    event_type=ExpenseRequestEvent.EventType.APPROVED,
                ).count(),
                1,
            )
        else:
            self.assertEqual(request.status, ExpenseRequest.Status.PENDING_DECISION)
            self.assertEqual(request.requested_amount, Decimal('55.00'))
