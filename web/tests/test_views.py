from decimal import Decimal
from pathlib import Path

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

    def test_login_uses_refined_internal_visual_system(self):
        response = self.client.get(reverse('login'))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'registration/login.html')
        self.assertContains(response, 'login-screen')
        self.assertContains(response, 'login-card')
        self.assertContains(response, 'ops-login-shell')
        self.assertContains(response, 'SIGEDON')
        self.assertContains(response, 'Iniciar sesión')
        self.assertContains(response, 'Panel operativo interno')
        self.assertContains(response, 'Acceso exclusivo para personal autorizado.')
        self.assertContains(response, 'required-mark')
        self.assertNotContains(response, 'public_portal/css/public_portal.css')

    def test_login_authentication_still_works(self):
        response = self.client.post(
            reverse('login'),
            data={'username': self.user.username, 'password': 'pass-12345'},
        )

        self.assertRedirects(response, reverse('dashboard'))

    def test_login_styles_are_defined_only_in_internal_stylesheet(self):
        source = Path('static/web/css/sigedon.css').read_text()
        public_source = Path('templates/public_portal/public_base.html').read_text()

        self.assertIn('.login-screen', source)
        self.assertIn('min-height: 100vh;', source)
        self.assertIn('.login-card', source)
        self.assertIn('.login-brand-panel', source)
        self.assertNotIn('login-screen', public_source)
        self.assertNotIn('login-card', public_source)

    def test_internal_base_does_not_load_public_assets(self):
        source = Path('templates/base.html').read_text()

        self.assertIn("web/css/sigedon.css", source)
        self.assertNotIn("public_portal/css/public_portal.css", source)

    def test_internal_base_loads_local_form_assets(self):
        source = Path('templates/base.html').read_text()

        self.assertIn("vendor/flatpickr/flatpickr.min.css", source)
        self.assertIn("vendor/flatpickr/flatpickr.min.js", source)
        self.assertIn("vendor/flatpickr/l10n/es.js", source)
        self.assertIn("vendor/autonumeric/autoNumeric.min.js", source)
        self.assertIn("web/js/ops_forms.js", source)
        self.assertLess(source.index("vendor/autonumeric/autoNumeric.min.js"), source.index("web/js/ops_forms.js"))
        self.assertNotIn("cdn.jsdelivr.net/npm/flatpickr", source)
        self.assertNotIn("cdn.jsdelivr.net/npm/autonumeric", source)
        self.assertNotIn("cdn.jsdelivr.net/npm/autoNumeric", source)

    def test_internal_form_javascript_initializes_datepicker_and_money_inputs(self):
        source = Path('static/web/js/ops_forms.js').read_text()

        self.assertIn("document.addEventListener('DOMContentLoaded'", source)
        self.assertIn("document.querySelectorAll('.datepicker')", source)
        self.assertIn("document.querySelectorAll('.money-input')", source)
        self.assertIn("dateFormat: 'Y-m-d'", source)
        self.assertIn("altFormat: 'd/m/Y'", source)
        self.assertIn('new window.AutoNumeric(input, moneyOptions)', source)
        self.assertIn('window.AutoNumeric.multiple([input], moneyOptions)', source)
        self.assertIn('isManagedByAutoNumeric', source)
        self.assertIn("input.dataset.autonumericInitialized", source)
        self.assertIn("digitGroupSeparator: '.'", source)
        self.assertIn("decimalCharacter: ','", source)
        self.assertIn("unformatOnSubmit: true", source)
        self.assertIn("emptyInputBehavior: 'null'", source)

    def test_internal_css_compacts_forms_and_hides_number_spinners(self):
        source = Path('static/web/css/sigedon.css').read_text()

        self.assertIn('.ops-form-grid', source)
        self.assertIn('align-items: start;', source)
        self.assertIn('gap: 0.95rem 1.15rem;', source)
        self.assertIn('.ops-form-card textarea', source)
        self.assertIn('min-height: 130px;', source)
        self.assertIn('max-height: 240px;', source)
        self.assertIn('.ops-field:has(.ops-textarea)', source)
        self.assertIn('grid-column: 1 / -1;', source)
        self.assertIn('input[type="number"]::-webkit-inner-spin-button', source)
        self.assertIn('-moz-appearance: textfield;', source)

    def test_generic_form_template_marks_fields_for_compact_grid_layout(self):
        source = Path('templates/web/object_form.html').read_text()

        self.assertIn('ops-form-grid', source)
        self.assertIn('ops-form-field ops-field', source)


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
        created_project = Project.objects.get(name='New Project')
        created_donation = Donation.objects.get(
            objective='Apoyar atención de emergencia', amount=Decimal('200.00')
        )
        self.assertRegex(created_project.code, r'^PRJ-\d{6}$')
        self.assertRegex(created_donation.code, r'^DON-\d{6}$')
        self.assertNotEqual(created_project.code, self.project.code)
        self.assertNotEqual(created_donation.code, self.donation.code)
        self.assertEqual(Project.objects.filter(code=created_project.code).count(), 1)
        self.assertEqual(Donation.objects.filter(code=created_donation.code).count(), 1)
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
