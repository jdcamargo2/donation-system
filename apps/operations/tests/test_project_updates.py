from datetime import date
from pathlib import Path

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
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

    def create_unpublished(self):
        # PRE: self.project está activo y self.user puede ser atribuido en auditoría.
        # POST: retorna un avance UNPUBLISHED persistido con fecha válida.
        return register_advance(
            project_id=self.project.pk,
            title='Avance operativo',
            description='Trabajo ejecutado durante la jornada.',
            update_date=date(2026, 7, 12),
            created_by=self.user,
            reported_by=self.reported_by,
        )

    def test_new_project_update_is_unpublished(self):
        update = self.create_unpublished()

        self.assertEqual(update.status, ProjectUpdate.Status.UNPUBLISHED)
        audit = AuditLog.objects.get(
            entity_id=str(update.pk), action=AuditLog.Action.CREATED, user=self.user
        )
        self.assertIn('no publicado', audit.summary.lower())
        self.assertNotIn('borrador', audit.summary.lower())

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

    def test_register_advance_rejects_closed_project(self):
        project = create_project(code='PRJ-UPDATE-CLOSED')
        project.status = Project.Status.CLOSED
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
                self.assertNotIn('progress_percentage', form.fields)

    def test_project_update_forms_expose_multiple_attachments_contract(self):
        from apps.operations.forms import MultipleFileField

        help_text = 'Puede seleccionar varios archivos a la vez.'
        for form_class in (ProjectUpdateForm, ProjectUpdateForProjectForm):
            with self.subTest(form_class=form_class.__name__):
                form = form_class()
                field = form.fields['attachments']

                self.assertIsInstance(field, MultipleFileField)
                self.assertFalse(field.required)
                self.assertEqual(str(field.help_text), help_text)
                self.assertTrue(field.widget.allow_multiple_selected)
                self.assertEqual(field.widget.attrs.get('data-file-upload'), 'multiple')
                self.assertEqual(field.widget.attrs.get('data-file-upload-preview'), 'true')
                rendered = str(field.widget.render('attachments', None))
                self.assertIn('multiple', rendered)
                self.assertIn('data-file-upload-preview="true"', rendered)
                self.assertIn(help_text, form.as_p())

    def test_project_update_create_pages_render_file_upload_preview_contract(self):
        self.client.force_login(self.user)
        help_text = 'Puede seleccionar varios archivos a la vez.'
        pages = (
            ('project_update_create', reverse('project_update_create')),
            (
                'project_update_create_for_project',
                reverse('project_update_create_for_project', args=[self.project.pk]),
            ),
        )

        for label, url in pages:
            with self.subTest(page=label):
                response = self.client.get(url)

                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'data-file-upload-preview')
                self.assertContains(response, 'data-file-upload-list')
                self.assertContains(response, 'data-file-upload-summary')
                self.assertContains(response, 'class="ops-file-upload"')
                self.assertContains(response, 'class="ops-file-upload-preview"')
                self.assertContains(response, 'class="ops-file-upload-summary"')
                self.assertContains(response, 'type="file"')
                self.assertContains(response, 'multiple')
                self.assertContains(response, help_text)
                self.assertEqual(response.content.decode().count('type="file"'), 1)
                self.assertNotContains(response, 'name="title" data-file-upload-preview')
                for field_name in ('project', 'title', 'description', 'update_date', 'reported_by'):
                    if field_name == 'project' and label == 'project_update_create_for_project':
                        continue
                    self.assertContains(response, f'name="{field_name}"')

    def test_create_for_project_view_keeps_creator_and_reporter_separate(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse('project_update_create_for_project', args=[self.project.pk]),
            data={
                'title': 'Avance desde formulario',
                'description': 'El formulario transmite el responsable seleccionado.',
                'update_date': '2026-07-12',
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

    def test_unpublished_can_be_edited(self):
        update = self.create_unpublished()

        edited = update_project_update(
            update_id=update.pk,
            project=self.project,
            title='Avance operativo corregido',
            description='Información operativa actualizada.',
            update_date=date(2026, 7, 11),
            reported_by=self.reported_by,
            actor=self.editor,
        )

        self.assertEqual(edited.title, 'Avance operativo corregido')
        self.assertEqual(edited.reported_by, self.reported_by)
        self.assertTrue(AuditLog.objects.filter(
            entity_id=str(update.pk), action=AuditLog.Action.UPDATED, user=self.editor
        ).exists())

    def test_unpublished_cannot_be_reassigned_to_closed_project(self):
        update = self.create_unpublished()
        target_project = create_project(code='PRJ-UPDATE-CLOSED-TARGET')
        target_project.status = Project.Status.CLOSED
        target_project.save(update_fields=('status',))

        with self.assertRaisesMessage(ValidationError, 'admiten gastos y avances'):
            update_project_update(
                update_id=update.pk,
                project=target_project,
                title='Reasignación no permitida',
                description='No debe persistirse en un proyecto cerrado.',
                update_date=date(2026, 7, 12),
                reported_by=self.reported_by,
                actor=self.editor,
            )

        update.refresh_from_db()
        self.assertEqual(update.project, self.project)
        self.assertEqual(update.title, 'Avance operativo')

    def test_unpublished_update_view_changes_reported_by(self):
        update = self.create_unpublished()
        self.client.force_login(self.editor)

        response = self.client.post(
            reverse('project_update_update', args=[update.pk]),
            data={
                'project': self.project.pk,
                'title': update.title,
                'description': update.description,
                'update_date': update.update_date.isoformat(),
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
        update = self.create_unpublished()
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

    def test_historical_unpublished_without_responsible_person_cannot_publish_or_audit(self):
        update = ProjectUpdate.objects.create(
            project=self.project,
            title='Avance histórico sin responsable para publicar',
            description='Debe exigir atribución antes de publicar.',
            created_by=self.user,
        )

        with self.assertRaisesMessage(ValidationError, 'Debe seleccionar una persona responsable'):
            publish_project_update(update.pk, self.user)

        update.refresh_from_db()
        self.assertEqual(update.status, ProjectUpdate.Status.UNPUBLISHED)
        self.assertFalse(AuditLog.objects.filter(
            entity_id=str(update.pk), action=AuditLog.Action.PUBLISHED
        ).exists())

    def test_changing_responsible_person_creates_safe_audit_summary(self):
        update = self.create_unpublished()
        replacement = create_user('replacement-reporter')
        replacement.email = 'replacement@example.com'
        replacement.save(update_fields=('email',))

        update_project_update(
            update_id=update.pk,
            project=self.project,
            title=update.title,
            description=update.description,
            update_date=update.update_date,
            reported_by=replacement,
            actor=self.editor,
        )

        audit = AuditLog.objects.filter(entity_id=str(update.pk), action=AuditLog.Action.UPDATED).latest('created_at')
        self.assertIn('Atribución de la persona responsable', audit.summary)
        self.assertNotIn(replacement.username, audit.summary)
        self.assertNotIn(replacement.email, audit.summary)

    def test_publish_rejects_closed_project(self):
        update = self.create_unpublished()
        self.project.status = Project.Status.CLOSED
        self.project.save(update_fields=('status',))

        with self.assertRaisesMessage(ValidationError, 'admiten gastos y avances'):
            publish_project_update(update.pk, self.user)

        update.refresh_from_db()
        self.assertEqual(update.status, ProjectUpdate.Status.UNPUBLISHED)
        self.project.status = Project.Status.ACTIVE
        self.project.save(update_fields=('status',))

    def test_published_project_update_cannot_be_edited(self):
        update = self.create_unpublished()
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
        self.assertNotContains(response, 'Porcentaje de progreso')
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
        attachment_actions = self._attachment_actions_markup(content)
        self.assertIn(f'id="project-update-attachment-delete-form-{attachment.pk}"', attachment_actions)
        self.assertIn('method="post"', attachment_actions)
        self.assertIn(f'action="{attachment_delete_url}"', attachment_actions)
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
        self.assertNotContains(published_response, 'Agregar adjuntos')

    def test_unpublished_detail_shows_plural_add_attachments_action(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse('project_update_detail', args=[self.project_update.pk]))

        self.assertContains(response, 'Agregar adjuntos')
        self.assertNotContains(response, 'Agregar adjunto</a>')
        self.assertContains(
            response,
            reverse('project_update_attachment_create', args=[self.project_update.pk]),
        )

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

    def _create_attachment(self, *, title='Evidencia de acciones'):
        return ProjectUpdateAttachment.objects.create(
            project_update=self.project_update,
            title=title,
            file='project_update_attachments/acciones.pdf',
            uploaded_by=self.user,
        )

    def _attachment_actions_markup(self, content):
        marker = 'ops-project-update-attachment-actions'
        start = content.index(marker)
        # Walk nested divs until the outer actions container closes.
        open_div = content.rfind('<div', 0, start)
        depth = 0
        idx = open_div
        while idx < len(content):
            next_open = content.find('<div', idx)
            next_close = content.find('</div>', idx)
            if next_close == -1:
                break
            if next_open != -1 and next_open < next_close:
                depth += 1
                idx = next_open + 4
                continue
            depth -= 1
            idx = next_close + len('</div>')
            if depth == 0:
                return content[start:idx]
        return content[start:]

    def test_unpublished_attachment_actions_render_download_and_delete_as_siblings(self):
        attachment = self._create_attachment()
        download_url = reverse('project_update_attachment_download', args=[self.project.pk, self.project_update.pk, attachment.pk])
        delete_url = reverse('project_update_attachment_delete', args=[attachment.pk])

        response = self.client.get(reverse('project_update_detail', args=[self.project_update.pk]))
        content = response.content.decode()
        actions = self._attachment_actions_markup(content)

        self.assertEqual(response.status_code, 200)
        self.assertIn(f'href="{download_url}"', actions)
        self.assertIn('>Descargar</a>', actions)
        self.assertEqual(actions.count('>Descargar</a>'), 1)
        self.assertIn(f'id="project-update-attachment-delete-form-{attachment.pk}"', actions)
        self.assertIn('method="post"', actions)
        self.assertIn(f'action="{delete_url}"', actions)
        self.assertIn('name="csrfmiddlewaretoken"', actions)
        self.assertIn('data-confirm-action', actions)
        self.assertIn('data-confirm-title="¿Eliminar este archivo?"', actions)
        self.assertIn(
            'data-confirm-text="El adjunto dejará de estar disponible en el avance."',
            actions,
        )
        self.assertIn('data-confirm-confirm-label="Sí, eliminar"', actions)
        self.assertIn('data-confirm-variant="danger"', actions)
        self.assertIn('>Eliminar</button>', actions)
        self.assertEqual(actions.count('>Eliminar</button>'), 1)
        self.assertIn('class="btn btn-sm btn-outline-danger"', actions)
        self.assertIn('type="submit"', actions)
        self.assertNotIn('⋮', actions)
        self.assertNotIn('ops-project-update-attachment-more', actions)
        self.assertNotIn('data-bs-toggle="dropdown"', actions)
        self.assertNotIn('dropdown-item', actions)
        self.assertNotIn('dropdown-menu', actions)
        self.assertNotIn('class="dropdown"', actions)

    def test_published_attachment_actions_show_download_without_delete(self):
        attachment = self._create_attachment(title='Evidencia publicada')
        download_url = reverse('project_update_attachment_download', args=[self.project.pk, self.project_update.pk, attachment.pk])
        delete_url = reverse('project_update_attachment_delete', args=[attachment.pk])
        publish_project_update(self.project_update.pk, self.user)

        response = self.client.get(reverse('project_update_detail', args=[self.project_update.pk]))
        content = response.content.decode()
        actions = self._attachment_actions_markup(content)

        self.assertEqual(response.status_code, 200)
        self.assertIn(f'href="{download_url}"', actions)
        self.assertIn('>Descargar</a>', actions)
        self.assertNotIn(f'project-update-attachment-delete-form-{attachment.pk}', actions)
        self.assertNotIn(delete_url, actions)
        self.assertNotIn('>Eliminar</button>', actions)
        self.assertNotIn('⋮', actions)
        self.assertNotIn('ops-project-update-attachment-more', actions)
        self.assertNotIn('data-bs-toggle="dropdown"', actions)

    def test_view_only_attachment_actions_show_download_without_delete_or_dropdown(self):
        attachment = self._create_attachment(title='Evidencia solo lectura')
        download_url = reverse('project_update_attachment_download', args=[self.project.pk, self.project_update.pk, attachment.pk])
        delete_url = reverse('project_update_attachment_delete', args=[attachment.pk])
        viewer = get_user_model().objects.create_user(
            username='detail-attachment-viewer',
            password='pass-12345',
        )
        viewer.user_permissions.add(*Permission.objects.filter(codename__in=(
            'view_project',
            'view_projectupdate',
            'view_projectupdateattachment',
        )))
        self.client.force_login(viewer)

        response = self.client.get(reverse('project_update_detail', args=[self.project_update.pk]))
        content = response.content.decode()
        actions = self._attachment_actions_markup(content)

        self.assertEqual(response.status_code, 200)
        self.assertIn(f'href="{download_url}"', actions)
        self.assertIn('>Descargar</a>', actions)
        self.assertNotIn(f'project-update-attachment-delete-form-{attachment.pk}', actions)
        self.assertNotIn(delete_url, actions)
        self.assertNotIn('>Eliminar</button>', actions)
        self.assertNotIn('⋮', actions)
        self.assertNotIn('ops-project-update-attachment-more', actions)
        self.assertNotIn('data-bs-toggle="dropdown"', actions)
        self.assertNotIn('class="dropdown"', actions)
        self.assertNotIn('dropdown-menu', actions)


class ProjectUpdateChunkTests(TestCase):
    def setUp(self):
        self.user = create_user('chunk-manager')
        self.project = create_project(code='PRJ-UPDATE-CHUNKS')
        self.project.status = Project.Status.ACTIVE
        self.project.save(update_fields=('status',))
        self.client.force_login(self.user)

    def create_updates(self, count, *, project=None, status=ProjectUpdate.Status.UNPUBLISHED):
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
        unpublished_updates = self.create_updates(3)
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
        for unpublished in unpublished_updates:
            self.assertNotContains(chunk_response, unpublished.title)

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


class OperatorProjectUpdateSelfReportTests(TestCase):
    def setUp(self):
        from apps.operations.role_services import sync_operation_roles
        from apps.operations.roles import ROLE_FIELD_OPERATOR, ROLE_SIGEDON_ADMIN

        sync_operation_roles()
        User = get_user_model()
        self.operator = User.objects.create_user(username='operador_demo', password='pass-12345')
        self.operator.groups.add(Group.objects.get(name=ROLE_FIELD_OPERATOR))
        self.admin = User.objects.create_user(username='admin_demo', password='pass-12345')
        self.admin.groups.add(Group.objects.get(name=ROLE_SIGEDON_ADMIN))
        self.eligible_other = create_user('eligible-other-reporter')
        self.project = create_project(code='PRJ-OP-SELF-REPORT')
        self.project.status = Project.Status.ACTIVE
        self.project.save(update_fields=('status',))

    def test_operator_form_disables_reported_by_as_self(self):
        help_text = (
            'El responsable se asigna automáticamente al usuario que registra el avance.'
        )
        for form_class in (ProjectUpdateForm, ProjectUpdateForProjectForm):
            with self.subTest(form_class=form_class.__name__):
                form = form_class(user=self.operator)
                field = form.fields['reported_by']

                self.assertIn('reported_by', form.fields)
                self.assertTrue(field.disabled)
                self.assertEqual(field.initial, self.operator)
                self.assertEqual(list(field.queryset), [self.operator])
                self.assertEqual(field.label, 'Persona responsable del avance')
                self.assertEqual(str(field.help_text), help_text)
                self.assertFalse(field.required)

    def test_admin_form_keeps_enabled_eligible_reporter_selector(self):
        form = ProjectUpdateForm(user=self.admin)
        field = form.fields['reported_by']

        self.assertFalse(field.disabled)
        self.assertTrue(field.required)
        self.assertIn(self.operator, field.queryset)
        self.assertIn(self.admin, field.queryset)
        self.assertIn(self.eligible_other, field.queryset)

    def test_operator_create_pages_render_disabled_self_reporter(self):
        self.client.force_login(self.operator)
        help_text = (
            'El responsable se asigna automáticamente al usuario que registra el avance.'
        )
        pages = (
            reverse('project_update_create'),
            reverse('project_update_create_for_project', args=[self.project.pk]),
        )
        for url in pages:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'Persona responsable del avance')
                self.assertContains(response, 'operador_demo')
                self.assertContains(response, 'disabled')
                self.assertContains(response, help_text)
                self.assertContains(response, 'name="reported_by"')

    def test_operator_form_ignores_forged_reported_by_in_post_data(self):
        form = ProjectUpdateForProjectForm(
            data={
                'title': 'Avance forjado',
                'description': 'El POST intenta otro responsable.',
                'update_date': '2026-07-12',
                'reported_by': self.eligible_other.pk,
            },
            user=self.operator,
        )

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['reported_by'], self.operator)

    def test_operator_creation_routes_persist_self_as_reporter(self):
        self.client.force_login(self.operator)
        routes = (
            (
                reverse('project_update_create'),
                {
                    'project': self.project.pk,
                    'title': 'Avance ruta general',
                    'description': 'Creado por operador en ruta general.',
                    'update_date': '2026-07-12',
                    'reported_by': self.eligible_other.pk,
                },
                reverse('project_update_list'),
                'Avance ruta general',
            ),
            (
                reverse('project_update_create_for_project', args=[self.project.pk]),
                {
                    'title': 'Avance ruta proyecto',
                    'description': 'Creado por operador en ruta de proyecto.',
                    'update_date': '2026-07-12',
                    'reported_by': self.eligible_other.pk,
                },
                reverse('project_detail', args=[self.project.pk]),
                'Avance ruta proyecto',
            ),
        )
        for url, payload, success_url, title in routes:
            with self.subTest(title=title):
                response = self.client.post(url, data=payload)
                update = ProjectUpdate.objects.get(title=title)

                self.assertRedirects(response, success_url)
                self.assertEqual(update.created_by, self.operator)
                self.assertEqual(update.reported_by, self.operator)
                self.assertNotEqual(update.reported_by, self.eligible_other)

    def test_register_advance_forces_operator_reporter_despite_submitted_value(self):
        update = register_advance(
            project_id=self.project.pk,
            title='Avance servicio operador',
            description='El servicio ignora el reporter forjado.',
            created_by=self.operator,
            reported_by=self.eligible_other,
        )

        self.assertEqual(update.created_by, self.operator)
        self.assertEqual(update.reported_by, self.operator)
        self.assertNotEqual(update.reported_by, self.eligible_other)

    def test_register_advance_preserves_admin_delegated_reporter(self):
        update = register_advance(
            project_id=self.project.pk,
            title='Avance servicio admin',
            description='El admin puede delegar responsabilidad.',
            created_by=self.admin,
            reported_by=self.eligible_other,
        )

        self.assertEqual(update.created_by, self.admin)
        self.assertEqual(update.reported_by, self.eligible_other)

    def test_operator_creation_with_attachment_keeps_operator_actors(self):
        from tempfile import TemporaryDirectory

        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.test import override_settings

        media = TemporaryDirectory()
        self.addCleanup(media.cleanup)
        override = override_settings(MEDIA_ROOT=media.name)
        override.enable()
        self.addCleanup(override.disable)

        self.client.force_login(self.operator)
        upload = SimpleUploadedFile('evidencia.pdf', b'operator-proof')
        response = self.client.post(
            reverse('project_update_create_for_project', args=[self.project.pk]),
            data={
                'title': 'Avance con adjunto operador',
                'description': 'Incluye evidencia.',
                'update_date': '2026-07-12',
                'reported_by': self.eligible_other.pk,
                'attachments': [upload],
            },
        )
        update = ProjectUpdate.objects.get(title='Avance con adjunto operador')
        attachment = update.attachments.get()

        self.assertRedirects(response, reverse('project_detail', args=[self.project.pk]))
        self.assertEqual(update.created_by, self.operator)
        self.assertEqual(update.reported_by, self.operator)
        self.assertEqual(attachment.uploaded_by, self.operator)
        self.assertTrue(AuditLog.objects.filter(
            entity_id=str(update.pk), action=AuditLog.Action.CREATED, user=self.operator
        ).exists())
        self.assertTrue(AuditLog.objects.filter(
            entity_id=str(attachment.pk),
            action=AuditLog.Action.CREATED,
            user=self.operator,
        ).exists())
