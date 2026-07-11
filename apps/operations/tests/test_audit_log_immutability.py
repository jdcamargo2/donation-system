from django.contrib import admin
from django.contrib.auth.models import Permission
from django.db.models.deletion import ProtectedError
from django.test import RequestFactory, TestCase
from django.urls import reverse

from apps.operations.admin import AuditLogAdmin
from apps.operations.models import AuditLog, AuditLogImmutableError
from apps.operations.services import log_action
from apps.operations.tests.helpers import create_project, create_user


class AuditLogAppendOnlyModelTests(TestCase):
    def setUp(self):
        self.user = create_user()
        self.project = create_project()
        self.log = log_action(
            self.user,
            AuditLog.Action.CREATED,
            self.project,
            'Proyecto creado.',
        )

    def test_authorized_single_creation_works(self):
        self.assertIsNotNone(self.log.pk)
        self.assertEqual(AuditLog.objects.get(pk=self.log.pk).summary, 'Proyecto creado.')

    def test_existing_instance_save_fails_without_modification(self):
        self.log.summary = 'Alterado.'

        with self.assertRaises(AuditLogImmutableError):
            self.log.save()

        self.assertEqual(AuditLog.objects.get(pk=self.log.pk).summary, 'Proyecto creado.')

    def test_instance_delete_fails(self):
        with self.assertRaises(AuditLogImmutableError):
            self.log.delete()

        self.assertTrue(AuditLog.objects.filter(pk=self.log.pk).exists())

    def test_queryset_update_fails(self):
        with self.assertRaises(AuditLogImmutableError):
            AuditLog.objects.filter(pk=self.log.pk).update(summary='Alterado.')

    def test_queryset_delete_fails(self):
        with self.assertRaises(AuditLogImmutableError):
            AuditLog.objects.filter(pk=self.log.pk).delete()

    def test_bulk_update_fails(self):
        self.log.summary = 'Alterado.'

        with self.assertRaises(AuditLogImmutableError):
            AuditLog.objects.bulk_update([self.log], ['summary'])

    def test_bulk_create_fails_explicitly(self):
        proposed = AuditLog(
            action=AuditLog.Action.CREATED,
            model_name='Proyecto',
            entity_id='2',
            entity_label='PRJ-2',
            summary='Proyecto creado.',
        )

        with self.assertRaises(AuditLogImmutableError):
            AuditLog.objects.bulk_create([proposed])

    def test_deleting_actor_cannot_rewrite_existing_event(self):
        with self.assertRaises(ProtectedError):
            self.user.delete()

        self.assertEqual(AuditLog.objects.get(pk=self.log.pk).user, self.user)


class AuditLogReadOnlyAdminTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.model_admin = AuditLogAdmin(AuditLog, admin.site)
        self.superuser = create_user(username='audit-admin')
        self.log = AuditLog.objects.create(
            action=AuditLog.Action.CREATED,
            model_name='Proyecto',
            entity_id='1',
            entity_label='PRJ-1',
            summary='Proyecto creado.',
        )

    def request_for(self, user):
        """
        PRE: user is a saved Django user.
        POST: returns an admin-compatible GET request attributed to that user.
        """
        request = self.factory.get('/admin/operations/auditlog/')
        request.user = user
        return request

    def test_superuser_cannot_add_change_or_delete(self):
        request = self.request_for(self.superuser)

        self.assertFalse(self.model_admin.has_add_permission(request))
        self.assertFalse(self.model_admin.has_change_permission(request, self.log))
        self.assertFalse(self.model_admin.has_delete_permission(request, self.log))

    def test_user_with_view_permission_can_view_only(self):
        viewer = create_user(username='audit-viewer')
        viewer.is_superuser = False
        viewer.is_staff = True
        viewer.save(update_fields=('is_superuser', 'is_staff'))
        viewer.user_permissions.add(
            Permission.objects.get(
                content_type__app_label='operations', codename='view_auditlog'
            )
        )
        request = self.request_for(viewer)

        self.assertTrue(self.model_admin.has_view_permission(request, self.log))
        self.assertFalse(self.model_admin.has_change_permission(request, self.log))
        self.assertEqual(
            set(self.model_admin.get_readonly_fields(request, self.log)),
            {field.name for field in AuditLog._meta.concrete_fields},
        )


class AuditLogReadOnlyUITests(TestCase):
    def test_list_has_no_edit_or_delete_actions(self):
        user = create_user()
        AuditLog.objects.create(
            action=AuditLog.Action.CREATED,
            model_name='Proyecto',
            entity_id='1',
            entity_label='PRJ-1',
            summary='Proyecto creado.',
        )
        self.client.force_login(user)

        response = self.client.get(reverse('audit_log_list'))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Editar')
        self.assertNotContains(response, 'Eliminar')

    def test_user_without_view_permission_receives_403(self):
        user = create_user(username='audit-no-view')
        user.is_superuser = False
        user.is_staff = False
        user.save(update_fields=('is_superuser', 'is_staff'))
        self.client.force_login(user)

        self.assertEqual(self.client.get(reverse('audit_log_list')).status_code, 403)
