from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser, Group, Permission
from django.core.exceptions import ValidationError
from django.test import RequestFactory, TestCase
from django.urls import NoReverseMatch, reverse

from apps.operations.admin import ProjectUpdateReviewAdmin
from apps.operations.forms import ProjectUpdateReviewForm
from apps.operations.models import AuditLog, Project, ProjectUpdate, ProjectUpdateReview
from apps.operations.role_services import sync_operation_roles
from apps.operations.roles import (
    ROLE_EXTERNAL_AUDITOR,
    ROLE_FIELD_OPERATOR,
    ROLE_PROJECT_COMMITTEE,
    ROLE_SIGEDON_ADMIN,
)
from apps.operations.services import (
    ProjectUpdateReviewError,
    create_project_update_review,
    publish_project_update,
    register_advance,
)
from apps.operations.tests.helpers import create_project, create_user


class ProjectUpdateReviewTests(TestCase):
    def setUp(self):
        sync_operation_roles()
        self.committee_member = get_user_model().objects.create_user(
            username='committee-reviewer', password='pass-12345'
        )
        self.committee_member.groups.add(Group.objects.get(name=ROLE_PROJECT_COMMITTEE))
        self.field_operator = get_user_model().objects.create_user(
            username='field-reviewer', password='pass-12345'
        )
        self.field_operator.groups.add(Group.objects.get(name=ROLE_FIELD_OPERATOR))
        self.project = create_project()
        self.project.status = Project.Status.ACTIVE
        self.project.save(update_fields=('status',))

    def create_unpublished_update(self):
        # PRE: self.project is ACTIVE and can receive advances.
        # POST: returns a persisted UNPUBLISHED advance without a committee review.
        return register_advance(
            project_id=self.project.pk,
            title='Avance para revisión documental',
            description='Contenido que debe permanecer inalterado al revisar.',
            created_by=self.field_operator,
            reported_by=self.field_operator,
        )

    def create_published_update(self):
        # PRE: self.project is ACTIVE and self.field_operator is authenticated.
        # POST: returns a PUBLISHED advance eligible for exactly one review.
        project_update = self.create_unpublished_update()
        return publish_project_update(project_update.pk, self.field_operator)

    def create_review(self, project_update=None, observations='Documentación revisada por el Comité.'):
        # PRE: project_update is PUBLISHED and has no existing review.
        # POST: returns the review persisted through the domain service.
        return create_project_update_review(
            update_id=(project_update or self.create_published_update()).pk,
            observations=observations,
            actor=self.committee_member,
        )

    def test_published_update_can_receive_one_review_without_mutating_it(self):
        project_update = self.create_published_update()
        before = {
            'status': project_update.status,
            'title': project_update.title,
            'description': project_update.description,
            'updated_at': project_update.updated_at,
        }

        review = self.create_review(project_update, observations='  Documentación revisada.  ')
        project_update.refresh_from_db()

        self.assertEqual(review.project_update, project_update)
        self.assertEqual(review.observations, 'Documentación revisada.')
        self.assertEqual(review.reviewed_by, self.committee_member)
        self.assertEqual(project_update.status, before['status'])
        self.assertEqual(project_update.title, before['title'])
        self.assertEqual(project_update.description, before['description'])
        self.assertEqual(project_update.updated_at, before['updated_at'])

    def test_review_rejects_unpublished_update(self):
        unpublished_update = self.create_unpublished_update()

        with self.assertRaises(ProjectUpdateReviewError):
            create_project_update_review(
                update_id=unpublished_update.pk,
                observations='Observación no permitida.',
                actor=self.committee_member,
            )
        self.assertFalse(ProjectUpdateReview.objects.filter(project_update=unpublished_update).exists())
        self.assertFalse(AuditLog.objects.filter(model_name='Revisión documental de avance').exists())

    def test_review_rejects_blank_observations(self):
        published_update = self.create_published_update()
        with self.assertRaises(ValidationError):
            create_project_update_review(
                update_id=published_update.pk,
                observations='   ',
                actor=self.committee_member,
            )
        self.assertFalse(ProjectUpdateReview.objects.filter(project_update=published_update).exists())
        self.assertFalse(AuditLog.objects.filter(model_name='Revisión documental de avance').exists())

    def test_review_rejects_anonymous_actor(self):
        published_update = self.create_published_update()
        with self.assertRaises(ProjectUpdateReviewError):
            create_project_update_review(
                update_id=published_update.pk,
                observations='Observación sin actor.',
                actor=AnonymousUser(),
            )
        self.assertFalse(ProjectUpdateReview.objects.filter(project_update=published_update).exists())
        self.assertFalse(AuditLog.objects.filter(model_name='Revisión documental de avance').exists())

    def test_model_validation_rejects_unpublished_updates_and_blank_observations(self):
        unpublished_review = ProjectUpdateReview(
            project_update=self.create_unpublished_update(),
            observations='Observación inválida.',
        )
        blank_review = ProjectUpdateReview(
            project_update=self.create_published_update(),
            observations='  ',
        )

        with self.assertRaisesMessage(Exception, 'Solo los avances publicados'):
            unpublished_review.full_clean()
        with self.assertRaisesMessage(Exception, 'Las observaciones del Comité son obligatorias'):
            blank_review.full_clean()

    def test_review_is_unique_and_audited_without_exposing_full_observations(self):
        project_update = self.create_published_update()
        observations = 'Observación reservada que no debe copiarse a la auditoría.'
        review = self.create_review(project_update, observations=observations)

        with self.assertRaises(ProjectUpdateReviewError):
            self.create_review(project_update)

        audit_log = AuditLog.objects.get(entity_id=str(review.pk), action=AuditLog.Action.CREATED)
        self.assertEqual(audit_log.user, self.committee_member)
        self.assertEqual(audit_log.summary, 'Revisión documental del Comité registrada.')
        self.assertNotIn(observations, audit_log.summary)

    def test_review_form_only_exposes_observations(self):
        self.assertEqual(list(ProjectUpdateReviewForm().fields), ['observations'])

    def test_committee_can_create_and_view_review_but_not_review_unpublished(self):
        published_update = self.create_published_update()
        unpublished_update = self.create_unpublished_update()
        self.client.force_login(self.committee_member)

        self.assertEqual(
            self.client.get(reverse('project_update_review_create', args=[published_update.pk])).status_code,
            200,
        )
        self.assertEqual(
            self.client.get(reverse('project_update_review_create', args=[unpublished_update.pk])).status_code,
            404,
        )
        create_response = self.client.post(
            reverse('project_update_review_create', args=[published_update.pk]),
            {'observations': 'Revisión registrada desde la interfaz.'},
        )
        review = ProjectUpdateReview.objects.get(project_update=published_update)

        self.assertRedirects(create_response, reverse('project_update_review_detail', args=[review.pk]))
        self.assertEqual(
            self.client.get(reverse('project_update_review_detail', args=[review.pk])).status_code,
            200,
        )

    def test_non_committee_user_cannot_create_review(self):
        published_update = self.create_published_update()
        user = get_user_model().objects.create_user(username='ordinary-reviewer', password='pass-12345')
        self.assertFalse(user.is_superuser)
        self.assertFalse(user.has_perm('operations.review_projectupdate'))
        self.client.force_login(user)

        response = self.client.post(
            reverse('project_update_review_create', args=[published_update.pk]),
            {'observations': 'Intento no autorizado.'},
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(ProjectUpdateReview.objects.filter(project_update=published_update).exists())

    def test_sigedon_admin_cannot_create_review(self):
        published_update = self.create_published_update()
        administrator = get_user_model().objects.create_user(
            username='admin-reviewer', password='pass-12345'
        )
        administrator.groups.add(Group.objects.get(name=ROLE_SIGEDON_ADMIN))
        self.client.force_login(administrator)

        response = self.client.post(
            reverse('project_update_review_create', args=[published_update.pk]),
            {'observations': 'Intento administrativo no autorizado.'},
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(ProjectUpdateReview.objects.filter(project_update=published_update).exists())

    def test_user_without_review_permission_cannot_distinguish_update_state(self):
        unpublished_update = self.create_unpublished_update()
        published_update = self.create_published_update()
        reviewed_update = self.create_published_update()
        self.create_review(reviewed_update)
        user = get_user_model().objects.create_user(username='review-state-probe', password='pass-12345')
        self.client.force_login(user)

        for project_update in (unpublished_update, published_update, reviewed_update):
            with self.subTest(project_update=project_update.pk):
                response = self.client.get(reverse('project_update_review_create', args=[project_update.pk]))

                self.assertEqual(response.status_code, 403)

    def test_review_action_is_hidden_without_functional_permission(self):
        published_update = self.create_published_update()
        user = get_user_model().objects.create_user(username='ordinary-detail-viewer', password='pass-12345')
        user.user_permissions.add(Permission.objects.get(
            content_type__app_label='operations', codename='view_projectupdate'
        ))
        self.assertFalse(user.is_superuser)
        self.assertFalse(user.has_perm('operations.review_projectupdate'))
        self.client.force_login(user)

        response = self.client.get(reverse('project_update_detail', args=[published_update.pk]))

        self.assertNotContains(response, reverse('project_update_review_create', args=[published_update.pk]))
        self.assertNotContains(response, 'Registrar revisión')

    def test_review_permission_matrix_is_limited_to_committee_role(self):
        auditor = get_user_model().objects.create_user(username='auditor-reviewer', password='pass-12345')
        auditor.groups.add(Group.objects.get(name=ROLE_EXTERNAL_AUDITOR))
        ordinary_user = get_user_model().objects.create_user(username='ordinary-permissions', password='pass-12345')
        administrator = get_user_model().objects.create_user(
            username='review-permissions-admin', password='pass-12345'
        )
        administrator.groups.add(Group.objects.get(name=ROLE_SIGEDON_ADMIN))

        self.assertTrue(self.committee_member.has_perm('operations.review_projectupdate'))
        self.assertTrue(self.committee_member.has_perm('operations.decide_projectupdate'))
        self.assertTrue(self.committee_member.has_perm('operations.resolve_projectupdateremediation'))
        self.assertFalse(self.field_operator.has_perm('operations.review_projectupdate'))
        self.assertFalse(auditor.has_perm('operations.review_projectupdate'))
        self.assertFalse(ordinary_user.has_perm('operations.review_projectupdate'))
        self.assertFalse(administrator.has_perm('operations.review_projectupdate'))

    def test_review_interface_only_offers_creation_for_unreviewed_published_updates(self):
        published_update = self.create_published_update()
        unpublished_update = self.create_unpublished_update()
        self.client.force_login(self.committee_member)

        published_response = self.client.get(reverse('project_update_detail', args=[published_update.pk]))
        unpublished_response = self.client.get(reverse('project_update_detail', args=[unpublished_update.pk]))

        self.assertContains(published_response, reverse('project_update_review_create', args=[published_update.pk]))
        self.assertContains(published_response, 'Publicado')
        self.assertContains(unpublished_response, 'Sin revisión del Comité')
        self.assertNotContains(unpublished_response, reverse('project_update_review_create', args=[unpublished_update.pk]))

        review = self.create_review(published_update)
        reviewed_response = self.client.get(reverse('project_update_detail', args=[published_update.pk]))

        self.assertNotContains(reviewed_response, reverse('project_update_review_create', args=[published_update.pk]))
        self.assertContains(reviewed_response, reverse('project_update_review_detail', args=[review.pk]))

    def test_review_has_no_operational_edit_or_delete_routes(self):
        with self.assertRaises(NoReverseMatch):
            reverse('project_update_review_update', args=[1])
        with self.assertRaises(NoReverseMatch):
            reverse('project_update_review_delete', args=[1])


class ProjectUpdateReviewAdminTests(TestCase):
    def setUp(self):
        self.request = RequestFactory().get('/admin/')
        self.request.user = create_user('review-admin')
        self.model_admin = ProjectUpdateReviewAdmin(ProjectUpdateReview, admin.site)

    def test_admin_disallows_creation_edits_and_deletion(self):
        self.assertFalse(self.model_admin.has_add_permission(self.request))
        self.assertFalse(self.model_admin.has_change_permission(self.request, ProjectUpdateReview()))
        self.assertFalse(self.model_admin.has_delete_permission(self.request, ProjectUpdateReview()))
