from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.operations.models import AuditLog, Donation, Expense, FundAllocation
from apps.operations.tests.helpers import TEST_DATE, create_allocation, create_donation, create_expense, create_institution, create_project, create_user


class AuditTests(TestCase):
    def setUp(self):
        self.user = create_user()
        self.client.force_login(self.user)
        self.donor = create_institution()
        self.project = create_project()
        self.donation = create_donation(donor=self.donor, amount=Decimal('100.00'))
        self.allocation = create_allocation(donation=self.donation, project=self.project, amount=Decimal('50.00'))
        self.expense = create_expense(allocation=self.allocation, amount=Decimal('10.00'))

    def test_creating_donation_records_audit_log(self):
        self.client.post(
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

        self.assertTrue(AuditLog.objects.filter(action=AuditLog.Action.CREATED, model_name='Donación').exists())

    def test_updating_donation_records_audit_log(self):
        self.client.post(
            reverse('donation_update', args=[self.donation.pk]),
            data={
                'donor': self.donor.pk,
                'donation_type': 'goods',
                'amount': '120.00',
                'currency': 'USD',
                'objective': 'Apoyar atención de emergencia',
                'restrictions': '',
                'commitment_date': '',
                'received_date': '',
                'status': Donation.Status.RECEIVED,
                'support_reference': '',
            },
        )

        self.assertTrue(AuditLog.objects.filter(action=AuditLog.Action.UPDATED, model_name='Donación').exists())

    def test_creating_and_updating_allocation_records_audit_logs(self):
        self.client.post(
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
        self.client.post(
            reverse('allocation_update', args=[self.allocation.pk]),
            data={
                'donation': self.donation.pk,
                'project': self.project.pk,
                'budget_category': self.allocation.budget_category,
                'amount': '55.00',
                'responsible_person': '',
                'allocation_date': TEST_DATE,
                'status': FundAllocation.Status.ACTIVE,
                'notes': '',
            },
        )

        self.assertEqual(AuditLog.objects.filter(action=AuditLog.Action.ASSIGNED, model_name='Asignación de fondos').count(), 2)

    def test_creating_and_updating_expense_records_audit_logs(self):
        self.client.post(
            reverse('expense_create'),
            data={
                'allocation': self.allocation.pk,
                'expense_date': TEST_DATE,
                'category': 'food',
                'amount': '5.00',
                'currency': 'USD',
                'reason': 'New expense',
                'provider_or_recipient': 'Provider A',
                'payment_method': 'bank_transfer',
                'description': '',
                'observations': '',
                'status': Expense.Status.REGISTERED,
            },
        )
        self.client.post(
            reverse('expense_update', args=[self.expense.pk]),
            data={
                'allocation': self.allocation.pk,
                'expense_date': TEST_DATE,
                'category': 'food',
                'amount': '15.00',
                'currency': 'USD',
                'reason': self.expense.reason,
                'provider_or_recipient': self.expense.provider_or_recipient,
                'payment_method': 'bank_transfer',
                'description': '',
                'observations': '',
                'status': Expense.Status.REGISTERED,
            },
        )

        self.assertEqual(AuditLog.objects.filter(action=AuditLog.Action.EXECUTED, model_name='Gasto').count(), 2)

    def test_delete_audit_logging_is_not_implemented_yet(self):
        # TODO: Add delete audit assertions when delete views create AuditLog records.
        self.assertFalse(AuditLog.objects.filter(action=AuditLog.Action.ANNULLED).exists())

    def test_legacy_model_names_are_displayed_in_spanish(self):
        cases = [
            ('Donation', 'Donación'),
            ('Project', 'Proyecto'),
            ('Institution', 'Institución'),
            ('Fund Allocation', 'Asignación de fondos'),
            ('Expense', 'Gasto'),
        ]

        for index, (legacy_name, expected_name) in enumerate(cases, start=1):
            AuditLog.objects.create(
                action=AuditLog.Action.CREATED,
                model_name=legacy_name,
                entity_id=str(index),
                entity_label=f'Entidad {index}',
                summary='Record updated.',
            )

        response = self.client.get(reverse('audit_log_list'))

        for legacy_name, expected_name in cases:
            with self.subTest(legacy_name=legacy_name):
                self.assertContains(response, expected_name)
                self.assertNotContains(response, f'Creada {legacy_name}')

    def test_legacy_audit_summary_is_displayed_in_spanish(self):
        AuditLog.objects.create(
            action=AuditLog.Action.CREATED,
            model_name='Project',
            entity_id='1',
            entity_label='PRJ-001',
            summary='Project created.',
        )

        response = self.client.get(reverse('audit_log_list'))

        self.assertContains(response, 'Proyecto creado.')
        self.assertNotContains(response, 'Project created.')
