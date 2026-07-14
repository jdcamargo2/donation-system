from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser, Group
from django.core.exceptions import ValidationError
from django.test import RequestFactory, TestCase
from django.urls import NoReverseMatch, reverse
from django.utils.text import capfirst

from apps.operations.admin import ProjectUpdateReviewDecisionAdmin
from apps.operations.forms import ProjectUpdateReviewDecisionForm
from apps.operations.models import AuditLog, Project, ProjectUpdateReviewDecision
from apps.operations.role_services import sync_operation_roles
from apps.operations.roles import (
    ROLE_EXTERNAL_AUDITOR,
    ROLE_FIELD_OPERATOR,
    ROLE_PROJECT_UPDATE_DECIDER,
    ROLE_PROJECT_UPDATE_REVIEWER,
    ROLE_SIGEDON_ADMIN,
)
from apps.operations.services import (
    ProjectUpdateReviewDecisionError,
    create_project_update_review,
    create_project_update_review_decision,
    publish_project_update,
    register_advance,
)
from apps.operations.tests.helpers import create_project, create_user


class ProjectUpdateReviewDecisionTests(TestCase):
    def setUp(self):
        sync_operation_roles()
        self.committee_member = get_user_model().objects.create_user(
            username='committee-decision-maker', password='pass-12345'
        )
        self.committee_member.groups.add(Group.objects.get(name=ROLE_PROJECT_UPDATE_DECIDER))
        self.reviewer = get_user_model().objects.create_user(
            username='committee-reviewer', password='pass-12345'
        )
        self.reviewer.groups.add(Group.objects.get(name=ROLE_PROJECT_UPDATE_REVIEWER))
        self.field_operator = get_user_model().objects.create_user(
            username='field-decision-operator', password='pass-12345'
        )
        self.field_operator.groups.add(Group.objects.get(name=ROLE_FIELD_OPERATOR))
        self.auditor = get_user_model().objects.create_user(username='decision-auditor', password='pass-12345')
        self.auditor.groups.add(Group.objects.get(name=ROLE_EXTERNAL_AUDITOR))
        self.project = create_project()
        self.project.status = Project.Status.ACTIVE
        self.project.save(update_fields=('status',))

    def create_review(self):
        # PRE: self.project is ACTIVE and the technical actor is authenticated.
        # POST: returns a persisted review for a PUBLISHED advance.
        project_update = register_advance(
            project_id=self.project.pk,
            title='Avance con resultado institucional',
            description='El avance no debe cambiar al registrar el resultado.',
            created_by=self.field_operator,
        )
        published_update = publish_project_update(project_update.pk, self.field_operator)
        return create_project_update_review(
            update_id=published_update.pk,
            observations='Revisión documental registrada.',
            actor=self.reviewer,
        )

    def create_decision(self, review=None, outcome='conforming', rationale='El expediente documental está completo.'):
        # PRE: review belongs to a PUBLISHED advance and has no decision.
        # POST: returns the institutional outcome persisted by the domain service.
        return create_project_update_review_decision(
            review_id=(review or self.create_review()).pk,
            outcome=outcome,
            rationale=rationale,
            actor=self.committee_member,
        )

    def test_conforming_and_observed_outcomes_can_be_registered(self):
        for outcome in ('conforming', 'observed'):
            with self.subTest(outcome=outcome):
                decision = self.create_decision(
                    review=self.create_review(),
                    outcome=outcome,
                    rationale=f'Fundamento para resultado {outcome}.',
                )

                self.assertEqual(decision.outcome, outcome)
                self.assertEqual(decision.get_outcome_display(), {'conforming': 'Conforme', 'observed': 'Observado'}[outcome])
                self.assertEqual(decision.decided_by, self.committee_member)

    def test_decision_rejects_duplicate_invalid_outcome_blank_rationale_and_anonymous_actor(self):
        review = self.create_review()
        self.create_decision(review)
        with self.assertRaises(ProjectUpdateReviewDecisionError):
            self.create_decision(review)

        invalid_review = self.create_review()
        with self.assertRaises(ValidationError):
            self.create_decision(invalid_review, outcome='invalid', rationale='Fundamento válido.')

        blank_review = self.create_review()
        with self.assertRaises(ValidationError):
            self.create_decision(blank_review, rationale='   ')

        anonymous_review = self.create_review()
        with self.assertRaises(ProjectUpdateReviewDecisionError):
            create_project_update_review_decision(
                review_id=anonymous_review.pk,
                outcome='conforming',
                rationale='Fundamento sin actor.',
                actor=AnonymousUser(),
            )

    def test_decision_preserves_review_and_published_advance_and_audits_actor(self):
        review = self.create_review()
        project_update = review.project_update
        before_review = (review.observations, review.reviewed_by, review.reviewed_at)
        before_update = (project_update.status, project_update.title, project_update.description, project_update.updated_at)
        rationale = 'Se requiere seguimiento documental posterior.'

        decision = self.create_decision(review, outcome='observed', rationale=rationale)
        review.refresh_from_db()
        project_update.refresh_from_db()
        audit_log = AuditLog.objects.get(
            action=AuditLog.Action.CREATED,
            entity_id=str(decision.pk),
            model_name=capfirst(ProjectUpdateReviewDecision._meta.verbose_name),
            user=self.committee_member,
            summary='Resultado de revisión del Comité registrado.',
        )

        self.assertEqual((review.observations, review.reviewed_by, review.reviewed_at), before_review)
        self.assertEqual(
            (project_update.status, project_update.title, project_update.description, project_update.updated_at),
            before_update,
        )
        self.assertEqual(project_update.status, project_update.Status.PUBLISHED)
        self.assertEqual(audit_log.user, self.committee_member)
        self.assertEqual(audit_log.summary, 'Resultado de revisión del Comité registrado.')
        self.assertNotIn(rationale, audit_log.summary)

    def test_decision_form_exposes_only_outcome_and_rationale(self):
        self.assertEqual(list(ProjectUpdateReviewDecisionForm().fields), ['outcome', 'rationale'])

    def test_committee_can_create_and_view_decision_once(self):
        review = self.create_review()
        self.client.force_login(self.committee_member)

        self.assertEqual(
            self.client.get(reverse('project_update_review_decision_create', args=[review.pk])).status_code,
            200,
        )
        response = self.client.post(
            reverse('project_update_review_decision_create', args=[review.pk]),
            {'outcome': 'observed', 'rationale': 'Se debe registrar seguimiento posterior.'},
        )
        decision = ProjectUpdateReviewDecision.objects.get(review=review)

        self.assertRedirects(response, reverse('project_update_review_detail', args=[review.pk]))
        self.assertEqual(
            self.client.get(reverse('project_update_review_decision_detail', args=[decision.pk])).status_code,
            200,
        )
        self.assertEqual(
            self.client.get(reverse('project_update_review_decision_create', args=[review.pk])).status_code,
            403,
        )

    def test_users_without_decision_permission_cannot_create_it(self):
        review = self.create_review()
        administrator = get_user_model().objects.create_user(
            username='decision-admin', password='pass-12345'
        )
        administrator.groups.add(Group.objects.get(name=ROLE_SIGEDON_ADMIN))

        for user in (administrator, self.reviewer, self.field_operator, self.auditor):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                response = self.client.post(
                    reverse('project_update_review_decision_create', args=[review.pk]),
                    {'outcome': 'conforming', 'rationale': 'Intento no autorizado.'},
                )
                self.assertEqual(response.status_code, 403)
                self.assertFalse(ProjectUpdateReviewDecision.objects.filter(review=review).exists())

    def test_user_without_decision_permission_cannot_distinguish_decision_state(self):
        undecided_review = self.create_review()
        decided_review = self.create_review()
        self.create_decision(decided_review)
        user = get_user_model().objects.create_user(username='decision-state-probe', password='pass-12345')
        self.client.force_login(user)

        for review in (undecided_review, decided_review):
            with self.subTest(review=review.pk):
                response = self.client.get(reverse('project_update_review_decision_create', args=[review.pk]))

                self.assertEqual(response.status_code, 403)

    def test_decision_interface_hides_mutation_and_approval_language(self):
        review = self.create_review()
        self.client.force_login(self.committee_member)

        before_response = self.client.get(reverse('project_update_review_detail', args=[review.pk]))
        self.assertContains(before_response, reverse('project_update_review_decision_create', args=[review.pk]))

        decision = self.create_decision(review, outcome='conforming')
        after_response = self.client.get(reverse('project_update_review_detail', args=[review.pk]))
        detail_response = self.client.get(reverse('project_update_review_decision_detail', args=[decision.pk]))

        self.assertNotContains(after_response, reverse('project_update_review_decision_create', args=[review.pk]))
        self.assertContains(after_response, 'Conforme')
        self.assertContains(detail_response, 'Conforme')
        self.assertNotContains(detail_response, 'Aprobado')
        self.assertNotContains(detail_response, 'Rechazado')

    def test_decision_has_no_operational_edit_or_delete_routes(self):
        with self.assertRaises(NoReverseMatch):
            reverse('project_update_review_decision_update', args=[1])
        with self.assertRaises(NoReverseMatch):
            reverse('project_update_review_decision_delete', args=[1])

    def test_decider_permission_matrix_keeps_crud_mutations_disabled(self):
        self.assertTrue(self.committee_member.has_perm('operations.decide_projectupdate'))
        self.assertTrue(self.committee_member.has_perm('operations.view_projectupdatereviewdecision'))
        self.assertFalse(self.committee_member.has_perm('operations.review_projectupdate'))
        self.assertFalse(self.committee_member.has_perm('operations.change_projectupdatereviewdecision'))
        self.assertFalse(self.committee_member.has_perm('operations.delete_projectupdatereviewdecision'))
        self.assertFalse(self.committee_member.has_perm('operations.change_projectupdate'))


class ProjectUpdateReviewDecisionAdminTests(TestCase):
    def setUp(self):
        self.request = RequestFactory().get('/admin/')
        self.request.user = create_user('decision-admin')
        self.model_admin = ProjectUpdateReviewDecisionAdmin(ProjectUpdateReviewDecision, admin.site)

    def test_admin_disallows_creation_edits_and_deletion(self):
        self.assertFalse(self.model_admin.has_add_permission(self.request))
        self.assertFalse(self.model_admin.has_change_permission(self.request, ProjectUpdateReviewDecision()))
        self.assertFalse(self.model_admin.has_delete_permission(self.request, ProjectUpdateReviewDecision()))
