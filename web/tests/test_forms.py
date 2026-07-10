import shutil
import tempfile
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from apps.operations.forms import DonationForm, ExpenseForm, FundAllocationForm, InstitutionForm, ProjectForm
from apps.operations.models import Donation, Expense, FundAllocation, Institution, Project, SupportingDocument
from apps.operations.tests.helpers import TEST_DATE, create_allocation, create_donation, create_institution, create_project


class FormTests(TestCase):
    def setUp(self):
        self.temp_media = tempfile.mkdtemp()
        self.override = override_settings(MEDIA_ROOT=self.temp_media)
        self.override.enable()
        self.donor = create_institution()
        self.project = create_project()
        self.donation = create_donation(donor=self.donor, amount=Decimal('100.00'))

    def tearDown(self):
        self.override.disable()
        shutil.rmtree(self.temp_media, ignore_errors=True)

    def test_institution_form_saves_valid_data(self):
        upload = SimpleUploadedFile('registro.pdf', b'documento legal', content_type='application/pdf')
        form = InstitutionForm(
            data={
                'name': 'Diocese Partner',
                'institution_type': 'parish',
                'role': Institution.Role.ALLY,
                'country': 'VE',
                'contact_email': 'ally@example.com',
                'contact_phone': '',
                'responsible_person': 'Coordinator',
                'legal_document': '',
                'status': Institution.Status.ACTIVE,
            },
            files={'legal_document': upload},
        )

        self.assertTrue(form.is_valid(), form.errors)
        institution = form.save()
        self.assertEqual(institution.role, Institution.Role.ALLY)
        self.assertEqual(institution.country.code, 'VE')
        self.assertTrue(institution.legal_document.name.startswith('institution_documents/'))

    def test_institution_form_shows_country_and_not_city(self):
        form = InstitutionForm()

        self.assertIn('country', form.fields)
        self.assertNotIn('city', form.fields)
        self.assertEqual(form.fields['country'].label, 'País')
        self.assertEqual(form.fields['institution_type'].widget.__class__.__name__, 'Select')

    def test_money_fields_and_selects_are_configured_for_forms(self):
        donation_form = DonationForm()
        project_form = ProjectForm()
        allocation_form = FundAllocationForm()
        expense_form = ExpenseForm()

        self.assertNotIn('code', donation_form.fields)
        self.assertNotIn('code', project_form.fields)
        self.assertEqual(donation_form.fields['donation_type'].widget.__class__.__name__, 'Select')
        self.assertEqual(donation_form.fields['currency'].widget.__class__.__name__, 'Select')
        self.assertIn('1500.00', donation_form.fields['amount'].help_text)
        self.assertIn('1500.00', project_form.fields['estimated_budget'].help_text)
        self.assertEqual(allocation_form.fields['budget_category'].widget.__class__.__name__, 'Select')
        self.assertFalse(allocation_form.fields['notes'].required)
        self.assertIn('1500.00', allocation_form.fields['amount'].help_text)
        self.assertEqual(expense_form.fields['category'].widget.__class__.__name__, 'Select')
        self.assertEqual(expense_form.fields['currency'].widget.__class__.__name__, 'Select')
        self.assertEqual(expense_form.fields['payment_method'].widget.__class__.__name__, 'Select')
        self.assertTrue(expense_form.fields['expense_date'].required)
        self.assertTrue(expense_form.fields['reason'].required)
        self.assertIn('1500.00', expense_form.fields['amount'].help_text)

    def test_project_form_saves_valid_data(self):
        form = ProjectForm(
            data={
                'name': 'Health support',
                'description': '',
                'objective': '',
                'responsible_unit': '',
                'location': '',
                'estimated_budget': '500.00',
                'start_date': '',
                'end_date': '',
                'status': Project.Status.ACTIVE,
            }
        )

        self.assertTrue(form.is_valid(), form.errors)
        project = form.save()
        self.assertEqual(project.estimated_budget, Decimal('500.00'))
        self.assertEqual(project.code, 'PRJ-000002')

    def test_donation_form_saves_valid_data(self):
        form = DonationForm(
            data={
                'donor': self.donor.pk,
                'donation_type': 'goods',
                'amount': '250.00',
                'currency': 'USD',
                'objective': 'Apoyar atención de emergencia',
                'restrictions': '',
                'commitment_date': '',
                'received_date': '',
                'status': Donation.Status.RECEIVED,
                'support_reference': '',
            }
        )

        self.assertTrue(form.is_valid(), form.errors)
        donation = form.save()
        self.assertEqual(donation.amount, Decimal('250.00'))
        self.assertEqual(donation.code, 'DON-000002')

    def test_forms_reject_zero_or_negative_money_where_validation_exists(self):
        donation_form = DonationForm(
            data={
                'donor': self.donor.pk,
                'donation_type': 'goods',
                'amount': '0.00',
                'currency': 'USD',
                'objective': '',
                'restrictions': '',
                'commitment_date': '',
                'received_date': '',
                'status': Donation.Status.RECEIVED,
                'support_reference': '',
            }
        )
        allocation_form = FundAllocationForm(
            data={
                'donation': self.donation.pk,
                'project': self.project.pk,
                'budget_category': 'health_psychosocial',
                'amount': '-1.00',
                'responsible_person': '',
                'allocation_date': TEST_DATE,
                'status': FundAllocation.Status.ACTIVE,
                'notes': '',
            }
        )

        self.assertFalse(donation_form.is_valid())
        self.assertFalse(allocation_form.is_valid())

    def test_allocation_form_rejects_over_allocation(self):
        create_allocation(donation=self.donation, project=self.project, amount=Decimal('90.00'))
        form = FundAllocationForm(
            data={
                'donation': self.donation.pk,
                'project': self.project.pk,
                'budget_category': 'health_psychosocial',
                'amount': '15.00',
                'responsible_person': '',
                'allocation_date': TEST_DATE,
                'status': FundAllocation.Status.ACTIVE,
                'notes': '',
            }
        )

        self.assertFalse(form.is_valid())
        self.assertIn('amount', form.errors)

    def test_expense_form_rejects_over_execution(self):
        allocation = create_allocation(donation=self.donation, project=self.project, amount=Decimal('40.00'))
        form = ExpenseForm(
            data={
                'allocation': allocation.pk,
                'expense_date': TEST_DATE,
                'category': 'food',
                'amount': '45.00',
                'currency': 'USD',
                'reason': 'Purchase',
                'provider_or_recipient': 'Provider A',
                'payment_method': 'bank_transfer',
                'description': '',
                'observations': '',
                'status': Expense.Status.REGISTERED,
            }
        )

        self.assertFalse(form.is_valid())
        self.assertIn('amount', form.errors)

    def test_validated_expense_requires_supporting_document(self):
        allocation = create_allocation(donation=self.donation, project=self.project, amount=Decimal('40.00'))
        form = ExpenseForm(
            data={
                'allocation': allocation.pk,
                'expense_date': TEST_DATE,
                'category': 'food',
                'amount': '10.00',
                'currency': 'USD',
                'reason': 'Purchase',
                'provider_or_recipient': 'Provider A',
                'payment_method': 'bank_transfer',
                'description': '',
                'observations': '',
                'status': Expense.Status.VALIDATED,
            }
        )

        self.assertFalse(form.is_valid())
        self.assertIn('documento soporte', form.errors['__all__'][0])

    def test_validated_expense_form_creates_supporting_document(self):
        allocation = create_allocation(donation=self.donation, project=self.project, amount=Decimal('40.00'))
        upload = SimpleUploadedFile('receipt.txt', b'receipt')
        form = ExpenseForm(
            data={
                'allocation': allocation.pk,
                'expense_date': TEST_DATE,
                'category': 'food',
                'amount': '10.00',
                'currency': 'USD',
                'reason': 'Purchase',
                'provider_or_recipient': 'Provider A',
                'payment_method': 'bank_transfer',
                'description': '',
                'observations': '',
                'status': Expense.Status.VALIDATED,
                'support_title': 'Receipt',
            },
            files={'support_file': upload},
        )

        self.assertTrue(form.is_valid(), form.errors)
        expense = form.save()
        self.assertEqual(SupportingDocument.objects.filter(expense=expense).count(), 1)

    def test_project_form_rejects_negative_budget(self):
        form = ProjectForm(
            data={
                'name': 'Negative budget',
                'description': '',
                'objective': '',
                'responsible_unit': '',
                'location': '',
                'estimated_budget': '-1.00',
                'start_date': '',
                'end_date': '',
                'status': Project.Status.ACTIVE,
            }
        )

        self.assertFalse(form.is_valid())
        self.assertIn('estimated_budget', form.errors)

    def test_project_form_rejects_end_date_before_start_date(self):
        form = ProjectForm(
            data={
                'name': 'Invalid dates',
                'description': '',
                'objective': '',
                'responsible_unit': '',
                'location': '',
                'estimated_budget': '0.00',
                'start_date': '2026-07-08',
                'end_date': '2026-07-07',
                'status': Project.Status.ACTIVE,
            }
        )

        self.assertFalse(form.is_valid())
        self.assertIn('end_date', form.errors)
