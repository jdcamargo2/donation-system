"""Expense Request form unit tests (ER3B)."""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase

from apps.operations.forms import (
    ExpenseRequestAllocationChoiceField,
    ExpenseRequestForProjectForm,
    ExpenseRequestForm,
)
from apps.operations.models import FundAllocation
from apps.operations.role_services import sync_operation_roles
from apps.operations.roles import ROLE_FIELD_OPERATOR, ROLE_SIGEDON_ADMIN
from apps.operations.selectors import expense_request_allocation_choices
from apps.operations.tests.helpers import (
    TEST_DATE,
    create_allocation,
    create_donation,
    create_institution,
    create_project,
)


class ExpenseRequestFormTests(TestCase):
    def setUp(self):
        sync_operation_roles()
        self.donor = create_institution(name='Donante Form ER3B')
        self.project_a = create_project(code='PRJ-ER3B-A', name='Proyecto A')
        self.project_b = create_project(code='PRJ-ER3B-B', name='Proyecto B')
        self.donation = create_donation(
            code='DON-ER3B-1',
            donor=self.donor,
            amount=Decimal('500.00'),
        )
        self.allocation_a = create_allocation(
            donation=self.donation,
            project=self.project_a,
            amount=Decimal('100.00'),
            category='health_psychosocial',
        )
        self.allocation_b = create_allocation(
            donation=self.donation,
            project=self.project_b,
            amount=Decimal('80.00'),
            category='training_entrepreneurship',
        )
        self.inactive = create_allocation(
            donation=self.donation,
            project=self.project_a,
            amount=Decimal('50.00'),
            status=FundAllocation.Status.ANNULLED,
        )
        self.admin = self._user('er3b-form-admin', ROLE_SIGEDON_ADMIN)
        self.operator = self._user('er3b-form-operator', ROLE_FIELD_OPERATOR)

    def _user(self, username, role_name):
        user = get_user_model().objects.create_user(username=username, password='pass-12345')
        user.groups.add(Group.objects.get(name=role_name))
        return user

    def test_allocation_label_uses_category_and_available_balance(self):
        qs = expense_request_allocation_choices(project=self.project_a)
        field = ExpenseRequestAllocationChoiceField(queryset=qs)
        label = field.label_from_instance(qs.get(pk=self.allocation_a.pk))
        self.assertIn('Salud y apoyo psicosocial', label)
        self.assertIn('Disponible:', label)
        self.assertIn('USD', label)
        self.assertNotIn(self.donor.name, label)
        self.assertNotIn(self.donation.code, label)
        self.assertNotIn(str(self.allocation_a), label)

    def test_global_form_appends_project_in_label(self):
        form = ExpenseRequestForm(include_project_in_label=True)
        allocation = form.fields['fund_allocation'].queryset.get(pk=self.allocation_a.pk)
        label = form.fields['fund_allocation'].label_from_instance(allocation)
        self.assertIn(self.project_a.code, label)
        self.assertIn(self.project_a.name, label)

    def test_project_form_queryset_excludes_other_project_allocations(self):
        form = ExpenseRequestForProjectForm(project=self.project_a)
        pks = set(form.fields['fund_allocation'].queryset.values_list('pk', flat=True))
        self.assertIn(self.allocation_a.pk, pks)
        self.assertNotIn(self.allocation_b.pk, pks)
        self.assertNotIn(self.inactive.pk, pks)

    def test_forged_other_project_allocation_rejected(self):
        form = ExpenseRequestForProjectForm(
            data={
                'fund_allocation': str(self.allocation_b.pk),
                'requested_amount': '10,00',
                'purpose': 'Propósito válido de prueba',
                'requested_date': TEST_DATE.isoformat(),
            },
            project=self.project_a,
        )
        self.assertFalse(form.is_valid())
        self.assertIn('fund_allocation', form.errors)

    def test_amount_may_exceed_available_balance(self):
        form = ExpenseRequestForProjectForm(
            data={
                'fund_allocation': str(self.allocation_a.pk),
                'requested_amount': '250,00',
                'purpose': 'Solicitud por encima del saldo',
                'requested_date': TEST_DATE.isoformat(),
            },
            project=self.project_a,
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['requested_amount'], Decimal('250.00'))

    def test_inactive_allocation_rejected(self):
        form = ExpenseRequestForm(
            data={
                'fund_allocation': str(self.inactive.pk),
                'requested_amount': '10,00',
                'purpose': 'Asignación inactiva',
                'requested_date': TEST_DATE.isoformat(),
            },
            include_project_in_label=True,
        )
        self.assertFalse(form.is_valid())
        self.assertIn('fund_allocation', form.errors)

    def test_empty_purpose_rejected(self):
        form = ExpenseRequestForm(
            data={
                'fund_allocation': str(self.allocation_a.pk),
                'requested_amount': '10,00',
                'purpose': '   ',
                'requested_date': TEST_DATE.isoformat(),
            },
            include_project_in_label=True,
        )
        self.assertFalse(form.is_valid())
        self.assertIn('purpose', form.errors)

    def test_form_has_no_immutable_fields(self):
        form = ExpenseRequestForm(include_project_in_label=True)
        for forbidden in (
            'requested_by',
            'status',
            'code',
            'reserved_amount',
            'expense',
            'decision_note',
        ):
            self.assertNotIn(forbidden, form.fields)
