"""Expense reassignment integrity: structural eligibility, balances, ER linkage, concurrency."""

import shutil
import tempfile
from decimal import Decimal
from queue import Queue
from threading import Barrier, Thread
from unittest import skipUnless

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import close_old_connections, connection, connections
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse

from apps.operations.forms import ExpenseForm
from apps.operations.models import (
    AuditLog,
    Donation,
    Expense,
    ExpenseRequest,
    ExpenseRequestEvent,
    FundAllocation,
    OperationalCodeSequence,
    Project,
    SupportingDocument,
    ZERO_MONEY,
    OPERATIONAL_CODE_PREFIXES,
)
from apps.operations.role_services import sync_operation_roles
from apps.operations.roles import (
    ROLE_FIELD_OPERATOR,
    ROLE_PROJECT_COMMITTEE,
    ROLE_SIGEDON_ADMIN,
)
from apps.operations.services import (
    ExpenseFinalizedError,
    annul_expense,
    create_expense_legacy,
    finish_fund_allocation,
    finish_project,
    update_expense,
    validate_fund_allocation_for_new_operational_use,
)
from apps.operations.tests.helpers import (
    TEST_DATE,
    create_allocation,
    create_donation,
    create_expense as create_expense_row,
    create_fulfilled_expense_request,
    create_institution,
    create_project,
    create_user,
)

POSTGRESQL_LOCKING_REQUIRED = 'Requires PostgreSQL row-level locking'
THREAD_TIMEOUT_SECONDS = 15
BARRIER_TIMEOUT_SECONDS = 10


class ExpenseReassignmentIntegrityTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._media_root = tempfile.mkdtemp()
        cls._media_override = override_settings(MEDIA_ROOT=cls._media_root)
        cls._media_override.enable()

    @classmethod
    def tearDownClass(cls):
        cls._media_override.disable()
        shutil.rmtree(cls._media_root, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.user = create_user(username='reassign-editor')
        self.donor = create_institution(name='Donante reasignación')
        self.project = create_project(code='PRJ-REASSIGN', name='Proyecto reasignación')
        self.donation = create_donation(
            code='DON-REASSIGN',
            donor=self.donor,
            amount=Decimal('5000.00'),
        )
        self.source = create_allocation(
            donation=self.donation,
            project=self.project,
            amount=Decimal('200.00'),
            category='health_psychosocial',
        )
        self.expense = self._expense_with_support(
            allocation=self.source,
            amount=Decimal('50.00'),
            reason='Gasto base',
        )

    def _expense_with_support(self, *, allocation, amount, reason='Gasto de prueba'):
        expense = create_expense_row(
            allocation=allocation,
            amount=amount,
            reason=reason,
        )
        SupportingDocument.objects.create(
            expense=expense,
            title='Soporte',
            document=SimpleUploadedFile('soporte.pdf', b'%PDF-1.4 soporte'),
        )
        return expense

    def _update(self, expense, allocation=None, amount=None, reason=None, actor=None, **kwargs):
        return update_expense(
            expense=expense,
            allocation=allocation if allocation is not None else expense.allocation,
            expense_date=kwargs.pop('expense_date', expense.expense_date),
            category=kwargs.pop('category', expense.category),
            amount=amount if amount is not None else expense.amount,
            reason=reason if reason is not None else expense.reason,
            provider_or_recipient=kwargs.pop(
                'provider_or_recipient',
                expense.provider_or_recipient,
            ),
            payment_method=kwargs.pop('payment_method', expense.payment_method),
            description=kwargs.pop('description', expense.description),
            observations=kwargs.pop('observations', expense.observations),
            actor=actor if actor is not None else self.user,
            **kwargs,
        )

    def _edit_data(self, expense, **overrides):
        data = {
            'allocation': expense.allocation_id,
            'expense_date': TEST_DATE,
            'category': expense.category,
            'amount': str(expense.amount),
            'reason': expense.reason,
            'provider_or_recipient': expense.provider_or_recipient,
            'payment_method': expense.payment_method,
            'description': expense.description,
            'observations': expense.observations,
        }
        data.update(overrides)
        return data

    def _snapshot(self, expense):
        expense.refresh_from_db()
        source = FundAllocation.objects.get(pk=self.source.pk)
        return {
            'allocation_id': expense.allocation_id,
            'amount': expense.amount,
            'reason': expense.reason,
            'source_executed': source.executed_amount,
            'source_available': source.available_balance,
        }

    # --- Unchanged allocation -------------------------------------------------

    def test_metadata_edit_succeeds_on_active_allocation(self):
        updated = self._update(self.expense, reason='Motivo corregido')
        self.assertEqual(updated.allocation_id, self.source.pk)
        self.assertEqual(updated.reason, 'Motivo corregido')

    def test_metadata_edit_succeeds_on_finished_historical_allocation(self):
        self.source.status = FundAllocation.Status.FINISHED
        self.source.save(update_fields=('status', 'updated_at'))
        updated = self._update(self.expense, reason='Edit sobre FINISHED')
        self.assertEqual(updated.allocation_id, self.source.pk)
        self.assertEqual(updated.reason, 'Edit sobre FINISHED')

    def test_metadata_edit_succeeds_when_current_project_closed(self):
        Project.objects.filter(pk=self.project.pk).update(status=Project.Status.CLOSED)
        updated = self._update(self.expense, reason='Proyecto cerrado histórico')
        self.assertEqual(updated.allocation_id, self.source.pk)
        self.assertEqual(updated.reason, 'Proyecto cerrado histórico')

    def test_metadata_edit_succeeds_when_current_donation_no_longer_received(self):
        Donation.objects.filter(pk=self.donation.pk).update(status=Donation.Status.REGISTERED)
        updated = self._update(self.expense, reason='Donación histórica')
        self.assertEqual(updated.allocation_id, self.source.pk)
        self.assertEqual(updated.reason, 'Donación histórica')

    def test_same_allocation_amount_increase_validates_delta_only(self):
        # capacity remaining 150; increase 50 → 180 needs only +130
        updated = self._update(self.expense, amount=Decimal('180.00'))
        self.assertEqual(updated.amount, Decimal('180.00'))
        self.assertEqual(self.source.executed_amount, Decimal('180.00'))

    def test_same_allocation_amount_decrease_releases_capacity(self):
        peer = self._expense_with_support(
            allocation=self.source,
            amount=Decimal('150.00'),
            reason='Consume resto',
        )
        self.assertEqual(self.source.available_balance, ZERO_MONEY)
        updated = self._update(self.expense, amount=Decimal('30.00'))
        self.assertEqual(updated.amount, Decimal('30.00'))
        self.assertEqual(self.source.available_balance, Decimal('20.00'))
        peer.refresh_from_db()
        self.assertEqual(peer.amount, Decimal('150.00'))

    def test_unchanged_allocation_does_not_trigger_structural_rejection(self):
        self.source.status = FundAllocation.Status.FINISHED
        self.source.save(update_fields=('status', 'updated_at'))
        Project.objects.filter(pk=self.project.pk).update(status=Project.Status.CLOSED)
        Donation.objects.filter(pk=self.donation.pk).update(status=Donation.Status.ANNULLED)
        updated = self._update(self.expense, reason='Sin reasignación')
        self.assertEqual(updated.allocation_id, self.source.pk)

    def test_same_pk_different_instance_is_not_reassignment(self):
        other_instance = FundAllocation.objects.get(pk=self.source.pk)
        audits_before = AuditLog.objects.filter(
            entity_id=str(self.expense.pk),
            action=AuditLog.Action.UPDATED,
        ).count()
        updated = self._update(self.expense, allocation=other_instance, reason='Misma pk')
        self.assertEqual(updated.allocation_id, self.source.pk)
        self.assertEqual(
            AuditLog.objects.filter(
                entity_id=str(self.expense.pk),
                action=AuditLog.Action.UPDATED,
            ).count(),
            audits_before + 1,
        )

    # --- Valid reassignment ---------------------------------------------------

    def test_valid_reassignment_moves_balance_and_writes_one_audit(self):
        target = create_allocation(
            donation=self.donation,
            project=self.project,
            amount=Decimal('100.00'),
            category='training_entrepreneurship',
        )
        audits_before = AuditLog.objects.filter(
            entity_id=str(self.expense.pk),
            action=AuditLog.Action.UPDATED,
        ).count()
        events_before = ExpenseRequestEvent.objects.count()
        updated = self._update(
            self.expense,
            allocation=target,
            amount=Decimal('40.00'),
            reason='Reasignado válido',
        )
        self.assertEqual(updated.allocation_id, target.pk)
        self.assertEqual(updated.amount, Decimal('40.00'))
        self.assertEqual(self.source.executed_amount, ZERO_MONEY)
        self.assertEqual(target.executed_amount, Decimal('40.00'))
        self.assertEqual(
            AuditLog.objects.filter(
                entity_id=str(self.expense.pk),
                action=AuditLog.Action.UPDATED,
            ).count(),
            audits_before + 1,
        )
        self.assertEqual(ExpenseRequestEvent.objects.count(), events_before)

    # --- Invalid structural targets -------------------------------------------

    def _assert_reassignment_rejected(self, target, *, amount=None, message_fragment):
        before = self._snapshot(self.expense)
        target_before = (
            FundAllocation.objects.get(pk=target.pk).executed_amount,
            FundAllocation.objects.get(pk=target.pk).available_balance,
        )
        audits_before = AuditLog.objects.filter(entity_id=str(self.expense.pk)).count()
        events_before = ExpenseRequestEvent.objects.count()
        with self.assertRaisesMessage(ValidationError, message_fragment):
            self._update(
                self.expense,
                allocation=target,
                amount=amount if amount is not None else self.expense.amount,
                reason='Intento inválido',
            )
        after = self._snapshot(self.expense)
        self.assertEqual(after, before)
        target.refresh_from_db()
        self.assertEqual(
            (target.executed_amount, target.available_balance),
            target_before,
        )
        self.assertEqual(
            AuditLog.objects.filter(entity_id=str(self.expense.pk)).count(),
            audits_before,
        )
        self.assertEqual(ExpenseRequestEvent.objects.count(), events_before)

    def test_finished_target_rejected(self):
        target = create_allocation(
            donation=self.donation,
            project=self.project,
            amount=Decimal('80.00'),
            category='infrastructure_supply',
            status=FundAllocation.Status.FINISHED,
        )
        self._assert_reassignment_rejected(
            target,
            message_fragment='asignación finalizada o anulada',
        )

    def test_annulled_target_rejected(self):
        target = create_allocation(
            donation=self.donation,
            project=self.project,
            amount=Decimal('80.00'),
            category='communication_networks',
            status=FundAllocation.Status.ANNULLED,
        )
        self._assert_reassignment_rejected(
            target,
            message_fragment='asignación finalizada o anulada',
        )

    def test_closed_project_target_rejected(self):
        closed_project = create_project(code='PRJ-REASSIGN-CLOSED', name='Cerrado')
        closed_project.status = Project.Status.CLOSED
        closed_project.save(update_fields=('status', 'updated_at'))
        target = create_allocation(
            donation=create_donation(
                code='DON-REASSIGN-CLOSED',
                donor=self.donor,
                amount=Decimal('200.00'),
            ),
            project=closed_project,
            amount=Decimal('80.00'),
        )
        self._assert_reassignment_rejected(
            target,
            message_fragment='proyecto de destino no está activo',
        )

    def test_registered_donation_target_rejected(self):
        target = create_allocation(
            donation=create_donation(
                code='DON-REASSIGN-REG',
                donor=self.donor,
                amount=Decimal('200.00'),
                status=Donation.Status.REGISTERED,
            ),
            project=self.project,
            amount=Decimal('80.00'),
        )
        self._assert_reassignment_rejected(
            target,
            message_fragment='donación de destino no está recibida',
        )

    def test_annulled_donation_target_rejected(self):
        target = create_allocation(
            donation=create_donation(
                code='DON-REASSIGN-ANN',
                donor=self.donor,
                amount=Decimal('200.00'),
                status=Donation.Status.ANNULLED,
            ),
            project=self.project,
            amount=Decimal('80.00'),
        )
        self._assert_reassignment_rejected(
            target,
            message_fragment='donación de destino no está recibida',
        )

    def test_insufficient_target_balance_rejected(self):
        target = create_allocation(
            donation=self.donation,
            project=self.project,
            amount=Decimal('30.00'),
            category='institutional_relations',
        )
        self._expense_with_support(
            allocation=target,
            amount=Decimal('25.00'),
            reason='Casi lleno',
        )
        self._assert_reassignment_rejected(
            target,
            amount=Decimal('20.00'),
            message_fragment='saldo disponible',
        )

    def test_validator_rejects_unsupported_currency_when_constructed(self):
        target = create_allocation(
            donation=self.donation,
            project=self.project,
            amount=Decimal('50.00'),
            category='institutional_relations',
        )
        donation = target.donation
        # Bypass DB check constraint for unit-level validator coverage.
        donation.currency = 'EUR'
        with self.assertRaisesMessage(ValidationError, 'moneda no admitida'):
            validate_fund_allocation_for_new_operational_use(
                target,
                project=self.project,
                donation=donation,
            )

    # --- Form / crafted POST --------------------------------------------------

    def test_form_excludes_invalid_targets_and_keeps_historical(self):
        finished = create_allocation(
            donation=self.donation,
            project=self.project,
            amount=Decimal('40.00'),
            category='infrastructure_supply',
            status=FundAllocation.Status.FINISHED,
        )
        eligible = create_allocation(
            donation=self.donation,
            project=self.project,
            amount=Decimal('40.00'),
            category='training_entrepreneurship',
        )
        self.source.status = FundAllocation.Status.FINISHED
        self.source.save(update_fields=('status', 'updated_at'))
        form = ExpenseForm(instance=self.expense)
        pks = set(form.fields['allocation'].queryset.values_list('pk', flat=True))
        self.assertIn(self.source.pk, pks)
        self.assertIn(eligible.pk, pks)
        self.assertNotIn(finished.pk, pks)

    def test_crafted_finished_post_fails_form_validation(self):
        finished = create_allocation(
            donation=self.donation,
            project=self.project,
            amount=Decimal('90.00'),
            category='communication_networks',
            status=FundAllocation.Status.FINISHED,
        )
        before = self._snapshot(self.expense)
        form = ExpenseForm(
            instance=self.expense,
            data=self._edit_data(self.expense, allocation=finished.pk),
        )
        self.assertFalse(form.is_valid())
        self.assertIn('allocation', form.errors)
        self.assertEqual(self._snapshot(self.expense), before)

    def test_crafted_non_received_post_fails_form_validation(self):
        target = create_allocation(
            donation=create_donation(
                code='DON-REASSIGN-POST-REG',
                donor=self.donor,
                amount=Decimal('100.00'),
                status=Donation.Status.REGISTERED,
            ),
            project=self.project,
            amount=Decimal('50.00'),
        )
        form = ExpenseForm(
            instance=self.expense,
            data=self._edit_data(self.expense, allocation=target.pk),
        )
        self.assertFalse(form.is_valid())
        self.assertIn('allocation', form.errors)

    def test_direct_service_bypass_of_finished_target_rejected(self):
        finished = create_allocation(
            donation=self.donation,
            project=self.project,
            amount=Decimal('90.00'),
            category='communication_networks',
            status=FundAllocation.Status.FINISHED,
        )
        self._assert_reassignment_rejected(
            finished,
            message_fragment='asignación finalizada o anulada',
        )

    def test_valid_post_via_update_view_succeeds(self):
        target = create_allocation(
            donation=self.donation,
            project=self.project,
            amount=Decimal('100.00'),
            category='training_entrepreneurship',
        )
        self.client.force_login(self.user)
        response = self.client.post(
            reverse('expense_update', args=[self.expense.pk]),
            data=self._edit_data(
                self.expense,
                allocation=target.pk,
                amount='35.00',
                reason='POST válido',
            ),
        )
        self.assertEqual(response.status_code, 302)
        self.expense.refresh_from_db()
        self.assertEqual(self.expense.allocation_id, target.pk)
        self.assertEqual(self.expense.amount, Decimal('35.00'))

    # --- ExpenseRequest consistency -------------------------------------------

    def test_fulfilled_expense_cannot_change_allocation(self):
        sync_operation_roles()
        admin = create_user(username='reassign-admin')
        admin.groups.add(Group.objects.get(name=ROLE_SIGEDON_ADMIN))
        operator = create_user(username='reassign-operator')
        operator.groups.add(Group.objects.get(name=ROLE_FIELD_OPERATOR))
        committee = create_user(username='reassign-committee')
        committee.groups.add(Group.objects.get(name=ROLE_PROJECT_COMMITTEE))
        allocation = create_allocation(
            donation=self.donation,
            project=self.project,
            amount=Decimal('300.00'),
            category='institutional_relations',
        )
        fulfilled, expense, _document = create_fulfilled_expense_request(
            allocation=allocation,
            requester=operator,
            committee_actor=committee,
            admin_actor=admin,
            requested_amount=Decimal('80.00'),
        )
        target = create_allocation(
            donation=self.donation,
            project=self.project,
            amount=Decimal('200.00'),
            category='training_entrepreneurship',
        )
        events_before = ExpenseRequestEvent.objects.filter(
            expense_request=fulfilled,
        ).count()
        before_allocation = expense.allocation_id
        with self.assertRaisesMessage(ValidationError, 'solicitud de gasto aprobada'):
            self._update(expense, allocation=target, reason='Mover cumplido')
        expense.refresh_from_db()
        fulfilled.refresh_from_db()
        self.assertEqual(expense.allocation_id, before_allocation)
        self.assertEqual(fulfilled.fund_allocation_id, allocation.pk)
        self.assertEqual(fulfilled.status, ExpenseRequest.Status.FULFILLED)
        self.assertEqual(
            ExpenseRequestEvent.objects.filter(expense_request=fulfilled).count(),
            events_before,
        )

    def test_fulfilled_expense_same_allocation_metadata_edit_allowed(self):
        sync_operation_roles()
        admin = create_user(username='reassign-admin-meta')
        admin.groups.add(Group.objects.get(name=ROLE_SIGEDON_ADMIN))
        operator = create_user(username='reassign-operator-meta')
        operator.groups.add(Group.objects.get(name=ROLE_FIELD_OPERATOR))
        committee = create_user(username='reassign-committee-meta')
        committee.groups.add(Group.objects.get(name=ROLE_PROJECT_COMMITTEE))
        allocation = create_allocation(
            donation=self.donation,
            project=self.project,
            amount=Decimal('300.00'),
            category='institutional_relations',
        )
        _fulfilled, expense, _document = create_fulfilled_expense_request(
            allocation=allocation,
            requester=operator,
            committee_actor=committee,
            admin_actor=admin,
            requested_amount=Decimal('60.00'),
        )
        updated = self._update(expense, reason='Solo motivo cumplido')
        self.assertEqual(updated.allocation_id, allocation.pk)
        self.assertEqual(updated.reason, 'Solo motivo cumplido')

    # --- Terminal Expense -----------------------------------------------------

    def test_annulled_expense_remains_non_editable(self):
        annul_expense(self.expense.pk, actor=self.user, reason='Anulación de integridad.')
        target = create_allocation(
            donation=self.donation,
            project=self.project,
            amount=Decimal('80.00'),
            category='training_entrepreneurship',
        )
        with self.assertRaises(ExpenseFinalizedError):
            self._update(self.expense, allocation=target, reason='Bypass terminal')
        self.expense.refresh_from_db()
        self.assertEqual(self.expense.status, Expense.Status.ANNULLED)
        self.assertEqual(self.expense.allocation_id, self.source.pk)


@skipUnless(connection.vendor == 'postgresql', POSTGRESQL_LOCKING_REQUIRED)
class ExpenseReassignmentConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        super().setUp()
        OperationalCodeSequence.objects.bulk_create(
            [
                OperationalCodeSequence(
                    namespace=namespace,
                    prefix=prefix,
                    next_value=1,
                )
                for namespace, prefix in OPERATIONAL_CODE_PREFIXES.items()
            ],
            ignore_conflicts=True,
        )
        self._media_root = tempfile.mkdtemp()
        self._media_override = override_settings(MEDIA_ROOT=self._media_root)
        self._media_override.enable()
        self.donor = create_institution(name='Donante concurrente reasignación')
        self.project = create_project(code='PRJ-REASSIGN-CONC', name='Concurrente')
        self.donation = create_donation(
            code='DON-REASSIGN-CONC',
            donor=self.donor,
            amount=Decimal('1000.00'),
        )

    def tearDown(self):
        self._media_override.disable()
        shutil.rmtree(self._media_root, ignore_errors=True)
        super().tearDown()

    def _expense_with_support(self, *, allocation, amount, reason):
        expense = create_expense_legacy(
            allocation=allocation,
            expense_date=TEST_DATE,
            category='food',
            amount=amount,
            reason=reason,
            provider_or_recipient='Proveedor',
            payment_method='bank_transfer',
            description='',
            observations='',
            support_file=SimpleUploadedFile('soporte.pdf', b'%PDF-1.4 soporte'),
        )
        return expense

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
            self.assertFalse(thread.is_alive(), 'Concurrent worker exceeded timeout')
        return [results.get_nowait() for _ in operations]

    def assert_one_success_one_domain_error(self, results):
        outcomes = [outcome for outcome, _value in results]
        self.assertEqual(outcomes.count('success'), 1, results)
        self.assertEqual(outcomes.count('error'), 1, results)
        error = next(value for outcome, value in results if outcome == 'error')
        self.assertIsInstance(error, (ValidationError, ExpenseFinalizedError))

    def test_concurrent_reassignments_into_nearly_full_target_cannot_overspend(self):
        target = create_allocation(
            donation=self.donation,
            project=self.project,
            amount=Decimal('100.00'),
            category='health_psychosocial',
        )
        source_a = create_allocation(
            donation=self.donation,
            project=self.project,
            amount=Decimal('80.00'),
            category='training_entrepreneurship',
        )
        source_b = create_allocation(
            donation=self.donation,
            project=self.project,
            amount=Decimal('80.00'),
            category='infrastructure_supply',
        )
        expense_a = self._expense_with_support(
            allocation=source_a,
            amount=Decimal('60.00'),
            reason='A',
        )
        expense_b = self._expense_with_support(
            allocation=source_b,
            amount=Decimal('60.00'),
            reason='B',
        )
        target_id = target.pk
        expense_ids = (expense_a.pk, expense_b.pk)

        def move(expense_id):
            current = Expense.objects.get(pk=expense_id)
            return update_expense(
                expense=current,
                allocation=FundAllocation.objects.get(pk=target_id),
                expense_date=current.expense_date,
                category=current.category,
                amount=current.amount,
                reason=current.reason,
                provider_or_recipient=current.provider_or_recipient,
                payment_method=current.payment_method,
                description=current.description,
                observations=current.observations,
            ).pk

        results = self.run_concurrently(
            [lambda: move(expense_ids[0]), lambda: move(expense_ids[1])]
        )
        self.assert_one_success_one_domain_error(results)
        target.refresh_from_db()
        self.assertLessEqual(target.executed_amount, target.amount)
        self.assertGreaterEqual(target.available_balance, ZERO_MONEY)
        moved = Expense.objects.filter(allocation_id=target_id)
        self.assertEqual(moved.count(), 1)
        self.assertEqual(moved.get().amount, Decimal('60.00'))

    def test_reassignment_racing_target_finish_cannot_land_on_terminal(self):
        target = create_allocation(
            donation=self.donation,
            project=self.project,
            amount=Decimal('100.00'),
            category='health_psychosocial',
        )
        source = create_allocation(
            donation=self.donation,
            project=self.project,
            amount=Decimal('80.00'),
            category='training_entrepreneurship',
        )
        expense = self._expense_with_support(
            allocation=source,
            amount=Decimal('40.00'),
            reason='Carrera finish',
        )
        actor = create_user(username='finish-racer')
        target_id = target.pk
        expense_id = expense.pk
        actor_id = actor.pk

        def reassign():
            current = Expense.objects.get(pk=expense_id)
            return update_expense(
                expense=current,
                allocation=FundAllocation.objects.get(pk=target_id),
                expense_date=current.expense_date,
                category=current.category,
                amount=current.amount,
                reason=current.reason,
                provider_or_recipient=current.provider_or_recipient,
                payment_method=current.payment_method,
                description=current.description,
                observations=current.observations,
            ).pk

        def finish():
            return finish_fund_allocation(
                target_id,
                actor=get_user_model().objects.get(pk=actor_id),
            ).pk

        results = self.run_concurrently([reassign, finish])
        self.assertGreaterEqual(
            [outcome for outcome, _value in results].count('success'),
            1,
            results,
        )
        expense.refresh_from_db()
        target.refresh_from_db()
        # Reassignment cannot commit onto an already-FINISHED target. After the race,
        # the expense may remain on source, or sit on target still ACTIVE / finished later.
        if expense.allocation_id == target_id:
            self.assertIn(
                target.status,
                {FundAllocation.Status.ACTIVE, FundAllocation.Status.FINISHED},
            )
        else:
            self.assertEqual(expense.allocation_id, source.pk)
        self.assertGreaterEqual(target.available_balance, ZERO_MONEY)

    def test_reassignment_racing_project_close_cannot_land_in_closed_scope(self):
        other_project = create_project(code='PRJ-REASSIGN-CLOSE-RACE', name='Cierre')
        target = create_allocation(
            donation=create_donation(
                code='DON-REASSIGN-CLOSE-RACE',
                donor=self.donor,
                amount=Decimal('200.00'),
            ),
            project=other_project,
            amount=Decimal('100.00'),
        )
        source = create_allocation(
            donation=self.donation,
            project=self.project,
            amount=Decimal('80.00'),
            category='training_entrepreneurship',
        )
        expense = self._expense_with_support(
            allocation=source,
            amount=Decimal('30.00'),
            reason='Carrera close',
        )
        actor = create_user(username='close-racer')
        target_id = target.pk
        expense_id = expense.pk
        actor_id = actor.pk
        project_id = other_project.pk

        def reassign():
            current = Expense.objects.get(pk=expense_id)
            return update_expense(
                expense=current,
                allocation=FundAllocation.objects.get(pk=target_id),
                expense_date=current.expense_date,
                category=current.category,
                amount=current.amount,
                reason=current.reason,
                provider_or_recipient=current.provider_or_recipient,
                payment_method=current.payment_method,
                description=current.description,
                observations=current.observations,
            ).pk

        def finish_target_then_close_project():
            user = get_user_model().objects.get(pk=actor_id)
            finish_fund_allocation(target_id, actor=user)
            return finish_project(project_id, actor=user).pk

        results = self.run_concurrently([reassign, finish_target_then_close_project])
        self.assertTrue(
            any(outcome == 'success' for outcome, _value in results),
            results,
        )
        expense.refresh_from_db()
        other_project.refresh_from_db()
        target.refresh_from_db()
        # Reassignment must never commit onto an already-CLOSED project.
        if expense.allocation_id == target_id:
            if other_project.status == Project.Status.CLOSED:
                self.assertEqual(target.status, FundAllocation.Status.FINISHED)
            else:
                self.assertEqual(other_project.status, Project.Status.ACTIVE)
                self.assertEqual(target.status, FundAllocation.Status.ACTIVE)
        else:
            self.assertEqual(expense.allocation_id, source.pk)
        self.assertGreaterEqual(target.available_balance, ZERO_MONEY)
