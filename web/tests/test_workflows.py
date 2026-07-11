from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.operations.models import Donation, Institution, Project
from apps.operations.tests.helpers import create_user


class MvpWorkflowRegressionTests(TestCase):
    def setUp(self):
        self.user = create_user()
        self.client.force_login(self.user)

    def test_user_can_create_institution_project_donation_and_see_dashboard_update(self):
        institution_form = self.client.get(reverse('institution_create'))
        self.assertEqual(institution_form.status_code, 200)
        self.assertContains(institution_form, 'name="name"')
        self.assertContains(institution_form, 'name="role"')

        institution_response = self.client.post(
            reverse('institution_create'),
            data={
                'name': 'Workflow Donor',
                'institution_type': 'foundation',
                'role': Institution.Role.DONOR,
                'country': 'VE',
                'contact_email': 'donor@example.com',
                'contact_phone': '',
                'responsible_person': 'Workflow Responsible',
                'legal_document': '',
                'status': Institution.Status.ACTIVE,
            },
            follow=True,
        )
        self.assertEqual(institution_response.status_code, 200)
        self.assertContains(institution_response, 'Workflow Donor')
        donor = Institution.objects.get(name='Workflow Donor')
        institution_detail = self.client.get(reverse('institution_detail', args=[donor.pk]))
        self.assertContains(institution_detail, 'Venezuela')
        self.assertNotContains(institution_detail, 'Ciudad')

        project_form = self.client.get(reverse('project_create'))
        self.assertEqual(project_form.status_code, 200)
        self.assertNotContains(project_form, 'name="code"')
        self.assertContains(project_form, 'name="estimated_budget"')
        self.assertContains(project_form, 'required-mark')
        self.assertContains(project_form, 'inputmode="decimal"')
        self.assertContains(project_form, 'class="ops-input datepicker form-control"')
        self.assertContains(project_form, 'placeholder="dd/mm/aaaa"')
        self.assertNotContains(project_form, 'type="number"')
        self.assertNotContains(project_form, 'type="date"')
        self.assertNotContains(project_form, 'value="0.00"')
        self.assertNotContains(project_form, '---------')

        project_response = self.client.post(
            reverse('project_create'),
            data={
                'name': 'Workflow Project',
                'description': '',
                'objective': '',
                'responsible_unit': '',
                'location': 'Caracas',
                'estimated_budget': '500.00',
                'start_date': '',
                'end_date': '',
                'status': Project.Status.ACTIVE,
            },
            follow=True,
        )
        self.assertEqual(project_response.status_code, 200)
        self.assertContains(project_response, 'PRJ-000001')
        self.assertTrue(Project.objects.filter(code='PRJ-000001').exists())

        donation_form = self.client.get(reverse('donation_create'))
        self.assertEqual(donation_form.status_code, 200)
        self.assertContains(donation_form, 'Nueva donación')
        self.assertContains(donation_form, 'Donante')
        self.assertContains(donation_form, 'Monto')
        self.assertContains(donation_form, 'USD')
        self.assertContains(donation_form, 'Estado')
        self.assertContains(donation_form, 'Tipo de donación')
        self.assertContains(donation_form, 'name="donor"')
        self.assertContains(donation_form, 'Workflow Donor')
        self.assertContains(donation_form, 'name="amount"')
        self.assertContains(donation_form, 'class="ops-input money-input form-control"')
        self.assertContains(donation_form, 'placeholder="Ej. 1.500,00"')
        self.assertContains(donation_form, 'class="ops-input datepicker form-control"')
        self.assertNotContains(donation_form, 'type="date"')
        self.assertNotContains(donation_form, 'name="currency"')
        self.assertContains(donation_form, 'name="status"')
        self.assertContains(donation_form, 'name="donation_type"')
        self.assertContains(donation_form, 'Dinero')
        self.assertContains(donation_form, 'Seleccione una opción')
        self.assertNotContains(donation_form, '---------')

        donation_amount = Decimal('1234.56')
        donation_response = self.client.post(
            reverse('donation_create'),
            data={
                'donor': donor.pk,
                'donation_type': 'goods',
                'amount': str(donation_amount),
                'objective': 'Workflow donation',
                'restrictions': '',
                'commitment_date': '',
                'received_date': '',
                'status': Donation.Status.RECEIVED,
                'support_reference': '',
            },
            follow=True,
        )
        self.assertEqual(donation_response.status_code, 200)
        self.assertContains(donation_response, 'DON-000001')
        self.assertTrue(Donation.objects.filter(code='DON-000001', amount=donation_amount).exists())

        dashboard = self.client.get(reverse('dashboard'))
        self.assertEqual(dashboard.status_code, 200)
        self.assertEqual(dashboard.context['total_donations'], donation_amount)
        self.assertEqual(dashboard.context['total_assigned'], Decimal('0.00'))
        self.assertEqual(dashboard.context['total_executed'], Decimal('0.00'))
        self.assertEqual(dashboard.context['available_balance'], donation_amount)
