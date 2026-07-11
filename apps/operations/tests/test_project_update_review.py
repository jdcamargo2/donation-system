from unittest.mock import patch

from django.contrib import admin
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import RequestFactory, TestCase
from django.urls import reverse

from apps.operations.admin import ProjectUpdateAdmin
from apps.operations.forms import ProjectUpdateReviewForm
from apps.operations.models import AuditLog, Project, ProjectUpdate
from apps.operations.services import (
    ProjectUpdateImmutableError,
    ensure_project_update_is_deletable,
    ensure_project_update_is_editable,
    register_advance,
    review_project_update,
)
from apps.operations.tests.helpers import create_project, create_user


class ProjectUpdateReviewTests(TestCase):
    def setUp(self):
        self.user = create_user()
        self.project = create_project()
        self.project.status = Project.Status.ACTIVE
        self.project.save()
        self.update = register_advance(
            project_id=self.project.pk,
            title='Avance pendiente',
            description='Listo para revisión.',
            created_by=self.user,
        )

    def test_review_view_get_returns_ok(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse('project_update_review', args=[self.update.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'web/project_update_review.html')
        self.assertContains(response, 'Guardar revisión')

    def test_review_form_rejects_pending_review_status(self):
        form = ProjectUpdateReviewForm(data={'status': ProjectUpdate.Status.PENDING_REVIEW, 'review_notes': ''})

        self.assertFalse(form.is_valid())
        self.assertIn('status', form.errors)

    def test_review_form_rejects_draft_status(self):
        form = ProjectUpdateReviewForm(data={'status': ProjectUpdate.Status.DRAFT, 'review_notes': ''})

        self.assertFalse(form.is_valid())
        self.assertIn('status', form.errors)

    def test_review_form_requires_rejection_reason(self):
        form = ProjectUpdateReviewForm(
            data={'status': ProjectUpdate.Status.REJECTED, 'review_notes': '  '}
        )

        self.assertFalse(form.is_valid())
        self.assertIn('review_notes', form.errors)

    def test_review_post_approves_update_and_redirects_to_project(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse('project_update_review', args=[self.update.pk]),
            data={'status': ProjectUpdate.Status.APPROVED, 'review_notes': 'Aprobado sin observaciones.'},
        )

        self.update.refresh_from_db()
        self.assertEqual(self.update.status, ProjectUpdate.Status.APPROVED)
        self.assertEqual(self.update.review_notes, 'Aprobado sin observaciones.')
        self.assertIsNotNone(self.update.reviewed_at)
        self.assertEqual(self.update.reviewed_by, self.user)
        self.assertRedirects(response, reverse('project_detail', args=[self.project.pk]))

    def test_review_post_rejects_update_and_saves_notes(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse('project_update_review', args=[self.update.pk]),
            data={'status': ProjectUpdate.Status.REJECTED, 'review_notes': 'Falta soporte.'},
        )

        self.update.refresh_from_db()
        self.assertEqual(self.update.status, ProjectUpdate.Status.REJECTED)
        self.assertEqual(self.update.review_notes, 'Falta soporte.')
        self.assertIsNotNone(self.update.reviewed_at)
        self.assertEqual(self.update.reviewed_by, self.user)
        self.assertRedirects(response, reverse('project_detail', args=[self.project.pk]))

    def test_project_detail_shows_review_link_for_pending_updates(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse('project_detail', args=[self.project.pk]))

        self.assertContains(response, 'Revisar')
        self.assertContains(response, reverse('project_update_review', args=[self.update.pk]))

    def test_project_update_detail_shows_review_link(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse('project_update_detail', args=[self.update.pk]))

        self.assertContains(response, 'Revisar avance')
        self.assertContains(response, reverse('project_update_review', args=[self.update.pk]))

    def test_draft_cannot_be_reviewed(self):
        self.update.status = ProjectUpdate.Status.DRAFT
        self.update.save(update_fields=('status',))

        with self.assertRaisesMessage(ValidationError, 'pendiente de revisión'):
            review_project_update(
                self.update.pk, self.user, ProjectUpdate.Status.APPROVED
            )

        self.update.refresh_from_db()
        self.assertEqual(self.update.status, ProjectUpdate.Status.DRAFT)
        self.assertFalse(AuditLog.objects.filter(entity_id=str(self.update.pk)).exists())

    def test_final_states_cannot_be_reviewed_twice_and_create_one_log(self):
        review_project_update(
            self.update.pk, self.user, ProjectUpdate.Status.APPROVED
        )

        with self.assertRaisesMessage(ValidationError, 'pendiente de revisión'):
            review_project_update(
                self.update.pk,
                self.user,
                ProjectUpdate.Status.REJECTED,
                'Intento posterior.',
            )

        self.update.refresh_from_db()
        self.assertEqual(self.update.status, ProjectUpdate.Status.APPROVED)
        self.assertEqual(
            AuditLog.objects.filter(
                entity_id=str(self.update.pk),
                action__in=(AuditLog.Action.VALIDATED, AuditLog.Action.REJECTED),
            ).count(),
            1,
        )

    def test_rejected_state_cannot_be_reviewed_twice(self):
        review_project_update(
            self.update.pk,
            self.user,
            ProjectUpdate.Status.REJECTED,
            'Evidencia insuficiente.',
        )

        with self.assertRaises(ValidationError):
            review_project_update(
                self.update.pk, self.user, ProjectUpdate.Status.APPROVED
            )

    def test_rejection_without_reason_does_not_mutate_or_audit(self):
        original = ProjectUpdate.objects.values().get(pk=self.update.pk)

        with self.assertRaisesMessage(ValidationError, 'razón del rechazo'):
            review_project_update(
                self.update.pk, self.user, ProjectUpdate.Status.REJECTED, ' '
            )

        self.assertEqual(ProjectUpdate.objects.values().get(pk=self.update.pk), original)
        self.assertFalse(AuditLog.objects.filter(entity_id=str(self.update.pk)).exists())

    def test_audit_failure_rolls_back_review(self):
        original = ProjectUpdate.objects.values().get(pk=self.update.pk)

        with patch('apps.operations.services.log_review', side_effect=RuntimeError('audit failed')):
            with self.assertRaises(RuntimeError):
                review_project_update(
                    self.update.pk, self.user, ProjectUpdate.Status.APPROVED
                )

        self.assertEqual(ProjectUpdate.objects.values().get(pk=self.update.pk), original)

    def test_success_preserves_material_fields_and_sets_review_metadata(self):
        material_fields = ('project_id', 'title', 'description', 'evidence')
        original_material = ProjectUpdate.objects.values(*material_fields).get(pk=self.update.pk)

        reviewed = review_project_update(
            self.update.pk, self.user, ProjectUpdate.Status.APPROVED
        )

        self.assertEqual(
            ProjectUpdate.objects.values(*material_fields).get(pk=self.update.pk),
            original_material,
        )
        self.assertEqual(reviewed.reviewed_by, self.user)
        self.assertIsNotNone(reviewed.reviewed_at)

    def test_pending_and_final_updates_cannot_be_edited_through_view(self):
        self.client.force_login(self.user)
        edit_url = reverse('project_update_update', args=[self.update.pk])

        self.assertEqual(self.client.get(edit_url).status_code, 403)
        review_project_update(self.update.pk, self.user, ProjectUpdate.Status.APPROVED)
        self.assertEqual(self.client.post(edit_url, data={}).status_code, 403)

    def test_detail_hides_material_actions_for_pending_and_final_states(self):
        self.client.force_login(self.user)
        detail_url = reverse('project_update_detail', args=[self.update.pk])

        pending_response = self.client.get(detail_url)
        self.assertNotContains(pending_response, reverse('project_update_update', args=[self.update.pk]))
        review_project_update(self.update.pk, self.user, ProjectUpdate.Status.APPROVED)
        final_response = self.client.get(detail_url)
        self.assertNotContains(final_response, reverse('project_update_update', args=[self.update.pk]))
        self.assertNotContains(final_response, reverse('project_update_delete', args=[self.update.pk]))
        self.assertNotContains(final_response, reverse('project_update_review', args=[self.update.pk]))
        self.assertContains(final_response, self.user.username)

    def test_final_updates_cannot_be_deleted_through_view_or_service_guard(self):
        self.client.force_login(self.user)
        review_project_update(self.update.pk, self.user, ProjectUpdate.Status.APPROVED)

        response = self.client.post(reverse('project_update_delete', args=[self.update.pk]))

        self.assertEqual(response.status_code, 403)
        self.assertTrue(ProjectUpdate.objects.filter(pk=self.update.pk).exists())
        self.update.refresh_from_db()
        with self.assertRaises(ProjectUpdateImmutableError):
            ensure_project_update_is_deletable(self.update)

    def test_post_review_requires_change_permission(self):
        restricted_user = create_user(username='restricted-reviewer')
        restricted_user.is_superuser = False
        restricted_user.is_staff = False
        restricted_user.save(update_fields=('is_superuser', 'is_staff'))
        self.client.force_login(restricted_user)

        response = self.client.post(
            reverse('project_update_review', args=[self.update.pk]),
            data={'status': ProjectUpdate.Status.APPROVED, 'review_notes': ''},
        )

        self.assertEqual(response.status_code, 403)
        self.update.refresh_from_db()
        self.assertEqual(self.update.status, ProjectUpdate.Status.PENDING_REVIEW)

    def test_review_get_does_not_modify_state(self):
        self.client.force_login(self.user)
        original = ProjectUpdate.objects.values().get(pk=self.update.pk)

        self.client.get(reverse('project_update_review', args=[self.update.pk]))

        self.assertEqual(ProjectUpdate.objects.values().get(pk=self.update.pk), original)

    def test_admin_marks_pending_and_final_material_fields_readonly(self):
        model_admin = ProjectUpdateAdmin(ProjectUpdate, admin.site)
        request = RequestFactory().get('/admin/')
        request.user = self.user

        pending_fields = model_admin.get_readonly_fields(request, self.update)
        self.assertTrue({'project', 'title', 'description', 'evidence', 'status'}.issubset(pending_fields))
        review_project_update(self.update.pk, self.user, ProjectUpdate.Status.APPROVED)
        self.update.refresh_from_db()
        self.assertFalse(model_admin.has_delete_permission(request, self.update))
        with self.assertRaises(ProjectUpdateImmutableError):
            model_admin.save_model(request, self.update, form=None, change=True)

    def test_draft_is_materially_editable(self):
        self.update.status = ProjectUpdate.Status.DRAFT
        self.update.save(update_fields=('status',))

        ensure_project_update_is_editable(self.update)
