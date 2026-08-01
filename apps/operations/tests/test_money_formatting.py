from decimal import Decimal

from django.test import SimpleTestCase, TestCase

from apps.operations.forms import DonationForm, ExpenseForm, FundAllocationForm, ProjectForm
from apps.operations.models import Donation, Expense, FundAllocation, Project
from apps.operations.tests.helpers import TEST_DATE, create_allocation, create_donation, create_institution, create_project
from apps.operations.templatetags.operations_format import money_es


class MoneyEsFilterTests(SimpleTestCase):
    def test_formats_supported_values(self):
        cases = [
            (Decimal('0'), '0,00'),
            (Decimal('12'), '12,00'),
            (Decimal('1234.5'), '1.234,50'),
            (Decimal('10000000'), '10.000.000,00'),
            ('1234.50', '1.234,50'),
            (None, '0,00'),
            ('invalid', 'invalid'),
        ]
        for value, expected in cases:
            with self.subTest(value=value):
                self.assertEqual(money_es(value), expected)


class LocalizedMoneyFormTests(TestCase):
    def test_all_operational_money_fields_accept_canonical_and_spanish_values(self):
        form_fields = [
            ProjectForm.base_fields['estimated_budget'],
            DonationForm.base_fields['amount'],
            FundAllocationForm.base_fields['amount'],
            ExpenseForm.base_fields['amount'],
        ]
        for field in form_fields:
            with self.subTest(field=field):
                self.assertEqual(field.clean('10000000.00'), Decimal('10000000.00'))
                self.assertEqual(field.clean('10.000.000,00'), Decimal('10000000.00'))
                with self.assertRaisesMessage(Exception, 'monto válido'):
                    field.clean('10.00.000,00')

        donor = create_institution()
        project = create_project()
        donation = create_donation(donor=donor)
        allocation = create_allocation(donation=donation, project=project)
        initial_counts = {
            FundAllocation: FundAllocation.objects.count(),
            Donation: Donation.objects.count(),
            Expense: Expense.objects.count(),
        }
        allocation_data = {
            'donation': donation.pk,
            'project': project.pk,
            'budget_category': 'health_psychosocial',
            'responsible_person': '',
            'allocation_date': TEST_DATE,
            'notes': '',
        }
        for amount in ('0', '-1', 'not-a-number'):
            with self.subTest(model=FundAllocation, amount=amount):
                form = FundAllocationForm(data={**allocation_data, 'amount': amount})
                self.assertFalse(form.is_valid())
                self.assertIn('amount', form.errors)

        invalid_forms = [
            DonationForm(data={
                'donor': donor.pk,
                'donation_type': 'money',
                'amount': 'not-a-number',
                'objective': 'Aporte inválido',
                'restrictions': '',
                'commitment_date': '',
                'received_date': '',
                'support_reference': '',
            }),
            ExpenseForm(data={
                'allocation': allocation.pk,
                'expense_date': TEST_DATE,
                'category': 'food',
                'amount': 'not-a-number',
                'reason': 'Monto inválido',
                'provider_or_recipient': 'Proveedor',
                'payment_method': 'bank_transfer',
                'description': '',
                'observations': '',
                'status': Expense.Status.REGISTERED,
            }),
        ]
        for form in invalid_forms:
            with self.subTest(form=type(form).__name__):
                self.assertFalse(form.is_valid())
                self.assertIn('amount', form.errors)
        for model, initial_count in initial_counts.items():
            self.assertEqual(model.objects.count(), initial_count)

    def test_project_edit_displays_localized_value_and_repeated_saves_preserve_it(self):
        project = Project.objects.create(name='Monto grande', estimated_budget=Decimal('10000000.00'))
        rendered = str(ProjectForm(instance=project)['estimated_budget'])
        self.assertIn('value="10.000.000,00"', rendered)
        self.assertIn('js-money-input', rendered)

        data = {
            'name': project.name,
            'description': '',
            'objective': '',
            'location': '',
            'estimated_budget': '10.000.000,00',
            'start_date': '',
            'end_date': '',
        }
        for _ in range(2):
            form = ProjectForm(data=data, instance=project)
            self.assertTrue(form.is_valid(), form.errors)
            project = form.save()
            self.assertEqual(project.estimated_budget, Decimal('10000000.00'))

    def test_other_field_error_preserves_submitted_money_text(self):
        form = ProjectForm(data={'name': '', 'estimated_budget': '10.000.000,00'})
        self.assertFalse(form.is_valid())
        self.assertIn('value="10.000.000,00"', str(form['estimated_budget']))
