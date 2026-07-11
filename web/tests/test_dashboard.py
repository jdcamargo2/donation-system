from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.operations.models import AuditLog, Donation, Expense, FundAllocation
from apps.operations.tests.helpers import create_allocation, create_donation, create_expense, create_institution, create_project, create_user


class DashboardTests(TestCase):
    def setUp(self):
        self.user = create_user()
        self.donor = create_institution()
        self.project = create_project()

    def test_dashboard_returns_200_for_logged_in_user(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse('dashboard'))

        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertContains(response, 'id="sigedonSidebar"')
        self.assertContains(response, 'ops-private-panel')
        self.assertContains(response, 'ops-shell')
        self.assertContains(response, 'ops-topbar')
        self.assertContains(response, 'aria-label="Colapsar navegación"')
        self.assertEqual(html.count('data-sidebar-toggle>'), 1)
        self.assertNotContains(response, 'data-sidebar-mobile-toggle')
        self.assertNotContains(response, 'data-sidebar-backdrop')
        self.assertNotContains(response, 'sidebar?.querySelectorAll')
        self.assertContains(response, 'bootstrap-icons@1.11.3')
        self.assertContains(response, 'bi-speedometer2')
        self.assertContains(response, 'bi-building')
        self.assertContains(response, 'localStorage')
        self.assertNotContains(response, 'id="mainMenu"')
        self.assertNotContains(response, 'data-bs-toggle="offcanvas"')
        self.assertContains(response, 'id="django-messages"')
        self.assertContains(response, 'sweetalert2@11')
        self.assertContains(response, 'Swal.fire')
        self.assertContains(response, 'Panel financiero')
        self.assertContains(response, 'Accesos rápidos')
        self.assertContains(response, 'ops-metric-card')
        self.assertContains(response, 'Instituciones')
        self.assertContains(response, 'Donaciones')
        self.assertContains(response, 'Proyectos')
        self.assertContains(response, 'Asignaciones')
        self.assertContains(response, 'Gastos')
        self.assertContains(response, 'Auditoría')
        self.assertContains(response, 'Cerrar sesión')
        self.assertContains(response, 'Donaciones recibidas')
        self.assertContains(response, 'Asignado')
        self.assertContains(response, 'Ejecutado')
        self.assertContains(response, 'Disponible')
        self.assertContains(response, 'Todavía no hay gastos.')

    def test_sidebar_links_do_not_trigger_sidebar_toggle(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse('dashboard'))
        html = response.content.decode()

        self.assertContains(response, 'data-sidebar-toggle')
        self.assertNotIn('data-sidebar-toggle href=', html)
        self.assertNotIn('data-bs-toggle="offcanvas"', html)
        self.assertNotIn('data-sidebar-mobile-toggle', html)

    def test_active_navigation_marks_current_section(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse('institution_list'))

        self.assertContains(response, 'sigedon-nav-link active')
        self.assertContains(response, 'href="/institutions/"')
        self.assertContains(response, 'aria-current="page"')

    def test_dashboard_totals_match_financial_data(self):
        donation = create_donation(donor=self.donor, amount=Decimal('100.00'))
        other_donation = create_donation(code='DON-002', donor=self.donor, amount=Decimal('50.00'))
        allocation = create_allocation(donation=donation, project=self.project, amount=Decimal('60.00'))
        create_allocation(donation=other_donation, project=self.project, amount=Decimal('25.00'))
        create_expense(allocation=allocation, amount=Decimal('20.00'))
        self.client.force_login(self.user)

        response = self.client.get(reverse('dashboard'))

        self.assertEqual(response.context['total_donations'], Decimal('150.00'))
        self.assertEqual(response.context['total_assigned'], Decimal('85.00'))
        self.assertEqual(response.context['total_executed'], Decimal('20.00'))
        self.assertEqual(response.context['available_balance'], Decimal('65.00'))

    def test_dashboard_excludes_annulled_financial_records(self):
        donation = create_donation(donor=self.donor, amount=Decimal('100.00'))
        create_donation(code='DON-ANNULLED', donor=self.donor, amount=Decimal('900.00'), status=Donation.Status.ANNULLED)
        allocation = create_allocation(donation=donation, project=self.project, amount=Decimal('60.00'))
        create_allocation(
            donation=donation,
            project=self.project,
            amount=Decimal('20.00'),
            category='Annulled',
            status=FundAllocation.Status.ANNULLED,
        )
        create_expense(allocation=allocation, amount=Decimal('15.00'))
        create_expense(allocation=allocation, amount=Decimal('10.00'), status=Expense.Status.ANNULLED)
        self.client.force_login(self.user)

        response = self.client.get(reverse('dashboard'))

        self.assertEqual(response.context['total_donations'], Decimal('100.00'))
        self.assertEqual(response.context['total_assigned'], Decimal('60.00'))
        self.assertEqual(response.context['total_executed'], Decimal('15.00'))
        self.assertEqual(response.context['available_balance'], Decimal('40.00'))

    def test_dashboard_excludes_legacy_non_usd_records(self):
        usd_donation = create_donation(donor=self.donor, amount=Decimal('100.00'))
        usd_allocation = create_allocation(
            donation=usd_donation,
            project=self.project,
            amount=Decimal('60.00'),
        )
        create_expense(allocation=usd_allocation, amount=Decimal('15.00'))
        legacy_donation = create_donation(
            code='DON-EUR-LEGACY',
            donor=self.donor,
            amount=Decimal('900.00'),
        )
        legacy_donation.currency = 'EUR'
        legacy_donation.save(update_fields=['currency'])
        legacy_allocation = create_allocation(
            donation=legacy_donation,
            project=self.project,
            amount=Decimal('500.00'),
        )
        legacy_expense = create_expense(allocation=legacy_allocation, amount=Decimal('200.00'))
        legacy_expense.currency = 'EUR'
        legacy_expense.save(update_fields=['currency'])
        self.client.force_login(self.user)

        response = self.client.get(reverse('dashboard'))

        self.assertEqual(response.context['total_donations'], Decimal('100.00'))
        self.assertEqual(response.context['total_assigned'], Decimal('60.00'))
        self.assertEqual(response.context['total_executed'], Decimal('15.00'))
        self.assertEqual(response.context['available_balance'], Decimal('40.00'))

    def test_dashboard_renders_legacy_audit_model_names_in_spanish(self):
        AuditLog.objects.create(
            action=AuditLog.Action.UPDATED,
            model_name='Donation',
            entity_id='1',
            entity_label='DON-001',
            summary='Donation updated.',
        )
        self.client.force_login(self.user)

        response = self.client.get(reverse('dashboard'))

        self.assertContains(response, 'Actualizada Donación')
        self.assertNotContains(response, 'Actualizada Donation')
