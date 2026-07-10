from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.operations.models import Donation, Expense, FundAllocation, Institution, Project
from apps.operations.tests.helpers import TEST_DATE, create_allocation, create_donation, create_expense, create_institution, create_project, create_user


class AuthenticatedViewTests(TestCase):
    def setUp(self):
        self.user = create_user()
        self.donor = create_institution()
        self.project = create_project()
        self.donation = create_donation(donor=self.donor, amount=Decimal('100.00'))
        self.allocation = create_allocation(donation=self.donation, project=self.project, amount=Decimal('50.00'))
        self.expense = create_expense(allocation=self.allocation, amount=Decimal('10.00'))

    def test_anonymous_users_are_redirected_from_protected_views(self):
        protected_urls = [
            reverse('dashboard'),
            reverse('institution_list'),
            reverse('institution_create'),
            reverse('institution_update', args=[self.donor.pk]),
            reverse('project_list'),
            reverse('project_create'),
            reverse('project_update', args=[self.project.pk]),
            reverse('donation_list'),
            reverse('donation_create'),
            reverse('donation_update', args=[self.donation.pk]),
            reverse('allocation_list'),
            reverse('allocation_create'),
            reverse('allocation_update', args=[self.allocation.pk]),
            reverse('expense_list'),
            reverse('expense_create'),
            reverse('expense_update', args=[self.expense.pk]),
            reverse('audit_log_list'),
        ]

        for url in protected_urls:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 302)
                self.assertIn(reverse('login'), response['Location'])

    def test_authenticated_users_can_access_mvp_views(self):
        self.client.force_login(self.user)
        urls = [
            reverse('dashboard'),
            reverse('institution_list'),
            reverse('institution_create'),
            reverse('institution_update', args=[self.donor.pk]),
            reverse('project_list'),
            reverse('project_create'),
            reverse('project_update', args=[self.project.pk]),
            reverse('donation_list'),
            reverse('donation_create'),
            reverse('donation_update', args=[self.donation.pk]),
            reverse('allocation_list'),
            reverse('allocation_create'),
            reverse('allocation_update', args=[self.allocation.pk]),
            reverse('expense_list'),
            reverse('expense_create'),
            reverse('expense_update', args=[self.expense.pk]),
            reverse('audit_log_list'),
        ]

        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_list_views_render_tables_inside_responsive_containers(self):
        self.client.force_login(self.user)
        list_urls = [
            reverse('institution_list'),
            reverse('project_list'),
            reverse('donation_list'),
            reverse('allocation_list'),
            reverse('expense_list'),
            reverse('audit_log_list'),
        ]

        for url in list_urls:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertContains(response, 'ops-table-card')
                self.assertContains(response, 'class="table-responsive"')


class CrudFlowTests(TestCase):
    def setUp(self):
        self.user = create_user()
        self.client.force_login(self.user)
        self.donor = create_institution()
        self.project = create_project()
        self.donation = create_donation(donor=self.donor, amount=Decimal('100.00'))
        self.allocation = create_allocation(donation=self.donation, project=self.project, amount=Decimal('50.00'))

    def test_create_flows_create_valid_objects(self):
        institution_response = self.client.post(
            reverse('institution_create'),
            data={
                'name': 'New Donor',
                'institution_type': 'foundation',
                'role': Institution.Role.DONOR,
                'country': 'VE',
                'contact_email': '',
                'contact_phone': '',
                'responsible_person': '',
                'legal_document': '',
                'status': Institution.Status.ACTIVE,
            },
        )
        project_response = self.client.post(
            reverse('project_create'),
            data={
                'name': 'New Project',
                'description': '',
                'objective': '',
                'responsible_unit': '',
                'location': '',
                'estimated_budget': '1000.00',
                'start_date': '',
                'end_date': '',
                'status': Project.Status.ACTIVE,
            },
        )
        donation_response = self.client.post(
            reverse('donation_create'),
            data={
                'donor': self.donor.pk,
                'donation_type': 'goods',
                'amount': '200.00',
                'currency': 'USD',
                'objective': 'Apoyar atención de emergencia',
                'restrictions': '',
                'commitment_date': '',
                'received_date': '',
                'status': Donation.Status.RECEIVED,
                'support_reference': '',
            },
        )
        allocation_response = self.client.post(
            reverse('allocation_create'),
            data={
                'donation': self.donation.pk,
                'project': self.project.pk,
                'budget_category': 'health_psychosocial',
                'amount': '20.00',
                'responsible_person': '',
                'allocation_date': TEST_DATE,
                'status': FundAllocation.Status.ACTIVE,
                'notes': '',
            },
        )
        expense_response = self.client.post(
            reverse('expense_create'),
            data={
                'allocation': self.allocation.pk,
                'expense_date': TEST_DATE,
                'category': 'food',
                'amount': '10.00',
                'currency': 'USD',
                'reason': 'Purchase',
                'provider_or_recipient': 'Provider A',
                'payment_method': 'bank_transfer',
                'description': '',
                'observations': '',
                'status': Expense.Status.REGISTERED,
            },
        )

        self.assertRedirects(institution_response, reverse('institution_list'))
        self.assertRedirects(project_response, reverse('project_list'))
        self.assertRedirects(donation_response, reverse('donation_list'))
        self.assertRedirects(allocation_response, reverse('allocation_list'))
        self.assertRedirects(expense_response, reverse('expense_list'))
        self.assertTrue(Institution.objects.filter(name='New Donor').exists())
        self.assertTrue(Project.objects.filter(code='PRJ-000002').exists())
        self.assertTrue(Donation.objects.filter(code='DON-000002').exists())
        self.assertTrue(FundAllocation.objects.filter(budget_category='health_psychosocial').exists())
        self.assertTrue(Expense.objects.filter(reason='Purchase').exists())

    def test_invalid_create_data_shows_errors_without_creating_invalid_object(self):
        response = self.client.post(
            reverse('donation_create'),
            data={
                'donor': self.donor.pk,
                'donation_type': 'goods',
                'amount': '0.00',
                'currency': 'USD',
                'objective': 'Apoyar atención de emergencia',
                'restrictions': '',
                'commitment_date': '',
                'received_date': '',
                'status': Donation.Status.RECEIVED,
                'support_reference': '',
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Donation.objects.filter(code='DON-BAD').exists())
        self.assertFormError(response.context['form'], 'amount', 'El monto de la donación debe ser positivo.')
        self.assertContains(response, 'id="django-messages"')
        self.assertContains(response, 'id_amount_error')
