from datetime import date

from django.contrib import admin
from django.core.exceptions import ValidationError
from django.test import RequestFactory, TestCase
from django.urls import reverse

from apps.operations.admin import ProjectUpdateAdmin
from apps.operations.forms import ProjectUpdateForProjectForm, ProjectUpdateForm
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
        self.reported_by = create_user('institutional-reporter')
        self.editor = create_user('technical-editor')
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

    def test_register_advance_keeps_technical_creator_and_institutional_reporter_separate(self):
        update = register_advance(
            project_id=self.project.pk,
            title='Avance con responsable institucional',
            description='Registro atribuible a una persona responsable.',
            created_by=self.user,
            reported_by=self.reported_by,
        )

        self.assertEqual(update.created_by, self.user)
        self.assertEqual(update.reported_by, self.reported_by)
        self.assertTrue(AuditLog.objects.filter(
            entity_id=str(update.pk), action=AuditLog.Action.CREATED, user=self.user
        ).exists())

    def test_register_advance_does_not_assign_creator_as_reporter(self):
        update = self.create_draft()

        self.assertEqual(update.created_by, self.user)
        self.assertIsNone(update.reported_by)

    def test_register_advance_rejects_non_active_project(self):
        rejected_statuses = (
            Project.Status.PLANNED,
            Project.Status.SUSPENDED,
            Project.Status.CLOSED,
            Project.Status.ANNULLED,
        )
        for status in rejected_statuses:
            with self.subTest(status=status):
                project = create_project(code=f'PRJ-UPDATE-{status}')
                project.status = status
                project.save(update_fields=('status',))

                with self.assertRaisesMessage(ValidationError, 'admiten gastos y avances'):
                    register_advance(
                        project_id=project.pk,
                        title='Avance no permitido',
                        description='No debe guardarse para un proyecto no activo.',
                        created_by=self.user,
                    )

                self.assertFalse(project.updates.exists())

    def test_project_update_forms_include_reported_by_and_exclude_created_by(self):
        for form_class in (ProjectUpdateForm, ProjectUpdateForProjectForm):
            with self.subTest(form_class=form_class.__name__):
                form = form_class()

                self.assertIn('reported_by', form.fields)
                self.assertEqual(form.fields['reported_by'].label, 'Responsable institucional')
                self.assertNotIn('created_by', form.fields)

    def test_create_for_project_view_keeps_creator_and_reporter_separate(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse('project_update_create_for_project', args=[self.project.pk]),
            data={
                'title': 'Avance desde formulario',
                'description': 'El formulario transmite el responsable seleccionado.',
                'update_date': '2026-07-12',
                'progress_percentage': '30',
                'reported_by': self.reported_by.pk,
            },
        )
        update = ProjectUpdate.objects.get(title='Avance desde formulario')

        self.assertRedirects(response, reverse('project_detail', args=[self.project.pk]))
        self.assertEqual(update.created_by, self.user)
        self.assertEqual(update.reported_by, self.reported_by)

    def test_detail_shows_reporter_or_neutral_value(self):
        update = self.create_draft()
        self.client.force_login(self.user)

        response_without_reporter = self.client.get(reverse('project_update_detail', args=[update.pk]))

        self.assertContains(response_without_reporter, 'Responsable institucional')
        self.assertContains(response_without_reporter, '—')

        update.reported_by = self.reported_by
        update.save(update_fields=('reported_by',))
        response_with_reporter = self.client.get(reverse('project_update_detail', args=[update.pk]))
        project_response = self.client.get(reverse('project_detail', args=[self.project.pk]))

        self.assertContains(response_with_reporter, self.reported_by.get_username())
        self.assertContains(project_response, f'Responsable institucional: {self.reported_by.get_username()}')

    def test_draft_can_be_edited(self):
        update = self.create_draft()

        edited = update_project_update(
            update_id=update.pk,
            project=self.project,
            title='Avance operativo corregido',
            description='Información operativa actualizada.',
            update_date=date(2026, 7, 11),
            progress_percentage=40,
            reported_by=self.reported_by,
            actor=self.editor,
        )

        self.assertEqual(edited.title, 'Avance operativo corregido')
        self.assertEqual(edited.progress_percentage, 40)
        self.assertEqual(edited.reported_by, self.reported_by)
        self.assertTrue(AuditLog.objects.filter(
            entity_id=str(update.pk), action=AuditLog.Action.UPDATED, user=self.editor
        ).exists())

    def test_draft_cannot_be_reassigned_to_non_active_project(self):
        update = self.create_draft()
        target_project = create_project(code='PRJ-UPDATE-SUSPENDED')
        target_project.status = Project.Status.SUSPENDED
        target_project.save(update_fields=('status',))

        with self.assertRaisesMessage(ValidationError, 'admiten gastos y avances'):
            update_project_update(
                update_id=update.pk,
                project=target_project,
                title='Reasignación no permitida',
                description='No debe persistirse en un proyecto suspendido.',
                update_date=date(2026, 7, 12),
                progress_percentage=40,
                reported_by=self.reported_by,
                actor=self.editor,
            )

        update.refresh_from_db()
        self.assertEqual(update.project, self.project)
        self.assertEqual(update.title, 'Avance operativo')

    def test_draft_update_view_changes_reported_by(self):
        update = self.create_draft()
        self.client.force_login(self.editor)

        response = self.client.post(
            reverse('project_update_update', args=[update.pk]),
            data={
                'project': self.project.pk,
                'title': update.title,
                'description': update.description,
                'update_date': update.update_date.isoformat(),
                'progress_percentage': update.progress_percentage,
                'reported_by': self.reported_by.pk,
            },
        )
        update.refresh_from_db()

        self.assertRedirects(response, reverse('project_update_list'))
        self.assertEqual(update.reported_by, self.reported_by)
        self.assertTrue(AuditLog.objects.filter(
            entity_id=str(update.pk), action=AuditLog.Action.UPDATED, user=self.editor
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

    def test_publish_rejects_project_no_longer_active(self):
        rejected_statuses = (
            Project.Status.SUSPENDED,
            Project.Status.CLOSED,
            Project.Status.ANNULLED,
        )
        for status in rejected_statuses:
            with self.subTest(status=status):
                update = self.create_draft()
                self.project.status = status
                self.project.save(update_fields=('status',))

                with self.assertRaisesMessage(ValidationError, 'admiten gastos y avances'):
                    publish_project_update(update.pk, self.user)

                update.refresh_from_db()
                self.assertEqual(update.status, ProjectUpdate.Status.DRAFT)
                self.project.status = Project.Status.ACTIVE
                self.project.save(update_fields=('status',))

    def test_published_project_update_cannot_be_edited(self):
        update = self.create_draft()
        update.reported_by = self.user
        update.save(update_fields=('reported_by',))
        publish_project_update(update.pk, self.user)

        with self.assertRaises(ProjectUpdateImmutableError):
            update_project_update(
                update_id=update.pk,
                project=self.project,
                title='Edición prohibida',
                description='No debe persistirse.',
                update_date=date(2026, 7, 12),
                progress_percentage=50,
                reported_by=self.reported_by,
                actor=self.user,
            )

        update.refresh_from_db()
        self.assertEqual(update.reported_by, self.user)

        self.client.force_login(self.user)
        self.assertEqual(
            self.client.get(reverse('project_update_update', args=[update.pk])).status_code,
            403,
        )

    def test_invalid_progress_percentage_is_rejected(self):
        with self.assertRaises(ValidationError):
            self.create_draft(progress_percentage=101)

    def test_admin_preserves_technical_creator_and_opens_published_update(self):
        model_admin = ProjectUpdateAdmin(ProjectUpdate, admin.site)
        request = RequestFactory().post('/admin/')
        request.user = self.editor
        update = ProjectUpdate(
            project=self.project,
            title='Avance creado desde administración',
            description='Debe conservar la atribución técnica.',
        )

        model_admin.save_model(request, update, form=None, change=False)

        self.assertEqual(update.created_by, self.editor)
        self.assertIn('created_by', model_admin.get_readonly_fields(request, update))
        self.assertNotIn('reported_by', model_admin.get_readonly_fields(request, update))

        update.created_by = self.reported_by
        update.reported_by = self.reported_by
        model_admin.save_model(request, update, form=None, change=True)
        update.refresh_from_db()

        self.assertEqual(update.created_by, self.editor)
        self.assertEqual(update.reported_by, self.reported_by)

        publish_project_update(update.pk, self.editor)
        update.refresh_from_db()
        readonly_fields = model_admin.get_readonly_fields(request, update)
        self.assertIn('reported_by', readonly_fields)
        self.assertNotIn('evidence', readonly_fields)

        self.client.force_login(self.user)
        response = self.client.get(reverse('admin:operations_projectupdate_change', args=[update.pk]))

        self.assertEqual(response.status_code, 200)
