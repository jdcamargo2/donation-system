from io import StringIO

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from apps.operations.models import Project
from apps.operations.role_services import sync_operation_roles
from apps.operations.roles import ROLE_EXTERNAL_AUDITOR, ROLE_FIELD_OPERATOR, ROLE_SIGEDON_ADMIN
from apps.operations.services import register_advance
from apps.operations.tests.helpers import create_project


class OperationRoleTests(TestCase):
    def setUp(self):
        sync_operation_roles()
        self.project = create_project()
        self.project.status = Project.Status.ACTIVE
        self.project.save()
        self.project_update = register_advance(
            project_id=self.project.pk,
            title='Avance para roles',
            description='Pendiente de revisión.',
        )

    def create_user_for_role(self, username, role_name):
        user = get_user_model().objects.create_user(username=username, password='pass-12345')
        user.groups.add(Group.objects.get(name=role_name))
        return user

    def assert_has_perm(self, user, codename):
        self.assertTrue(user.has_perm(f'operations.{codename}'), codename)

    def assert_lacks_perm(self, user, codename):
        self.assertFalse(user.has_perm(f'operations.{codename}'), codename)

    def test_sync_sigedon_roles_command_runs_without_error(self):
        output = StringIO()

        call_command('sync_sigedon_roles', stdout=output)

        self.assertIn(ROLE_SIGEDON_ADMIN, output.getvalue())
        self.assertIn(ROLE_FIELD_OPERATOR, output.getvalue())
        self.assertIn(ROLE_EXTERNAL_AUDITOR, output.getvalue())

    def test_sync_operation_roles_creates_required_groups(self):
        for role_name in [ROLE_SIGEDON_ADMIN, ROLE_FIELD_OPERATOR, ROLE_EXTERNAL_AUDITOR]:
            with self.subTest(role=role_name):
                self.assertTrue(Group.objects.filter(name=role_name).exists())

    def test_sigedon_admin_has_all_operations_permissions(self):
        user = self.create_user_for_role('admin-role', ROLE_SIGEDON_ADMIN)
        operations_permissions = Permission.objects.filter(content_type__app_label='operations')

        for permission in operations_permissions:
            with self.subTest(codename=permission.codename):
                self.assert_has_perm(user, permission.codename)

    def test_field_operator_permission_matrix(self):
        user = self.create_user_for_role('field-role', ROLE_FIELD_OPERATOR)

        self.assert_has_perm(user, 'view_project')
        self.assert_has_perm(user, 'view_projectupdate')
        self.assert_has_perm(user, 'add_projectupdate')
        self.assert_lacks_perm(user, 'add_project')
        self.assert_lacks_perm(user, 'change_donation')
        self.assert_lacks_perm(user, 'change_projectupdate')

    def test_external_auditor_permission_matrix(self):
        user = self.create_user_for_role('auditor-role', ROLE_EXTERNAL_AUDITOR)

        self.assert_has_perm(user, 'view_auditlog')
        self.assert_has_perm(user, 'view_expense')
        self.assert_lacks_perm(user, 'add_expense')
        self.assert_lacks_perm(user, 'change_projectupdate')

    def test_field_operator_can_open_project_update_create_from_project(self):
        self.client.force_login(self.create_user_for_role('field-create-update', ROLE_FIELD_OPERATOR))

        response = self.client.get(reverse('project_update_create_for_project', args=[self.project.pk]))

        self.assertEqual(response.status_code, 200)

    def test_field_operator_cannot_open_project_update_review(self):
        self.client.force_login(self.create_user_for_role('field-review-update', ROLE_FIELD_OPERATOR))

        response = self.client.get(reverse('project_update_review', args=[self.project_update.pk]))

        self.assertEqual(response.status_code, 403)

    def test_external_auditor_can_open_audit_log_list(self):
        self.client.force_login(self.create_user_for_role('auditor-view-audit', ROLE_EXTERNAL_AUDITOR))

        response = self.client.get(reverse('audit_log_list'))

        self.assertEqual(response.status_code, 200)

    def test_external_auditor_cannot_create_project(self):
        self.client.force_login(self.create_user_for_role('auditor-create-project', ROLE_EXTERNAL_AUDITOR))

        response = self.client.get(reverse('project_create'))

        self.assertEqual(response.status_code, 403)

    def test_sync_operation_roles_is_idempotent(self):
        first_snapshot = {
            group.name: set(group.permissions.values_list('codename', flat=True))
            for group in Group.objects.filter(name__in=[ROLE_SIGEDON_ADMIN, ROLE_FIELD_OPERATOR, ROLE_EXTERNAL_AUDITOR])
        }

        sync_operation_roles()

        second_snapshot = {
            group.name: set(group.permissions.values_list('codename', flat=True))
            for group in Group.objects.filter(name__in=[ROLE_SIGEDON_ADMIN, ROLE_FIELD_OPERATOR, ROLE_EXTERNAL_AUDITOR])
        }
        self.assertEqual(first_snapshot, second_snapshot)
        self.assertEqual(Group.objects.filter(name=ROLE_SIGEDON_ADMIN).count(), 1)
        self.assertEqual(Group.objects.filter(name=ROLE_FIELD_OPERATOR).count(), 1)
        self.assertEqual(Group.objects.filter(name=ROLE_EXTERNAL_AUDITOR).count(), 1)
