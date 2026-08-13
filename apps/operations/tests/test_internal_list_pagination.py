from datetime import date
from decimal import Decimal
from urllib.parse import parse_qs

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.operations.models import (
    Donation,
    Expense,
    FundAllocation,
    Institution,
    Project,
    ProjectUpdate,
)
from apps.operations.tests.helpers import (
    TEST_DATE,
    create_allocation,
    create_donation,
    create_expense,
    create_institution,
    create_project,
    create_user,
)


class ListPaginationAssertionsMixin:
    url = None
    context_key = 'objects'
    total_records = 45
    ordered_codes = ()
    filter_params = None
    export_url_name = None
    export_filter_params = None
    export_row_marker = None

    def assert_pagination_contract(self):
        response = self.client.get(self.url)
        page_objects = list(response.context[self.context_key])

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(page_objects), 20)
        self.assertEqual(response.context['page_size'], 20)
        self.assertTrue(response.context['is_paginated'])
        self.assertContains(response, 'Mostrando 1–20 de 45')
        self.assertContains(response, 'aria-label="Paginación del listado"')
        self.assertContains(response, 'class="ops-pagination"')

        if self.ordered_codes:
            self.assertEqual(
                [getattr(item, 'code', None) or item.name or item.title for item in page_objects],
                list(self.ordered_codes[:20]),
            )

    def assert_second_page_without_overlap(self):
        first_page = self.client.get(self.url)
        second_page = self.client.get(self.url, {'page': '2'})
        first_ids = [item.pk for item in first_page.context[self.context_key]]
        second_ids = [item.pk for item in second_page.context[self.context_key]]

        self.assertEqual(len(second_ids), 20)
        self.assertEqual(len(set(first_ids) & set(second_ids)), 0)
        self.assertContains(second_page, 'Mostrando 21–40 de 45')
        self.assertContains(second_page, 'page_size=20')

    def assert_page_size_options_and_invalid_fallback(self):
        for size in (20, 50, 100):
            with self.subTest(size=size):
                response = self.client.get(self.url, {'page_size': str(size)})
                self.assertEqual(response.context['page_size'], size)
                self.assertEqual(len(response.context[self.context_key]), min(size, self.total_records))

        invalid = self.client.get(self.url, {'page_size': '999'})
        self.assertEqual(invalid.context['page_size'], 20)
        self.assertEqual(len(invalid.context[self.context_key]), 20)

    def assert_filters_preserved_in_pagination_links(self):
        if not self.filter_params:
            return

        params = {**self.filter_params, 'page_size': '50', 'page': '1'}
        response = self.client.get(self.url, params)
        content = response.content.decode()

        self.assertEqual(response.context['page_size'], 50)
        self.assertIn('name="page_size" value="50"', content)
        pagination_query = response.context['pagination_query']
        parsed = {key: values[0] for key, values in parse_qs(pagination_query).items()}
        self.assertIn('page_size=50', pagination_query)
        self.assertNotIn('page=', pagination_query)
        for key, value in self.filter_params.items():
            self.assertEqual(parsed.get(key), str(value))

    def assert_filter_form_omits_page(self):
        if not self.filter_params:
            return

        response = self.client.get(
            self.url,
            {**self.filter_params, 'page': '2', 'page_size': '20'},
        )
        content = response.content.decode()
        filter_markup = content.split('ops-list-filters', 1)[1].split('</form>', 1)[0]

        self.assertIn('name="page_size" value="20"', filter_markup)
        self.assertNotIn('name="page"', filter_markup)

    def assert_csv_export_is_not_paginated(self):
        if not self.export_url_name:
            return

        params = self.export_filter_params or {}
        response = self.client.get(reverse(self.export_url_name), params)
        content = response.content.decode('utf-8')
        row_count = max(content.count('\n') - 1, 0)

        self.assertEqual(response.status_code, 200)
        self.assertGreater(row_count, 20)
        if self.export_row_marker:
            self.assertGreaterEqual(content.count(self.export_row_marker), self.total_records)


class InstitutionListPaginationTests(ListPaginationAssertionsMixin, TestCase):
    url = reverse('institution_list')

    @classmethod
    def setUpTestData(cls):
        cls.user = create_user(username='institution-pager')
        for index in range(cls.total_records):
            create_institution(name=f'Inst PAGE {index:03d}')

    def setUp(self):
        self.client.force_login(self.user)
        self.ordered_codes = tuple(
            Institution.objects.order_by('name', 'pk').values_list('name', flat=True)
        )

    def test_institution_list_pagination_contract(self):
        self.assert_pagination_contract()
        self.assert_second_page_without_overlap()
        self.assert_page_size_options_and_invalid_fallback()

    def test_institution_list_stable_ordering_uses_pk_tiebreaker(self):
        Institution.objects.create(name='Inst PAGE TIE', role=Institution.Role.DONOR)
        Institution.objects.create(name='Inst PAGE TIE', role=Institution.Role.DONOR)
        response = self.client.get(self.url, {'page': '3'})
        ties = [item for item in response.context[self.context_key] if item.name == 'Inst PAGE TIE']

        self.assertEqual(len(ties), 2)
        self.assertLess(ties[0].pk, ties[1].pk)

    def test_institution_list_edge_cases(self):
        Institution.objects.all().delete()
        empty = self.client.get(self.url)
        self.assertEqual(len(empty.context[self.context_key]), 0)
        self.assertContains(empty, 'Mostrando 0 de 0')
        self.assertFalse(empty.context['is_paginated'])

        for index in range(21):
            create_institution(name=f'Inst EDGE {index:02d}')
        response = self.client.get(self.url)
        self.assertEqual(len(response.context[self.context_key]), 20)
        self.assertTrue(response.context['is_paginated'])

    def test_institution_list_out_of_range_page_returns_404(self):
        self.assertEqual(self.client.get(self.url, {'page': '999'}).status_code, 404)


class ProjectListPaginationTests(ListPaginationAssertionsMixin, TestCase):
    url = reverse('project_list')
    export_url_name = 'project_export_csv'
    export_filter_params = {'q': 'PRJ-PAGE'}
    export_row_marker = 'PRJ-PAGE-'

    @classmethod
    def setUpTestData(cls):
        cls.user = create_user(username='project-pager')
        for index in range(cls.total_records):
            project = create_project(code=f'PRJ-PAGE-{index:03d}', name=f'Proyecto {index:03d}')
            project.status = Project.Status.ACTIVE
            project.save(update_fields=('status', 'updated_at'))

    def setUp(self):
        self.client.force_login(self.user)
        self.ordered_codes = tuple(
            Project.objects.filter(code__startswith='PRJ-PAGE-').order_by('code', 'pk').values_list('code', flat=True)
        )
        self.filter_params = {
            'q': 'PRJ-PAGE',
            'status': Project.Status.ACTIVE,
        }

    def test_project_list_pagination_contract(self):
        self.assert_pagination_contract()
        self.assert_second_page_without_overlap()
        self.assert_page_size_options_and_invalid_fallback()
        self.assert_filters_preserved_in_pagination_links()
        self.assert_filter_form_omits_page()
        self.assert_csv_export_is_not_paginated()


class ProjectUpdateListPaginationTests(ListPaginationAssertionsMixin, TestCase):
    url = reverse('project_update_list')

    @classmethod
    def setUpTestData(cls):
        cls.user = create_user(username='update-pager')
        cls.project = create_project(code='PRJ-UPD-PAGE', name='Proyecto avances')
        for index in range(cls.total_records):
            ProjectUpdate.objects.create(
                project=cls.project,
                title=f'Avance PAGE {index:03d}',
                description='Paginación',
                update_date=TEST_DATE,
                created_by=cls.user,
                reported_by=cls.user,
            )

    def setUp(self):
        self.client.force_login(self.user)

    def test_project_update_list_pagination_contract(self):
        self.assert_pagination_contract()
        self.assert_second_page_without_overlap()
        self.assert_page_size_options_and_invalid_fallback()

    def test_project_update_list_out_of_range_page_returns_404(self):
        self.assertEqual(self.client.get(self.url, {'page': '999'}).status_code, 404)


class DonationListPaginationTests(ListPaginationAssertionsMixin, TestCase):
    url = reverse('donation_list')
    export_url_name = 'donation_export_csv'
    export_filter_params = {'q': 'DON-PAGE-'}
    export_row_marker = 'DON-PAGE-'

    @classmethod
    def setUpTestData(cls):
        cls.user = create_user(username='donation-pager')
        cls.donor = create_institution(name='Donante paginación')
        for index in range(cls.total_records):
            donation = create_donation(
                code=f'DON-PAGE-{index:03d}',
                donor=cls.donor,
                amount=Decimal('100.00'),
            )
            donation.received_date = date(2026, 7, 10)
            donation.save(update_fields=('received_date', 'updated_at'))

    def setUp(self):
        self.client.force_login(self.user)
        self.filter_params = {
            'q': 'DON-PAGE-',
            'status': Donation.Status.RECEIVED,
            'institution': str(self.donor.pk),
            'date_from': '2026-07-01',
            'date_to': '2026-07-31',
        }

    def test_donation_list_pagination_contract(self):
        self.assert_pagination_contract()
        self.assert_second_page_without_overlap()
        self.assert_page_size_options_and_invalid_fallback()
        self.assert_filters_preserved_in_pagination_links()
        self.assert_filter_form_omits_page()
        self.assert_csv_export_is_not_paginated()

    def test_donation_list_stable_ordering_uses_pk_tiebreaker(self):
        same_date = date(2026, 7, 1)
        first = create_donation(code='DON-TIE-001', donor=self.donor)
        second = create_donation(code='DON-TIE-002', donor=self.donor)
        Donation.objects.filter(pk=first.pk).update(received_date=same_date)
        Donation.objects.filter(pk=second.pk).update(received_date=same_date)
        response = self.client.get(self.url, {'q': 'DON-TIE'})
        donations = list(response.context[self.context_key])
        self.assertEqual(len(donations), 2)
        self.assertGreater(donations[0].pk, donations[1].pk)


class AllocationListPaginationTests(ListPaginationAssertionsMixin, TestCase):
    url = reverse('allocation_list')
    export_url_name = 'allocation_export_csv'
    export_filter_params = {'q': 'DON-ALLOC-PAGE'}
    export_row_marker = 'DON-ALLOC-PAGE'

    @classmethod
    def setUpTestData(cls):
        cls.user = create_user(username='allocation-pager')
        cls.donor = create_institution(name='Donante asignación paginación')
        cls.project = create_project(code='PRJ-ALLOC-PAGE', name='Proyecto asignación')
        cls.donation = create_donation(
            code='DON-ALLOC-PAGE',
            donor=cls.donor,
            amount=Decimal('10000.00'),
        )
        for index in range(cls.total_records):
            create_allocation(
                donation=cls.donation,
                project=cls.project,
                amount=Decimal('10.00'),
            )

    def setUp(self):
        self.client.force_login(self.user)
        self.filter_params = {
            'q': 'DON-ALLOC-PAGE',
            'status': FundAllocation.Status.ACTIVE,
            'institution': str(self.donor.pk),
            'project': str(self.project.pk),
            'date_from': '2026-07-01',
            'date_to': '2026-07-31',
        }

    def test_allocation_list_pagination_contract(self):
        self.assert_pagination_contract()
        self.assert_second_page_without_overlap()
        self.assert_page_size_options_and_invalid_fallback()
        self.assert_filters_preserved_in_pagination_links()
        self.assert_filter_form_omits_page()
        self.assert_csv_export_is_not_paginated()


class ExpenseListPaginationTests(ListPaginationAssertionsMixin, TestCase):
    url = reverse('expense_list')
    export_url_name = 'expense_export_csv'
    export_filter_params = {'q': 'Gasto PAGE'}
    export_row_marker = 'Gasto PAGE'

    @classmethod
    def setUpTestData(cls):
        cls.user = create_user(username='expense-pager')
        cls.donor = create_institution(name='Donante gasto paginación')
        cls.project = create_project(code='PRJ-EXP-PAGE', name='Proyecto gasto')
        cls.allocation = create_allocation(
            donation=create_donation(code='DON-EXP-PAGE', donor=cls.donor, amount=Decimal('10000.00')),
            project=cls.project,
            amount=Decimal('5000.00'),
        )
        for index in range(cls.total_records):
            create_expense(
                allocation=cls.allocation,
                amount=Decimal('1.00'),
                reason=f'Gasto PAGE {index:03d}',
            )

    def setUp(self):
        self.client.force_login(self.user)
        self.filter_params = {
            'q': 'Gasto PAGE',
            'status': Expense.Status.REGISTERED,
            'institution': str(self.donor.pk),
            'project': str(self.project.pk),
            'date_from': '2026-07-01',
            'date_to': '2026-07-31',
        }

    def test_expense_list_pagination_contract(self):
        self.assert_pagination_contract()
        self.assert_second_page_without_overlap()
        self.assert_page_size_options_and_invalid_fallback()
        self.assert_filters_preserved_in_pagination_links()
        self.assert_filter_form_omits_page()
        self.assert_csv_export_is_not_paginated()

    def test_expense_list_page_size_change_from_second_page_resets_via_form(self):
        response = self.client.get(self.url, {'page': '2', 'page_size': '20', 'q': 'Gasto PAGE'})
        content = response.content.decode()
        pagination_form = content.split('ops-pagination-size', 1)[1].split('</form>', 1)[0]

        self.assertIn('name="page_size"', pagination_form)
        self.assertIn('value="20" selected', pagination_form)
        self.assertNotIn('name="page"', pagination_form)
