"""FLOW-COMMITTEE-QUEUES: permission-scoped project-update governance dashboard queues."""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from apps.operations.models import (
    Project,
    ProjectUpdate,
    ProjectUpdateRemediation,
    ProjectUpdateReviewDecision,
)
from apps.operations.role_services import sync_operation_roles
from apps.operations.roles import (
    ROLE_EXTERNAL_AUDITOR,
    ROLE_FIELD_OPERATOR,
    ROLE_PROJECT_COMMITTEE,
    ROLE_SIGEDON_ADMIN,
)
from apps.operations.selectors import (
    decidable_project_update_reviews_for_user,
    resolvable_project_update_remediations_for_user,
    reviewable_project_updates_for_user,
)
from apps.operations.services import (
    DASHBOARD_PROJECT_UPDATE_GOVERNANCE_PREVIEW_LIMIT,
    create_project_update_remediation,
    create_project_update_review,
    create_project_update_review_decision,
    get_dashboard_metrics,
    get_dashboard_project_update_governance,
    publish_project_update,
    register_advance,
    resolve_project_update_remediation,
    submit_project_update_remediation,
)
from apps.operations.tests.helpers import create_project
from apps.operations.tests.test_permissions import create_user_with_permissions


class DashboardProjectUpdateQueueTests(TestCase):
    def setUp(self):
        sync_operation_roles()
        self.admin = self._role_user('gov-admin', ROLE_SIGEDON_ADMIN)
        self.operator = self._role_user('gov-operator', ROLE_FIELD_OPERATOR)
        self.committee = self._role_user('gov-committee', ROLE_PROJECT_COMMITTEE)
        self.auditor = self._role_user('gov-auditor', ROLE_EXTERNAL_AUDITOR)
        self.superuser = get_user_model().objects.create_superuser(
            username='gov-super',
            email='gov-super@example.com',
            password='pass-12345',
        )
        self.project = create_project(code='PRJ-GOV-QUEUES', name='Gobernanza Queues')
        self.project.status = Project.Status.ACTIVE
        self.project.save(update_fields=('status',))
        self.hidden_project = create_project(
            code='PRJ-HIDDEN-LEAK',
            name='Proyecto Secreto No Visible En Colas',
        )
        self.hidden_project.status = Project.Status.ACTIVE
        self.hidden_project.save(update_fields=('status',))

    def _role_user(self, username, role_name):
        user = get_user_model().objects.create_user(
            username=username,
            password='pass-12345',
        )
        user.groups.add(Group.objects.get(name=role_name))
        return user

    def _unpublished(self, *, title='Avance no publicado GOV', project=None, actor=None):
        return register_advance(
            project_id=(project or self.project).pk,
            title=title,
            description='Contenido de avance para colas de gobernanza.',
            created_by=actor or self.operator,
            reported_by=actor or self.operator,
        )

    def _published(self, *, title='Avance publicado GOV', project=None, actor=None):
        update = self._unpublished(title=title, project=project, actor=actor)
        return publish_project_update(update.pk, actor or self.operator)

    def _review(self, project_update=None, *, observations='Revisión GOV.'):
        return create_project_update_review(
            update_id=(project_update or self._published()).pk,
            observations=observations,
            actor=self.committee,
        )

    def _decision(self, review=None, *, outcome='observed'):
        return create_project_update_review_decision(
            review_id=(review or self._review()).pk,
            outcome=outcome,
            rationale='Fundamento GOV.',
            actor=self.committee,
        )

    def _submitted_remediation(self, decision=None):
        remediation = create_project_update_remediation(
            decision_id=(decision or self._decision()).pk,
            response='Respuesta de remediación GOV.',
            actor=self.operator,
        )
        return submit_project_update_remediation(
            remediation_id=remediation.pk,
            actor=self.operator,
        )

    def test_reviewable_selector_scopes_published_without_review(self):
        pending = self._published(title='Pendiente revisión UNIQUE-A')
        unpublished = self._unpublished(title='No publicado UNIQUE-B')
        reviewed = self._published(title='Ya revisado UNIQUE-C')
        self._review(reviewed)

        qs = reviewable_project_updates_for_user(self.committee)
        titles = set(qs.values_list('title', flat=True))
        self.assertIn(pending.title, titles)
        self.assertNotIn(unpublished.title, titles)
        self.assertNotIn(reviewed.title, titles)
        self.assertEqual(list(reviewable_project_updates_for_user(self.operator)), [])
        self.assertEqual(list(reviewable_project_updates_for_user(self.auditor)), [])

    def test_decidable_selector_scopes_reviews_without_decision(self):
        pending_review = self._review(self._published(title='Pendiente decisión UNIQUE-D'))
        decided_review = self._review(self._published(title='Ya decidido UNIQUE-E'))
        self._decision(decided_review, outcome='conforming')

        qs = decidable_project_update_reviews_for_user(self.committee)
        pks = set(qs.values_list('pk', flat=True))
        self.assertIn(pending_review.pk, pks)
        self.assertNotIn(decided_review.pk, pks)
        self.assertEqual(list(decidable_project_update_reviews_for_user(self.operator)), [])

    def test_resolvable_selector_scopes_submitted_only(self):
        submitted = self._submitted_remediation()
        draft = create_project_update_remediation(
            decision_id=self._decision().pk,
            response='Borrador UNIQUE-F.',
            actor=self.operator,
        )
        accepted_parent = self._submitted_remediation()
        resolve_project_update_remediation(
            remediation_id=accepted_parent.pk,
            status=ProjectUpdateRemediation.Status.ACCEPTED,
            resolution_notes='Aceptada.',
            actor=self.committee,
        )
        rejected_parent = self._submitted_remediation()
        resolve_project_update_remediation(
            remediation_id=rejected_parent.pk,
            status=ProjectUpdateRemediation.Status.REJECTED,
            resolution_notes='Rechazada.',
            actor=self.committee,
        )

        qs = resolvable_project_update_remediations_for_user(self.committee)
        pks = set(qs.values_list('pk', flat=True))
        self.assertIn(submitted.pk, pks)
        self.assertNotIn(draft.pk, pks)
        self.assertNotIn(accepted_parent.pk, pks)
        self.assertNotIn(rejected_parent.pk, pks)
        self.assertEqual(list(resolvable_project_update_remediations_for_user(self.operator)), [])

    def test_partial_permissions_enable_only_matching_queues(self):
        pending = self._published(title='Solo revisión PARTIAL-REV')
        review = self._review(self._published(title='Solo decisión PARTIAL-DEC'))
        remediation = self._submitted_remediation()

        review_only = create_user_with_permissions(
            'gov-review-only',
            'review_projectupdate',
            'view_projectupdate',
        )
        decide_only = create_user_with_permissions(
            'gov-decide-only',
            'decide_projectupdate',
            'view_projectupdatereview',
        )
        resolve_only = create_user_with_permissions(
            'gov-resolve-only',
            'resolve_projectupdateremediation',
            'view_projectupdateremediation',
        )
        view_only = create_user_with_permissions(
            'gov-view-only',
            'view_projectupdate',
            'view_projectupdatereview',
            'view_projectupdatereviewdecision',
            'view_projectupdateremediation',
        )

        review_gov = get_dashboard_project_update_governance(user=review_only)
        self.assertTrue(review_gov['show_section'])
        self.assertIsNotNone(review_gov['review'])
        self.assertIsNone(review_gov['decision'])
        self.assertIsNone(review_gov['remediation'])
        self.assertEqual(review_gov['review']['total_count'], 1)
        self.assertEqual(review_gov['review']['items'][0]['identifier'], pending.title)

        decide_gov = get_dashboard_project_update_governance(user=decide_only)
        self.assertIsNotNone(decide_gov['decision'])
        self.assertIsNone(decide_gov['review'])
        self.assertIsNone(decide_gov['remediation'])
        self.assertEqual(decide_gov['decision']['items'][0]['identifier'], review.project_update.title)

        resolve_gov = get_dashboard_project_update_governance(user=resolve_only)
        self.assertIsNotNone(resolve_gov['remediation'])
        self.assertIsNone(resolve_gov['review'])
        self.assertIsNone(resolve_gov['decision'])
        self.assertEqual(
            resolve_gov['remediation']['items'][0]['identifier'],
            remediation.decision.review.project_update.title,
        )

        view_gov = get_dashboard_project_update_governance(user=view_only)
        self.assertFalse(view_gov['show_section'])
        self.assertIsNone(view_gov['review'])
        self.assertIsNone(view_gov['decision'])
        self.assertIsNone(view_gov['remediation'])

    def test_committee_dashboard_shows_governance_queues_and_actions(self):
        pending = self._published(title='Comité revisión QUEUE-REV')
        review = self._review(self._published(title='Comité decisión QUEUE-DEC'))
        remediation = self._submitted_remediation()
        remediation_title = remediation.decision.review.project_update.title

        self.client.force_login(self.committee)
        response = self.client.get(reverse('dashboard'))
        html = response.content.decode()

        self.assertContains(response, 'Gobernanza de avances')
        self.assertContains(response, 'Pendientes de revisión')
        self.assertContains(response, 'Pendientes de decisión')
        self.assertContains(response, 'Remediaciones por resolver')
        self.assertContains(response, 'Revisar avance')
        self.assertContains(response, 'Emitir decisión')
        self.assertContains(response, 'Resolver remediación')
        self.assertContains(response, pending.title)
        self.assertContains(response, review.project_update.title)
        self.assertContains(response, remediation_title)
        self.assertIn(reverse('project_update_detail', args=[pending.pk]), html)
        self.assertIn(reverse('project_update_review_detail', args=[review.pk]), html)
        self.assertIn(
            reverse('project_update_remediation_detail', args=[remediation.pk]),
            html,
        )

        for url in (
            reverse('project_update_detail', args=[pending.pk]),
            reverse('project_update_review_detail', args=[review.pk]),
            reverse('project_update_remediation_detail', args=[remediation.pk]),
        ):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_operator_and_auditor_see_no_governance_section(self):
        hidden = self._published(
            title='Secreto Operador FILTER-OP',
            project=self.hidden_project,
        )
        self._review(self._published(title='Secreto Auditor FILTER-AUD'))
        self._submitted_remediation()

        for user in (self.operator, self.auditor, self.admin):
            with self.subTest(user=user.username):
                gov = get_dashboard_project_update_governance(user=user)
                self.assertFalse(gov['show_section'])
                metrics = get_dashboard_metrics(user=user)
                self.assertFalse(metrics['project_update_governance']['show_section'])
                self.client.force_login(user)
                response = self.client.get(reverse('dashboard'))
                self.assertNotContains(response, 'Gobernanza de avances')
                self.assertNotContains(response, hidden.title)
                self.assertNotContains(response, 'Secreto Operador FILTER-OP')
                self.assertNotContains(response, 'Secreto Auditor FILTER-AUD')
                self.assertNotContains(response, 'Pendientes de revisión')
                self.assertNotContains(response, 'Revisar avance')
                self.assertNotContains(response, 'Emitir decisión')
                self.assertNotContains(response, 'Resolver remediación')

    def test_superuser_sees_all_three_queues(self):
        pending = self._published(title='Super revisión SUPER-REV')
        review = self._review(self._published(title='Super decisión SUPER-DEC'))
        remediation = self._submitted_remediation()
        gov = get_dashboard_project_update_governance(user=self.superuser)

        self.assertTrue(gov['show_section'])
        self.assertIsNotNone(gov['review'])
        self.assertIsNotNone(gov['decision'])
        self.assertIsNotNone(gov['remediation'])
        self.assertIn(pending.title, {item['identifier'] for item in gov['review']['items']})
        self.assertIn(
            review.project_update.title,
            {item['identifier'] for item in gov['decision']['items']},
        )
        self.assertIn(
            remediation.decision.review.project_update.title,
            {item['identifier'] for item in gov['remediation']['items']},
        )

    def test_queue_membership_updates_after_transitions(self):
        pending = self._published(title='Transición revisión TRANS-REV')
        self.assertIn(
            pending.pk,
            reviewable_project_updates_for_user(self.committee).values_list('pk', flat=True),
        )
        review = create_project_update_review(
            update_id=pending.pk,
            observations='Hecha.',
            actor=self.committee,
        )
        self.assertNotIn(
            pending.pk,
            reviewable_project_updates_for_user(self.committee).values_list('pk', flat=True),
        )
        self.assertIn(
            review.pk,
            decidable_project_update_reviews_for_user(self.committee).values_list(
                'pk', flat=True
            ),
        )
        decision = create_project_update_review_decision(
            review_id=review.pk,
            outcome=ProjectUpdateReviewDecision.Outcome.OBSERVED,
            rationale='Observado.',
            actor=self.committee,
        )
        self.assertNotIn(
            review.pk,
            decidable_project_update_reviews_for_user(self.committee).values_list(
                'pk', flat=True
            ),
        )
        remediation = create_project_update_remediation(
            decision_id=decision.pk,
            response='Respuesta.',
            actor=self.operator,
        )
        self.assertNotIn(
            remediation.pk,
            resolvable_project_update_remediations_for_user(self.committee).values_list(
                'pk', flat=True
            ),
        )
        submit_project_update_remediation(remediation_id=remediation.pk, actor=self.operator)
        self.assertIn(
            remediation.pk,
            resolvable_project_update_remediations_for_user(self.committee).values_list(
                'pk', flat=True
            ),
        )
        resolve_project_update_remediation(
            remediation_id=remediation.pk,
            status=ProjectUpdateRemediation.Status.ACCEPTED,
            resolution_notes='OK.',
            actor=self.committee,
        )
        self.assertNotIn(
            remediation.pk,
            resolvable_project_update_remediations_for_user(self.committee).values_list(
                'pk', flat=True
            ),
        )

    def test_preview_limit_and_has_more(self):
        limit = DASHBOARD_PROJECT_UPDATE_GOVERNANCE_PREVIEW_LIMIT
        for index in range(limit + 2):
            self._published(title=f'Preview límite PREVIEW-{index:02d}')

        gov = get_dashboard_project_update_governance(user=self.committee)
        review_queue = gov['review']
        self.assertEqual(len(review_queue['items']), limit)
        self.assertEqual(review_queue['total_count'], limit + 2)
        self.assertTrue(review_queue['has_more'])
        self.assertFalse(review_queue['show_view_all'])
        self.assertEqual(review_queue['list_url'], '')

    def test_empty_states_only_for_authorized_queues(self):
        decide_only = create_user_with_permissions(
            'gov-empty-decide',
            'decide_projectupdate',
        )
        gov = get_dashboard_project_update_governance(user=decide_only)
        self.assertTrue(gov['show_section'])
        self.assertIsNone(gov['review'])
        self.assertIsNotNone(gov['decision'])
        self.assertEqual(gov['decision']['total_count'], 0)
        self.assertEqual(
            gov['decision']['empty_message'],
            'No hay revisiones pendientes de decisión.',
        )

        self.client.force_login(decide_only)
        response = self.client.get(reverse('dashboard'))
        self.assertContains(response, 'Gobernanza de avances')
        self.assertContains(response, 'No hay revisiones pendientes de decisión.')
        self.assertNotContains(response, 'Pendientes de revisión')
        self.assertNotContains(response, 'No hay avances pendientes de revisión.')
        self.assertNotContains(response, 'Remediaciones por resolver')

    def test_stale_action_urls_fail_safely_and_direct_guess_is_protected(self):
        unpublished = self._unpublished(title='Stale unpublished STALE-UNPUB')
        published = self._published(title='Stale after review STALE-REV')
        review = self._review(published)
        decided = self._review(self._published(title='Stale after decision STALE-DEC'))
        self._decision(decided, outcome='conforming')
        draft = create_project_update_remediation(
            decision_id=self._decision().pk,
            response='Draft resolve.',
            actor=self.operator,
        )

        self.client.force_login(self.committee)
        self.assertEqual(
            self.client.get(
                reverse('project_update_review_create', args=[unpublished.pk])
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.get(
                reverse('project_update_review_create', args=[published.pk])
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.get(
                reverse('project_update_review_decision_create', args=[decided.pk])
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.get(
                reverse('project_update_review_decision_create', args=[review.pk])
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.get(
                reverse('project_update_remediation_resolve', args=[draft.pk])
            ).status_code,
            404,
        )

        outsider = get_user_model().objects.create_user(
            username='gov-outsider',
            password='pass-12345',
        )
        self.client.force_login(outsider)
        self.assertEqual(
            self.client.get(
                reverse('project_update_review_create', args=[self._published().pk])
            ).status_code,
            403,
        )

    def test_expense_request_queues_remain_independent(self):
        self._published(title='Independiente ER')
        metrics = get_dashboard_metrics(user=self.committee)
        self.assertTrue(metrics['project_update_governance']['show_section'])
        # Committee still receives its Expense Request decision queue when present.
        er_keys = {queue['key'] for queue in metrics['expense_request_queues']}
        self.assertIn('decision', er_keys)
        self.assertNotIn('fulfillment', er_keys)

    def test_no_permissions_yields_empty_governance_without_leakage(self):
        secret = self._published(
            title='Leak Label SECRET-LEAK-XYZ',
            project=self.hidden_project,
        )
        none_user = get_user_model().objects.create_user(
            username='gov-none',
            password='pass-12345',
        )
        gov = get_dashboard_project_update_governance(user=none_user)
        self.assertEqual(
            gov,
            {
                'show_section': False,
                'review': None,
                'decision': None,
                'remediation': None,
            },
        )
        self.client.force_login(none_user)
        response = self.client.get(reverse('dashboard'))
        self.assertNotContains(response, secret.title)
        self.assertNotContains(response, 'SECRET-LEAK-XYZ')
        self.assertNotContains(response, 'PRJ-HIDDEN-LEAK')
        self.assertNotContains(response, 'Gobernanza de avances')

    def test_counts_match_scoped_rows_and_ordering_is_oldest_first(self):
        first = self._published(title='Orden primero ORDER-01')
        second = self._published(title='Orden segundo ORDER-02')
        third = self._published(title='Orden tercero ORDER-03')
        # Force distinct updated_at ordering by touching timestamps if needed.
        ProjectUpdate.objects.filter(pk=first.pk).update(updated_at=first.updated_at)
        gov = get_dashboard_project_update_governance(user=self.committee)
        identifiers = [item['identifier'] for item in gov['review']['items']]
        self.assertEqual(gov['review']['total_count'], 3)
        self.assertEqual(len(gov['review']['items']), 3)
        self.assertEqual(identifiers, [first.title, second.title, third.title])
