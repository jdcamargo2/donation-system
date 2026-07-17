from datetime import date
from pathlib import Path

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import RequestFactory, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.operations.admin import ProjectUpdateAdmin
from apps.operations.forms import ProjectUpdateForProjectForm, ProjectUpdateForm
from apps.operations.models import AuditLog, Project, ProjectUpdate, ProjectUpdateAttachment
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
            reported_by=self.reported_by,
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

    def test_register_advance_requires_explicit_responsible_person(self):
        with self.assertRaisesMessage(ValidationError, 'Debe seleccionar una persona responsable'):
            register_advance(
                project_id=self.project.pk,
                title='Avance sin responsable',
                description='No debe crear un avance nuevo sin atribución.',
                created_by=self.user,
            )

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
                self.assertEqual(form.fields['reported_by'].label, 'Persona responsable del avance')
                self.assertTrue(form.fields['reported_by'].required)
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
        update = ProjectUpdate.objects.create(
            project=self.project,
            title='Avance histórico sin responsable',
            description='Registro anterior que permanece editable.',
        )
        self.client.force_login(self.user)

        response_without_reporter = self.client.get(reverse('project_update_detail', args=[update.pk]))

        self.assertContains(response_without_reporter, 'Información de registro')
        self.assertContains(response_without_reporter, '—')

        update.reported_by = self.reported_by
        update.save(update_fields=('reported_by',))
        response_with_reporter = self.client.get(reverse('project_update_detail', args=[update.pk]))
        project_response = self.client.get(reverse('project_detail', args=[self.project.pk]))

        self.assertContains(response_with_reporter, self.reported_by.get_username())
        self.assertContains(project_response, f'Persona responsable del avance: {self.reported_by.get_username()}')

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

    def test_form_excludes_inactive_and_users_without_project_update_permissions(self):
        User = get_user_model()
        inactive = User.objects.create_user(username='inactive-reporter', password='pass-12345', is_active=False)
        no_permission = User.objects.create_user(username='no-update-permission', password='pass-12345')
        direct_permission = User.objects.create_user(username='direct-update-permission', password='pass-12345')
        direct_permission.user_permissions.add(Permission.objects.get(
            content_type__app_label='operations', codename='add_projectupdate'
        ))

        form = ProjectUpdateForm()

        self.assertNotIn(inactive, form.fields['reported_by'].queryset)
        self.assertNotIn(no_permission, form.fields['reported_by'].queryset)
        self.assertIn(direct_permission, form.fields['reported_by'].queryset)

    def test_service_rejects_inactive_or_ineligible_responsible_person(self):
        User = get_user_model()
        inactive = User.objects.create_user(username='inactive-service-reporter', password='pass-12345', is_active=False)
        no_permission = User.objects.create_user(username='ineligible-service-reporter', password='pass-12345')

        for reporter in (inactive, no_permission):
            with self.subTest(reporter=reporter.username):
                with self.assertRaisesMessage(ValidationError, 'permisos operativos sobre avances'):
                    register_advance(
                        project_id=self.project.pk,
                        title='Avance con responsable no elegible',
                        description='La validación de servicio evita el bypass del formulario.',
                        created_by=self.user,
                        reported_by=reporter,
                    )

    def test_historical_draft_without_responsible_person_cannot_publish_or_audit(self):
        update = ProjectUpdate.objects.create(
            project=self.project,
            title='Avance histórico sin responsable para publicar',
            description='Debe exigir atribución antes de publicar.',
            created_by=self.user,
        )

        with self.assertRaisesMessage(ValidationError, 'Debe seleccionar una persona responsable'):
            publish_project_update(update.pk, self.user)

        update.refresh_from_db()
        self.assertEqual(update.status, ProjectUpdate.Status.DRAFT)
        self.assertFalse(AuditLog.objects.filter(
            entity_id=str(update.pk), action=AuditLog.Action.PUBLISHED
        ).exists())

    def test_changing_responsible_person_creates_safe_audit_summary(self):
        update = self.create_draft()
        replacement = create_user('replacement-reporter')
        replacement.email = 'replacement@example.com'
        replacement.save(update_fields=('email',))

        update_project_update(
            update_id=update.pk,
            project=self.project,
            title=update.title,
            description=update.description,
            update_date=update.update_date,
            progress_percentage=update.progress_percentage,
            reported_by=replacement,
            actor=self.editor,
        )

        audit = AuditLog.objects.filter(entity_id=str(update.pk), action=AuditLog.Action.UPDATED).latest('created_at')
        self.assertIn('Atribución de la persona responsable', audit.summary)
        self.assertNotIn(replacement.username, audit.summary)
        self.assertNotIn(replacement.email, audit.summary)

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
            reported_by=self.reported_by,
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


class ProjectUpdateDetailTests(TestCase):
    def setUp(self):
        self.user = create_user('detail-update-manager')
        self.reported_by = create_user('detail-update-reporter')
        self.project = create_project(code='PRJ-UPDATE-DETAIL', name='Proyecto de detalle')
        self.project.status = Project.Status.ACTIVE
        self.project.save(update_fields=('status',))
        self.project_update = register_advance(
            project_id=self.project.pk,
            title='Avance compacto',
            description='Descripción completa para verificar la composición del detalle.',
            update_date=date(2026, 7, 17),
            progress_percentage=73,
            created_by=self.user,
            reported_by=self.reported_by,
        )
        self.client.force_login(self.user)

    def test_detail_compacts_identity_removes_progress_and_keeps_confirmed_post_actions(self):
        attachment = ProjectUpdateAttachment.objects.create(
            project_update=self.project_update,
            title='Evidencia operativa',
            file='project_update_attachments/evidencia.pdf',
            uploaded_by=self.user,
        )

        response = self.client.get(reverse('project_update_detail', args=[self.project_update.pk]))
        content = response.content.decode()

        self.assertContains(response, 'Avance de proyecto')
        self.assertContains(response, self.project_update.title)
        self.assertContains(response, self.project_update.get_status_display())
        self.assertContains(response, '17 de julio de 2026')
        self.assertContains(response, self.reported_by.get_username())
        self.assertContains(response, self.project.code)
        self.assertContains(response, self.project.name)
        self.assertContains(response, reverse('project_detail', args=[self.project.pk]))
        self.assertContains(response, 'Descripción del avance')
        self.assertContains(response, self.project_update.description)
        self.assertContains(response, 'Información de registro')
        self.assertNotContains(response, 'Progreso')
        self.assertNotContains(response, f'>{self.project_update.progress_percentage}%<')
        detail_template = Path('templates/web/project_update_detail.html').read_text()
        self.assertNotIn('progress_percentage', detail_template)
        self.assertEqual(detail_template.count('{{ object.title }}'), 1)
        self.assertEqual(detail_template.count('{{ object.project.code }}'), 1)
        self.assertEqual(detail_template.count('{{ object.get_status_display }}'), 1)

        publish_url = reverse('project_update_publish', args=[self.project_update.pk])
        delete_url = reverse('project_update_delete', args=[self.project_update.pk])
        attachment_delete_url = reverse('project_update_attachment_delete', args=[attachment.pk])
        self.assertIn(f'id="project-update-publish-form" method="post" action="{publish_url}"', content)
        self.assertIn(f'id="project-update-delete-form" method="post" action="{delete_url}"', content)
        self.assertIn(
            f'id="project-update-attachment-delete-form-{attachment.pk}" method="post" action="{attachment_delete_url}"',
            content,
        )
        self.assertContains(response, 'data-confirm-title="¿Publicar este avance?"')
        self.assertContains(response, 'data-confirm-title="¿Eliminar este avance?"')
        self.assertContains(response, 'data-confirm-title="¿Eliminar este archivo?"')
        self.assertContains(response, 'web/js/confirm_actions.js')
        self.assertGreaterEqual(content.count('name="csrfmiddlewaretoken"'), 3)

    def test_detail_action_visibility_follows_status_and_permissions(self):
        detail_url = reverse('project_update_detail', args=[self.project_update.pk])
        edit_url = reverse('project_update_update', args=[self.project_update.pk])
        attachment_url = reverse('project_update_attachment_create', args=[self.project_update.pk])
        delete_url = reverse('project_update_delete', args=[self.project_update.pk])
        publish_url = reverse('project_update_publish', args=[self.project_update.pk])

        editor = get_user_model().objects.create_user(username='detail-editor', password='pass-12345')
        editor.user_permissions.add(*Permission.objects.filter(codename__in=(
            'view_projectupdate', 'change_projectupdate',
        )))
        self.client.force_login(editor)
        editor_response = self.client.get(detail_url)
        self.assertContains(editor_response, edit_url)
        self.assertNotContains(editor_response, attachment_url)
        self.assertNotContains(editor_response, delete_url)
        self.assertNotContains(editor_response, publish_url)

        self.client.force_login(self.user)
        publish_project_update(self.project_update.pk, self.user)
        published_response = self.client.get(detail_url)
        self.assertNotContains(published_response, edit_url)
        self.assertNotContains(published_response, attachment_url)
        self.assertNotContains(published_response, delete_url)
        self.assertNotContains(published_response, publish_url)
        self.assertNotContains(published_response, 'Más acciones del avance')

    def test_detail_body_uses_one_surface_with_four_integrated_sections(self):
        response = self.client.get(reverse('project_update_detail', args=[self.project_update.pk]))
        content = response.content.decode()

        self.assertEqual(content.count('class="ops-project-update-body"'), 1)
        body_start = content.index('<article class="ops-project-update-body">')
        body_end = content.index('</article>', body_start) + len('</article>')
        body = content[body_start:body_end]
        self.assertEqual(body.count('ops-project-update-section'), 4)
        self.assertIn(
            'class="ops-project-update-section ops-project-update-metadata"',
            body,
        )

        attachments_start = body.index('aria-labelledby="project-update-attachments-title"')
        metadata_start = body.index('class="ops-project-update-section ops-project-update-metadata"')
        attachments_section = body[attachments_start:metadata_start]
        self.assertIn('Este avance no tiene adjuntos.', attachments_section)
        self.assertNotIn('card', attachments_section)
        self.assertNotIn('list-group', attachments_section)

        stylesheet = Path('static/web/css/sigedon.css').read_text()
        self.assertNotIn(
            '.ops-project-update-detail-description,\n'
            '.ops-project-update-detail-review {\n'
            '    border: 1px solid var(--ops-border);',
            stylesheet,
        )
        self.assertIn('.ops-project-update-body {', stylesheet)
        self.assertIn('.ops-project-update-section + .ops-project-update-section {', stylesheet)

    def test_detail_prefetches_attachments_without_per_attachment_queries(self):
        projects = []
        for attachment_count in (1, 5):
            project = create_project(code=f'PRJ-UPDATE-DETAIL-QUERY-{attachment_count}')
            update = ProjectUpdate.objects.create(
                project=project,
                title=f'Avance con {attachment_count} adjuntos',
                description='Consulta de adjuntos en el detalle.',
                created_by=self.user,
                reported_by=self.user,
            )
            ProjectUpdateAttachment.objects.bulk_create([
                ProjectUpdateAttachment(
                    project_update=update,
                    title=f'Adjunto {index}',
                    file=f'project_update_attachments/query-{attachment_count}-{index}.pdf',
                    uploaded_by=self.user,
                )
                for index in range(attachment_count)
            ])
            projects.append(update)

        with CaptureQueriesContext(connection) as one_attachment_queries:
            self.client.get(reverse('project_update_detail', args=[projects[0].pk]))
        with CaptureQueriesContext(connection) as five_attachment_queries:
            self.client.get(reverse('project_update_detail', args=[projects[1].pk]))

        self.assertEqual(len(one_attachment_queries), len(five_attachment_queries))


class ProjectUpdateChunkTests(TestCase):
    def setUp(self):
        self.user = create_user('chunk-manager')
        self.project = create_project(code='PRJ-UPDATE-CHUNKS')
        self.project.status = Project.Status.ACTIVE
        self.project.save(update_fields=('status',))
        self.client.force_login(self.user)

    def create_updates(self, count, *, project=None, status=ProjectUpdate.Status.DRAFT):
        # PRE: count is non-negative and project is persisted when provided.
        # POST: returns count updates whose created_at values are equal for pk tie-break tests.
        target_project = project or self.project
        updates = [
            ProjectUpdate.objects.create(
                project=target_project,
                title=f'Avance por lote {status}-{target_project.pk}-{index}',
                description='Contenido paginado sin cargar toda la colección.',
                status=status,
                created_by=self.user,
                reported_by=self.user,
            )
            for index in range(count)
        ]
        if updates:
            fixed_created_at = timezone.now()
            ProjectUpdate.objects.filter(pk__in=[update.pk for update in updates]).update(
                created_at=fixed_created_at
            )
            for update in updates:
                update.created_at = fixed_created_at
        return updates

    def test_chunks_do_not_overlap_and_preserve_complete_stable_order(self):
        updates = self.create_updates(11)
        detail_response = self.client.get(
            reverse('project_detail', args=[self.project.pk])
        )
        chunk_url = reverse('project_update_chunk', args=[self.project.pk])
        second_response = self.client.get(f'{chunk_url}?page=2')
        last_response = self.client.get(f'{chunk_url}?page=3')

        first_ids = [
            update.pk for update in detail_response.context['recent_project_updates']
        ]
        second_ids = [
            update.pk for update in second_response.context['project_updates']
        ]
        last_ids = [
            update.pk for update in last_response.context['project_updates']
        ]

        self.assertEqual(len(first_ids), 5)
        self.assertEqual(len(second_ids), 5)
        self.assertEqual(len(last_ids), 1)
        self.assertFalse(set(first_ids) & set(second_ids))
        self.assertFalse(set(second_ids) & set(last_ids))
        self.assertEqual(
            first_ids + second_ids + last_ids,
            [update.pk for update in reversed(updates)],
        )
        self.assertContains(second_response, 'Ver más avances', count=1)
        self.assertContains(second_response, 'hx-target="this"')
        self.assertContains(second_response, 'hx-swap="outerHTML"')
        self.assertNotContains(last_response, 'Ver más avances')

    def test_chunk_is_partial_html_with_shared_items_and_fallback_link(self):
        self.create_updates(6)
        chunk_url = reverse('project_update_chunk', args=[self.project.pk])

        response = self.client.get(chunk_url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'web/includes/project_update_chunk.html')
        self.assertTemplateUsed(response, 'web/includes/project_update_items.html')
        self.assertTemplateUsed(response, 'web/includes/project_update_item.html')
        self.assertNotContains(response, '<html')
        self.assertNotContains(response, 'ops-page-header')
        self.assertContains(response, f'href="{chunk_url}?page=2"')
        self.assertContains(response, f'hx-get="{chunk_url}?page=2"')
        self.assertContains(response, 'aria-controls="project-update-list"')
        self.assertContains(response, 'Cargando avances…')

    def test_chunk_uses_same_visibility_rules_as_project_detail(self):
        published_updates = self.create_updates(
            6,
            status=ProjectUpdate.Status.PUBLISHED,
        )
        draft_updates = self.create_updates(3)
        viewer = get_user_model().objects.create_user(
            username='chunk-project-viewer',
            password='pass-12345',
        )
        viewer.user_permissions.add(Permission.objects.get(codename='view_project'))
        self.client.force_login(viewer)
        chunk_url = reverse('project_update_chunk', args=[self.project.pk])

        detail_response = self.client.get(
            reverse('project_detail', args=[self.project.pk])
        )
        chunk_response = self.client.get(chunk_url)

        detail_ids = [
            update.pk for update in detail_response.context['recent_project_updates']
        ]
        chunk_ids = [
            update.pk for update in chunk_response.context['project_updates']
        ]
        self.assertEqual(chunk_ids, detail_ids)
        self.assertEqual(chunk_ids, [update.pk for update in reversed(published_updates[-5:])])
        for draft in draft_updates:
            self.assertNotContains(chunk_response, draft.title)

    def test_chunk_requires_authentication_permission_and_existing_project(self):
        chunk_url = reverse('project_update_chunk', args=[self.project.pk])
        self.client.logout()

        anonymous_response = self.client.get(chunk_url)

        self.assertEqual(anonymous_response.status_code, 302)
        no_permission = get_user_model().objects.create_user(
            username='chunk-no-permission',
            password='pass-12345',
        )
        self.client.force_login(no_permission)
        self.assertEqual(self.client.get(chunk_url).status_code, 403)
        self.client.force_login(self.user)
        self.assertEqual(self.client.post(chunk_url).status_code, 405)
        missing_url = reverse('project_update_chunk', args=[self.project.pk + 9999])
        self.assertEqual(self.client.get(missing_url).status_code, 404)

    def test_chunk_does_not_add_queries_per_update(self):
        projects = []
        for count in (1, 5):
            project = create_project(code=f'PRJ-UPDATE-CHUNK-QUERY-{count}')
            self.create_updates(count, project=project)
            projects.append(project)

        with CaptureQueriesContext(connection) as one_update_queries:
            self.client.get(reverse('project_update_chunk', args=[projects[0].pk]))
        with CaptureQueriesContext(connection) as five_update_queries:
            self.client.get(reverse('project_update_chunk', args=[projects[1].pk]))

        self.assertEqual(len(one_update_queries), len(five_update_queries))
