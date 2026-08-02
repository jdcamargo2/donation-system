from datetime import date
from decimal import Decimal

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import RequestFactory, TestCase
from django.urls import NoReverseMatch, reverse

from apps.operations.admin import DonationAdmin, FundAllocationAdmin, ProjectAdmin
from apps.operations.forms import DonationForm, FundAllocationForm, ProjectForm
from apps.operations.models import AuditLog, Donation, FundAllocation, Project
from apps.operations.services import (
    DONATION_STATUS_TRANSITIONS,
    FUND_ALLOCATION_STATUS_TRANSITIONS,
    PROJECT_STATUS_TRANSITIONS,
    InvalidStateTransitionError,
    finish_fund_allocation,
    transition_donation_status,
    transition_fund_allocation_status,
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
                is_generic_target = target_status not in {'closed', 'finished', 'annulled'}
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

    def test_project_lifecycle_allows_only_active_to_closed(self):
        self.assertEqual(
            PROJECT_STATUS_TRANSITIONS,
            {
                Project.Status.ACTIVE: frozenset({Project.Status.CLOSED}),
                Project.Status.CLOSED: frozenset(),
            },
        )
        project = self.create_project(Project.Status.ACTIVE)
        finished = finish_project(project.pk, actor=self.actor)
        self.assertEqual(finished.status, Project.Status.CLOSED)
        with self.assertRaises(InvalidStateTransitionError):
            finish_project(project.pk, actor=self.actor)

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

    def test_finish_project_requires_authenticated_actor(self):
        project = self.create_project(Project.Status.ACTIVE)

        with self.assertRaises(InvalidStateTransitionError):
            finish_project(project.pk, actor=None)

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
        allocation = self.create_allocation(FundAllocation.Status.ACTIVE)
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
        self.assertEqual(allocation.status, FundAllocation.Status.ACTIVE)

    def test_service_uses_persisted_state_and_second_attempt_does_not_duplicate_audit(self):
        donation = self.create_donation(Donation.Status.REGISTERED)
        transitioned = transition_donation_status(
            donation.pk, actor=self.actor, target_status=Donation.Status.RECEIVED
        )
        with self.assertRaises(InvalidStateTransitionError):
            transition_donation_status(
                donation.pk, actor=self.actor, target_status=Donation.Status.RECEIVED
            )

        self.assertEqual(transitioned.status, Donation.Status.RECEIVED)
        log = AuditLog.objects.get(entity_id=str(donation.pk))
        self.assertIn(Donation.Status.REGISTERED, log.summary)
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
        self.assertNotIn('is_public', ProjectForm().fields)
        self.assertNotIn('status', DonationForm().fields)
        self.assertNotIn('status', FundAllocationForm().fields)

        form = ProjectForm(
            instance=self.project,
            data={
                'name': self.project.name,
                'description': '',
                'objective': '',
                'location': '',
                'estimated_budget': '1000.00',
                'start_date': '',
                'end_date': '',
                'status': Project.Status.CLOSED,
                'is_public': True,
            },
        )
        self.assertTrue(form.is_valid(), form.errors)
        saved = form.save()
        self.assertEqual(saved.status, Project.Status.ACTIVE)
        self.assertFalse(saved.is_public)

    def test_project_defaults_to_active_and_not_public(self):
        project = Project.objects.create(
            code='PRJ-DEFAULTS',
            name='Proyecto por defecto',
            estimated_budget=Decimal('100.00'),
        )
        self.assertEqual(project.status, Project.Status.ACTIVE)
        self.assertFalse(project.is_public)

        self.client.force_login(self.user)
        response = self.client.post(
            reverse('project_create'),
            data={
                'name': 'Proyecto web',
                'description': '',
                'objective': '',
                'location': '',
                'estimated_budget': '100.00',
                'start_date': '',
                'end_date': '',
                'status': Project.Status.CLOSED,
                'is_public': True,
            },
        )
        self.assertEqual(response.status_code, 302)
        created = Project.objects.get(name='Proyecto web')
        self.assertEqual(created.status, Project.Status.ACTIVE)
        self.assertFalse(created.is_public)

    def test_removed_project_transition_and_annul_routes_do_not_exist(self):
        with self.assertRaises(NoReverseMatch):
            reverse('project_status_transition', args=(self.project.pk, Project.Status.ACTIVE))
        with self.assertRaises(NoReverseMatch):
            reverse('project_annul', args=(self.project.pk,))

    def test_detail_shows_finish_without_estado_or_annul(self):
        self.client.force_login(self.user)

        blocked = self.client.get(reverse('project_detail', args=(self.project.pk,)))
        self.assertNotContains(blocked, 'Terminar proyecto')
        self.assertContains(
            blocked,
            'Para cerrar el proyecto, finaliza o anula sus asignaciones',
        )
        self.assertNotContains(blocked, 'aria-label="Cambiar estado del proyecto"')
        self.assertNotContains(blocked, 'Anular proyecto')

        finish_fund_allocation(self.allocation.pk, actor=self.user)
        response = self.client.get(reverse('project_detail', args=(self.project.pk,)))

        self.assertContains(response, reverse('project_finish', args=(self.project.pk,)))
        self.assertContains(response, 'Terminar proyecto')
        self.assertContains(response, 'Privado')
        self.assertNotContains(response, 'aria-label="Cambiar estado del proyecto"')
        self.assertNotContains(response, 'Anular proyecto')
        self.assertContains(response, self.project.get_status_display())

        finish_project(self.project.pk, actor=self.user)
        closed_response = self.client.get(reverse('project_detail', args=(self.project.pk,)))
        self.assertNotContains(closed_response, 'Terminar proyecto')
        self.assertNotContains(closed_response, 'Publicar en portal')
        self.assertNotContains(closed_response, 'Retirar del portal')
        self.assertContains(closed_response, 'Cerrado')
        self.assertContains(closed_response, 'Privado')

    def test_admin_status_is_readonly_and_save_cannot_bypass(self):
        request = RequestFactory().post('/admin/')
        request.user = self.user
        cases = (
            (ProjectAdmin(Project, admin.site), self.project, Project.Status.CLOSED),
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
        self.assertIn('is_public', ProjectAdmin(Project, admin.site).get_readonly_fields(request, self.project))
