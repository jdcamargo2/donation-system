import tempfile
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse

from apps.operations.forms import ExpenseForm
from apps.operations.models import AuditLog, Expense, Project
from apps.operations.services import (
    ExpenseFinalizedError,
    annul_expense,
    create_expense,
    update_expense,
)
from apps.operations.tests.helpers import TEST_DATE, create_allocation, create_user


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class ExpenseLifecycleTests(TestCase):
    def setUp(self):
        self.user = create_user()
        self.allocation = create_allocation(amount=Decimal('100.00'))
        self.allocation.project.status = Project.Status.ACTIVE
        self.allocation.project.save(update_fields=('status', 'updated_at'))

    def expense_data(self, amount='30.00'):
        # PRE: amount is candidate browser text for a payment already executed.
        # POST: returns otherwise-valid ExpenseForm data without lifecycle input.
        return {
            'allocation': self.allocation.pk,
            'expense_date': TEST_DATE,
            'category': 'food',
            'amount': amount,
            'reason': 'Pago autorizado',
            'provider_or_recipient': 'Proveedor',
            'payment_method': 'bank_transfer',
            'description': '',
            'observations': '',
            'support_title': 'Factura 001',
        }

    def create_registered_expense(self, amount=Decimal('30.00')):
        # PRE: allocation has enough balance and the actor is authenticated.
        # POST: creates a REGISTERED expense with one protected support and audit event.
        return create_expense(
            allocation=self.allocation,
            expense_date=TEST_DATE,
            category='food',
            amount=amount,
            reason='Pago autorizado',
            provider_or_recipient='Proveedor',
            payment_method='bank_transfer',
            description='',
            observations='',
            actor=self.user,
            support_title='Factura 001',
            support_file=SimpleUploadedFile('factura.pdf', b'%PDF-1.4 soporte'),
        )

    def test_form_requires_support_and_excludes_status(self):
        form = ExpenseForm(data=self.expense_data())
        self.assertFalse(form.is_valid())
        self.assertIn('support_file', form.errors)
        self.assertNotIn('status', form.fields)

    def test_create_counts_immediately_and_creates_support_and_audit(self):
        expense = self.create_registered_expense()
        self.assertEqual(expense.status, Expense.Status.REGISTERED)
        self.assertEqual(self.allocation.executed_amount, Decimal('30.00'))
        self.assertEqual(self.allocation.available_balance, Decimal('70.00'))
        self.assertTrue(expense.supporting_documents.exists())
        self.assertTrue(AuditLog.objects.filter(entity_id=str(expense.pk)).exists())

    def test_update_amount_rechecks_balance_and_preserves_code(self):
        expense = self.create_registered_expense()
        code = expense.code
        updated = update_expense(
            expense=expense,
            allocation=self.allocation,
            expense_date=expense.expense_date,
            category=expense.category,
            amount=Decimal('40.00'),
            reason=expense.reason,
            provider_or_recipient=expense.provider_or_recipient,
            payment_method=expense.payment_method,
            description='',
            observations='',
            actor=self.user,
        )
        self.assertEqual(updated.code, code)
        self.assertEqual(self.allocation.executed_amount, Decimal('40.00'))

    def test_annul_releases_balance_once_and_preserves_terminal_metadata(self):
        expense = self.create_registered_expense()
        annulled = annul_expense(
            expense.pk, actor=self.user, reason='Pago duplicado confirmado.'
        )
        self.assertEqual(annulled.status, Expense.Status.ANNULLED)
        self.assertEqual(self.allocation.executed_amount, Decimal('0.00'))
        self.assertEqual(annulled.terminal_by, self.user)
        self.assertIsNotNone(annulled.terminal_at)
        metadata = (annulled.terminal_reason, annulled.terminal_at, annulled.terminal_by_id)
        audit_count = AuditLog.objects.filter(entity_id=str(expense.pk)).count()
        with self.assertRaises(ExpenseFinalizedError):
            annul_expense(expense.pk, actor=self.user, reason='Segundo intento inválido.')
        annulled.refresh_from_db()
        self.assertEqual(metadata, (annulled.terminal_reason, annulled.terminal_at, annulled.terminal_by_id))
        self.assertEqual(AuditLog.objects.filter(entity_id=str(expense.pk)).count(), audit_count)

    def test_annulled_expense_is_immutable_and_ui_has_no_review_actions(self):
        expense = self.create_registered_expense()
        annul_expense(expense.pk, actor=self.user, reason='Pago duplicado confirmado.')
        self.client.force_login(self.user)
        response = self.client.get(reverse('expense_detail', args=(expense.pk,)))
        self.assertNotContains(response, reverse('expense_update', args=(expense.pk,)))
        self.assertNotContains(response, 'Validar')
        self.assertNotContains(response, 'Rechazar')
        self.assertNotContains(response, 'Cancelar')


class ExpenseLifecycleMigrationTests(TransactionTestCase):
    migrate_from = ('operations', '0009_separate_lifecycle_and_financial_progress')
    migrate_to = ('operations', '0010_simplify_expense_lifecycle')

    def setUp(self):
        self.executor = MigrationExecutor(connection)
        self.executor.migrate([self.migrate_from])
        old_apps = self.executor.loader.project_state([self.migrate_from]).apps
        Institution = old_apps.get_model('operations', 'Institution')
        Project = old_apps.get_model('operations', 'Project')
        Donation = old_apps.get_model('operations', 'Donation')
        FundAllocation = old_apps.get_model('operations', 'FundAllocation')
        Expense = old_apps.get_model('operations', 'Expense')
        donor = Institution.objects.create(
            name='Donante migración', institution_type='foundation', role='donor', country='VE'
        )
        project = Project.objects.create(code='PRJ-MIG-EXP', name='Proyecto migración')
        donation = Donation.objects.create(
            code='DON-MIG-EXP', donor=donor, amount=Decimal('500.00'), objective='Migración'
        )
        allocation = FundAllocation.objects.create(
            code='ASG-MIG-EXP',
            donation=donation,
            project=project,
            budget_category='health_psychosocial',
            amount=Decimal('500.00'),
            allocation_date=TEST_DATE,
        )
        for index, status in enumerate(
            ('registered', 'validated', 'in_review', 'rejected', 'cancelled', 'annulled'),
            start=1,
        ):
            Expense.objects.create(
                code=f'GAS-MIG-{index:03d}',
                allocation=allocation,
                expense_date=TEST_DATE,
                category='food',
                amount=Decimal('10.00'),
                reason=f'Gasto {status}',
                provider_or_recipient='Proveedor',
                payment_method='bank_transfer',
                status=status,
            )

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_migration_maps_every_legacy_status_without_deleting_rows(self):
        self.executor = MigrationExecutor(connection)
        self.executor.migrate([self.migrate_to])
        apps = self.executor.loader.project_state([self.migrate_to]).apps
        Expense = apps.get_model('operations', 'Expense')
        statuses = dict(Expense.objects.values_list('reason', 'status'))
        self.assertEqual(Expense.objects.count(), 6)
        for legacy in ('registered', 'validated', 'in_review'):
            self.assertEqual(statuses[f'Gasto {legacy}'], 'registered')
        for legacy in ('rejected', 'cancelled', 'annulled'):
            self.assertEqual(statuses[f'Gasto {legacy}'], 'annulled')
        self.assertEqual(
            Expense.objects.get(reason='Gasto rejected').terminal_reason,
            'Migrado desde estado REJECTED',
        )
