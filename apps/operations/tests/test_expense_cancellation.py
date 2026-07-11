from decimal import Decimal

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.test import RequestFactory, TestCase
from django.urls import reverse

from apps.operations.admin import ExpenseAdmin
from apps.operations.models import AuditLog, Expense, SupportingDocument
from apps.operations.services import (
    ExpenseFinalizedError,
    cancel_expense,
    create_expense as create_expense_service,
    update_expense,
    validate_expense,
)
from apps.operations.tests.helpers import TEST_DATE, create_allocation, create_expense, create_user


class ExpenseCancellationTests(TestCase):
    def setUp(self):
        self.user = create_user(username='expense-canceller')
        self.allocation = create_allocation(amount=Decimal('100.00'))
        self.expense = create_expense(
            allocation=self.allocation,
            amount=Decimal('30.00'),
            reason='Gasto anulable',
        )

    def update_values(self, expense=None, **overrides):
        # PRE: expense is a persisted editable/final expense used as update target.
        # POST: returns a complete update_expense argument mapping with overrides.
        expense = expense or self.expense
        values = {
            'expense': expense,
            'allocation': expense.allocation,
            'expense_date': expense.expense_date,
            'category': expense.category,
            'amount': expense.amount,
            'reason': expense.reason,
            'provider_or_recipient': expense.provider_or_recipient,
            'payment_method': expense.payment_method,
            'description': expense.description,
            'observations': expense.observations,
            'status': expense.status,
            'user': self.user,
        }
        values.update(overrides)
        return values

    def validate_current_expense(self):
        # PRE: the default expense is registered and has no validation support.
        # POST: returns it validated with persisted actor/date metadata.
        SupportingDocument.objects.create(
            expense=self.expense,
            title='Soporte',
            document='supporting_documents/support.pdf',
        )
        return validate_expense(self.expense.pk, self.user)

    def test_validated_expense_rejects_all_ordinary_updates_without_mutation(self):
        validated = self.validate_current_expense()
        before = Expense.objects.values().get(pk=validated.pk)
        other_allocation = create_allocation(
            donation=self.allocation.donation,
            project=self.allocation.project,
            amount=Decimal('40.00'),
            category='communication_networks',
        )
        attempts = (
            {'amount': Decimal('15.00')},
            {'allocation': other_allocation},
            {'status': Expense.Status.REGISTERED},
        )
        for overrides in attempts:
            with self.subTest(overrides=overrides):
                with self.assertRaises(ExpenseFinalizedError):
                    update_expense(**self.update_values(validated, **overrides))
                self.assertEqual(
                    Expense.objects.values().get(pk=validated.pk),
                    before,
                )

    def test_cancelled_expense_rejects_update_and_second_cancellation(self):
        cancelled = cancel_expense(
            self.expense.pk,
            actor=self.user,
            reason='Registro incorrecto.',
        )
        before = Expense.objects.values().get(pk=cancelled.pk)

        with self.assertRaises(ExpenseFinalizedError):
            update_expense(**self.update_values(cancelled, amount=Decimal('1.00')))
        with self.assertRaises(ExpenseFinalizedError):
            cancel_expense(cancelled.pk, actor=self.user, reason='Otra razón')

        self.assertEqual(Expense.objects.values().get(pk=cancelled.pk), before)
        self.assertEqual(
            AuditLog.objects.filter(
                action=AuditLog.Action.EXPENSE_CANCELLED,
                entity_id=str(cancelled.pk),
            ).count(),
            1,
        )

    def test_pending_and_validated_expenses_can_be_cancelled_and_audited(self):
        pending = cancel_expense(
            self.expense.pk,
            actor=self.user,
            reason='  Duplicado operativo.  ',
        )
        self.assertEqual(pending.status, Expense.Status.CANCELLED)
        self.assertIsNone(pending.validated_by)
        self.assertIsNone(pending.validated_at)
        log = AuditLog.objects.get(
            action=AuditLog.Action.EXPENSE_CANCELLED,
            entity_id=str(pending.pk),
        )
        self.assertEqual(log.user, self.user)
        self.assertIn('registered', log.summary)
        self.assertNotIn('Duplicado operativo.', log.summary)
        self.assertIn('Razón registrada por separado.', log.summary)

        validated_expense = create_expense(
            allocation=self.allocation,
            amount=Decimal('20.00'),
            reason='Validado anulable',
        )
        SupportingDocument.objects.create(
            expense=validated_expense,
            title='Soporte validado',
            document='supporting_documents/validated.pdf',
        )
        validated_expense = validate_expense(validated_expense.pk, self.user)
        validator_id = validated_expense.validated_by_id
        validated_at = validated_expense.validated_at

        cancelled_validated = cancel_expense(
            validated_expense.pk,
            actor=self.user,
            reason='Validación posteriormente anulada.',
        )

        self.assertEqual(cancelled_validated.status, Expense.Status.CANCELLED)
        self.assertEqual(cancelled_validated.validated_by_id, validator_id)
        self.assertEqual(cancelled_validated.validated_at, validated_at)

    def test_cancel_requires_authenticated_actor_and_non_empty_reason(self):
        before = Expense.objects.values().get(pk=self.expense.pk)
        with self.assertRaises(ValidationError):
            cancel_expense(self.expense.pk, actor=None, reason='Razón')
        with self.assertRaises(ValidationError):
            cancel_expense(self.expense.pk, actor=self.user, reason='   ')
        self.assertEqual(Expense.objects.values().get(pk=self.expense.pk), before)
        self.assertFalse(
            AuditLog.objects.filter(action=AuditLog.Action.EXPENSE_CANCELLED).exists()
        )

    def test_cancelled_expense_releases_balance_and_new_expense_can_use_it(self):
        self.assertEqual(self.allocation.executed_amount, Decimal('30.00'))
        self.assertEqual(self.allocation.available_balance, Decimal('70.00'))

        cancel_expense(self.expense.pk, actor=self.user, reason='Liberar saldo.')
        self.allocation.refresh_from_db()

        self.assertEqual(self.allocation.executed_amount, Decimal('0.00'))
        self.assertEqual(self.allocation.available_balance, Decimal('100.00'))
        replacement = create_expense_service(
            allocation=self.allocation,
            expense_date=TEST_DATE,
            category='food',
            amount=Decimal('100.00'),
            reason='Reemplazo',
            provider_or_recipient='Proveedor',
            payment_method='cash',
            description='',
            observations='',
            status=Expense.Status.REGISTERED,
        )
        self.assertEqual(replacement.amount, Decimal('100.00'))

    def test_cancellation_view_get_is_read_only_and_post_requires_permission(self):
        url = reverse('expense_cancel', args=(self.expense.pk,))
        limited_user = get_user_model().objects.create_user(
            username='expense-cancellation-limited',
            password='pass-12345',
        )
        self.client.force_login(limited_user)
        before = Expense.objects.values().get(pk=self.expense.pk)

        denied_get = self.client.get(url)
        denied_post = self.client.post(url, {'reason': 'No autorizado'})

        self.assertEqual(denied_get.status_code, 403)
        self.assertEqual(denied_post.status_code, 403)
        self.assertEqual(Expense.objects.values().get(pk=self.expense.pk), before)

        permission = Permission.objects.get(
            codename='change_expense',
            content_type__app_label='operations',
        )
        limited_user.user_permissions.add(permission)
        limited_user = get_user_model().objects.get(pk=limited_user.pk)
        self.client.force_login(limited_user)
        get_response = self.client.get(url)
        self.assertEqual(get_response.status_code, 200)
        self.assertNotContains(get_response, 'name="status"')
        self.assertNotContains(get_response, 'name="amount"')
        self.assertEqual(Expense.objects.values().get(pk=self.expense.pk), before)

        post_response = self.client.post(
            url,
            {'reason': 'Anulación autorizada.', 'status': Expense.Status.VALIDATED, 'amount': '1'},
        )
        self.assertEqual(post_response.status_code, 302)
        self.assertEqual(
            post_response['Location'],
            reverse('expense_detail', args=(self.expense.pk,)),
        )
        self.expense.refresh_from_db()
        self.assertEqual(self.expense.status, Expense.Status.CANCELLED)
        self.assertEqual(self.expense.amount, Decimal('30.00'))

    def test_finalized_expenses_cannot_use_ordinary_update_or_delete_views(self):
        self.validate_current_expense()
        self.client.force_login(create_user(username='finalized-superuser'))
        urls = (
            reverse('expense_update', args=(self.expense.pk,)),
            reverse('expense_delete', args=(self.expense.pk,)),
        )
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)
                self.assertEqual(self.client.post(url).status_code, 403)
        self.assertTrue(Expense.objects.filter(pk=self.expense.pk).exists())

        cancel_expense(self.expense.pk, actor=self.user, reason='Anulación final.')
        for url in urls:
            with self.subTest(cancelled_url=url):
                self.assertEqual(self.client.get(url).status_code, 403)
                self.assertEqual(self.client.post(url).status_code, 403)
        self.assertTrue(Expense.objects.filter(pk=self.expense.pk).exists())

    def test_detail_actions_match_editable_validated_and_cancelled_states(self):
        user = create_user(username='expense-ui-admin')
        self.client.force_login(user)
        detail_url = reverse('expense_detail', args=(self.expense.pk,))

        editable_response = self.client.get(detail_url)
        self.assertContains(editable_response, reverse('expense_update', args=(self.expense.pk,)))
        self.assertContains(editable_response, reverse('expense_delete', args=(self.expense.pk,)))
        self.assertContains(editable_response, reverse('expense_cancel', args=(self.expense.pk,)))

        self.validate_current_expense()
        validated_response = self.client.get(detail_url)
        self.assertNotContains(validated_response, reverse('expense_update', args=(self.expense.pk,)))
        self.assertNotContains(validated_response, reverse('expense_delete', args=(self.expense.pk,)))
        self.assertContains(validated_response, reverse('expense_cancel', args=(self.expense.pk,)))

        cancel_expense(self.expense.pk, actor=user, reason='Cierre UI.')
        cancelled_response = self.client.get(detail_url)
        self.assertNotContains(cancelled_response, reverse('expense_update', args=(self.expense.pk,)))
        self.assertNotContains(cancelled_response, reverse('expense_delete', args=(self.expense.pk,)))
        self.assertNotContains(cancelled_response, reverse('expense_cancel', args=(self.expense.pk,)))

    def test_admin_makes_finalized_expense_readonly_and_not_deletable(self):
        validated = self.validate_current_expense()
        model_admin = ExpenseAdmin(Expense, admin.site)
        request = RequestFactory().get('/admin/operations/expense/')
        request.user = create_user(username='expense-admin')

        readonly = model_admin.get_readonly_fields(request, validated)

        self.assertIn('amount', readonly)
        self.assertIn('allocation', readonly)
        self.assertIn('status', readonly)
        self.assertIn('validated_by', readonly)
        self.assertIn('validated_at', readonly)
        self.assertFalse(model_admin.has_delete_permission(request, validated))
        with self.assertRaises(ExpenseFinalizedError):
            model_admin.save_model(request, validated, form=None, change=True)

        cancelled = cancel_expense(
            validated.pk,
            actor=request.user,
            reason='Anulación administrativa segura.',
        )
        self.assertFalse(model_admin.has_delete_permission(request, cancelled))
        with self.assertRaises(ExpenseFinalizedError):
            model_admin.delete_model(request, cancelled)
