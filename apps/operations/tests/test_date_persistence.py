from datetime import date
from decimal import Decimal

from django.contrib.messages import get_messages
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from apps.operations.forms import DonationForm, ExpenseForm, FundAllocationForm, ProjectForm
from apps.operations.models import Donation, Expense, FundAllocation, Project
from apps.operations.tests.helpers import (
    create_allocation,
    create_donation,
    create_institution,
    create_project,
    create_user,
)


INITIAL_DATE = date(2026, 7, 9)
UPDATED_DATE = date(2026, 7, 10)


class DateFormContractTests(TestCase):
    def setUp(self):
        self.donor = create_institution()
        self.project = create_project()
        self.donation = create_donation(donor=self.donor)
        self.allocation = create_allocation(donation=self.donation, project=self.project)

    def test_all_operations_dates_render_iso_and_accept_both_contract_formats(self):
        instances_and_fields = (
            (ProjectForm(instance=Project(start_date=INITIAL_DATE)), 'start_date'),
            (DonationForm(instance=Donation(commitment_date=INITIAL_DATE)), 'commitment_date'),
            (FundAllocationForm(instance=FundAllocation(allocation_date=INITIAL_DATE)), 'allocation_date'),
            (ExpenseForm(instance=Expense(expense_date=INITIAL_DATE)), 'expense_date'),
        )

        for form, field_name in instances_and_fields:
            with self.subTest(field=field_name):
                rendered = str(form[field_name])
                self.assertIn('value="2026-07-09"', rendered)
                self.assertIn('data-date-picker="operations"', rendered)

        for submitted_date in ('2026-07-09', '09/07/2026'):
            with self.subTest(submitted_date=submitted_date):
                form = ProjectForm(data=self._project_data(submitted_date, submitted_date))
                self.assertTrue(form.is_valid(), form.errors)
                self.assertEqual(form.cleaned_data['start_date'], INITIAL_DATE)

    def test_invalid_date_is_clear_and_other_error_preserves_submitted_date(self):
        invalid_date_form = ProjectForm(data=self._project_data('31/02/2026', ''))
        self.assertFalse(invalid_date_form.is_valid())
        self.assertIn('start_date', invalid_date_form.errors)

        other_error_form = ProjectForm(data=self._project_data('09/07/2026', '10/07/2026', name=''))
        self.assertFalse(other_error_form.is_valid())
        self.assertEqual(other_error_form['start_date'].value(), '09/07/2026')

    def test_optional_dates_accept_empty_values(self):
        project_form = ProjectForm(data=self._project_data('', ''))
        donation_form = DonationForm(data=self._donation_data('', ''))

        self.assertTrue(project_form.is_valid(), project_form.errors)
        self.assertTrue(donation_form.is_valid(), donation_form.errors)
        self.assertIsNone(project_form.save().start_date)
        self.assertIsNone(donation_form.save().received_date)

    def test_required_dates_reject_empty_values(self):
        allocation_form = FundAllocationForm(data=self._allocation_data(''))
        expense_form = ExpenseForm(data=self._expense_data(''))

        self.assertFalse(allocation_form.is_valid())
        self.assertFalse(expense_form.is_valid())
        self.assertIn('allocation_date', allocation_form.errors)
        self.assertIn('expense_date', expense_form.errors)

    def _project_data(self, start_date, end_date, *, name='Proyecto con fechas'):
        return {
            'name': name,
            'description': '',
            'objective': '',
            'responsible_unit': '',
            'location': '',
            'estimated_budget': '100.00',
            'start_date': start_date,
            'end_date': end_date,
        }

    def _donation_data(self, commitment_date, received_date):
        return {
            'donor': self.donor.pk,
            'donation_type': 'money',
            'amount': '100.00',
            'objective': 'Objetivo',
            'restrictions': '',
            'commitment_date': commitment_date,
            'received_date': received_date,
            'support_reference': '',
        }

    def _allocation_data(self, allocation_date):
        return {
            'donation': self.donation.pk,
            'project': self.project.pk,
            'budget_category': 'health_psychosocial',
            'amount': '10.00',
            'responsible_person': '',
            'allocation_date': allocation_date,
            'notes': '',
        }

    def _expense_data(self, expense_date):
        return {
            'allocation': self.allocation.pk,
            'expense_date': expense_date,
            'category': 'food',
            'amount': '10.00',
            'reason': 'Compra',
            'provider_or_recipient': 'Proveedor',
            'payment_method': 'bank_transfer',
            'description': '',
            'observations': '',
            'support_title': 'Soporte de fecha',
            'support_file': SimpleUploadedFile('fecha.pdf', b'%PDF soporte'),
        }


class DatePersistenceViewTests(TestCase):
    def setUp(self):
        self.user = create_user(username='date-operator')
        self.client.force_login(self.user)
        self.donor = create_institution()
        self.project = create_project()
        self.donation = create_donation(donor=self.donor, amount=Decimal('200.00'))
        self.allocation = create_allocation(
            donation=self.donation,
            project=self.project,
            amount=Decimal('100.00'),
        )

    def test_project_create_update_and_edit_render_persist_dates(self):
        response = self.client.post(reverse('project_create'), self._project_data(INITIAL_DATE, UPDATED_DATE))
        self.assertRedirects(response, reverse('project_list'))
        project = Project.objects.get(name='Proyecto persistente')
        self.assertEqual((project.start_date, project.end_date), (INITIAL_DATE, UPDATED_DATE))

        edit_response = self.client.get(reverse('project_update', args=[project.pk]))
        self.assertContains(edit_response, 'value="2026-07-09"')
        response = self.client.post(reverse('project_update', args=[project.pk]), self._project_data(UPDATED_DATE, UPDATED_DATE))
        self.assertRedirects(response, reverse('project_list'))
        project.refresh_from_db()
        self.assertEqual((project.start_date, project.end_date), (UPDATED_DATE, UPDATED_DATE))

    def test_project_rejects_reversed_dates_without_success_message(self):
        response = self.client.post(reverse('project_create'), self._project_data(UPDATED_DATE, INITIAL_DATE))
        self.assertEqual(response.status_code, 200)
        self.assertFormError(response.context['form'], 'end_date', 'La fecha de cierre no puede ser anterior a la fecha de inicio.')
        self.assertFalse(any(message.tags == 'success' for message in get_messages(response.wsgi_request)))

    def test_donation_create_update_and_received_transition_persist_dates(self):
        response = self.client.post(reverse('donation_create'), self._donation_data(INITIAL_DATE, INITIAL_DATE))
        self.assertRedirects(response, reverse('donation_list'))
        donation = Donation.objects.get(objective='Donación con fechas')
        self.assertEqual((donation.commitment_date, donation.received_date), (INITIAL_DATE, INITIAL_DATE))

        response = self.client.post(reverse('donation_update', args=[donation.pk]), self._donation_data(UPDATED_DATE, UPDATED_DATE))
        self.assertRedirects(response, reverse('donation_list'))
        self.assertRedirects(
            self.client.post(reverse('donation_status_transition', args=[donation.pk, Donation.Status.RECEIVED])),
            reverse('donation_detail', args=[donation.pk]),
        )
        donation.refresh_from_db()
        self.assertEqual(donation.received_date, UPDATED_DATE)
        self.assertEqual(donation.status, Donation.Status.RECEIVED)

    def test_allocation_create_update_and_missing_date_behave_correctly(self):
        data = self._allocation_data(INITIAL_DATE, amount='25.00')
        response = self.client.post(reverse('allocation_create'), data)
        self.assertRedirects(response, reverse('allocation_list'))
        allocation = FundAllocation.objects.get(amount=Decimal('25.00'))
        self.assertEqual(allocation.allocation_date, INITIAL_DATE)

        response = self.client.post(reverse('allocation_update', args=[allocation.pk]), self._allocation_data(UPDATED_DATE, amount='25.00'))
        self.assertRedirects(response, reverse('allocation_list'))
        allocation.refresh_from_db()
        self.assertEqual(allocation.allocation_date, UPDATED_DATE)

        invalid_response = self.client.post(reverse('allocation_create'), self._allocation_data('', amount='5.00'))
        self.assertEqual(invalid_response.status_code, 200)
        self.assertFormError(invalid_response.context['form'], 'allocation_date', 'Este campo es obligatorio.')

    def test_expense_create_update_and_missing_date_behave_correctly(self):
        response = self.client.post(reverse('expense_create'), self._expense_data(INITIAL_DATE))
        self.assertRedirects(response, reverse('expense_list'))
        expense = Expense.objects.get(reason='Gasto con fechas')
        self.assertEqual(expense.expense_date, INITIAL_DATE)

        response = self.client.post(reverse('expense_update', args=[expense.pk]), self._expense_data(UPDATED_DATE))
        self.assertRedirects(response, reverse('expense_list'))
        expense.refresh_from_db()
        self.assertEqual(expense.expense_date, UPDATED_DATE)

        invalid_response = self.client.post(reverse('expense_create'), self._expense_data(''))
        self.assertEqual(invalid_response.status_code, 200)
        self.assertFormError(invalid_response.context['form'], 'expense_date', 'Este campo es obligatorio.')

    def _project_data(self, start_date, end_date):
        return {
            'name': 'Proyecto persistente',
            'description': '',
            'objective': '',
            'responsible_unit': '',
            'location': '',
            'estimated_budget': '100.00',
            'start_date': start_date,
            'end_date': end_date,
        }

    def _donation_data(self, commitment_date, received_date):
        return {
            'donor': self.donor.pk,
            'donation_type': 'money',
            'amount': '50.00',
            'objective': 'Donación con fechas',
            'restrictions': '',
            'commitment_date': commitment_date,
            'received_date': received_date,
            'support_reference': '',
        }

    def _allocation_data(self, allocation_date, *, amount):
        return {
            'donation': self.donation.pk,
            'project': self.project.pk,
            'budget_category': 'health_psychosocial',
            'amount': amount,
            'responsible_person': '',
            'allocation_date': allocation_date,
            'notes': '',
        }

    def _expense_data(self, expense_date):
        return {
            'allocation': self.allocation.pk,
            'expense_date': expense_date,
            'category': 'food',
            'amount': '10.00',
            'reason': 'Gasto con fechas',
            'provider_or_recipient': 'Proveedor',
            'payment_method': 'bank_transfer',
            'description': '',
            'observations': '',
            'support_title': 'Soporte de fecha',
            'support_file': SimpleUploadedFile('fecha.pdf', b'%PDF soporte'),
        }
