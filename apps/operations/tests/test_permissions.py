from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse

from apps.operations.models import Project
from apps.operations.services import register_advance
from apps.operations.tests.helpers import create_project


def create_user(username):
    return get_user_model().objects.create_user(username=username, password='pass-12345')


def create_user_with_permissions(username, *permission_codenames):
    user = create_user(username)
    permissions = Permission.objects.filter(
        content_type__app_label='operations',
        codename__in=permission_codenames,
    )
    user.user_permissions.add(*permissions)
    return user


class OperationsPermissionTests(TestCase):
    def setUp(self):
        self.project = create_project()
        self.project.status = Project.Status.ACTIVE
        self.project.save()
        self.project_update = register_advance(
            project_id=self.project.pk,
            title='Avance pendiente',
            description='Listo para revisión.',
        )

    def test_anonymous_user_is_redirected_from_dashboard(self):
        response = self.client.get(reverse('dashboard'))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('login'), response['Location'])

    def test_authenticated_user_can_access_dashboard(self):
        self.client.force_login(create_user('dashboard-user'))

        response = self.client.get(reverse('dashboard'))

        self.assertEqual(response.status_code, 200)

    def test_anonymous_user_is_redirected_from_project_list(self):
        response = self.client.get(reverse('project_list'))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('login'), response['Location'])

    def test_user_without_view_project_permission_gets_403_on_project_list(self):
        self.client.force_login(create_user('no-view-project'))

        response = self.client.get(reverse('project_list'))

        self.assertEqual(response.status_code, 403)

    def test_user_with_view_project_permission_can_access_project_list(self):
        self.client.force_login(create_user_with_permissions('view-project', 'view_project'))

        response = self.client.get(reverse('project_list'))

        self.assertEqual(response.status_code, 200)

    def test_user_with_add_project_permission_can_access_project_create(self):
        self.client.force_login(create_user_with_permissions('add-project', 'add_project'))

        response = self.client.get(reverse('project_create'))

        self.assertEqual(response.status_code, 200)

    def test_user_without_add_project_permission_gets_403_on_project_create(self):
        self.client.force_login(create_user('no-add-project'))

        response = self.client.get(reverse('project_create'))

        self.assertEqual(response.status_code, 403)

    def test_change_projectupdate_permission_can_edit_but_not_publish(self):
        self.client.force_login(create_user_with_permissions('edit-update', 'change_projectupdate'))

        edit_response = self.client.get(reverse('project_update_update', args=[self.project_update.pk]))
        publish_response = self.client.post(reverse('project_update_publish', args=[self.project_update.pk]))

        self.assertEqual(edit_response.status_code, 200)
        self.assertEqual(publish_response.status_code, 403)

    def test_publish_projectupdate_permission_can_publish_without_editing(self):
        self.client.force_login(create_user_with_permissions('publish-update', 'publish_projectupdate'))

        edit_response = self.client.get(reverse('project_update_update', args=[self.project_update.pk]))
        publish_response = self.client.post(reverse('project_update_publish', args=[self.project_update.pk]))

        self.assertEqual(edit_response.status_code, 403)
        self.assertEqual(publish_response.status_code, 302)

    def test_user_with_view_auditlog_permission_can_access_audit_log_list(self):
        self.client.force_login(create_user_with_permissions('view-auditlog', 'view_auditlog'))

        response = self.client.get(reverse('audit_log_list'))

        self.assertEqual(response.status_code, 200)

    def test_user_without_view_auditlog_permission_gets_403_on_audit_log_list(self):
        self.client.force_login(create_user('no-view-auditlog'))

        response = self.client.get(reverse('audit_log_list'))

        self.assertEqual(response.status_code, 403)
