"""ExpenseForm edit-only contract and canonical allocation choice narrowing."""

import shutil
import tempfile
from decimal import Decimal
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.operations.forms import ExpenseForm
from apps.operations.models import (
    AuditLog,
    Donation,
    Expense,
    FundAllocation,
    Project,
    SupportingDocument,
)
from apps.operations.selectors import (
    expense_request_allocation_choices,
    operational_fund_allocation_choices,
)
from apps.operations.services import create_expense, update_expense
from apps.operations.tests.helpers import (
    TEST_DATE,
    create_allocation,
    create_donation,
    create_expense as create_expense_row,
    create_institution,
    create_project,
    create_user,
)


class ExpenseFormAllocationChoicesTests(TestCase):
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
        self.user = create_user(username='expense-form-editor')
        self.donor = create_institution(name='Donante ExpenseForm')
        self.project = create_project(code='PRJ-EXP-FORM', name='Proyecto ExpenseForm')
        self.donation = create_donation(
            code='DON-EXP-FORM',
            donor=self.donor,
            amount=Decimal('2000.00'),
        )
        self.allocation = create_allocation(
            donation=self.donation,
            project=self.project,
            amount=Decimal('200.00'),
            category='health_psychosocial',
        )
        self.expense = self._expense_with_support(
            allocation=self.allocation,
            amount=Decimal('25.00'),
            reason='Gasto editable',
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

    def test_form_requires_persisted_expense_instance(self):
        ExpenseForm(instance=self.expense)
        with self.assertRaises(ValueError):
            ExpenseForm()
        with self.assertRaises(ValueError):
            ExpenseForm(instance=Expense(expense_date=TEST_DATE))

    def test_save_never_calls_create_expense_and_delegates_to_update(self):
        form = ExpenseForm(
            instance=self.expense,
            data=self._edit_data(self.expense, reason='Motivo corregido', amount='26.00'),
        )
        self.assertTrue(form.is_valid(), form.errors)
        with patch('apps.operations.services.create_expense') as mocked_create:
            with patch(
                'apps.operations.services.update_expense',
                wraps=update_expense,
            ) as mocked_update:
                updated = form.save()
        mocked_create.assert_not_called()
        mocked_update.assert_called_once()
        self.assertEqual(updated.reason, 'Motivo corregido')
        self.assertEqual(updated.amount, Decimal('26.00'))

    def test_direct_create_expense_still_raises_canonical_validation_error(self):
        with self.assertRaisesMessage(ValidationError, 'solicitud de gasto'):
            create_expense(
                allocation=self.allocation,
                expense_date=TEST_DATE,
                category='food',
                amount=Decimal('10.00'),
                reason='Bypass',
                provider_or_recipient='Proveedor',
                payment_method='bank_transfer',
                description='',
                observations='',
                support_file=SimpleUploadedFile('x.pdf', b'%PDF'),
            )

    def test_expense_create_view_does_not_instantiate_expense_form(self):
        self.client.force_login(self.user)
        with patch('apps.operations.views.expenses.ExpenseForm') as form_cls:
            response = self.client.get(reverse('expense_create'))
            self.assertEqual(response.status_code, 302)
            form_cls.assert_not_called()
            post = self.client.post(reverse('expense_create'), data=self._edit_data(self.expense))
            self.assertEqual(post.status_code, 302)
            form_cls.assert_not_called()

    def test_valid_active_allocation_appears(self):
        other = create_allocation(
            donation=self.donation,
            project=self.project,
            amount=Decimal('50.00'),
            category='training_entrepreneurship',
        )
        form = ExpenseForm(instance=self.expense)
        pks = list(form.fields['allocation'].queryset.values_list('pk', flat=True))
        self.assertIn(self.allocation.pk, pks)
        self.assertIn(other.pk, pks)

    def test_finished_and_annulled_non_current_allocations_excluded(self):
        finished = create_allocation(
            donation=self.donation,
            project=self.project,
            amount=Decimal('40.00'),
            category='infrastructure_supply',
            status=FundAllocation.Status.FINISHED,
        )
        annulled = create_allocation(
            donation=self.donation,
            project=self.project,
            amount=Decimal('40.00'),
            category='communication_networks',
            status=FundAllocation.Status.ANNULLED,
        )
        form = ExpenseForm(instance=self.expense)
        pks = set(form.fields['allocation'].queryset.values_list('pk', flat=True))
        self.assertNotIn(finished.pk, pks)
        self.assertNotIn(annulled.pk, pks)

    def test_closed_project_and_non_received_donations_excluded(self):
        closed_project = create_project(code='PRJ-CLOSED-EF', name='Cerrado')
        closed_project.status = Project.Status.CLOSED
        closed_project.save(update_fields=('status', 'updated_at'))
        closed_alloc = create_allocation(
            donation=create_donation(code='DON-CLOSED-EF', donor=self.donor, amount=Decimal('80.00')),
            project=closed_project,
            amount=Decimal('40.00'),
        )
        registered_donation = create_donation(
            code='DON-REG-EF',
            donor=self.donor,
            amount=Decimal('80.00'),
            status=Donation.Status.REGISTERED,
        )
        registered_alloc = create_allocation(
            donation=registered_donation,
            project=self.project,
            amount=Decimal('40.00'),
        )
        annulled_donation = create_donation(
            code='DON-ANN-EF',
            donor=self.donor,
            amount=Decimal('80.00'),
            status=Donation.Status.ANNULLED,
        )
        annulled_don_alloc = create_allocation(
            donation=annulled_donation,
            project=self.project,
            amount=Decimal('40.00'),
        )
        form = ExpenseForm(instance=self.expense)
        pks = set(form.fields['allocation'].queryset.values_list('pk', flat=True))
        self.assertNotIn(closed_alloc.pk, pks)
        self.assertNotIn(registered_alloc.pk, pks)
        self.assertNotIn(annulled_don_alloc.pk, pks)

    def test_current_historical_allocation_preserved_only(self):
        historical = create_allocation(
            donation=self.donation,
            project=self.project,
            amount=Decimal('80.00'),
            category='institutional_relations',
        )
        other_finished = create_allocation(
            donation=self.donation,
            project=self.project,
            amount=Decimal('40.00'),
            category='infrastructure_supply',
            status=FundAllocation.Status.FINISHED,
        )
        expense = self._expense_with_support(
            allocation=historical,
            amount=Decimal('10.00'),
            reason='Histórico',
        )
        historical.status = FundAllocation.Status.FINISHED
        historical.save(update_fields=('status', 'updated_at'))

        form = ExpenseForm(instance=expense)
        pks = list(form.fields['allocation'].queryset.values_list('pk', flat=True))
        self.assertEqual(pks.count(historical.pk), 1)
        self.assertIn(historical.pk, pks)
        self.assertNotIn(other_finished.pk, pks)
        self.assertIn(self.allocation.pk, pks)

    def test_ordering_is_deterministic_and_labels_render(self):
        second = create_allocation(
            donation=self.donation,
            project=self.project,
            amount=Decimal('30.00'),
            category='training_entrepreneurship',
        )
        form = ExpenseForm(instance=self.expense)
        ordered = list(form.fields['allocation'].queryset.values_list('pk', flat=True))
        self.assertEqual(
            ordered,
            list(
                operational_fund_allocation_choices(
                    include_allocation_id=self.expense.allocation_id,
                ).values_list('pk', flat=True)
            ),
        )
        label = form.fields['allocation'].label_from_instance(
            form.fields['allocation'].queryset.get(pk=second.pk)
        )
        self.assertIn('Ejecutado:', label)
        self.assertIn('Disponible:', label)

    def test_crafted_ineligible_allocation_posts_are_rejected(self):
        finished = create_allocation(
            donation=self.donation,
            project=self.project,
            amount=Decimal('90.00'),
            category='communication_networks',
            status=FundAllocation.Status.FINISHED,
        )
        closed_project = create_project(code='PRJ-POST-CLOSED', name='Cerrado POST')
        closed_project.status = Project.Status.CLOSED
        closed_project.save(update_fields=('status', 'updated_at'))
        closed_alloc = create_allocation(
            donation=create_donation(code='DON-POST-CLOSED', donor=self.donor, amount=Decimal('90.00')),
            project=closed_project,
            amount=Decimal('50.00'),
        )
        registered_alloc = create_allocation(
            donation=create_donation(
                code='DON-POST-REG',
                donor=self.donor,
                amount=Decimal('90.00'),
                status=Donation.Status.REGISTERED,
            ),
            project=self.project,
            amount=Decimal('50.00'),
        )
        for target in (finished, closed_alloc, registered_alloc):
            with self.subTest(target=target.pk):
                before_amount = self.expense.amount
                before_allocation = self.expense.allocation_id
                audits_before = AuditLog.objects.filter(entity_id=str(self.expense.pk)).count()
                form = ExpenseForm(
                    instance=self.expense,
                    data=self._edit_data(self.expense, allocation=target.pk, reason='Intento inválido'),
                )
                self.assertFalse(form.is_valid())
                self.assertIn('allocation', form.errors)
                self.expense.refresh_from_db()
                self.assertEqual(self.expense.amount, before_amount)
                self.assertEqual(self.expense.allocation_id, before_allocation)
                self.assertEqual(
                    AuditLog.objects.filter(entity_id=str(self.expense.pk)).count(),
                    audits_before,
                )

    def test_valid_reassignment_via_update_view_succeeds_and_writes_audit(self):
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
        self.client.force_login(self.user)
        response = self.client.post(
            reverse('expense_update', args=[self.expense.pk]),
            data=self._edit_data(
                self.expense,
                allocation=target.pk,
                amount='20.00',
                reason='Reasignado válido',
            ),
        )
        self.assertEqual(response.status_code, 302)
        self.expense.refresh_from_db()
        self.assertEqual(self.expense.allocation_id, target.pk)
        self.assertEqual(self.expense.amount, Decimal('20.00'))
        self.assertEqual(
            AuditLog.objects.filter(
                entity_id=str(self.expense.pk),
                action=AuditLog.Action.UPDATED,
            ).count(),
            audits_before + 1,
        )

    def test_unchanged_historical_current_allows_other_field_edit(self):
        historical = create_allocation(
            donation=self.donation,
            project=self.project,
            amount=Decimal('120.00'),
            category='training_entrepreneurship',
        )
        expense = self._expense_with_support(
            allocation=historical,
            amount=Decimal('15.00'),
            reason='Original histórico',
        )
        historical.status = FundAllocation.Status.FINISHED
        historical.save(update_fields=('status', 'updated_at'))
        form = ExpenseForm(
            instance=expense,
            data=self._edit_data(expense, reason='Solo cambia motivo'),
        )
        self.assertTrue(form.is_valid(), form.errors)
        updated = form.save()
        self.assertEqual(updated.allocation_id, historical.pk)
        self.assertEqual(updated.reason, 'Solo cambia motivo')

    def test_insufficient_target_balance_rejected_without_mutation(self):
        target = create_allocation(
            donation=self.donation,
            project=self.project,
            amount=Decimal('30.00'),
            category='institutional_relations',
        )
        self._expense_with_support(
            allocation=target,
            amount=Decimal('25.00'),
            reason='Consume casi todo',
        )
        before = (self.expense.allocation_id, self.expense.amount, self.expense.reason)
        audits_before = AuditLog.objects.filter(entity_id=str(self.expense.pk)).count()
        form = ExpenseForm(
            instance=self.expense,
            data=self._edit_data(
                self.expense,
                allocation=target.pk,
                amount='20.00',
                reason='Sin saldo suficiente',
            ),
        )
        self.assertFalse(form.is_valid())
        self.assertTrue(form.errors)
        self.expense.refresh_from_db()
        self.assertEqual(
            (self.expense.allocation_id, self.expense.amount, self.expense.reason),
            before,
        )
        self.assertEqual(
            AuditLog.objects.filter(entity_id=str(self.expense.pk)).count(),
            audits_before,
        )

    def test_expense_request_choices_still_exclude_zero_balance(self):
        depleted = create_allocation(
            donation=self.donation,
            project=self.project,
            amount=Decimal('40.00'),
            category='communication_networks',
        )
        self._expense_with_support(
            allocation=depleted,
            amount=Decimal('40.00'),
            reason='Agota saldo',
        )
        er_pks = set(
            expense_request_allocation_choices(project=self.project).values_list('pk', flat=True)
        )
        op_pks = set(
            operational_fund_allocation_choices(project=self.project).values_list('pk', flat=True)
        )
        self.assertIn(depleted.pk, op_pks)
        self.assertNotIn(depleted.pk, er_pks)
        self.assertIn(self.allocation.pk, er_pks)

    def test_expense_request_include_still_bypasses_only_balance_not_terminal(self):
        depleted = create_allocation(
            donation=self.donation,
            project=self.project,
            amount=Decimal('35.00'),
            category='infrastructure_supply',
        )
        self._expense_with_support(
            allocation=depleted,
            amount=Decimal('35.00'),
            reason='Agota include',
        )
        included = expense_request_allocation_choices(
            project=self.project,
            include_allocation_id=depleted.pk,
        )
        self.assertIn(depleted.pk, included.values_list('pk', flat=True))

        finished = create_allocation(
            donation=self.donation,
            project=self.project,
            amount=Decimal('20.00'),
            category='institutional_relations',
            status=FundAllocation.Status.FINISHED,
        )
        with_finished = expense_request_allocation_choices(
            project=self.project,
            include_allocation_id=finished.pk,
        )
        self.assertNotIn(finished.pk, with_finished.values_list('pk', flat=True))
