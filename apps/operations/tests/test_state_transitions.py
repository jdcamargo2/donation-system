from datetime import date
from decimal import Decimal

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import RequestFactory, TestCase
from django.urls import reverse

from apps.operations.admin import DonationAdmin, FundAllocationAdmin, ProjectAdmin
from apps.operations.forms import DonationForm, FundAllocationForm, ProjectForm
from apps.operations.models import AuditLog, Donation, FundAllocation, Project
from apps.operations.services import (
    DONATION_STATUS_TRANSITIONS,
    FUND_ALLOCATION_STATUS_TRANSITIONS,
    PROJECT_STATUS_TRANSITIONS,
    InvalidStateTransitionError,
    transition_donation_status,
    transition_fund_allocation_status,
    transition_project_status,
    validate_state_transition,
    finish_project,
)
from apps.operations.tests.helpers import TEST_DATE, create_donation, create_institution, create_project, create_user


class StateTransitionServiceTests(TestCase):
    def setUp(self):
        self.actor = create_user()
        self.counter = 0

    def next_counter(self):
        self.counter += 1
        return self.counter

    def create_donation(self, status):
        number = self.next_counter()
        donation = create_donation(
            code=f'DON-STATE-{number}', status=status, amount=Decimal('100.00')
        )
        donation.received_date = TEST_DATE
        donation.objective = 'Transición de prueba'
        donation.save(update_fields=('received_date', 'objective'))
        return donation

    def create_project(self, status):
        number = self.next_counter()
        return Project.objects.create(
            code=f'PRJ-STATE-{number}',
            name=f'Proyecto {number}',
            estimated_budget=Decimal('100.00'),
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
            status=status,
        )

    def create_allocation(self, status):
        number = self.next_counter()
        return FundAllocation.objects.create(
            donation=create_donation(code=f'DON-STATE-ALLOC-{number}'),
            project=create_project(code=f'PRJ-STATE-ALLOC-{number}'),
            budget_category='health_psychosocial',
            amount=Decimal('20.00'),
            allocation_date=TEST_DATE,
            status=status,
        )

    def assert_transition_matrix(self, *, transitions, statuses, factory, service):
        """
        PRE: transitions is complete and factory/service create and transition one model.
        POST: every allowed pair succeeds and every other pair fails without an audit event.
        """
        for current_status in statuses:
            for target_status in statuses:
                instance = factory(current_status)
                audit_count = AuditLog.objects.count()
                is_generic_target = target_status not in {
                    Project.Status.CLOSED,
                    Project.Status.ANNULLED,
                }
                if target_status in transitions[current_status] and is_generic_target:
                    transitioned = service(instance.pk, actor=self.actor, target_status=target_status)
                    self.assertEqual(transitioned.status, target_status)
                    self.assertEqual(AuditLog.objects.count(), audit_count + 1)
                else:
                    with self.assertRaises(InvalidStateTransitionError):
                        service(instance.pk, actor=self.actor, target_status=target_status)
                    instance.refresh_from_db()
                    self.assertEqual(instance.status, current_status)
                    self.assertEqual(AuditLog.objects.count(), audit_count)

    def test_donation_transition_matrix(self):
        self.assert_transition_matrix(
            transitions=DONATION_STATUS_TRANSITIONS,
            statuses=Donation.Status.values,
            factory=self.create_donation,
            service=transition_donation_status,
        )

    def test_project_transition_matrix(self):
        self.assert_transition_matrix(
            transitions=PROJECT_STATUS_TRANSITIONS,
            statuses=Project.Status.values,
            factory=self.create_project,
            service=transition_project_status,
        )

    def test_allocation_transition_matrix(self):
        self.assert_transition_matrix(
            transitions=FUND_ALLOCATION_STATUS_TRANSITIONS,
            statuses=FundAllocation.Status.values,
            factory=self.create_allocation,
            service=transition_fund_allocation_status,
        )

    def test_pure_validator_rejects_same_and_unknown_states(self):
        with self.assertRaises(InvalidStateTransitionError):
            validate_state_transition(
                current_status=Project.Status.ACTIVE,
                target_status=Project.Status.ACTIVE,
                allowed_transitions=PROJECT_STATUS_TRANSITIONS,
            )
        with self.assertRaises(InvalidStateTransitionError):
            validate_state_transition(
                current_status=Project.Status.ACTIVE,
                target_status='unknown',
                allowed_transitions=PROJECT_STATUS_TRANSITIONS,
            )

    def test_donation_requires_received_date(self):
        donation = self.create_donation(Donation.Status.REGISTERED)
        donation.received_date = None
        donation.save(update_fields=('received_date',))

        with self.assertRaises(InvalidStateTransitionError):
            transition_donation_status(
                donation.pk, actor=self.actor, target_status=Donation.Status.RECEIVED
            )

        donation.refresh_from_db()
        self.assertEqual(donation.status, Donation.Status.REGISTERED)

    def test_transition_requires_authenticated_actor(self):
        project = self.create_project(Project.Status.PLANNED)

        with self.assertRaises(InvalidStateTransitionError):
            transition_project_status(
                project.pk, actor=None, target_status=Project.Status.ACTIVE
            )

    def test_project_with_incoherent_dates_cannot_close(self):
        project = self.create_project(Project.Status.ACTIVE)
        Project.objects.filter(pk=project.pk).update(
            start_date=date(2026, 12, 31), end_date=date(2026, 1, 1)
        )

        with self.assertRaises(InvalidStateTransitionError):
            finish_project(project.pk, actor=self.actor)

        project.refresh_from_db()
        self.assertEqual(project.status, Project.Status.ACTIVE)

    def test_invalid_allocation_cannot_activate(self):
        allocation = self.create_allocation(FundAllocation.Status.CREATED)
        FundAllocation.objects.create(
            donation=allocation.donation,
            project=allocation.project,
            budget_category='food_security',
            amount=Decimal('90.00'),
            allocation_date=TEST_DATE,
        )

        with self.assertRaises(ValidationError):
            transition_fund_allocation_status(
                allocation.pk,
                actor=self.actor,
                target_status=FundAllocation.Status.ACTIVE,
            )

        allocation.refresh_from_db()
        self.assertEqual(allocation.status, FundAllocation.Status.CREATED)

    def test_service_uses_persisted_state_and_second_attempt_does_not_duplicate_audit(self):
        donation = self.create_donation(Donation.Status.REGISTERED)
        Donation.objects.filter(pk=donation.pk).update(status=Donation.Status.COMMITTED)

        transitioned = transition_donation_status(
            donation.pk, actor=self.actor, target_status=Donation.Status.RECEIVED
        )
        with self.assertRaises(InvalidStateTransitionError):
            transition_donation_status(
                donation.pk, actor=self.actor, target_status=Donation.Status.RECEIVED
            )

        self.assertEqual(transitioned.status, Donation.Status.RECEIVED)
        log = AuditLog.objects.get(entity_id=str(donation.pk))
        self.assertIn(Donation.Status.COMMITTED, log.summary)
        self.assertIn(Donation.Status.RECEIVED, log.summary)


class StateTransitionBoundaryTests(TestCase):
    def setUp(self):
        self.user = create_user()
        self.project = create_project()
        self.donor = create_institution()
        self.donation = create_donation(donor=self.donor)
        self.allocation = FundAllocation.objects.create(
            donation=self.donation,
            project=self.project,
            budget_category='health_psychosocial',
            amount=Decimal('20.00'),
            allocation_date=TEST_DATE,
        )

    def test_ordinary_forms_exclude_and_ignore_status(self):
        self.assertNotIn('status', ProjectForm().fields)
        self.assertNotIn('status', DonationForm().fields)
        self.assertNotIn('status', FundAllocationForm().fields)

        form = ProjectForm(
            instance=self.project,
            data={
                'name': self.project.name,
                'description': '',
                'objective': '',
                'responsible_unit': '',
                'location': '',
                'estimated_budget': '1000.00',
                'start_date': '',
                'end_date': '',
                'status': Project.Status.ACTIVE,
            },
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().status, Project.Status.PLANNED)

    def test_transition_route_is_post_only_and_requires_permission(self):
        url = reverse(
            'project_status_transition', args=(self.project.pk, Project.Status.ACTIVE)
        )
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(url).status_code, 405)

        limited = get_user_model().objects.create_user(username='limited')
        self.client.force_login(limited)
        self.assertEqual(self.client.post(url).status_code, 403)
        self.project.refresh_from_db()
        self.assertEqual(self.project.status, Project.Status.PLANNED)

    def test_detail_shows_only_allowed_transition_posts(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse('project_detail', args=(self.project.pk,)))

        self.assertContains(
            response,
            reverse('project_status_transition', args=(self.project.pk, Project.Status.ACTIVE)),
        )
        self.assertNotContains(response, reverse('project_annul', args=(self.project.pk,)))
        unallocated_project = create_project(code='PRJ-NAMED-ANNUL')
        unallocated_response = self.client.get(
            reverse('project_detail', args=(unallocated_project.pk,))
        )
        self.assertContains(
            unallocated_response,
            reverse('project_annul', args=(unallocated_project.pk,)),
        )
        self.assertNotContains(
            response,
            reverse('project_status_transition', args=(self.project.pk, Project.Status.SUSPENDED)),
        )

    def test_admin_status_is_readonly_and_save_cannot_bypass(self):
        request = RequestFactory().post('/admin/')
        request.user = self.user
        cases = (
            (ProjectAdmin(Project, admin.site), self.project, Project.Status.ACTIVE),
            (DonationAdmin(Donation, admin.site), self.donation, Donation.Status.ANNULLED),
            (FundAllocationAdmin(FundAllocation, admin.site), self.allocation, FundAllocation.Status.ACTIVE),
        )
        for model_admin, instance, manipulated_status in cases:
            with self.subTest(model=instance._meta.label):
                self.assertIn('status', model_admin.get_readonly_fields(request, instance))
                original_status = type(instance).objects.get(pk=instance.pk).status
                instance.status = manipulated_status
                model_admin.save_model(request, instance, form=None, change=True)
                instance.refresh_from_db()
                self.assertEqual(instance.status, original_status)
