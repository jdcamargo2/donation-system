from decimal import Decimal
from queue import Queue
from threading import Barrier, Thread
from unittest import skipUnless

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection
from django.db.models import Sum
from django.test import RequestFactory, TestCase, TransactionTestCase
from django.urls import reverse

from apps.operations.admin import DonationAdmin
from apps.operations.forms import DonationForm
from apps.operations.models import (
    AuditLog,
    Donation,
    FundAllocation,
    Institution,
    OperationalCodeSequence,
    Project,
    ZERO_MONEY,
    OPERATIONAL_CODE_PREFIXES,
)
from apps.operations.services import create_donation, create_fund_allocation, update_donation
from apps.operations.tests.helpers import (
    TEST_DATE,
    create_allocation,
    create_donation as create_donation_fixture,
    create_institution,
    create_project,
    create_user,
)


User = get_user_model()
POSTGRESQL_LOCKING_REQUIRED = 'Requires PostgreSQL row-level locking'
THREAD_TIMEOUT_SECONDS = 15
BARRIER_TIMEOUT_SECONDS = 10


def create_staff_user(username, *permission_codenames, is_superuser=False):
    if is_superuser:
        return User.objects.create_superuser(username=username, password='pass-12345')
    user = User.objects.create_user(username=username, password='pass-12345')
    user.is_staff = True
    user.save(update_fields=('is_staff',))
    permissions = Permission.objects.filter(
        content_type__app_label='operations',
        codename__in=permission_codenames,
    )
    user.user_permissions.add(*permissions)
    return user


class DonationAmountInvariantTests(TestCase):
    def setUp(self):
        self.actor = create_user(username='donation-integrity-actor')
        self.donor = create_institution(name='Donante activo P1A')
        self.project = create_project(code='PRJ-P1A-AMT', name='Proyecto P1A amount')

    def _create_via_service(self, *, amount=Decimal('10000.00'), donor=None, **extra):
        return create_donation(
            actor=self.actor,
            donor=donor or self.donor,
            donation_type='money',
            amount=amount,
            objective='Objetivo P1A',
            **extra,
        )

    def test_create_positive_donation_succeeds(self):
        donation = self._create_via_service(amount=Decimal('100.00'))
        self.assertEqual(donation.amount, Decimal('100.00'))
        self.assertEqual(donation.status, Donation.Status.REGISTERED)
        self.assertEqual(donation.currency, 'USD')

    def test_zero_and_negative_amount_rejected(self):
        for amount in (Decimal('0.00'), Decimal('-1.00')):
            with self.subTest(amount=amount):
                with self.assertRaises(ValidationError) as ctx:
                    self._create_via_service(amount=amount)
                self.assertIn('amount', ctx.exception.message_dict)

    def test_update_above_equal_and_below_allocated_total(self):
        donation = self._create_via_service(amount=Decimal('10000.00'))
        donation.status = Donation.Status.RECEIVED
        donation.received_date = TEST_DATE
        donation.save(update_fields=('status', 'received_date', 'updated_at'))
        create_allocation(
            donation=donation,
            project=self.project,
            amount=Decimal('8000.00'),
            status=FundAllocation.Status.ACTIVE,
        )

        above = update_donation(
            actor=self.actor,
            donation=donation,
            donor=self.donor,
            donation_type='money',
            amount=Decimal('9000.00'),
            objective=donation.objective,
            received_date=TEST_DATE,
        )
        self.assertEqual(above.amount, Decimal('9000.00'))

        equal = update_donation(
            actor=self.actor,
            donation=above,
            donor=self.donor,
            donation_type='money',
            amount=Decimal('8000.00'),
            objective=donation.objective,
            received_date=TEST_DATE,
        )
        self.assertEqual(equal.amount, Decimal('8000.00'))

        snapshot = {
            'amount': equal.amount,
            'donor_id': equal.donor_id,
            'objective': equal.objective,
        }
        with self.assertRaises(ValidationError) as ctx:
            update_donation(
                actor=self.actor,
                donation=equal,
                donor=self.donor,
                donation_type='money',
                amount=Decimal('7000.00'),
                objective=donation.objective,
                received_date=TEST_DATE,
            )
        self.assertIn('amount', ctx.exception.message_dict)
        self.assertIn('8000.00', str(ctx.exception.message_dict['amount'][0]))
        equal.refresh_from_db()
        self.assertEqual(equal.amount, snapshot['amount'])
        self.assertEqual(equal.donor_id, snapshot['donor_id'])
        self.assertEqual(equal.objective, snapshot['objective'])

    def test_active_and_finished_count_annulled_do_not(self):
        donation = self._create_via_service(amount=Decimal('10000.00'))
        donation.status = Donation.Status.RECEIVED
        donation.received_date = TEST_DATE
        donation.save(update_fields=('status', 'received_date', 'updated_at'))
        create_allocation(
            donation=donation,
            project=self.project,
            amount=Decimal('3000.00'),
            status=FundAllocation.Status.ACTIVE,
        )
        create_allocation(
            donation=donation,
            project=create_project(code='PRJ-P1A-FIN', name='Finished P1A'),
            amount=Decimal('2000.00'),
            status=FundAllocation.Status.FINISHED,
        )
        create_allocation(
            donation=donation,
            project=create_project(code='PRJ-P1A-ANN', name='Annulled P1A'),
            amount=Decimal('4000.00'),
            status=FundAllocation.Status.ANNULLED,
        )

        updated = update_donation(
            actor=self.actor,
            donation=donation,
            donor=self.donor,
            donation_type='money',
            amount=Decimal('5000.00'),
            objective=donation.objective,
            received_date=TEST_DATE,
        )
        self.assertEqual(updated.amount, Decimal('5000.00'))

        with self.assertRaises(ValidationError) as ctx:
            update_donation(
                actor=self.actor,
                donation=updated,
                donor=self.donor,
                donation_type='money',
                amount=Decimal('4999.99'),
                objective=donation.objective,
                received_date=TEST_DATE,
            )
        self.assertIn('5000.00', str(ctx.exception.message_dict['amount'][0]))


class DonationInstitutionEligibilityTests(TestCase):
    def setUp(self):
        self.actor = create_user(username='donation-inst-actor')
        self.active = create_institution(name='Institución activa P1A')
        self.inactive = create_institution(name='Institución inactiva P1A')
        self.inactive.status = Institution.Status.INACTIVE
        self.inactive.save(update_fields=('status', 'updated_at'))
        self.other_inactive = create_institution(name='Otra inactiva P1A')
        self.other_inactive.status = Institution.Status.INACTIVE
        self.other_inactive.save(update_fields=('status', 'updated_at'))

    def test_create_form_queryset_excludes_inactive(self):
        form = DonationForm()
        donor_ids = set(form.fields['donor'].queryset.values_list('pk', flat=True))
        self.assertIn(self.active.pk, donor_ids)
        self.assertNotIn(self.inactive.pk, donor_ids)

    def test_crafted_create_with_inactive_donor_rejected(self):
        with self.assertRaises(ValidationError) as ctx:
            create_donation(
                actor=self.actor,
                donor=self.inactive,
                donation_type='money',
                amount=Decimal('50.00'),
                objective='Intento inactivo',
            )
        self.assertIn('donor', ctx.exception.message_dict)

        form = DonationForm(
            data={
                'donor': self.inactive.pk,
                'donation_type': 'money',
                'amount': '50.00',
                'objective': 'Intento inactivo',
                'restrictions': '',
                'commitment_date': '',
                'received_date': '',
                'support_reference': '',
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn('donor', form.errors)

    def test_historical_inactive_donor_editable_but_not_replaceable(self):
        donation = create_donation_fixture(
            code='DON-P1A-HIST',
            donor=self.inactive,
            amount=Decimal('200.00'),
            status=Donation.Status.RECEIVED,
        )
        form = DonationForm(instance=donation)
        donor_ids = set(form.fields['donor'].queryset.values_list('pk', flat=True))
        self.assertIn(self.inactive.pk, donor_ids)
        self.assertIn(self.active.pk, donor_ids)
        self.assertNotIn(self.other_inactive.pk, donor_ids)

        kept = update_donation(
            actor=self.actor,
            donation=donation,
            donor=self.inactive,
            donation_type=donation.donation_type,
            amount=donation.amount,
            objective='Histórica actualizada',
            received_date=TEST_DATE,
        )
        self.assertEqual(kept.donor_id, self.inactive.pk)
        self.assertEqual(kept.objective, 'Histórica actualizada')

        with self.assertRaises(ValidationError) as ctx:
            update_donation(
                actor=self.actor,
                donation=kept,
                donor=self.other_inactive,
                donation_type=kept.donation_type,
                amount=kept.amount,
                objective=kept.objective,
                received_date=TEST_DATE,
            )
        self.assertIn('donor', ctx.exception.message_dict)

        switched = update_donation(
            actor=self.actor,
            donation=kept,
            donor=self.active,
            donation_type=kept.donation_type,
            amount=kept.amount,
            objective=kept.objective,
            received_date=TEST_DATE,
        )
        self.assertEqual(switched.donor_id, self.active.pk)

    def test_historical_inactive_donation_remains_visible(self):
        donation = create_donation_fixture(
            code='DON-P1A-VISIBLE',
            donor=self.inactive,
            amount=Decimal('75.00'),
        )
        self.assertTrue(Donation.objects.filter(pk=donation.pk, donor=self.inactive).exists())
        self.client.force_login(self.actor)
        response = self.client.get(reverse('donation_detail', args=[donation.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.inactive.name)


class DonationAuditAndViewTests(TestCase):
    def setUp(self):
        self.actor = create_user(username='donation-audit-actor')
        self.donor = create_institution(name='Donante auditoría P1A')
        self.client.force_login(self.actor)

    def _post_data(self, **overrides):
        data = {
            'donor': self.donor.pk,
            'donation_type': 'money',
            'amount': '100.00',
            'objective': 'Auditoría P1A',
            'restrictions': '',
            'commitment_date': '',
            'received_date': TEST_DATE,
            'support_reference': '',
        }
        data.update(overrides)
        return data

    def test_successful_create_and_update_log_once_each(self):
        before = AuditLog.objects.count()
        response = self.client.post(reverse('donation_create'), self._post_data())
        self.assertEqual(response.status_code, 302)
        donation = Donation.objects.get(objective='Auditoría P1A')
        create_logs = AuditLog.objects.filter(
            entity_id=str(donation.pk),
            action=AuditLog.Action.CREATED,
            summary='Donación creada.',
        )
        self.assertEqual(create_logs.count(), 1)
        self.assertEqual(AuditLog.objects.count(), before + 1)

        before_update = AuditLog.objects.count()
        response = self.client.post(
            reverse('donation_update', args=[donation.pk]),
            self._post_data(amount='150.00', objective='Auditoría P1A editada'),
        )
        self.assertEqual(response.status_code, 302)
        update_logs = AuditLog.objects.filter(
            entity_id=str(donation.pk),
            action=AuditLog.Action.UPDATED,
        )
        self.assertEqual(update_logs.count(), 1)
        self.assertEqual(AuditLog.objects.count(), before_update + 1)
        self.assertIn('previous_amount=100.00', update_logs.get().summary)
        self.assertIn('new_amount=150.00', update_logs.get().summary)

    def test_rejected_update_does_not_create_success_audit(self):
        donation = create_donation_fixture(
            code='DON-P1A-REJ',
            donor=self.donor,
            amount=Decimal('100.00'),
            status=Donation.Status.RECEIVED,
        )
        create_allocation(
            donation=donation,
            project=create_project(code='PRJ-P1A-REJ'),
            amount=Decimal('80.00'),
        )
        before = AuditLog.objects.count()
        response = self.client.post(
            reverse('donation_update', args=[donation.pk]),
            self._post_data(amount='50.00', objective=donation.objective or 'Obj'),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(AuditLog.objects.count(), before)
        donation.refresh_from_db()
        self.assertEqual(donation.amount, Decimal('100.00'))

    def test_crafted_form_post_below_allocated_is_protected(self):
        donation = create_donation_fixture(
            code='DON-P1A-POST',
            donor=self.donor,
            amount=Decimal('10000.00'),
            status=Donation.Status.RECEIVED,
        )
        create_allocation(
            donation=donation,
            project=create_project(code='PRJ-P1A-POST'),
            amount=Decimal('8000.00'),
        )
        response = self.client.post(
            reverse('donation_update', args=[donation.pk]),
            self._post_data(amount='7000.00', objective='Obj post'),
        )
        self.assertEqual(response.status_code, 200)
        self.assertFormError(
            response.context['form'],
            'amount',
            'El importe de la donación no puede ser inferior al total ya asignado (8000.00 USD).',
        )
        donation.refresh_from_db()
        self.assertEqual(donation.amount, Decimal('10000.00'))


class DonationAdminHardeningTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.model_admin = DonationAdmin(Donation, admin.site)
        self.donation = create_donation_fixture(
            code='DON-P1A-ADMIN',
            amount=Decimal('500.00'),
        )
        self.superuser = create_staff_user('donation-admin-super', is_superuser=True)
        self.staff = create_staff_user(
            'donation-admin-staff',
            'view_donation',
            'add_donation',
            'change_donation',
            'delete_donation',
        )

    def request_for(self, user, path='/admin/operations/donation/'):
        request = self.factory.get(path)
        request.user = user
        return request

    def assert_mutation_permissions_denied(self, user):
        request = self.request_for(user)
        self.assertFalse(self.model_admin.has_add_permission(request))
        self.assertFalse(self.model_admin.has_change_permission(request, self.donation))
        self.assertFalse(self.model_admin.has_delete_permission(request, self.donation))
        self.assertNotIn('delete_selected', self.model_admin.get_actions(request))

    def test_superuser_and_staff_cannot_mutate_via_admin(self):
        self.assert_mutation_permissions_denied(self.superuser)
        self.assert_mutation_permissions_denied(self.staff)

    def test_list_and_detail_remain_readable(self):
        self.client.force_login(self.superuser)
        changelist = reverse('admin:operations_donation_changelist')
        detail = reverse('admin:operations_donation_change', args=[self.donation.pk])
        list_response = self.client.get(changelist)
        detail_response = self.client.get(detail)
        self.assertEqual(list_response.status_code, 200)
        self.assertContains(list_response, self.donation.code)
        self.assertEqual(detail_response.status_code, 200)
        self.assertContains(detail_response, self.donation.code)
        self.assertNotContains(detail_response, 'name="_save"')
        self.assertNotContains(detail_response, 'name="amount"')
        self.assertNotContains(detail_response, 'name="status"')

    def test_status_remains_readonly_in_admin_contract(self):
        request = self.request_for(self.superuser)
        self.assertIn('status', self.model_admin.get_readonly_fields(request, self.donation))
        self.assertIn('code', self.model_admin.get_readonly_fields(request, self.donation))
        self.assertIn('currency', self.model_admin.get_readonly_fields(request, self.donation))


@skipUnless(connection.vendor == 'postgresql', POSTGRESQL_LOCKING_REQUIRED)
class DonationIntegrityConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        OperationalCodeSequence.objects.bulk_create(
            [
                OperationalCodeSequence(
                    namespace=namespace,
                    prefix=prefix,
                    next_value=1,
                )
                for namespace, prefix in OPERATIONAL_CODE_PREFIXES.items()
            ],
            ignore_conflicts=True,
        )
        self.actor = create_user(username='donation-lock-actor')
        self.donor = create_institution(name='Donante concurrency P1A')
        self.project_a = create_project(code='PRJ-P1A-LOCK-A', name='Lock A')
        self.project_b = create_project(code='PRJ-P1A-LOCK-B', name='Lock B')

    def run_concurrently(self, operations):
        barrier = Barrier(len(operations), timeout=BARRIER_TIMEOUT_SECONDS)
        results = Queue()

        def runner(operation):
            close_old_connections()
            try:
                barrier.wait()
                value = operation()
                results.put(('success', value))
            except Exception as exc:
                results.put(('error', exc))
            finally:
                close_old_connections()

        threads = [Thread(target=runner, args=(operation,)) for operation in operations]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=THREAD_TIMEOUT_SECONDS)
            self.assertFalse(thread.is_alive())
        return [results.get() for _ in operations]

    def test_concurrent_reduction_and_allocation_cannot_violate_invariant(self):
        donation = create_donation_fixture(
            code='DON-P1A-LOCK',
            donor=self.donor,
            amount=Decimal('10000.00'),
            status=Donation.Status.RECEIVED,
        )
        create_allocation(
            donation=donation,
            project=self.project_a,
            amount=Decimal('8000.00'),
        )
        donation_id = donation.pk
        actor_id = self.actor.pk
        donor_id = self.donor.pk
        project_b_id = self.project_b.pk

        def reduce_amount():
            return update_donation(
                actor=User.objects.get(pk=actor_id),
                donation=Donation.objects.get(pk=donation_id),
                donor=Institution.objects.get(pk=donor_id),
                donation_type='goods',
                amount=Decimal('7000.00'),
                objective='Reduce under lock',
                received_date=TEST_DATE,
            )

        def allocate_more():
            return create_fund_allocation(
                donation=Donation.objects.get(pk=donation_id),
                project=Project.objects.get(pk=project_b_id),
                budget_category='health_psychosocial',
                amount=Decimal('1500.00'),
                responsible_person='',
                allocation_date=TEST_DATE,
                status=FundAllocation.Status.ACTIVE,
                notes='',
            )

        results = self.run_concurrently([reduce_amount, allocate_more])
        outcomes = [outcome for outcome, _value in results]
        self.assertEqual(outcomes.count('success'), 1)
        self.assertEqual(outcomes.count('error'), 1)
        error = next(value for outcome, value in results if outcome == 'error')
        self.assertIsInstance(error, ValidationError)

        donation.refresh_from_db()
        assigned = (
            donation.allocations.exclude(status=FundAllocation.Status.ANNULLED)
            .aggregate(total=Sum('amount'))['total']
            or ZERO_MONEY
        )
        self.assertLessEqual(assigned, donation.amount)

    def test_update_donation_equal_and_above_succeed_under_lock(self):
        donation = create_donation_fixture(
            code='DON-P1A-EQ',
            donor=self.donor,
            amount=Decimal('10000.00'),
            status=Donation.Status.RECEIVED,
        )
        create_allocation(
            donation=donation,
            project=self.project_a,
            amount=Decimal('8000.00'),
        )
        equal = update_donation(
            actor=self.actor,
            donation=donation,
            donor=self.donor,
            donation_type='goods',
            amount=Decimal('8000.00'),
            objective='Equal ok',
            received_date=TEST_DATE,
        )
        self.assertEqual(equal.amount, Decimal('8000.00'))
        above = update_donation(
            actor=self.actor,
            donation=equal,
            donor=self.donor,
            donation_type='goods',
            amount=Decimal('8500.00'),
            objective='Above ok',
            received_date=TEST_DATE,
        )
        self.assertEqual(above.amount, Decimal('8500.00'))
