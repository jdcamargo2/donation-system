from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.operations.models import Donation, Expense, FundAllocation, Project, ProjectUpdate, SupportingDocument
from apps.operations.services import register_advance
from apps.operations.tests.helpers import create_allocation, create_donation, create_expense, create_institution, create_project


class InternalExperienceTemplateTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(username='operator', password='pass-12345')
        self.client.force_login(self.user)
        self.institution = create_institution(name='Fundación Operativa')
        self.project = create_project(code='PRJ-OPS-001', name='Proyecto operativo')
        self.project.location = 'Zona central'
        self.project.responsible_unit = 'Unidad de proyectos'
        self.project.objective = 'Mejorar la atención comunitaria.'
        self.project.description = 'Intervención operativa priorizada.'
        self.project.status = Project.Status.ACTIVE
        self.project.save(update_fields=(
            'location', 'responsible_unit', 'objective', 'description',
            'status', 'updated_at',
        ))
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
        self.assertContains(detail_response, 'Estado de ejecución')
        self.assertContains(detail_response, self.allocation.execution_progress_label)
        self.assertContains(detail_response, 'Ejecutado')
        self.assertContains(detail_response, 'Disponible')
        self.assertContains(detail_response, self.allocation.get_status_display())

    def test_allocation_detail_compacts_actions_and_preserves_delete_fallback(self):
        response = self.client.get(reverse('allocation_detail', args=[self.allocation.pk]))
        content = response.content.decode()
        delete_url = reverse('allocation_delete', args=[self.allocation.pk])

        self.assertContains(response, 'Distribución financiera')
        self.assertContains(response, f'<h1>{self.allocation.code}</h1>', count=1, html=True)
        self.assertContains(response, self.allocation.get_status_display(), count=1)
        self.assertContains(response, 'Nuevo gasto')
        self.assertContains(response, reverse('expense_create'))
        self.assertContains(response, 'Más')
        self.assertIn(f'href="{delete_url}"', content)
        self.assertIn(
            f'id="allocation-delete-form" method="post" action="{delete_url}"',
            content,
        )
        self.assertContains(response, 'data-confirm-action')
        self.assertContains(response, 'data-confirm-title="¿Eliminar esta asignación?"')
        self.assertContains(response, 'data-confirm-variant="danger"')
        self.assertNotContains(response, 'Finalizar')
        self.assertNotIn('status_transitions', Path('templates/web/allocation_detail.html').read_text())

    def test_allocation_detail_links_relations_only_with_their_permissions(self):
        viewer = get_user_model().objects.create_user(
            username='allocation-relationship-viewer', password='pass-12345',
        )
        viewer.user_permissions.add(
            Permission.objects.get(codename='view_fundallocation'),
        )
        self.client.force_login(viewer)
        detail_url = reverse('allocation_detail', args=[self.allocation.pk])
        donation_url = reverse('donation_detail', args=[self.donation.pk])
        project_url = reverse('project_detail', args=[self.project.pk])

        response = self.client.get(detail_url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.donation.code)
        self.assertContains(response, self.project.code)
        self.assertNotContains(response, f'href="{donation_url}"')
        self.assertNotContains(response, f'href="{project_url}"')

        viewer.user_permissions.add(
            Permission.objects.get(codename='view_donation'),
            Permission.objects.get(codename='view_project'),
        )
        response = self.client.get(detail_url)
        self.assertContains(response, f'href="{donation_url}"')
        self.assertContains(response, f'href="{project_url}"')

    def test_allocation_detail_limits_recent_expenses_without_limiting_financial_summary(self):
        allocation = create_allocation(
            donation=create_donation(code='DON-DETAIL-EXP', amount=Decimal('200.00')),
            project=create_project(code='PRJ-DETAIL-EXP'),
            amount=Decimal('100.00'),
        )
        expenses = []
        for index in range(6):
            expense = create_expense(
                allocation=allocation,
                amount=Decimal('10.00'),
                reason=f'Gasto ordenado {index}',
            )
            Expense.objects.filter(pk=expense.pk).update(
                expense_date=allocation.allocation_date + timedelta(days=index),
            )
            expenses.append(expense)
        Expense.objects.filter(pk=expenses[0].pk).update(status=Expense.Status.ANNULLED)

        response = self.client.get(reverse('allocation_detail', args=[allocation.pk]))
        recent_expenses = response.context['recent_allocation_expenses']
        summary = response.context['allocation_financial_summary']

        self.assertEqual(response.context['allocation_expense_count'], 6)
        self.assertTrue(response.context['has_more_allocation_expenses'])
        self.assertEqual(len(recent_expenses), 5)
        self.assertEqual(
            [expense.reason for expense in recent_expenses],
            [f'Gasto ordenado {index}' for index in (5, 4, 3, 2, 1)],
        )
        self.assertEqual(summary['executed_amount'], Decimal('50.00'))
        self.assertEqual(summary['available_amount'], Decimal('50.00'))
        self.assertContains(response, 'Mostrando 5 de 6 gastos')
        self.assertContains(response, expenses[0].get_status_display())
        self.assertNotContains(response, 'Gastos registrados')
        self.assertNotContains(response, 'Gastos anulados')

    def test_allocation_detail_recent_expenses_do_not_add_queries_per_row(self):
        one_expense = create_allocation(
            donation=create_donation(code='DON-DETAIL-QUERY-1', amount=Decimal('200.00')),
            project=create_project(code='PRJ-DETAIL-QUERY-1'),
            amount=Decimal('100.00'),
        )
        five_expenses = create_allocation(
            donation=create_donation(code='DON-DETAIL-QUERY-5', amount=Decimal('200.00')),
            project=create_project(code='PRJ-DETAIL-QUERY-5'),
            amount=Decimal('100.00'),
        )
        create_expense(allocation=one_expense, amount=Decimal('10.00'))
        for index in range(5):
            create_expense(
                allocation=five_expenses,
                amount=Decimal('10.00'),
                reason=f'Consulta de gasto {index}',
            )

        with CaptureQueriesContext(connection) as one_expense_queries:
            self.client.get(reverse('allocation_detail', args=[one_expense.pk]))
        with CaptureQueriesContext(connection) as five_expenses_queries:
            self.client.get(reverse('allocation_detail', args=[five_expenses.pk]))

        self.assertEqual(len(one_expense_queries), len(five_expenses_queries))

    def test_allocation_detail_hides_empty_information_and_keeps_metadata(self):
        allocation = create_allocation(
            donation=create_donation(code='DON-DETAIL-EMPTY'),
            project=create_project(code='PRJ-DETAIL-EMPTY'),
        )

        response = self.client.get(reverse('allocation_detail', args=[allocation.pk]))

        self.assertContains(response, 'Sin gastos vinculados')
        self.assertContains(response, 'Información de registro')
        self.assertContains(response, 'Creada')
        self.assertContains(response, 'Actualizada')
        self.assertNotContains(response, 'Información de la asignación')
        self.assertNotContains(response, '>Responsable<')
        self.assertNotContains(response, '>Notas<')

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
        self.assertContains(detail_response, 'Origen financiero')
        self.assertContains(detail_response, 'Proveedor o destinatario')
        self.assertContains(detail_response, 'Categoría')
        self.assertContains(detail_response, 'Documentos de soporte')

    def test_expense_detail_compacts_information_and_prefetches_support(self):
        self.expense.payment_method = 'transfer'
        self.expense.observations = 'Confirmado con el proveedor.'
        self.expense.save(update_fields=('payment_method', 'observations', 'updated_at'))
        document = SupportingDocument.objects.create(
            expense=self.expense,
            title='Factura operativa',
            document='supporting_documents/factura.pdf',
            notes='Archivo revisado.',
        )

        response = self.client.get(reverse('expense_detail', args=[self.expense.pk]))
        expense = response.context['object']

        self.assertContains(response, 'Ejecución financiera')
        self.assertContains(response, self.expense.code)
        self.assertContains(response, self.expense.get_status_display(), count=1)
        self.assertContains(response, self.expense.reason, count=1)
        self.assertContains(response, '8 de julio de 2026')
        self.assertContains(response, 'Monto del gasto')
        self.assertContains(response, '20,00 USD', count=1)
        self.assertContains(response, self.expense.get_payment_method_display())
        self.assertContains(response, self.expense.observations)
        self.assertContains(response, 'Información de registro')
        self.assertContains(response, document.title)
        self.assertContains(response, 'Descargar')
        with CaptureQueriesContext(connection) as queries:
            list(expense.supporting_documents.all())
            str(expense.allocation.donation.donor)
            str(expense.allocation.project)
        self.assertEqual(len(queries), 0)

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
                ['Ficha institucional', 'Donaciones asociadas', 'Fundación Operativa', 'Venezuela'],
            ),
            (
                reverse('donation_detail', args=[self.donation.pk]),
                'web/donation_detail.html',
                ['Monto recibido', 'Monto asignado', 'Monto disponible', 'Asignaciones vinculadas', 'DON-OPS-001'],
            ),
            (
                reverse('allocation_detail', args=[self.allocation.pk]),
                'web/allocation_detail.html',
                ['Donación origen', 'Proyecto destino', 'Monto asignado', 'Ejecutado', 'Disponible', 'Gastos vinculados'],
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
        self.assertContains(response, 'Financiado')
        self.assertContains(response, 'Ejecutado')
        self.assertContains(response, 'Disponible')
        self.assertContains(response, 'Presupuesto estimado:')
        self.assertContains(response, 'Ejecución financiera:')
        self.assertNotContains(
            response,
            'Existen movimientos históricos en otras monedas excluidos de este resumen.',
        )

        financial_html = response.content.decode().split(
            '<section class="ops-project-financial-summary', 1
        )[1].split('</section>', 1)[0]
        for interactive_markup in (
            '<input', 'type="number"', '<button', '<select', '<details', '<summary',
        ):
            with self.subTest(interactive_markup=interactive_markup):
                self.assertNotIn(interactive_markup, financial_html)
        css_source = Path('static/web/css/sigedon.css').read_text()
        financial_css = css_source.split('.ops-project-financial-value {', 1)[1].split('}', 1)[0]
        self.assertNotIn('overflow-x: auto', financial_css)

    def test_project_detail_compacts_identity_and_general_information(self):
        response = self.client.get(reverse('project_detail', args=[self.project.pk]))

        self.assertContains(response, f'<h1>{self.project.name}</h1>', count=1, html=True)
        self.assertContains(response, self.project.code, count=1)
        self.assertContains(response, self.project.get_status_display(), count=1)
        self.assertContains(response, self.project.location, count=2)
        self.assertContains(response, self.project.responsible_unit, count=2)
        self.assertContains(response, 'Información general')
        self.assertContains(response, self.project.objective)
        self.assertContains(response, self.project.description)
        self.assertNotContains(response, 'Beneficiarios')
        self.assertNotContains(response, 'Sin información registrada para esta vista')
        self.assertNotContains(response, 'Sin información registrada en esta fase')

        source = Path('templates/web/project_detail.html').read_text()
        general_section = source.split('ops-project-general-info', 1)[1].split(
            'Avances del proyecto', 1
        )[0]
        self.assertNotIn('{{ object.name }}', general_section)
        self.assertNotIn('{{ object.code }}', general_section)
        self.assertNotIn('{{ object.get_status_display }}', general_section)
        self.assertNotIn('estimated_budget', general_section)

    def test_project_detail_unifies_actions_and_keeps_mutations_as_post_forms(self):
        response = self.client.get(reverse('project_detail', args=[self.project.pk]))
        content = response.content.decode()

        self.assertContains(response, reverse('project_update', args=[self.project.pk]))
        self.assertNotContains(response, 'aria-label="Cambiar estado del proyecto"')
        self.assertContains(response, 'aria-label="Más acciones del proyecto"')
        self.assertContains(response, reverse('project_finish', args=[self.project.pk]))
        self.assertContains(response, 'Terminar proyecto')
        self.assertNotContains(response, 'Anular proyecto')
        self.assertIn('name="csrfmiddlewaretoken"', content)

        viewer = get_user_model().objects.create_user(
            username='project-detail-viewer',
            password='pass-12345',
        )
        viewer.user_permissions.add(Permission.objects.get(codename='view_project'))
        published_update = ProjectUpdate.objects.create(
            project=self.project,
            title='Avance publicado visible',
            description='Visible sin permiso técnico de avances.',
            status=ProjectUpdate.Status.PUBLISHED,
            created_by=self.user,
            reported_by=self.user,
        )
        self.client.force_login(viewer)
        viewer_response = self.client.get(
            reverse('project_detail', args=[self.project.pk])
        )
        self.assertEqual(viewer_response.status_code, 200)
        self.assertNotContains(
            viewer_response,
            reverse('project_update', args=[self.project.pk]),
        )
        self.assertNotContains(viewer_response, 'aria-label="Más acciones del proyecto"')
        self.assertNotContains(viewer_response, self.project_update.title)
        self.assertContains(viewer_response, published_update.title)
        self.client.force_login(self.user)

        self.project.status = Project.Status.CLOSED
        self.project.save(update_fields=('status', 'updated_at'))
        terminal_response = self.client.get(
            reverse('project_detail', args=[self.project.pk])
        )
        self.assertNotContains(
            terminal_response,
            reverse('project_update', args=[self.project.pk]),
        )
        self.assertNotContains(terminal_response, 'Terminar proyecto')

    def test_project_detail_keeps_milestones_once_after_financial_summary(self):
        response = self.client.get(reverse('project_detail', args=[self.project.pk]))
        content = response.content.decode()

        self.assertContains(response, 'id="project-milestones"', count=1)
        self.assertLess(
            content.index('ops-project-financial-summary'),
            content.index('id="project-milestones"'),
        )

    def test_project_detail_limits_recent_updates_and_reports_total_in_stable_order(self):
        created_updates = [
            ProjectUpdate.objects.create(
                project=self.project,
                title=f'Avance reciente {index}',
                description='Descripción breve para el detalle.',
                created_by=self.user,
                reported_by=self.user,
            )
            for index in range(6)
        ]

        response = self.client.get(reverse('project_detail', args=[self.project.pk]))

        self.assertEqual(response.context['project_update_count'], 7)
        self.assertTrue(response.context['has_more_project_updates'])
        recent_updates = response.context['recent_project_updates']
        self.assertEqual(len(recent_updates), 5)
        self.assertEqual(
            [update.pk for update in recent_updates],
            [update.pk for update in reversed(created_updates[-5:])],
        )
        chunk_url = reverse('project_update_chunk', args=[self.project.pk])
        self.assertContains(response, 'Avances del proyecto')
        self.assertContains(response, 'Ver más avances', count=1)
        self.assertContains(response, f'hx-get="{chunk_url}?page=2"')
        self.assertContains(response, 'hx-target="this"')
        self.assertContains(response, 'hx-swap="outerHTML"')
        self.assertContains(response, 'aria-controls="project-update-list"')
        self.assertContains(response, 'aria-live="polite"')
        self.assertContains(response, 'web/js/project_update_chunks.js')
        self.assertTemplateUsed(response, 'web/includes/project_update_item.html')
        self.assertContains(
            response,
            reverse('project_update_create_for_project', args=[self.project.pk]),
        )
        self.assertNotContains(response, created_updates[0].title)

        chunk_script = Path('static/web/js/project_update_chunks.js').read_text()
        self.assertIn('focus({ preventScroll: true })', chunk_script)
        self.assertNotIn('scrollIntoView', chunk_script)

    def test_project_detail_handles_zero_one_and_five_visible_updates(self):
        for count in (0, 1, 5):
            with self.subTest(count=count):
                project = create_project(code=f'PRJ-OPS-UPDATES-{count}')
                for index in range(count):
                    ProjectUpdate.objects.create(
                        project=project,
                        title=f'Avance {count}-{index}',
                        description='Registro para cardinalidad.',
                        created_by=self.user,
                        reported_by=self.user,
                    )

                response = self.client.get(reverse('project_detail', args=[project.pk]))

                self.assertEqual(response.context['project_update_count'], count)
                self.assertEqual(len(response.context['recent_project_updates']), count)
                self.assertFalse(response.context['has_more_project_updates'])
                if count == 0:
                    self.assertContains(response, 'No hay avances')
                else:
                    self.assertNotContains(response, 'Ver más avances')

    def test_project_detail_recent_updates_do_not_add_queries_per_row(self):
        projects = []
        for count in (1, 5):
            project = create_project(code=f'PRJ-OPS-QUERY-{count}')
            project.status = Project.Status.ACTIVE
            project.save(update_fields=('status', 'updated_at'))
            for index in range(count):
                ProjectUpdate.objects.create(
                    project=project,
                    title=f'Consulta {count}-{index}',
                    description='Control de consultas.',
                    created_by=self.user,
                    reported_by=self.user,
                )
            projects.append(project)

        with CaptureQueriesContext(connection) as one_update_queries:
            self.client.get(reverse('project_detail', args=[projects[0].pk]))
        with CaptureQueriesContext(connection) as five_update_queries:
            self.client.get(reverse('project_detail', args=[projects[1].pk]))

        self.assertEqual(len(one_update_queries), len(five_update_queries))

    def test_project_detail_confirmation_hooks_keep_post_forms_and_get_fallbacks(self):
        response = self.client.get(reverse('project_detail', args=[self.project.pk]))
        content = response.content.decode()
        finish_url = reverse('project_finish', args=[self.project.pk])
        update_delete_url = reverse(
            'project_update_delete', args=[self.project_update.pk]
        )

        for url, form_id in (
            (finish_url, 'project-finish-form'),
            (update_delete_url, f'project-update-delete-form-{self.project_update.pk}'),
        ):
            with self.subTest(url=url):
                self.assertIn(f'href="{url}"', content)
                self.assertIn(f'id="{form_id}" method="post" action="{url}"', content)
        self.assertContains(response, 'data-confirm-action', count=2)
        self.assertContains(response, 'data-confirm-title="¿Terminar este proyecto?"')
        self.assertContains(response, 'data-confirm-title="¿Eliminar este avance?"')
        self.assertContains(response, 'web/js/confirm_actions.js')
        self.assertGreaterEqual(content.count('name="csrfmiddlewaretoken"'), 2)

        self.assertEqual(self.client.get(finish_url).status_code, 200)
        self.assertEqual(self.client.get(update_delete_url).status_code, 200)
        self.project.refresh_from_db()
        self.assertEqual(self.project.status, Project.Status.ACTIVE)
        self.assertTrue(ProjectUpdate.objects.filter(pk=self.project_update.pk).exists())

        script = Path('static/web/js/confirm_actions.js').read_text()
        self.assertEqual(script.count("document.addEventListener('click'"), 1)
        self.assertIn('form.requestSubmit()', script)
        self.assertIn("prefers-reduced-motion: reduce", script)
        self.assertIn('trigger.focus({ preventScroll: true })', script)
        self.assertNotIn('project_update', script)
        self.assertNotIn('project_document', script)

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
