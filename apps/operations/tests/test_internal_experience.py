from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.operations.models import Donation, FundAllocation, Project
from apps.operations.services import register_advance
from apps.operations.tests.helpers import create_allocation, create_donation, create_expense, create_institution, create_project


class InternalExperienceTemplateTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(username='operator', password='pass-12345')
        self.client.force_login(self.user)
        self.institution = create_institution(name='Fundación Operativa')
        self.project = create_project(code='PRJ-OPS-001', name='Proyecto operativo')
        self.project.location = 'Zona central'
        self.project.status = Project.Status.ACTIVE
        self.project.save(update_fields=('location', 'status', 'updated_at'))
        self.donation = create_donation(code='DON-OPS-001', donor=self.institution)
        self.allocation = create_allocation(donation=self.donation, project=self.project)
        self.expense = create_expense(allocation=self.allocation)
        self.project_update = register_advance(
            project_id=self.project.pk,
            title='Avance operativo',
            description='Evidencia interna del avance.',
            reported_by=self.user,
            created_by=self.user,
        )

    def test_central_list_views_use_specific_templates_and_columns(self):
        cases = [
            (
                reverse('project_list'),
                'web/project_list.html',
                ['Proyecto', 'Estado', 'Presupuesto', 'Periodo', 'Acciones', 'Proyecto operativo'],
            ),
            (
                reverse('institution_list'),
                'web/institution_list.html',
                ['Institución', 'Tipo', 'Estado', 'Acciones', 'Fundación Operativa'],
            ),
            (
                reverse('donation_list'),
                'web/donation_list.html',
                [
                    'Donación', 'Donante', 'Monto', 'Estado',
                    'Acciones', 'DON-OPS-001',
                ],
            ),
            (
                reverse('allocation_list'),
                'web/allocation_list.html',
                ['Asignación', 'Proyecto', 'Monto', 'Categoría', 'Acciones'],
            ),
            (
                reverse('expense_list'),
                'web/expense_list.html',
                ['Gasto', 'Proyecto', 'Monto', 'Estado', 'Acciones'],
            ),
            (
                reverse('project_update_list'),
                'web/project_update_list.html',
                ['Avance', 'Proyecto', 'Estado', 'Acciones', 'Avance operativo'],
            ),
        ]

        for url, template_name, expected_texts in cases:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertTemplateUsed(response, template_name)
                self.assertContains(response, 'ops-table-card')
                self.assertContains(response, 'class="table-responsive"')
                for text in expected_texts:
                    self.assertContains(response, text)

    def test_project_and_institution_lists_compact_existing_context_and_detail_links(self):
        project_response = self.client.get(reverse('project_list'))
        institution_response = self.client.get(reverse('institution_list'))

        self.assertContains(
            project_response,
            reverse('project_detail', args=[self.project.pk]),
        )
        self.assertContains(project_response, self.project.code)
        self.assertContains(project_response, self.project.location)
        self.assertContains(project_response, 'Cierre:')
        self.assertNotContains(project_response, '<th>Código</th>')
        self.assertNotContains(project_response, '<th>Nombre</th>')
        self.assertNotContains(project_response, '<th>Inicio</th>')
        self.assertNotContains(project_response, '<th>Cierre</th>')

        self.assertContains(
            institution_response,
            reverse('institution_detail', args=[self.institution.pk]),
        )
        self.assertContains(institution_response, self.institution.get_role_display())
        self.assertContains(institution_response, self.institution.country.name)
        self.assertNotContains(institution_response, '<th>Nombre</th>')
        self.assertNotContains(institution_response, '<th>País</th>')

    def test_donation_list_keeps_only_total_amount_and_cycle(self):
        list_response = self.client.get(reverse('donation_list'))
        detail_response = self.client.get(
            reverse('donation_detail', args=[self.donation.pk])
        )

        self.assertContains(
            list_response,
            reverse('donation_detail', args=[self.donation.pk]),
        )
        self.assertContains(list_response, '100,00 USD')
        self.assertContains(list_response, self.donation.get_status_display())
        self.assertNotContains(list_response, 'Recibido')
        self.assertNotContains(list_response, 'Asignado')
        self.assertNotContains(list_response, 'Disponible')
        self.assertNotContains(list_response, '60,00 USD')
        self.assertNotContains(list_response, '40,00 USD')
        self.assertNotContains(list_response, self.donation.allocation_progress_label)
        self.assertContains(detail_response, 'Monto asignado')
        self.assertContains(detail_response, 'Monto disponible')
        self.assertContains(detail_response, self.donation.allocation_progress_label)
        for old_header in (
            'Código', 'Asignado', 'Disponible',
            'Ciclo', 'Asignación', 'Fecha',
        ):
            with self.subTest(old_header=old_header):
                self.assertNotContains(list_response, f'<th>{old_header}</th>')

    def test_allocation_list_keeps_origin_amount_and_category_without_execution(self):
        list_response = self.client.get(reverse('allocation_list'))
        detail_response = self.client.get(
            reverse('allocation_detail', args=[self.allocation.pk])
        )

        self.assertContains(
            list_response,
            reverse('allocation_detail', args=[self.allocation.pk]),
        )
        self.assertContains(list_response, self.allocation.code)
        self.assertContains(list_response, self.donation.code)
        self.assertContains(list_response, self.institution.name)
        self.assertContains(list_response, self.project.name)
        self.assertContains(list_response, self.project.code)
        self.assertContains(list_response, '60,00 USD')
        self.assertContains(list_response, self.allocation.get_budget_category_display())
        self.assertNotContains(list_response, '<th>Ejecución</th>')
        self.assertNotContains(list_response, self.allocation.execution_progress_label)
        self.assertNotContains(list_response, '<th>Ejecutado</th>')
        self.assertNotContains(list_response, '<th>Disponible</th>')
        self.assertNotContains(list_response, '<th>Ciclo</th>')
        self.assertContains(detail_response, 'Monto asignado')
        self.assertContains(detail_response, 'Ejecución')
        self.assertContains(detail_response, self.allocation.execution_progress_label)
        self.assertContains(detail_response, 'Ejecutado')
        self.assertContains(detail_response, 'Disponible')
        self.assertContains(detail_response, self.allocation.get_status_display())

    def test_expense_list_keeps_concept_date_project_amount_and_state(self):
        list_response = self.client.get(reverse('expense_list'))
        detail_response = self.client.get(
            reverse('expense_detail', args=[self.expense.pk])
        )

        self.assertContains(
            list_response,
            reverse('expense_detail', args=[self.expense.pk]),
        )
        self.assertContains(list_response, self.expense.code)
        self.assertContains(list_response, self.expense.reason)
        self.assertContains(list_response, '8 de julio de 2026')
        self.assertContains(list_response, self.project.name)
        self.assertContains(list_response, self.project.code)
        self.assertContains(list_response, self.allocation.code)
        self.assertContains(list_response, '20,00 USD')
        self.assertContains(list_response, self.expense.get_status_display())
        self.assertNotContains(list_response, '<th>Categoría</th>')
        self.assertNotContains(list_response, '<th>Soporte</th>')
        self.assertNotContains(list_response, '<th>Fecha</th>')
        self.assertNotContains(list_response, self.expense.provider_or_recipient)
        self.assertNotContains(list_response, self.donation.code)
        self.assertContains(detail_response, 'Donación origen')
        self.assertContains(detail_response, 'Proveedor o destinatario')
        self.assertContains(detail_response, 'Categoría')
        self.assertContains(detail_response, 'Documentos soporte')

    def test_filtered_internal_lists_share_search_and_advanced_filter_structure(self):
        cases = (
            (reverse('project_list'), ('status', 'date_from', 'date_to'), False),
            (
                reverse('donation_list'),
                ('status', 'institution', 'date_from', 'date_to'),
                False,
            ),
            (
                reverse('allocation_list'),
                ('status', 'institution', 'project', 'date_from', 'date_to'),
                False,
            ),
            (
                reverse('expense_list'),
                ('status', 'institution', 'project', 'date_from', 'date_to'),
                False,
            ),
            (reverse('audit_log_list'), ('status', 'date_from', 'date_to'), True),
        )

        for url, advanced_names, omits_export in cases:
            with self.subTest(url=url):
                response = self.client.get(url)

                self.assertContains(response, 'class="card border-0 mb-3 ops-list-filters"')
                self.assertContains(response, 'method="get"')
                self.assertContains(response, 'name="q"')
                self.assertContains(response, '>Buscar</button>')
                self.assertContains(response, 'data-bs-target="#list-advanced-filters"')
                self.assertContains(response, 'aria-expanded="false"')
                self.assertContains(response, 'aria-controls="list-advanced-filters"')
                self.assertContains(response, 'id="list-advanced-filters"')
                for name in advanced_names:
                    self.assertContains(response, f'name="{name}"')
                for name in {'status', 'institution', 'project'} - set(advanced_names):
                    self.assertNotContains(response, f'name="{name}"')
                if omits_export:
                    self.assertNotContains(response, 'Exportar CSV')
                else:
                    self.assertContains(response, 'Exportar CSV')

        source = Path('templates/web/includes/list_filters.html').read_text()
        css_source = Path('static/web/css/sigedon.css').read_text()
        self.assertIn('<noscript>', source)
        self.assertIn('href="#list-advanced-filters"', source)
        self.assertIn('.ops-list-filter-panel:target', css_source)

    def test_compact_list_badges_prevent_internal_word_breaks(self):
        css_source = Path('static/web/css/sigedon.css').read_text()

        for template_name in (
            'project_list.html',
            'institution_list.html',
            'donation_list.html',
        ):
            with self.subTest(template_name=template_name):
                source = Path('templates/web', template_name).read_text()
                self.assertIn('class="badge ops-status-badge"', source)
                self.assertNotIn('text-break', source)

        self.assertRegex(
            css_source,
            r'\.ops-status-badge,\s*\.badge\s*\{[^}]*white-space: nowrap;[^}]*word-break: normal;',
        )
        donation_source = Path('templates/web/donation_list.html').read_text()
        self.assertIn('class="ops-money text-nowrap"', donation_source)

    def test_financial_detail_views_show_relationships_and_metrics(self):
        cases = [
            (
                reverse('institution_detail', args=[self.institution.pk]),
                'web/institution_detail.html',
                ['Datos de la institución', 'Donaciones asociadas', 'Fundación Operativa', 'Venezuela'],
            ),
            (
                reverse('donation_detail', args=[self.donation.pk]),
                'web/donation_detail.html',
                ['Monto total', 'Monto asignado', 'Monto disponible', 'Asignaciones vinculadas', 'DON-OPS-001'],
            ),
            (
                reverse('allocation_detail', args=[self.allocation.pk]),
                'web/allocation_detail.html',
                ['Donación origen', 'Proyecto destino', 'Monto asignado', 'Ejecutado', 'Disponible', 'Gastos registrados', 'Gastos anulados'],
            ),
        ]

        for url, template_name, expected_texts in cases:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertTemplateUsed(response, template_name)
                for text in expected_texts:
                    self.assertContains(response, text)

    def test_project_detail_shows_usd_summary_without_historical_currency_warning(self):
        response = self.client.get(reverse('project_detail', args=[self.project.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Presupuesto USD')
        self.assertContains(response, 'Financiado USD')
        self.assertContains(response, 'Ejecutado USD')
        self.assertContains(response, 'Disponible USD')
        self.assertNotContains(
            response,
            'Existen movimientos históricos en otras monedas excluidos de este resumen.',
        )

    def test_user_without_permissions_still_gets_403_on_specific_internal_template_view(self):
        limited_user = get_user_model().objects.create_user(username='limited', password='pass-12345')
        self.client.force_login(limited_user)

        response = self.client.get(reverse('donation_list'))

        self.assertEqual(response.status_code, 403)

    def test_anonymous_user_still_redirects_to_login_on_specific_internal_template_view(self):
        self.client.logout()

        response = self.client.get(reverse('donation_list'))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('login'), response['Location'])
