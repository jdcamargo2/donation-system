from django.test import TestCase
from django.urls import reverse

from apps.operations.forms import ProjectUpdateReviewForm
from apps.operations.models import Project, ProjectUpdate
from apps.operations.services import register_advance
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
