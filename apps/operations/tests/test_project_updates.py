from datetime import date

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from apps.operations.models import AuditLog, Project, ProjectUpdate
from apps.operations.services import (
    ProjectUpdateImmutableError,
    publish_project_update,
    register_advance,
    update_project_update,
)
from apps.operations.tests.helpers import create_project, create_user


class ProjectUpdateTests(TestCase):
    def setUp(self):
        self.user = create_user()
        self.project = create_project()
        self.project.status = Project.Status.ACTIVE
        self.project.save(update_fields=('status',))

    def create_draft(self, *, progress_percentage=25):
        # PRE: self.project está activo y self.user puede ser atribuido en auditoría.
        # POST: retorna un avance DRAFT persistido con fecha y progreso válidos.
        return register_advance(
            project_id=self.project.pk,
            title='Avance operativo',
            description='Trabajo ejecutado durante la jornada.',
            update_date=date(2026, 7, 12),
            progress_percentage=progress_percentage,
            created_by=self.user,
        )

    def test_new_project_update_is_draft(self):
        update = self.create_draft()

        self.assertEqual(update.status, ProjectUpdate.Status.DRAFT)
        self.assertEqual(update.progress_percentage, 25)
        self.assertTrue(AuditLog.objects.filter(
            entity_id=str(update.pk), action=AuditLog.Action.CREATED, user=self.user
        ).exists())

    def test_draft_can_be_edited(self):
        update = self.create_draft()

        edited = update_project_update(
            update_id=update.pk,
            project=self.project,
            title='Avance operativo corregido',
            description='Información operativa actualizada.',
            update_date=date(2026, 7, 11),
            progress_percentage=40,
            actor=self.user,
        )

        self.assertEqual(edited.title, 'Avance operativo corregido')
        self.assertEqual(edited.progress_percentage, 40)
        self.assertTrue(AuditLog.objects.filter(
            entity_id=str(update.pk), action=AuditLog.Action.UPDATED, user=self.user
        ).exists())

    def test_publish_changes_status_to_published_via_post(self):
        update = self.create_draft()
        self.client.force_login(self.user)

        get_response = self.client.get(reverse('project_update_publish', args=[update.pk]))
        post_response = self.client.post(reverse('project_update_publish', args=[update.pk]))
        update.refresh_from_db()

        self.assertEqual(get_response.status_code, 405)
        self.assertRedirects(post_response, reverse('project_update_detail', args=[update.pk]))
        self.assertEqual(update.status, ProjectUpdate.Status.PUBLISHED)
        self.assertTrue(AuditLog.objects.filter(
            entity_id=str(update.pk), action=AuditLog.Action.PUBLISHED, user=self.user
        ).exists())

    def test_published_project_update_cannot_be_edited(self):
        update = self.create_draft()
        publish_project_update(update.pk, self.user)

        with self.assertRaises(ProjectUpdateImmutableError):
            update_project_update(
                update_id=update.pk,
                project=self.project,
                title='Edición prohibida',
                description='No debe persistirse.',
                update_date=date(2026, 7, 12),
                progress_percentage=50,
                actor=self.user,
            )

        self.client.force_login(self.user)
        self.assertEqual(
            self.client.get(reverse('project_update_update', args=[update.pk])).status_code,
            403,
        )

    def test_invalid_progress_percentage_is_rejected(self):
        with self.assertRaises(ValidationError):
            self.create_draft(progress_percentage=101)
