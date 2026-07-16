from datetime import date
from html import escape
from urllib.parse import urlencode

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.operations.models import Donation, Project
from apps.operations.tests.helpers import (
    create_allocation,
    create_donation,
    create_expense,
    create_institution,
    create_project,
    create_user,
)


class SearchAndExportTests(TestCase):
    def setUp(self):
        self.user = create_user()
        self.client.force_login(self.user)
        self.active = create_project(code='PRJ-SEARCH-001', name='Proyecto Alfa')
        self.active.status = Project.Status.ACTIVE
        self.active.save(update_fields=('status',))
        self.closed = create_project(code='PRJ-SEARCH-002', name='Proyecto Beta')
        self.closed.status = Project.Status.CLOSED
        self.closed.save(update_fields=('status',))
        self.donor = create_institution(name='Donante filtrado')
        self.other_donor = create_institution(name='Otro donante')
        self.received_donation = create_donation(
            code='DON-SEARCH-001',
            donor=self.donor,
            amount=100,
            status=Donation.Status.RECEIVED,
        )
        self.received_donation.received_date = date(2026, 7, 10)
        self.received_donation.save(update_fields=('received_date', 'updated_at'))
        self.other_donation = create_donation(
            code='DON-SEARCH-002',
            donor=self.other_donor,
            amount=200,
            status=Donation.Status.REGISTERED,
        )
        self.other_donation.received_date = date(2026, 6, 10)
        self.other_donation.save(update_fields=('received_date', 'updated_at'))
        self.allocation = create_allocation(
            donation=self.received_donation,
            project=self.active,
            amount=20,
        )
        self.expense = create_expense(
            allocation=self.allocation,
            amount=5,
            reason='Gasto para exportación',
        )

    def test_search_by_code(self):
        response = self.client.get(reverse('project_list'), {'q': 'SEARCH-001'})

        self.assertContains(response, self.active.code)
        self.assertNotContains(response, self.closed.code)

    def test_filter_by_status(self):
        response = self.client.get(reverse('project_list'), {'status': Project.Status.CLOSED})

        self.assertContains(response, self.closed.code)
        self.assertNotContains(response, self.active.code)

    def test_combines_search_and_status_filters(self):
        response = self.client.get(
            reverse('project_list'), {'q': 'Proyecto', 'status': Project.Status.ACTIVE}
        )

        self.assertContains(response, self.active.code)
        self.assertNotContains(response, self.closed.code)

    def test_csv_respects_list_filters_and_uses_canonical_amount(self):
        response = self.client.get(
            reverse('project_export_csv'), {'status': Project.Status.ACTIVE}
        )
        content = response.content.decode('utf-8')

        self.assertEqual(response.status_code, 200)
        self.assertIn('Código,Nombre,Estado,Presupuesto USD', content)
        self.assertIn(self.active.code, content)
        self.assertNotIn(self.closed.code, content)
        self.assertIn(str(self.active.estimated_budget), content)

    def test_csv_requires_view_permission(self):
        limited_user = get_user_model().objects.create_user('no-export', password='pass-12345')
        self.client.force_login(limited_user)

        response = self.client.get(reverse('project_export_csv'))

        self.assertEqual(response.status_code, 403)

    def test_donation_filters_and_export_link_preserve_active_query(self):
        params = {
            'q': 'DON-SEARCH',
            'status': Donation.Status.RECEIVED,
            'institution': str(self.donor.pk),
            'date_from': '2026-07-01',
            'date_to': '2026-07-31',
        }

        response = self.client.get(reverse('donation_list'), params)
        active_query = response.context['active_filter_query']

        self.assertContains(response, self.received_donation.code)
        self.assertNotContains(response, self.other_donation.code)
        self.assertEqual(active_query, urlencode(params))
        self.assertContains(response, f'href="{reverse("donation_list")}"')
        self.assertContains(response, 'aria-expanded="true"')
        self.assertContains(response, 'collapse ops-list-filter-panel show')
        self.assertContains(response, '>Activos</span>')
        self.assertIn(
            f'{reverse("donation_export_csv")}?{escape(active_query)}',
            response.content.decode(),
        )

    def test_text_search_alone_preserves_value_and_keeps_advanced_panel_closed(self):
        response = self.client.get(reverse('project_list'), {'q': 'Proyecto'})

        self.assertContains(response, 'name="q" value="Proyecto"')
        self.assertContains(response, 'aria-expanded="false"')
        self.assertContains(response, 'class="collapse ops-list-filter-panel"')
        self.assertNotContains(response, 'collapse ops-list-filter-panel show')

    def test_allocation_filters_preserve_every_existing_query_parameter(self):
        params = {
            'q': self.allocation.code,
            'status': self.allocation.status,
            'institution': str(self.donor.pk),
            'project': str(self.active.pk),
            'date_from': '2026-07-01',
            'date_to': '2026-07-31',
        }

        response = self.client.get(reverse('allocation_list'), params)
        active_query = response.context['active_filter_query']

        self.assertContains(response, self.allocation.code)
        self.assertEqual(active_query, urlencode(params))
        self.assertContains(response, f'name="q" value="{self.allocation.code}"')
        self.assertContains(response, f'value="{self.allocation.status}" selected')
        self.assertContains(response, f'value="{self.donor.pk}" selected')
        self.assertContains(response, f'value="{self.active.pk}" selected')
        self.assertContains(response, 'name="date_from" value="2026-07-01"')
        self.assertContains(response, 'name="date_to" value="2026-07-31"')
        self.assertContains(response, 'aria-expanded="true"')
        self.assertContains(response, f'href="{reverse("allocation_list")}"')
        self.assertIn(
            f'{reverse("allocation_export_csv")}?{escape(active_query)}',
            response.content.decode(),
        )

    def test_allocation_csv_keeps_original_amount_cycle_and_columns(self):
        response = self.client.get(
            reverse('allocation_export_csv'),
            {'q': self.allocation.code},
        )
        content = response.content.decode('utf-8')

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            'Código,Donación,Proyecto,Monto USD,Estado,Ejecución,Fecha,Categoría',
            content,
        )
        self.assertIn(self.allocation.code, content)
        self.assertIn(self.received_donation.code, content)
        self.assertIn(self.active.code, content)
        self.assertIn(str(self.allocation.amount), content)
        self.assertIn(self.allocation.get_status_display(), content)
        self.assertIn(str(self.allocation.execution_progress_label), content)

    def test_expense_csv_keeps_existing_columns_and_values(self):
        response = self.client.get(
            reverse('expense_export_csv'),
            {'q': self.expense.code},
        )
        content = response.content.decode('utf-8')

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            'Código,Proyecto,Asignación,Motivo,Monto,Moneda,Estado,Fecha',
            content,
        )
        self.assertIn(self.expense.code, content)
        self.assertIn(self.active.code, content)
        self.assertIn(self.allocation.code, content)
        self.assertIn(self.expense.reason, content)
        self.assertIn(str(self.expense.amount), content)
        self.assertIn(self.expense.currency, content)
        self.assertIn(self.expense.get_status_display(), content)

    def test_donation_csv_keeps_columns_values_and_filters_after_html_compaction(self):
        response = self.client.get(
            reverse('donation_export_csv'),
            {
                'status': Donation.Status.RECEIVED,
                'institution': self.donor.pk,
            },
        )
        content = response.content.decode('utf-8')

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            'Código,Institución donante,Monto,Moneda,Estado,Compromiso,Recepción',
            content,
        )
        self.assertIn(self.received_donation.code, content)
        self.assertIn(str(self.received_donation.amount), content)
        self.assertIn(self.received_donation.currency, content)
        self.assertNotIn(self.other_donation.code, content)
