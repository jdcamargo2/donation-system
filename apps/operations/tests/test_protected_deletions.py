from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.operations.models import AuditLog, Expense, FundAllocation
from apps.operations.tests.helpers import (
    create_allocation,
    create_donation,
    create_expense,
    create_institution,
)


class ProtectedDeleteViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username='protected-delete-admin',
            password='pass-12345',
        )
        self.client.force_login(self.user)

    def assert_protected_delete(self, *, url, model, pk, expected_texts):
        """
        PRE: url targets a persisted object protected by at least one related row.
        POST: asserts a safe redirect, human error, preserved data, and no deletion audit.
        """
        audit_count = AuditLog.objects.filter(action=AuditLog.Action.ANNULLED).count()

        response = self.client.post(url, follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(model.objects.filter(pk=pk).exists())
        self.assertEqual(
            AuditLog.objects.filter(action=AuditLog.Action.ANNULLED).count(),
            audit_count,
        )
        for text in expected_texts:
            self.assertContains(response, text)
        self.assertNotContains(response, 'ProtectedError')
        self.assertNotContains(response, 'Traceback')
        self.assertNotContains(response, 'eliminada con éxito')

    def test_allocation_with_multiple_expenses_is_preserved_and_counted(self):
        allocation = create_allocation(amount=Decimal('100.00'))
        expenses = (
            create_expense(allocation=allocation, reason='Primer gasto'),
            create_expense(allocation=allocation, reason='Segundo gasto'),
        )

        self.assert_protected_delete(
            url=reverse('allocation_delete', args=[allocation.pk]),
            model=FundAllocation,
            pk=allocation.pk,
            expected_texts=(allocation.code, '2 gastos asociados', 'registro histórico'),
        )
        self.assertEqual(Expense.objects.filter(pk__in=[item.pk for item in expenses]).count(), 2)

    def test_donation_with_allocation_is_preserved(self):
        donation = create_donation(amount=Decimal('100.00'))
        allocation = create_allocation(donation=donation, amount=Decimal('20.00'))

        self.assert_protected_delete(
            url=reverse('donation_delete', args=[donation.pk]),
            model=type(donation),
            pk=donation.pk,
            expected_texts=(donation.code, '1 asignación asociada'),
        )
        self.assertTrue(FundAllocation.objects.filter(pk=allocation.pk).exists())

    def test_institution_with_donation_is_preserved(self):
        institution = create_institution(name='Institución protegida')
        donation = create_donation(code='DON-PROTECTED', donor=institution)

        self.assert_protected_delete(
            url=reverse('institution_delete', args=[institution.pk]),
            model=type(institution),
            pk=institution.pk,
            expected_texts=('Institución protegida', '1 donación asociada'),
        )
        self.assertTrue(type(donation).objects.filter(pk=donation.pk).exists())

    def test_valid_institution_delete_succeeds_and_creates_exactly_one_audit_event(self):
        institution = create_institution(name='Institución eliminable')

        response = self.client.post(
            reverse('institution_delete', args=[institution.pk]),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(type(institution).objects.filter(pk=institution.pk).exists())
        self.assertContains(response, 'Institución eliminada.')
        self.assertEqual(
            AuditLog.objects.filter(
                action=AuditLog.Action.ANNULLED,
                entity_label='Institución eliminable',
            ).count(),
            1,
        )

    def test_get_never_deletes_institution_and_security_responses_are_preserved(self):
        institution = create_institution(name='Institución segura')

        self.assertEqual(
            self.client.get(reverse('institution_delete', args=[institution.pk])).status_code,
            200,
        )
        self.assertTrue(type(institution).objects.filter(pk=institution.pk).exists())

        limited_user = get_user_model().objects.create_user('no-delete-permission', password='pass-12345')
        self.client.force_login(limited_user)
        self.assertEqual(
            self.client.post(reverse('institution_delete', args=[institution.pk])).status_code,
            403,
        )

        self.client.force_login(self.user)
        self.assertEqual(self.client.post(reverse('institution_delete', args=[999999])).status_code, 404)

    def test_admin_protected_delete_returns_confirmation_instead_of_traceback(self):
        donation = create_donation(amount=Decimal('100.00'))
        create_allocation(donation=donation, amount=Decimal('20.00'))
        delete_url = reverse('admin:operations_donation_delete', args=[donation.pk])

        get_response = self.client.get(delete_url)
        post_response = self.client.post(delete_url, {'post': 'yes'})

        self.assertEqual(get_response.status_code, 200)
        self.assertEqual(post_response.status_code, 200)
        self.assertTrue(type(donation).objects.filter(pk=donation.pk).exists())
        self.assertNotContains(post_response, 'Traceback')
