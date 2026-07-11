import shutil
import tempfile

from django.contrib import admin
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, TestCase, override_settings

from apps.operations.admin import ExpenseAdmin, ExpenseAdminForm
from apps.operations.models import AuditLog, Expense, SupportingDocument
from apps.operations.services import validate_expense
from apps.operations.tests.helpers import TEST_DATE, create_expense, create_user


TEMP_MEDIA_ROOT = tempfile.mkdtemp()


@override_settings(MEDIA_ROOT=TEMP_MEDIA_ROOT)
class ExpenseValidationServiceTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TEMP_MEDIA_ROOT, ignore_errors=True)

    def setUp(self):
        self.user = create_user(username='expense-validator')
        self.expense = create_expense(reason='Gasto pendiente de validación')

    def create_support(self, name='validation.pdf'):
        return SupportingDocument.objects.create(
            expense=self.expense,
            title='Soporte de validación',
            document=SimpleUploadedFile(name, b'validation-content', content_type='application/pdf'),
        )

    def admin_form_data(self):
        return {
            'allocation': self.expense.allocation.pk,
            'expense_date': TEST_DATE,
            'category': self.expense.category,
            'amount': self.expense.amount,
            'currency': self.expense.currency,
            'reason': self.expense.reason,
            'provider_or_recipient': self.expense.provider_or_recipient,
            'payment_method': self.expense.payment_method,
            'description': self.expense.description,
            'observations': self.expense.observations,
            'status': Expense.Status.VALIDATED,
            'validated_by': '',
            'validated_at': '',
        }

    def test_validate_expense_without_support_fails_and_preserves_state(self):
        with self.assertRaisesMessage(ValidationError, 'debe tener al menos un documento soporte'):
            validate_expense(self.expense.pk, self.user)

        self.expense.refresh_from_db()
        self.assertEqual(self.expense.status, Expense.Status.REGISTERED)
        self.assertIsNone(self.expense.validated_by)
        self.assertFalse(AuditLog.objects.filter(action=AuditLog.Action.VALIDATED).exists())

    def test_validate_expense_with_support_updates_state_and_creates_audit(self):
        self.create_support()

        validated_expense = validate_expense(self.expense.pk, self.user)

        self.assertEqual(validated_expense.status, Expense.Status.VALIDATED)
        self.assertEqual(validated_expense.validated_by, self.user)
        self.assertIsNotNone(validated_expense.validated_at)
        self.assertTrue(
            AuditLog.objects.filter(
                action=AuditLog.Action.VALIDATED,
                entity_id=str(self.expense.pk),
                user=self.user,
                summary='Gasto validado.',
            ).exists()
        )

    def test_validate_expense_is_idempotent_after_successful_transition(self):
        self.create_support()
        validate_expense(self.expense.pk, self.user)

        validate_expense(self.expense.pk, self.user)

        self.assertEqual(
            AuditLog.objects.filter(
                action=AuditLog.Action.VALIDATED,
                entity_id=str(self.expense.pk),
            ).count(),
            1,
        )

    def test_expense_admin_form_rejects_validation_without_support(self):
        form = ExpenseAdminForm(data=self.admin_form_data(), instance=self.expense)

        self.assertFalse(form.is_valid())
        self.assertIn('Un gasto validado debe tener al menos un documento soporte.', form.non_field_errors())

    def test_expense_admin_routes_valid_transition_through_domain_service(self):
        self.create_support()
        form = ExpenseAdminForm(data=self.admin_form_data(), instance=self.expense)
        self.assertTrue(form.is_valid(), form.errors)
        request = RequestFactory().post('/admin/operations/expense/')
        request.user = self.user
        model_admin = ExpenseAdmin(Expense, admin.site)

        model_admin.save_model(request, form.save(commit=False), form, change=True)

        self.expense.refresh_from_db()
        self.assertEqual(self.expense.status, Expense.Status.VALIDATED)
        self.assertTrue(
            AuditLog.objects.filter(
                action=AuditLog.Action.VALIDATED,
                entity_id=str(self.expense.pk),
                user=self.user,
            ).exists()
        )
