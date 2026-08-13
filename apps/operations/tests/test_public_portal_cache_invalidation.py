"""Focused public portal cache invalidation after financial mutations (BUG-E2E-005)."""

from __future__ import annotations

import tempfile
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.operations.expense_request_services import (
    approve_expense_request,
    create_expense_request,
    fulfill_expense_request,
)
from apps.operations.models import Donation, Project
from apps.operations.public_portal_cache import (
    invalidate_public_portal_cache,
    schedule_public_portal_cache_invalidation,
)
from apps.operations.role_services import sync_operation_roles
from apps.operations.roles import ROLE_FIELD_OPERATOR, ROLE_PROJECT_COMMITTEE, ROLE_SIGEDON_ADMIN
from apps.operations.services import (
    InvalidStateTransitionError,
    annul_donation,
    annul_expense,
    annul_fund_allocation,
    create_expense_legacy,
    create_fund_allocation,
    publish_project,
    transition_donation_status,
    update_expense,
    update_fund_allocation,
)
from apps.operations.tests.helpers import (
    TEST_DATE,
    create_allocation,
    create_donation,
    create_institution,
    create_project,
    create_user,
)
from apps.public_portal.selectors import get_public_transparency_summary


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class PublicPortalFinancialCacheInvalidationTests(TestCase):
    def setUp(self):
        cache.clear()
        sync_operation_roles()
        self.actor = create_user('portal-cache-actor')
        self.admin = get_user_model().objects.create_user(
            username='portal-cache-admin',
            password='pass-12345',
        )
        self.admin.groups.add(Group.objects.get(name=ROLE_SIGEDON_ADMIN))
        self.operator = get_user_model().objects.create_user(
            username='portal-cache-op',
            password='pass-12345',
        )
        self.operator.groups.add(Group.objects.get(name=ROLE_FIELD_OPERATOR))
        self.committee = get_user_model().objects.create_user(
            username='portal-cache-committee',
            password='pass-12345',
        )
        self.committee.groups.add(Group.objects.get(name=ROLE_PROJECT_COMMITTEE))

        self.institution = create_institution()
        self.public_project = create_project(code='PRJ-CACHE-PUB', name='Público cache')
        self.public_project.status = Project.Status.ACTIVE
        self.public_project.is_public = True
        self.public_project.save(update_fields=['status', 'is_public'])
        self.donation = create_donation(
            code='DON-CACHE-001',
            donor=self.institution,
            amount=Decimal('1000.00'),
            status=Donation.Status.REGISTERED,
        )
        self.donation.received_date = TEST_DATE
        self.donation.objective = 'Donación de prueba para invalidación de caché pública.'
        self.donation.donation_type = 'money'
        self.donation.save(
            update_fields=['received_date', 'objective', 'donation_type', 'updated_at']
        )

    def _support(self, name='soporte-cache.pdf'):
        return SimpleUploadedFile(name, b'%PDF-1.4 soporte cache')

    def test_successful_donation_received_invalidates_after_commit(self):
        with patch(
            'apps.operations.services.schedule_public_portal_cache_invalidation',
            wraps=schedule_public_portal_cache_invalidation,
        ) as schedule_mock:
            with patch(
                'apps.operations.public_portal_cache.invalidate_public_portal_cache',
            ) as invalidate_mock:
                with self.captureOnCommitCallbacks(execute=True):
                    transition_donation_status(
                        self.donation.pk,
                        actor=self.actor,
                        target_status=Donation.Status.RECEIVED,
                    )
                schedule_mock.assert_called_once()
                invalidate_mock.assert_called_once()

    def test_rolled_back_donation_mutation_does_not_invalidate(self):
        with patch(
            'apps.operations.public_portal_cache.invalidate_public_portal_cache',
        ) as invalidate_mock:
            with self.captureOnCommitCallbacks(execute=True):
                with self.assertRaises(RuntimeError):
                    with transaction.atomic():
                        transition_donation_status(
                            self.donation.pk,
                            actor=self.actor,
                            target_status=Donation.Status.RECEIVED,
                        )
                        raise RuntimeError('force rollback')
            invalidate_mock.assert_not_called()
            self.donation.refresh_from_db()
            self.assertEqual(self.donation.status, Donation.Status.REGISTERED)

    def test_allocation_mutation_invalidates(self):
        received = transition_donation_status(
            self.donation.pk,
            actor=self.actor,
            target_status=Donation.Status.RECEIVED,
        )
        with patch(
            'apps.operations.services.schedule_public_portal_cache_invalidation',
        ) as schedule_mock:
            with self.captureOnCommitCallbacks(execute=True):
                create_fund_allocation(
                    donation=received,
                    project=self.public_project,
                    budget_category='health_psychosocial',
                    amount=Decimal('200.00'),
                    responsible_person='Cache tester',
                    allocation_date=TEST_DATE,
                    status='active',
                    notes='',
                )
            schedule_mock.assert_called_once()

    def test_expense_mutation_invalidates(self):
        received = transition_donation_status(
            self.donation.pk,
            actor=self.actor,
            target_status=Donation.Status.RECEIVED,
        )
        allocation = create_fund_allocation(
            donation=received,
            project=self.public_project,
            budget_category='health_psychosocial',
            amount=Decimal('300.00'),
            responsible_person='Cache tester',
            allocation_date=TEST_DATE,
            status='active',
            notes='',
        )
        with patch(
            'apps.operations.services.schedule_public_portal_cache_invalidation',
        ) as schedule_mock:
            with self.captureOnCommitCallbacks(execute=True):
                expense = create_expense_legacy(
                    allocation=allocation,
                    expense_date=TEST_DATE,
                    category='food',
                    amount=Decimal('40.00'),
                    reason='Gasto cache público',
                    provider_or_recipient='Proveedor',
                    payment_method='bank_transfer',
                    description='',
                    observations='',
                    actor=self.actor,
                    support_title='Factura',
                    support_file=self._support(),
                )
            schedule_mock.assert_called_once()
            schedule_mock.reset_mock()
            with self.captureOnCommitCallbacks(execute=True):
                update_expense(
                    expense=expense,
                    allocation=allocation,
                    expense_date=TEST_DATE,
                    category='food',
                    amount=Decimal('45.00'),
                    reason='Gasto cache público corregido',
                    provider_or_recipient='Proveedor',
                    payment_method='bank_transfer',
                    description='',
                    observations='',
                    actor=self.actor,
                )
            schedule_mock.assert_called_once()
            schedule_mock.reset_mock()
            with self.captureOnCommitCallbacks(execute=True):
                annul_expense(
                    expense.pk,
                    actor=self.actor,
                    reason='Anulación controlada de gasto de prueba.',
                )
            schedule_mock.assert_called_once()

    def test_expense_request_fulfillment_invalidates_through_expense_path(self):
        received = transition_donation_status(
            self.donation.pk,
            actor=self.actor,
            target_status=Donation.Status.RECEIVED,
        )
        allocation = create_fund_allocation(
            donation=received,
            project=self.public_project,
            budget_category='health_psychosocial',
            amount=Decimal('400.00'),
            responsible_person='Cache tester',
            allocation_date=TEST_DATE,
            status='active',
            notes='',
        )
        request = create_expense_request(
            fund_allocation=allocation,
            requested_amount=Decimal('50.00'),
            purpose='Solicitud para invalidar cache del portal',
            requested_date=TEST_DATE,
            actor=self.operator,
        )
        approved = approve_expense_request(request, actor=self.committee)
        with patch(
            'apps.operations.services.schedule_public_portal_cache_invalidation',
        ) as schedule_mock:
            with self.captureOnCommitCallbacks(execute=True):
                fulfill_expense_request(
                    approved,
                    expense_date=TEST_DATE,
                    amount=Decimal('50.00'),
                    reason='Pago final que afecta métricas públicas',
                    provider_or_recipient='Proveedor final',
                    payment_method='bank_transfer',
                    description='Cumplimiento',
                    support_file=self._support('fulfill-cache.pdf'),
                    support_title='Factura final',
                    category='materials',
                    actor=self.admin,
                )
            schedule_mock.assert_called_once()

    def test_project_publication_invalidation_still_works(self):
        private = create_project(code='PRJ-CACHE-PRIV', name='Privado a publicar')
        private.status = Project.Status.ACTIVE
        private.is_public = False
        private.save(update_fields=['status', 'is_public'])
        with patch(
            'apps.operations.services.schedule_public_portal_cache_invalidation',
        ) as schedule_mock:
            with self.captureOnCommitCallbacks(execute=True):
                publish_project(project_id=private.pk, actor=self.actor)
            schedule_mock.assert_called_once()

    def test_allocation_amount_and_annul_invalidate(self):
        received = transition_donation_status(
            self.donation.pk,
            actor=self.actor,
            target_status=Donation.Status.RECEIVED,
        )
        allocation = create_fund_allocation(
            donation=received,
            project=self.public_project,
            budget_category='health_psychosocial',
            amount=Decimal('250.00'),
            responsible_person='Cache tester',
            allocation_date=TEST_DATE,
            status='active',
            notes='',
        )
        with patch(
            'apps.operations.services.schedule_public_portal_cache_invalidation',
        ) as schedule_mock:
            with self.captureOnCommitCallbacks(execute=True):
                update_fund_allocation(
                    allocation=allocation,
                    donation=received,
                    project=self.public_project,
                    budget_category='health_psychosocial',
                    amount=Decimal('275.00'),
                    responsible_person='Cache tester',
                    allocation_date=TEST_DATE,
                    status='active',
                    notes='Monto ajustado',
                )
            schedule_mock.assert_called_once()
            schedule_mock.reset_mock()
            with self.captureOnCommitCallbacks(execute=True):
                annul_fund_allocation(
                    allocation.pk,
                    actor=self.actor,
                    reason='Anulación controlada de asignación de prueba.',
                )
            schedule_mock.assert_called_once()

    def test_annul_donation_invalidates(self):
        received = transition_donation_status(
            self.donation.pk,
            actor=self.actor,
            target_status=Donation.Status.RECEIVED,
        )
        with patch(
            'apps.operations.services.schedule_public_portal_cache_invalidation',
        ) as schedule_mock:
            with self.captureOnCommitCallbacks(execute=True):
                annul_donation(
                    received.pk,
                    actor=self.actor,
                    reason='Anulación controlada de donación de prueba.',
                )
            schedule_mock.assert_called_once()

    def test_rejected_donation_transition_does_not_invalidate(self):
        with patch(
            'apps.operations.services.schedule_public_portal_cache_invalidation',
        ) as schedule_mock:
            with self.assertRaises(InvalidStateTransitionError):
                transition_donation_status(
                    self.donation.pk,
                    actor=self.actor,
                    target_status=Donation.Status.ANNULLED,
                )
            schedule_mock.assert_not_called()

    def test_cache_refresh_yields_updated_public_metric(self):
        cache.set('public-home-probe', 'stale')
        home_url = reverse('public_portal:public_home')
        first = self.client.get(home_url)
        self.assertEqual(first.status_code, 200)
        before = get_public_transparency_summary()['linked_received_donations_total']

        with self.captureOnCommitCallbacks(execute=True):
            received = transition_donation_status(
                self.donation.pk,
                actor=self.actor,
                target_status=Donation.Status.RECEIVED,
            )
            create_fund_allocation(
                donation=received,
                project=self.public_project,
                budget_category='health_psychosocial',
                amount=Decimal('150.00'),
                responsible_person='Cache tester',
                allocation_date=TEST_DATE,
                status='active',
                notes='',
            )

        self.assertIsNone(cache.get('public-home-probe'))
        after = get_public_transparency_summary()['linked_received_donations_total']
        self.assertEqual(before, Decimal('0.00'))
        self.assertEqual(after, Decimal('1000.00'))
        second = self.client.get(home_url)
        self.assertContains(second, '1.000,00')


class SchedulePublicPortalCacheHelperTests(TestCase):
    def test_schedule_registers_invalidate_on_commit_once(self):
        with patch(
            'apps.operations.public_portal_cache.invalidate_public_portal_cache',
            wraps=invalidate_public_portal_cache,
        ) as invalidate_mock:
            with self.captureOnCommitCallbacks(execute=True):
                with transaction.atomic():
                    schedule_public_portal_cache_invalidation()
            invalidate_mock.assert_called_once()
