from decimal import Decimal
from queue import Queue
from threading import Barrier, Thread
from unittest import skipUnless

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import close_old_connections, connection, connections
from django.db.models import Sum
from django.test import TransactionTestCase

from apps.operations.models import (
    AuditLog,
    Donation,
    Expense,
    FundAllocation,
    OperationalCodeSequence,
    Project,
    ProjectUpdate,
    SupportingDocument,
    ZERO_MONEY,
)
from apps.operations.services import (
    ExpenseFinalizedError,
    annul_expense,
    create_expense,
    create_fund_allocation,
    review_project_update,
    update_expense,
    update_fund_allocation,
    annul_fund_allocation,
)
from apps.operations.tests.helpers import (
    TEST_DATE,
    create_allocation,
    create_donation,
    create_expense as create_expense_fixture,
    create_project,
    create_user,
)


POSTGRESQL_LOCKING_REQUIRED = 'Requires PostgreSQL row-level locking'
THREAD_TIMEOUT_SECONDS = 15
BARRIER_TIMEOUT_SECONDS = 10


@skipUnless(connection.vendor == 'postgresql', POSTGRESQL_LOCKING_REQUIRED)
class PostgreSQLConcurrencyTests(TransactionTestCase):
    reset_sequences = True
    serialized_rollback = True

    def run_concurrently(self, operations):
        """
        PRE: operations is a non-empty sequence of zero-argument callables using only ORM ids.
        POST: runs each callable on its own thread-local connection and returns every result/error.
        """
        barrier = Barrier(len(operations))
        results = Queue()

        def run_operation(operation):
            """
            PRE: operation is callable, shares only primitive values, and the thread obtains its own connection.
            POST: records one success or exception and closes every connection owned by the thread.
            """
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
        alive_threads = [thread.name for thread in threads if thread.is_alive()]
        self.assertFalse(alive_threads, f'Concurrent workers timed out: {alive_threads}')
        collected = [results.get_nowait() for _ in threads]
        unexpected = [
            error
            for outcome, error in collected
            if outcome == 'error'
            and not isinstance(error, (ValidationError, ExpenseFinalizedError))
        ]
        self.assertFalse(unexpected, f'Unexpected concurrent errors: {unexpected!r}')
        return collected

    def assert_one_success_one_domain_error(self, results):
        """
        PRE: results contains outcomes from exactly two competing domain operations.
        POST: asserts one committed success and one explicit domain rejection.
        """
        self.assertEqual([outcome for outcome, _ in results].count('success'), 1)
        errors = [value for outcome, value in results if outcome == 'error']
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], (ValidationError, ExpenseFinalizedError))

    def test_concurrent_allocations_preserve_donation_balance(self):
        donation = create_donation(amount=Decimal('100.00'))
        donation_id = donation.pk
        donation_amount = donation.amount
        project_ids = (
            create_project(code='PRJ-CONCURRENT-1').pk,
            create_project(code='PRJ-CONCURRENT-2').pk,
        )

        def allocate(project_id):
            donation_local = Donation.objects.get(pk=donation_id)
            project_local = Project.objects.get(pk=project_id)
            created = create_fund_allocation(
                donation=donation_local,
                project=project_local,
                budget_category='health_psychosocial',
                amount=Decimal('80.00'),
                responsible_person='',
                allocation_date=TEST_DATE,
                status=FundAllocation.Status.ACTIVE,
                notes='',
            )
            return created.pk

        results = self.run_concurrently(
            [lambda: allocate(project_ids[0]), lambda: allocate(project_ids[1])]
        )

        self.assert_one_success_one_domain_error(results)
        allocations = FundAllocation.objects.exclude(status=FundAllocation.Status.ANNULLED)
        self.assertEqual(allocations.count(), 1)
        self.assertLessEqual(allocations.aggregate(total=Sum('amount'))['total'], donation_amount)

    def test_concurrent_allocation_creations_reserve_distinct_codes(self):
        donation = create_donation(amount=Decimal('100.00'))
        project_ids = (
            create_project(code='PRJ-CODE-ALLOC-A').pk,
            create_project(code='PRJ-CODE-ALLOC-B').pk,
        )
        before = OperationalCodeSequence.objects.get(namespace='fund_allocation').next_value

        def allocate(project_id):
            created = create_fund_allocation(
                donation=Donation.objects.get(pk=donation.pk),
                project=Project.objects.get(pk=project_id),
                budget_category='health_psychosocial',
                amount=Decimal('20.00'),
                responsible_person='',
                allocation_date=TEST_DATE,
                status=FundAllocation.Status.ACTIVE,
                notes='',
            )
            return created.code

        results = self.run_concurrently(
            [lambda: allocate(project_ids[0]), lambda: allocate(project_ids[1])]
        )

        self.assertEqual([outcome for outcome, _value in results].count('success'), 2)
        self.assertEqual(len({value for _outcome, value in results}), 2)
        self.assertEqual(FundAllocation.objects.count(), 2)
        self.assertEqual(
            OperationalCodeSequence.objects.get(namespace='fund_allocation').next_value,
            before + 2,
        )

    def test_concurrent_allocation_annulment_has_one_winner_and_one_audit(self):
        donation = create_donation(amount=Decimal('100.00'))
        allocation = create_allocation(donation=donation, amount=Decimal('60.00'))
        actors = (create_user('annul-winner-a'), create_user('annul-winner-b'))

        def annul(actor_id, reason):
            actor = get_user_model().objects.get(pk=actor_id)
            result = annul_fund_allocation(allocation.pk, actor=actor, reason=reason)
            return result.terminal_by_id

        results = self.run_concurrently(
            [
                lambda: annul(actors[0].pk, 'Anulación concurrente del actor A.'),
                lambda: annul(actors[1].pk, 'Anulación concurrente del actor B.'),
            ]
        )

        self.assert_one_success_one_domain_error(results)
        allocation.refresh_from_db()
        donation.refresh_from_db()
        winner_id = next(value for outcome, value in results if outcome == 'success')
        self.assertEqual(allocation.status, FundAllocation.Status.ANNULLED)
        self.assertEqual(allocation.terminal_by_id, winner_id)
        self.assertEqual(donation.available_balance, donation.amount)
        self.assertEqual(
            AuditLog.objects.filter(
                model_name='Asignación de fondos',
                entity_id=str(allocation.pk),
                action=AuditLog.Action.ANNULLED,
            ).count(),
            1,
        )

    def test_concurrent_expenses_preserve_allocation_balance(self):
        allocation = create_allocation(amount=Decimal('100.00'))
        allocation_id = allocation.pk
        allocation_amount = allocation.amount
        Expense.objects.create(
            allocation=allocation,
            expense_date=TEST_DATE,
            category='food',
            amount=Decimal('40.00'),
            currency='USD',
            reason='Gasto cancelado previo',
            provider_or_recipient='Proveedor',
            payment_method='bank_transfer',
            status=Expense.Status.ANNULLED,
        )

        def spend(label):
            allocation_local = FundAllocation.objects.get(pk=allocation_id)
            created = create_expense(
                allocation=allocation_local,
                expense_date=TEST_DATE,
                category='food',
                amount=Decimal('80.00'),
                reason=f'Gasto concurrente {label}',
                provider_or_recipient='Proveedor',
                payment_method='bank_transfer',
                description='',
                observations='',
                support_file=SimpleUploadedFile(f'{label}.pdf', b'%PDF soporte'),
            )
            return created.pk

        results = self.run_concurrently([lambda: spend('A'), lambda: spend('B')])

        self.assert_one_success_one_domain_error(results)
        operational = Expense.objects.exclude(status__in=Expense.non_executing_statuses())
        self.assertEqual(operational.count(), 1)
        self.assertLessEqual(operational.aggregate(total=Sum('amount'))['total'], allocation_amount)
        self.assertEqual(SupportingDocument.objects.count(), 1)

    def test_concurrent_expense_creations_reserve_distinct_codes(self):
        allocation = create_allocation(amount=Decimal('100.00'))
        before = OperationalCodeSequence.objects.get(namespace='expense').next_value

        def spend(label):
            created = create_expense(
                allocation=FundAllocation.objects.get(pk=allocation.pk),
                expense_date=TEST_DATE,
                category='food',
                amount=Decimal('20.00'),
                reason=f'Gasto de código {label}',
                provider_or_recipient='Proveedor',
                payment_method='bank_transfer',
                description='',
                observations='',
                support_file=SimpleUploadedFile(f'{label}.pdf', b'%PDF soporte'),
            )
            return created.code

        results = self.run_concurrently([lambda: spend('A'), lambda: spend('B')])

        self.assertEqual([outcome for outcome, _value in results].count('success'), 2)
        self.assertEqual(len({value for _outcome, value in results}), 2)
        self.assertEqual(Expense.objects.count(), 2)
        self.assertEqual(
            OperationalCodeSequence.objects.get(namespace='expense').next_value,
            before + 2,
        )

    def test_concurrent_allocation_updates_preserve_donation_balance(self):
        donation = create_donation(amount=Decimal('100.00'))
        donation_id = donation.pk
        donation_amount = donation.amount
        project_a = create_project(code='PRJ-UPDATE-ALLOC-A')
        project_b = create_project(code='PRJ-UPDATE-ALLOC-B')
        allocation_ids = (
            create_allocation(donation=donation, project=project_a, amount=Decimal('20.00')).pk,
            create_allocation(donation=donation, project=project_b, amount=Decimal('20.00')).pk,
        )

        def increase(allocation_id):
            allocation_local = FundAllocation.objects.get(pk=allocation_id)
            donation_local = Donation.objects.get(pk=donation_id)
            project_local = Project.objects.get(pk=allocation_local.project_id)
            updated = update_fund_allocation(
                allocation=allocation_local,
                donation=donation_local,
                project=project_local,
                budget_category=allocation_local.budget_category,
                amount=Decimal('70.00'),
                responsible_person=allocation_local.responsible_person,
                allocation_date=allocation_local.allocation_date,
                status=allocation_local.status,
                notes=allocation_local.notes,
            )
            return updated.pk

        results = self.run_concurrently(
            [lambda: increase(allocation_ids[0]), lambda: increase(allocation_ids[1])]
        )

        self.assert_one_success_one_domain_error(results)
        amounts = list(
            FundAllocation.objects.filter(pk__in=allocation_ids)
            .order_by('pk')
            .values_list('amount', flat=True)
        )
        self.assertEqual(sorted(amounts), [Decimal('20.00'), Decimal('70.00')])
        self.assertLessEqual(sum(amounts, ZERO_MONEY), donation_amount)
        successful_id = next(value for outcome, value in results if outcome == 'success')
        failed_id = next(pk for pk in allocation_ids if pk != successful_id)
        self.assertEqual(FundAllocation.objects.get(pk=failed_id).amount, Decimal('20.00'))

    def test_concurrent_expense_updates_preserve_allocation_balance(self):
        allocation = create_allocation(amount=Decimal('100.00'))
        allocation_id = allocation.pk
        allocation_amount = allocation.amount
        expense_ids = (
            create_expense_fixture(allocation=allocation, amount=Decimal('20.00'), reason='A').pk,
            create_expense_fixture(allocation=allocation, amount=Decimal('20.00'), reason='B').pk,
        )
        for expense in Expense.objects.filter(pk__in=expense_ids):
            SupportingDocument.objects.create(
                expense=expense,
                title='Soporte existente',
                document=f'supporting_documents/{expense.pk}.pdf',
            )

        def increase(expense_id):
            expense_local = Expense.objects.get(pk=expense_id)
            allocation_local = FundAllocation.objects.get(pk=allocation_id)
            updated = update_expense(
                expense=expense_local,
                allocation=allocation_local,
                expense_date=expense_local.expense_date,
                category=expense_local.category,
                amount=Decimal('70.00'),
                reason=expense_local.reason,
                provider_or_recipient=expense_local.provider_or_recipient,
                payment_method=expense_local.payment_method,
                description=expense_local.description,
                observations=expense_local.observations,
            )
            return updated.pk

        results = self.run_concurrently(
            [lambda: increase(expense_ids[0]), lambda: increase(expense_ids[1])]
        )

        self.assert_one_success_one_domain_error(results)
        amounts = list(
            Expense.objects.filter(pk__in=expense_ids)
            .order_by('pk')
            .values_list('amount', flat=True)
        )
        self.assertEqual(sorted(amounts), [Decimal('20.00'), Decimal('70.00')])
        self.assertLessEqual(sum(amounts, ZERO_MONEY), allocation_amount)
        successful_id = next(value for outcome, value in results if outcome == 'success')
        failed_id = next(pk for pk in expense_ids if pk != successful_id)
        self.assertEqual(Expense.objects.get(pk=failed_id).amount, Decimal('20.00'))

    def test_concurrent_project_update_review_creates_one_decision(self):
        project = create_project(code='PRJ-CONCURRENT-REVIEW')
        project.status = Project.Status.ACTIVE
        project.save(update_fields=('status', 'updated_at'))
        reviewers = (create_user(username='reviewer-a'), create_user(username='reviewer-b'))
        reviewer_ids = (reviewers[0].pk, reviewers[1].pk)
        project_update = ProjectUpdate.objects.create(
            project=project,
            title='Avance concurrente',
            description='Pendiente de dos revisores.',
            status=ProjectUpdate.Status.PENDING_REVIEW,
        )
        project_update_id = project_update.pk

        def review(reviewer_id, status, notes):
            reviewer = get_user_model().objects.get(pk=reviewer_id)
            reviewed = review_project_update(project_update_id, reviewer, status, notes)
            return reviewed.status

        results = self.run_concurrently(
            [
                lambda: review(reviewer_ids[0], ProjectUpdate.Status.APPROVED, ''),
                lambda: review(reviewer_ids[1], ProjectUpdate.Status.REJECTED, 'Rechazado.'),
            ]
        )

        self.assert_one_success_one_domain_error(results)
        project_update.refresh_from_db()
        self.assertIn(project_update.status, (ProjectUpdate.Status.APPROVED, ProjectUpdate.Status.REJECTED))
        self.assertIn(project_update.reviewed_by_id, reviewer_ids)
        self.assertIsNotNone(project_update.reviewed_at)
        expected_reviewer = reviewer_ids[0] if project_update.status == ProjectUpdate.Status.APPROVED else reviewer_ids[1]
        self.assertEqual(project_update.reviewed_by_id, expected_reviewer)
        self.assertEqual(
            AuditLog.objects.filter(
                entity_id=str(project_update.pk),
                action__in=(AuditLog.Action.VALIDATED, AuditLog.Action.REJECTED),
            ).count(),
            1,
        )

    def test_concurrent_expense_annulment_creates_one_event(self):
        allocation = create_allocation(amount=Decimal('100.00'))
        expense = create_expense_fixture(allocation=allocation, amount=Decimal('80.00'))
        expense_id = expense.pk
        allocation_amount = allocation.amount
        SupportingDocument.objects.create(
            expense=expense,
            title='Soporte de validación',
            document='supporting_documents/concurrent-validation.pdf',
        )
        actors = (create_user(username='annuller-a'), create_user(username='annuller-b'))
        actor_ids = (actors[0].pk, actors[1].pk)

        def annul(actor_id):
            actor = get_user_model().objects.get(pk=actor_id)
            annulled = annul_expense(expense_id, actor=actor, reason='Anulación concurrente válida.')
            return annulled.pk

        results = self.run_concurrently(
            [lambda: annul(actor_ids[0]), lambda: annul(actor_ids[1])]
        )

        self.assert_one_success_one_domain_error(results)
        expense.refresh_from_db()
        self.assertEqual(expense.status, Expense.Status.ANNULLED)
        self.assertIsNotNone(expense.terminal_by_id)
        self.assertIsNotNone(expense.terminal_at)
        self.assertEqual(allocation.executed_amount, ZERO_MONEY)
        self.assertEqual(allocation.available_balance, allocation_amount)
        self.assertEqual(
            AuditLog.objects.filter(
                entity_id=str(expense.pk), action=AuditLog.Action.EXPENSE_CANCELLED
            ).count(),
            1,
        )

    def test_concurrent_expense_update_and_annulment_keep_serializable_balance(self):
        allocation = create_allocation(amount=Decimal('100.00'))
        expense = create_expense_fixture(allocation=allocation, amount=Decimal('30.00'))
        SupportingDocument.objects.create(
            expense=expense,
            title='Soporte concurrente',
            document='supporting_documents/update-annul.pdf',
        )
        editor = create_user(username='expense-editor')
        annuller = create_user(username='expense-annuller')

        def update():
            current = Expense.objects.get(pk=expense.pk)
            return update_expense(
                expense=current,
                allocation=FundAllocation.objects.get(pk=allocation.pk),
                expense_date=current.expense_date,
                category=current.category,
                amount=Decimal('40.00'),
                reason=current.reason,
                provider_or_recipient=current.provider_or_recipient,
                payment_method=current.payment_method,
                description=current.description,
                observations=current.observations,
                actor=get_user_model().objects.get(pk=editor.pk),
            ).pk

        def annul():
            return annul_expense(
                expense.pk,
                actor=get_user_model().objects.get(pk=annuller.pk),
                reason='Anulación concurrente autorizada.',
            ).pk

        results = self.run_concurrently([update, annul])
        self.assertGreaterEqual(
            [outcome for outcome, _value in results].count('success'),
            1,
        )
        expense.refresh_from_db()
        allocation.refresh_from_db()
        if expense.status == Expense.Status.ANNULLED:
            self.assertEqual(allocation.executed_amount, ZERO_MONEY)
        else:
            self.assertEqual(expense.status, Expense.Status.REGISTERED)
            self.assertEqual(allocation.executed_amount, expense.amount)
        self.assertGreaterEqual(
            AuditLog.objects.filter(entity_id=str(expense.pk)).count(),
            1,
        )
