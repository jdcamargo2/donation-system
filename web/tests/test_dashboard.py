from decimal import Decimal
import re
from pathlib import Path

from django.test import TestCase
from django.urls import reverse

from django.contrib.auth.models import Permission
from django.contrib.auth import get_user_model

from apps.operations.models import AuditLog, Donation, Expense, FundAllocation
from apps.operations.tests.helpers import create_allocation, create_donation, create_expense, create_institution, create_project
from web.tests.test_audit import SIGEDON_CSS, _extract_css_rule_block


def _declaration_value(rule_body, property_name):
    """
    PRE: rule_body is a CSS declarations block.
    POST: returns the declared value for property_name, or raises AssertionError.
    """
    match = re.search(
        rf'(?<![\w-]){re.escape(property_name)}\s*:\s*([^;]+);',
        rule_body,
    )
    if match is None:
        raise AssertionError(
            f'Expected declaration {property_name!r} in CSS rule body, got: {rule_body!r}'
        )
    return match.group(1).strip()


class DashboardTests(TestCase):
    def grant_permissions(self, *codenames):
        self.user.user_permissions.add(
            *Permission.objects.filter(
                content_type__app_label='operations',
                codename__in=codenames,
            )
        )

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='dashboard-user',
            password='pass-12345',
        )
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
        self.assertContains(response, 'localStorage')
        self.assertNotContains(response, 'id="mainMenu"')
        self.assertNotContains(response, 'data-bs-toggle="offcanvas"')
        self.assertContains(response, 'id="django-messages"')
        self.assertContains(response, 'sweetalert2@11')
        self.assertContains(response, 'Swal.fire')
        self.assertContains(response, 'Cerrar sesión')
        self.assertContains(
            response,
            'El panel muestra información acorde con tus permisos.',
        )
        self.assertNotContains(response, 'Fondos recibidos')
        self.assertNotContains(response, 'Fondos asignados')
        self.assertNotContains(response, 'Gastos registrados')
        self.assertNotContains(response, 'Fondos sin asignar')
        self.assertNotContains(response, 'Accesos rápidos')
        self.assertNotContains(response, 'ops-action-panel')
        self.assertNotContains(response, 'Actividad reciente')
        self.assertNotContains(response, 'Gastos')
        self.assertNotContains(response, 'Acciones recientes de auditoría')
        self.assertIsNone(response.context['total_donations'])
        self.assertIsNone(response.context['total_assigned'])
        self.assertIsNone(response.context['total_executed'])
        self.assertIsNone(response.context['available_balance'])
        self.assertEqual(response.context['financial_kpis'], [])
        self.assertEqual(response.context['financial_ratios'], [])
        self.assertEqual(response.context['expense_request_queues'], [])
        self.assertFalse(response.context['expense_request_queues_have_items'])
        self.assertFalse(response.context['show_project_financial_section'])
        self.assertEqual(response.context['project_financial_rows'], [])
        self.assertNotContains(response, 'Estado financiero por proyecto')
        self.assertNotIn('show_financial_quick_actions', response.context)

    def test_sidebar_links_do_not_trigger_sidebar_toggle(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse('dashboard'))
        html = response.content.decode()

        self.assertContains(response, 'data-sidebar-toggle')
        self.assertNotIn('data-sidebar-toggle href=', html)
        self.assertNotIn('data-bs-toggle="offcanvas"', html)
        self.assertNotIn('data-sidebar-mobile-toggle', html)

    def test_active_navigation_marks_current_section(self):
        self.grant_permissions('view_institution')
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
        self.grant_permissions(
            'view_donation',
            'view_fundallocation',
            'view_expense',
        )
        self.client.force_login(self.user)

        response = self.client.get(reverse('dashboard'))
        html = response.content.decode()

        self.assertEqual(response.context['total_donations'], Decimal('150.00'))
        self.assertEqual(response.context['total_assigned'], Decimal('85.00'))
        self.assertEqual(response.context['total_executed'], Decimal('20.00'))
        self.assertEqual(response.context['available_balance'], Decimal('65.00'))
        kpi_keys = [item['key'] for item in response.context['financial_kpis']]
        self.assertEqual(kpi_keys, ['received', 'assigned', 'spent', 'unallocated'])
        ratio_keys = [item['key'] for item in response.context['financial_ratios']]
        self.assertEqual(ratio_keys, ['assignment', 'execution'])
        self.assertContains(response, 'Fondos recibidos')
        self.assertContains(response, 'Fondos asignados')
        self.assertContains(response, 'Gastos registrados')
        self.assertContains(response, 'Fondos sin asignar')
        self.assertContains(response, 'Asignación de fondos')
        self.assertContains(response, 'Ejecución financiera')
        self.assertContains(response, 'ops-financial-progress')
        self.assertContains(response, '150,00 USD')
        self.assertContains(response, 'Actividad reciente')
        self.assertLess(html.find('Fondos recibidos'), html.find('Actividad reciente'))
        self.assertContains(response, 'Estado financiero por proyecto')
        self.assertLess(
            html.find('Estado financiero por proyecto'),
            html.find('Actividad reciente'),
        )
        self.assertNotContains(response, 'Solicitudes que requieren atención')
        self.assertNotContains(response, 'Accesos rápidos')
        self.assertNotContains(response, 'ops-action-panel')
        self.assertNotContains(response, 'Top 5')
        self.assertNotContains(response, 'Top 10')
        self.assertEqual(html.count('<h1'), 1)

    def test_dashboard_excludes_registered_donations_from_received_kpi(self):
        create_donation(
            code='DON-REGISTERED',
            donor=self.donor,
            amount=Decimal('200.00'),
            status=Donation.Status.REGISTERED,
        )
        create_donation(
            code='DON-RECEIVED',
            donor=self.donor,
            amount=Decimal('100.00'),
            status=Donation.Status.RECEIVED,
        )
        create_donation(
            code='DON-ANNULLED',
            donor=self.donor,
            amount=Decimal('900.00'),
            status=Donation.Status.ANNULLED,
        )
        self.grant_permissions('view_donation')
        self.client.force_login(self.user)

        response = self.client.get(reverse('dashboard'))

        self.assertEqual(response.context['total_donations'], Decimal('100.00'))
        self.assertEqual(response.context['financial_kpis'][0]['value'], Decimal('100.00'))
        self.assertEqual(len(response.context['financial_kpis']), 1)
        self.assertEqual(response.context['financial_ratios'], [])
        self.assertContains(response, 'Fondos recibidos')
        self.assertNotContains(response, 'Fondos sin asignar')
        self.assertNotContains(response, 'Asignación de fondos')

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
        self.grant_permissions(
            'view_donation',
            'view_fundallocation',
            'view_expense',
        )
        self.client.force_login(self.user)

        response = self.client.get(reverse('dashboard'))

        self.assertEqual(response.context['total_donations'], Decimal('100.00'))
        self.assertEqual(response.context['total_assigned'], Decimal('60.00'))
        self.assertEqual(response.context['total_executed'], Decimal('15.00'))
        self.assertEqual(response.context['available_balance'], Decimal('40.00'))

    def test_dashboard_unallocated_never_renders_negative(self):
        donation = create_donation(donor=self.donor, amount=Decimal('50.00'))
        create_allocation(donation=donation, project=self.project, amount=Decimal('50.00'))
        # Legacy anomaly fixture: assigned already equals received; unallocated stays zero.
        self.grant_permissions('view_donation', 'view_fundallocation')
        self.client.force_login(self.user)

        response = self.client.get(reverse('dashboard'))
        unallocated = next(
            item for item in response.context['financial_kpis'] if item['key'] == 'unallocated'
        )
        assignment = response.context['financial_ratios'][0]

        self.assertEqual(unallocated['value'], Decimal('0.00'))
        self.assertGreaterEqual(unallocated['value'], Decimal('0.00'))
        self.assertEqual(assignment['percentage'], Decimal('100.0'))
        self.assertEqual(assignment['visual_percentage'], Decimal('100.0'))

    def test_dashboard_ratio_zero_denominators_and_visual_cap(self):
        self.grant_permissions(
            'view_donation',
            'view_fundallocation',
            'view_expense',
        )
        self.client.force_login(self.user)

        empty = self.client.get(reverse('dashboard'))
        assignment, execution = empty.context['financial_ratios']
        self.assertIsNone(assignment['percentage'])
        self.assertEqual(assignment['visual_percentage'], Decimal('0.00'))
        self.assertIsNone(execution['percentage'])
        self.assertEqual(execution['visual_percentage'], Decimal('0.00'))
        self.assertContains(empty, '—')
        self.assertContains(empty, 'Aún no hay fondos recibidos para calcular esta relación.')
        self.assertContains(empty, 'Aún no hay fondos asignados para calcular esta relación.')

        donation = create_donation(donor=self.donor, amount=Decimal('100.00'))
        allocation = create_allocation(
            donation=donation,
            project=self.project,
            amount=Decimal('80.00'),
        )
        create_expense(allocation=allocation, amount=Decimal('50.00'))
        # Force assigned > received via ORM to exercise visual cap without mutating domain services.
        FundAllocation.objects.filter(pk=allocation.pk).update(amount=Decimal('150.00'))

        capped = self.client.get(reverse('dashboard'))
        assignment = capped.context['financial_ratios'][0]
        self.assertEqual(assignment['percentage'], Decimal('150.0'))
        self.assertEqual(assignment['visual_percentage'], Decimal('100'))
        self.assertEqual(assignment['visual_width'], '100')
        self.assertContains(capped, '150 %')
        self.assertContains(capped, 'width: 100%')
        self.assertIsInstance(assignment['numerator'], Decimal)
        self.assertIsInstance(assignment['denominator'], Decimal)

    def test_dashboard_uses_usd_financial_records(self):
        usd_donation = create_donation(donor=self.donor, amount=Decimal('100.00'))
        usd_allocation = create_allocation(
            donation=usd_donation,
            project=self.project,
            amount=Decimal('60.00'),
        )
        create_expense(allocation=usd_allocation, amount=Decimal('15.00'))
        self.grant_permissions(
            'view_donation',
            'view_fundallocation',
            'view_expense',
        )
        self.client.force_login(self.user)

        response = self.client.get(reverse('dashboard'))

        self.assertEqual(response.context['total_donations'], Decimal('100.00'))
        self.assertEqual(response.context['total_assigned'], Decimal('60.00'))
        self.assertEqual(response.context['total_executed'], Decimal('15.00'))
        self.assertEqual(response.context['available_balance'], Decimal('40.00'))
        assignment, execution = response.context['financial_ratios']
        self.assertEqual(assignment['percentage'], Decimal('60.0'))
        self.assertEqual(execution['percentage'], Decimal('25.0'))

    def test_dashboard_renders_legacy_audit_model_names_in_spanish(self):
        self.grant_permissions('view_auditlog')
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
        self.assertContains(response, 'Actividad reciente')

    def test_dashboard_does_not_expose_financial_or_audit_data_without_permissions(self):
        donation = create_donation(donor=self.donor, amount=Decimal('100.00'))
        allocation = create_allocation(
            donation=donation,
            project=self.project,
            amount=Decimal('60.00'),
        )
        create_expense(allocation=allocation, amount=Decimal('20.00'))
        AuditLog.objects.create(
            action=AuditLog.Action.CREATED,
            model_name='Proyecto',
            entity_id=str(self.project.pk),
            entity_label=str(self.project),
            summary='Proyecto creado.',
        )
        self.client.force_login(self.user)

        response = self.client.get(reverse('dashboard'))
        html = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context['total_donations'])
        self.assertIsNone(response.context['total_assigned'])
        self.assertIsNone(response.context['total_executed'])
        self.assertIsNone(response.context['available_balance'])
        self.assertEqual(response.context['financial_kpis'], [])
        self.assertEqual(response.context['financial_ratios'], [])
        self.assertNotContains(response, donation.code)
        self.assertNotContains(response, '100,00')
        self.assertNotContains(response, '60,00')
        self.assertNotContains(response, '20,00')
        self.assertNotContains(response, 'Fondos recibidos')
        self.assertNotContains(response, 'Actividad reciente')
        self.assertNotContains(response, 'Accesos rápidos')
        self.assertNotContains(response, 'Ver solicitudes de gasto')
        self.assertNotContains(response, 'Mis solicitudes de gasto')
        self.assertNotContains(response, 'Solicitudes pendientes de decisión')
        self.assertNotContains(
            response,
            'Aprobadas pendientes de registrar gasto',
        )
        self.assertNotContains(response, reverse('expense_request_list'))
        self.assertNotContains(response, 'Crear gasto')
        self.assertNotContains(response, reverse('expense_create'))
        self.assertNotIn('aria-valuenow="60"', html)

    def test_dashboard_partial_permissions_hide_derived_values(self):
        donation = create_donation(donor=self.donor, amount=Decimal('100.00'))
        allocation = create_allocation(
            donation=donation,
            project=self.project,
            amount=Decimal('60.00'),
        )
        create_expense(allocation=allocation, amount=Decimal('20.00'))
        self.client.force_login(self.user)

        self.grant_permissions('view_donation')
        donation_only = self.client.get(reverse('dashboard'))
        self.assertEqual(
            [item['key'] for item in donation_only.context['financial_kpis']],
            ['received'],
        )
        self.assertEqual(donation_only.context['financial_ratios'], [])
        self.assertContains(donation_only, 'Fondos recibidos')
        self.assertNotContains(donation_only, 'Fondos sin asignar')
        self.assertNotContains(donation_only, 'Asignación de fondos')
        self.assertNotContains(donation_only, '60,00')
        self.assertNotContains(donation_only, '20,00')

        self.user.user_permissions.clear()
        self.grant_permissions('view_fundallocation')
        allocation_only = self.client.get(reverse('dashboard'))
        self.assertEqual(
            [item['key'] for item in allocation_only.context['financial_kpis']],
            ['assigned'],
        )
        self.assertEqual(allocation_only.context['financial_ratios'], [])
        self.assertContains(allocation_only, 'Fondos asignados')
        self.assertNotContains(allocation_only, 'Fondos recibidos')
        self.assertNotContains(allocation_only, '100,00')
        self.assertNotContains(allocation_only, '20,00')

        self.user.user_permissions.clear()
        self.grant_permissions('view_expense')
        expense_only = self.client.get(reverse('dashboard'))
        self.assertEqual(
            [item['key'] for item in expense_only.context['financial_kpis']],
            ['spent'],
        )
        self.assertEqual(expense_only.context['financial_ratios'], [])
        self.assertContains(expense_only, 'Gastos registrados')
        self.assertNotContains(expense_only, 'Ejecución financiera')
        self.assertNotContains(expense_only, '100,00')
        self.assertNotContains(expense_only, '60,00')

    def test_dashboard_has_no_quick_access_block(self):
        self.grant_permissions(
            'view_project',
            'view_donation',
            'view_expenserequest',
            'fulfill_expenserequest',
            'decide_expenserequest',
            'add_projectupdate',
            'add_donation',
            'add_fundallocation',
            'view_auditlog',
        )
        self.client.force_login(self.user)

        response = self.client.get(reverse('dashboard'))

        self.assertNotIn('show_financial_quick_actions', response.context)
        self.assertNotContains(response, 'Accesos rápidos')
        self.assertNotContains(response, 'ops-action-panel')
        self.assertNotContains(response, 'Ver proyectos')
        self.assertNotContains(response, 'Registrar avances')
        self.assertNotContains(response, 'Crear asignación')
        self.assertNotContains(response, 'Crear donación')
        self.assertNotContains(response, 'Ver solicitudes de gasto')
        self.assertNotContains(response, 'Aprobadas pendientes de registrar gasto')
        self.assertNotContains(response, 'Solicitudes pendientes de decisión')
        self.assertNotContains(response, 'Mis solicitudes de gasto')
        self.assertNotContains(response, 'Crear gasto')
        self.assertNotContains(response, reverse('expense_create'))

class SidebarOverflowContractTests(TestCase):
    """Regression: short/zoomed viewports must scroll nav without covering the footer."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='sidebar-overflow-user',
            password='pass-12345',
        )
        self.css_text = SIGEDON_CSS.read_text(encoding='utf-8')

    def test_sidebar_overflow_contract_in_stylesheet(self):
        sidebar_rule = _extract_css_rule_block(
            self.css_text,
            '.sigedon-sidebar',
            exact_selector=True,
        )
        nav_rule = _extract_css_rule_block(
            self.css_text,
            '.sigedon-sidebar-nav',
            exact_selector=True,
        )
        header_rule = _extract_css_rule_block(
            self.css_text,
            '.sigedon-sidebar-header',
            exact_selector=True,
        )
        footer_rule = _extract_css_rule_block(
            self.css_text,
            '.sigedon-sidebar-footer',
            exact_selector=True,
        )

        self.assertEqual(
            _declaration_value(sidebar_rule, 'overflow'),
            'hidden',
            'Sidebar root must contain flex children (overflow: hidden), not scroll them.',
        )
        self.assertNotIn(
            'overflow-y: auto',
            sidebar_rule,
            'Sidebar root must not remain the competing vertical scroll container.',
        )
        self.assertEqual(
            _declaration_value(nav_rule, 'overflow-y'),
            'auto',
            'Nav must be the only vertical scroll region inside the sidebar.',
        )
        self.assertEqual(
            _declaration_value(nav_rule, 'min-height'),
            '0',
            'Nav must stay shrinkable under a short viewport (min-height: 0).',
        )
        self.assertEqual(
            _declaration_value(nav_rule, 'overscroll-behavior'),
            'contain',
            'Nav scroll must not chain to outer page scroll.',
        )
        self.assertEqual(
            _declaration_value(nav_rule, 'flex'),
            '1 1 auto',
        )
        self.assertEqual(
            _declaration_value(header_rule, 'flex'),
            '0 0 auto',
            'Sidebar header must remain non-shrinking.',
        )
        self.assertEqual(
            _declaration_value(footer_rule, 'flex'),
            '0 0 auto',
            'Sidebar footer must remain non-shrinking.',
        )

        later_css = self.css_text.split('.sigedon-sidebar-nav {', 1)[1]
        self.assertNotRegex(
            later_css,
            r'\.sigedon-sidebar-nav\s*\{[^}]*overflow-y\s*:',
            'No later .sigedon-sidebar-nav rule may reset overflow-y.',
        )
        self.assertNotRegex(
            later_css,
            r'\.sigedon-sidebar-nav\s*\{[^}]*min-height\s*:',
            'No later .sigedon-sidebar-nav rule may reset min-height.',
        )
        self.assertNotIn('100dvh', self.css_text)

    def test_sidebar_markup_preserves_nav_footer_toggle_and_logout(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse('dashboard'))
        html = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="sigedon-sidebar-nav"')
        self.assertContains(response, 'class="sigedon-sidebar-footer"')
        self.assertContains(response, 'data-sidebar-toggle')
        self.assertContains(response, 'aria-label="Colapsar navegación"')
        self.assertContains(response, 'Cerrar sesión')
        self.assertContains(response, 'sigedon-logout-button')
        self.assertIn('action="/accounts/logout/"', html)
        self.assertNotContains(response, 'data-sidebar-mobile-toggle')
        self.assertNotContains(response, 'data-bs-toggle="offcanvas"')
        self.assertTrue(SIGEDON_CSS.is_file())
        self.assertIn('web/css/sigedon.css', Path('templates/base.html').read_text())


class SidebarBrandingTests(TestCase):
    """UI-BRAND1: authenticated sidebar uses local ILDE logos; login brand unchanged."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='sidebar-brand-user',
            password='pass-12345',
        )

    def _sidebar_header_html(self, html):
        match = re.search(
            r'<div class="sigedon-sidebar-header">(.*?)</div>',
            html,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(match, 'Expected .sigedon-sidebar-header in authenticated HTML')
        return match.group(1)

    def _brand_link_html(self, header_html):
        match = re.search(
            r'<a\s[^>]*class="sigedon-brand[^"]*"[^>]*>.*?</a>',
            header_html,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(match, 'Expected .sigedon-brand link in sidebar header')
        return match.group(0)

    def test_authenticated_sidebar_uses_local_ilde_logos(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse('dashboard'))
        html = response.content.decode()
        header = self._sidebar_header_html(html)
        brand = self._brand_link_html(header)

        self.assertEqual(response.status_code, 200)
        self.assertIn('web/img/logo_ilde.png', brand)
        self.assertIn('web/img/logo_ilde_short.png', brand)
        self.assertIn(f'href="{reverse("dashboard")}"', brand)
        self.assertIn('aria-label="ILDE · SIGEDON"', brand)
        self.assertIn('title="ILDE · SIGEDON"', brand)
        self.assertIn('class="sigedon-brand-logo sigedon-brand-logo-expanded"', brand)
        self.assertIn('class="sigedon-brand-logo sigedon-brand-logo-collapsed"', brand)
        self.assertEqual(brand.count('alt=""'), 2)
        self.assertNotIn('class="sigedon-brand-mark">S</span>', brand)
        self.assertNotIn('sigedon-nav-label">SIGEDON', brand)
        self.assertIn('data-sidebar-toggle', header)
        self.assertNotIn('http://', brand)
        self.assertNotIn('https://', brand)
        self.assertNotRegex(brand, r'src="/(?:mnt|home)/')
        # Topbar title remains intentionally.
        self.assertContains(response, 'ops-topbar-title">SIGEDON')

    def test_login_brand_mark_remains_unchanged(self):
        response = self.client.get(reverse('login'))
        html = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="login-badge sigedon-brand-mark">S</span>')
        self.assertNotContains(response, 'web/img/logo_ilde.png')
        self.assertNotContains(response, 'web/img/logo_ilde_short.png')
        self.assertNotContains(response, 'aria-label="ILDE · SIGEDON"')
        self.assertNotIn('id="sigedonSidebar"', html)
