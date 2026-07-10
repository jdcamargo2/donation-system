from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from apps.operations.models import Project, ProjectUpdate
from apps.operations.services import register_advance, review_project_update
from apps.operations.tests.helpers import create_project, create_user


class ProjectUpdateTests(TestCase):
    def setUp(self):
        self.user = create_user()
        self.project = create_project()
        self.project.status = Project.Status.ACTIVE
        self.project.save()

    def test_project_update_can_be_created_for_project(self):
        update = ProjectUpdate(
            project=self.project,
            title='Avance técnico',
            description='Se completó la primera revisión técnica.',
            status=ProjectUpdate.Status.PENDING_REVIEW,
            created_by=self.user,
        )
        update.full_clean()
        update.save()

        self.assertEqual(update.project, self.project)
        self.assertEqual(self.project.updates.count(), 1)
        self.assertEqual(str(update), f'{self.project.code} - Avance técnico')

    def test_register_advance_creates_pending_review_update(self):
        update = register_advance(
            project_id=self.project.pk,
            title='Evidencia documental',
            description='Se cargó evidencia del avance.',
            created_by=self.user,
        )

        self.assertEqual(update.status, ProjectUpdate.Status.PENDING_REVIEW)
        self.assertEqual(update.created_by, self.user)
        self.assertEqual(update.project, self.project)

    def test_register_advance_rejects_empty_title(self):
        with self.assertRaises(ValidationError):
            register_advance(
                project_id=self.project.pk,
                title='',
                description='Descripción válida.',
                created_by=self.user,
            )

    def test_register_advance_rejects_empty_description(self):
        with self.assertRaises(ValidationError):
            register_advance(
                project_id=self.project.pk,
                title='Título válido',
                description='',
                created_by=self.user,
            )

    def test_review_project_update_approves_update(self):
        update = register_advance(
            project_id=self.project.pk,
            title='Avance para aprobación',
            description='Pendiente de revisión.',
            created_by=self.user,
        )

        reviewed = review_project_update(
            update_id=update.pk,
            reviewer=self.user,
            status=ProjectUpdate.Status.APPROVED,
            notes='Aprobado.',
        )

        self.assertEqual(reviewed.status, ProjectUpdate.Status.APPROVED)
        self.assertEqual(reviewed.reviewed_by, self.user)
        self.assertIsNotNone(reviewed.reviewed_at)
        self.assertEqual(reviewed.review_notes, 'Aprobado.')

    def test_review_project_update_rejects_update(self):
        update = register_advance(
            project_id=self.project.pk,
            title='Avance para rechazo',
            description='Pendiente de revisión.',
            created_by=self.user,
        )

        reviewed = review_project_update(
            update_id=update.pk,
            reviewer=self.user,
            status=ProjectUpdate.Status.REJECTED,
            notes='Falta evidencia.',
        )

        self.assertEqual(reviewed.status, ProjectUpdate.Status.REJECTED)
        self.assertEqual(reviewed.reviewed_by, self.user)
        self.assertIsNotNone(reviewed.reviewed_at)

    def test_review_project_update_rejects_invalid_status(self):
        update = register_advance(
            project_id=self.project.pk,
            title='Avance inválido',
            description='Pendiente de revisión.',
            created_by=self.user,
        )

        with self.assertRaises(ValidationError):
            review_project_update(
                update_id=update.pk,
                reviewer=self.user,
                status=ProjectUpdate.Status.PENDING_REVIEW,
            )

    def test_review_project_update_rejects_second_review_of_final_state(self):
        update = register_advance(
            project_id=self.project.pk,
            title='Avance revisado',
            description='Pendiente de revisión.',
            created_by=self.user,
        )
        review_project_update(
            update_id=update.pk,
            reviewer=self.user,
            status=ProjectUpdate.Status.APPROVED,
            notes='Primera revisión.',
        )

        with self.assertRaises(ValidationError):
            review_project_update(
                update_id=update.pk,
                reviewer=self.user,
                status=ProjectUpdate.Status.REJECTED,
                notes='Segunda revisión.',
            )

    def test_project_detail_view_includes_project_updates(self):
        update = register_advance(
            project_id=self.project.pk,
            title='Avance visible',
            description='Debe aparecer en contexto.',
            created_by=self.user,
        )
        self.client.force_login(self.user)

        response = self.client.get(reverse('project_detail', args=[self.project.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertIn('project_updates', response.context)
        self.assertIn(update, response.context['project_updates'])
        self.assertTemplateUsed(response, 'web/project_detail.html')
        self.assertContains(response, 'Avances del proyecto')
        self.assertContains(response, 'Registrar avance')
        self.assertContains(response, reverse('project_update_create_for_project', args=[self.project.pk]))

    def test_project_update_routes_are_available_for_authenticated_user(self):
        self.client.force_login(self.user)

        list_response = self.client.get(reverse('project_update_list'))
        create_response = self.client.get(reverse('project_update_create'))

        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(create_response.status_code, 200)

    def test_project_update_create_for_project_get_hides_project_field(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse('project_update_create_for_project', args=[self.project.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'web/project_update_form.html')
        self.assertContains(response, self.project.name)
        self.assertNotContains(response, 'name="project"')
        self.assertContains(response, 'name="title"')
        self.assertContains(response, 'name="description"')
        self.assertContains(response, 'name="evidence"')

    def test_project_update_create_for_project_post_creates_update_and_redirects(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse('project_update_create_for_project', args=[self.project.pk]),
            data={
                'title': 'Avance desde proyecto',
                'description': 'Registrado desde el detalle del proyecto.',
                'evidence': '',
            },
        )

        update = ProjectUpdate.objects.get(title='Avance desde proyecto')
        self.assertEqual(update.project, self.project)
        self.assertEqual(update.status, ProjectUpdate.Status.PENDING_REVIEW)
        self.assertEqual(update.created_by, self.user)
        self.assertRedirects(response, reverse('project_detail', args=[self.project.pk]))

    def test_project_update_create_for_missing_project_returns_404(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse('project_update_create_for_project', args=[9999]))

        self.assertEqual(response.status_code, 404)
