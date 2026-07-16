from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from apps.operations.models import Expense
from apps.operations.role_services import sync_operation_roles
from apps.operations.roles import ROLE_EXTERNAL_AUDITOR, ROLE_FIELD_OPERATOR
from apps.operations.tests.helpers import (
    create_allocation,
    create_donation,
    create_expense,
    create_institution,
    create_project,
)


class FinancialDetailLinkTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username='financial-links-admin',
            password='pass-12345',
        )
        self.client.force_login(self.user)
        self.donor = create_institution(name='Fundación Donante')
        self.project = create_project(code='PRJ-LINK-001', name='Proyecto Humano')
        self.donation = create_donation(
            code='DON-DEMO-001',
            donor=self.donor,
            amount=Decimal('300.00'),
        )
        self.first_allocation = create_allocation(
            donation=self.donation,
            project=self.project,
            amount=Decimal('100.00'),
        )
        self.second_allocation = create_allocation(
            donation=self.donation,
            project=self.project,
            amount=Decimal('80.00'),
        )
        self.first_expense = create_expense(
            allocation=self.first_allocation,
            amount=Decimal('10.00'),
            reason='Compra de alimentos',
        )
        self.second_expense = create_expense(
            allocation=self.first_allocation,
            amount=Decimal('15.00'),
            reason='Transporte humanitario',
        )

    def test_allocation_list_links_each_row_to_its_own_detail(self):
        response = self.client.get(reverse('allocation_list'))

        self.assertContains(response, reverse('allocation_detail', args=[self.first_allocation.pk]))
        self.assertContains(response, reverse('allocation_detail', args=[self.second_allocation.pk]))
        self.assertContains(response, self.first_allocation.code)
        self.assertContains(response, self.second_allocation.code)
        self.assertContains(response, self.project.name)
        self.assertContains(response, self.donation.code)

    def test_donation_detail_links_distinct_allocations(self):
        response = self.client.get(reverse('donation_detail', args=[self.donation.pk]))

        self.assertContains(response, reverse('allocation_detail', args=[self.first_allocation.pk]))
        self.assertContains(response, reverse('allocation_detail', args=[self.second_allocation.pk]))

    def test_expense_list_links_distinct_expenses_and_uses_human_labels(self):
        response = self.client.get(reverse('expense_list'))

        self.assertContains(response, reverse('expense_detail', args=[self.first_expense.pk]))
        self.assertContains(response, reverse('expense_detail', args=[self.second_expense.pk]))
        self.assertContains(response, self.first_expense.code)
        self.assertContains(response, self.second_expense.code)
        self.assertContains(response, self.first_expense.reason)
        self.assertNotContains(response, self.first_expense.provider_or_recipient)
        self.assertContains(response, self.project.name)

    def test_allocation_detail_links_related_entities(self):
        response = self.client.get(reverse('allocation_detail', args=[self.first_allocation.pk]))

        self.assertContains(response, reverse('donation_detail', args=[self.donation.pk]))
        self.assertContains(response, reverse('project_detail', args=[self.project.pk]))
        self.assertContains(response, reverse('expense_detail', args=[self.first_expense.pk]))
        self.assertContains(response, reverse('expense_detail', args=[self.second_expense.pk]))

    def test_expense_detail_links_all_financial_context(self):
        response = self.client.get(reverse('expense_detail', args=[self.first_expense.pk]))

        self.assertContains(response, reverse('allocation_detail', args=[self.first_allocation.pk]))
        self.assertContains(response, reverse('donation_detail', args=[self.donation.pk]))
        self.assertContains(response, reverse('project_detail', args=[self.project.pk]))

    def test_dashboard_recent_expense_links_to_expense_detail(self):
        response = self.client.get(reverse('dashboard'))

        self.assertContains(response, reverse('expense_detail', args=[self.first_expense.pk]))
        self.assertContains(response, reverse('expense_detail', args=[self.second_expense.pk]))

    def test_demo_donation_code_does_not_resolve_expense_route(self):
        response = self.client.get(reverse('expense_detail', args=[self.first_expense.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['object'], self.first_expense)
        self.assertContains(response, 'DON-DEMO-001')


class FinancialDetailPermissionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        sync_operation_roles()
        donor = create_institution(name='Donante de permisos')
        project = create_project(code='PRJ-PERM-001', name='Proyecto de permisos')
        donation = create_donation(code='DON-PERM-001', donor=donor)
        cls.allocation = create_allocation(donation=donation, project=project)
        cls.expense = create_expense(allocation=cls.allocation)

    def _create_role_user(self, username, role_name):
        user = get_user_model().objects.create_user(username=username, password='pass-12345')
        user.groups.add(Group.objects.get(name=role_name))
        return user

    def test_admin_and_auditor_can_open_financial_details(self):
        users = (
            get_user_model().objects.create_superuser('route-admin', password='pass-12345'),
            self._create_role_user('route-auditor', ROLE_EXTERNAL_AUDITOR),
        )

        for user in users:
            self.client.force_login(user)
            for url in (
                reverse('allocation_detail', args=[self.allocation.pk]),
                reverse('expense_detail', args=[self.expense.pk]),
            ):
                with self.subTest(user=user.username, url=url):
                    self.assertEqual(self.client.get(url).status_code, 200)

    def test_field_operator_without_financial_permissions_gets_403(self):
        self.client.force_login(self._create_role_user('route-field', ROLE_FIELD_OPERATOR))

        self.assertEqual(
            self.client.get(reverse('allocation_detail', args=[self.allocation.pk])).status_code,
            403,
        )
        self.assertEqual(
            self.client.get(reverse('expense_detail', args=[self.expense.pk])).status_code,
            403,
        )

    def test_missing_financial_objects_return_404_for_authorized_user(self):
        self.client.force_login(get_user_model().objects.create_superuser('route-404', password='pass-12345'))

        self.assertEqual(self.client.get(reverse('allocation_detail', args=[999999])).status_code, 404)
        self.assertEqual(self.client.get(reverse('expense_detail', args=[999999])).status_code, 404)
