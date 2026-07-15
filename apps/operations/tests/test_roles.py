from io import StringIO

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from apps.operations.models import Project
from apps.operations.role_services import sync_operation_roles
from apps.operations.roles import (
    ROLE_EXTERNAL_AUDITOR,
    ROLE_FIELD_OPERATOR,
    ROLE_PROJECT_COMMITTEE,
    ROLE_PROJECT_UPDATE_DECIDER,
    ROLE_PROJECT_UPDATE_REVIEWER,
    ROLE_SIGEDON_ADMIN,
)
from apps.operations.services import register_advance
from apps.operations.tests.helpers import create_project


class OperationRoleTests(TestCase):
    def setUp(self):
        sync_operation_roles()
        self.project = create_project()
        self.project.status = Project.Status.ACTIVE
        self.project.save()
        self.reporter = self.create_user_for_role('role-update-reporter', ROLE_SIGEDON_ADMIN)
        self.project_update = register_advance(
            project_id=self.project.pk,
            title='Avance para roles',
            description='Pendiente de revisión.',
            reported_by=self.reporter,
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
        self.assertIn(ROLE_PROJECT_COMMITTEE, output.getvalue())
        self.assertIn(ROLE_PROJECT_UPDATE_REVIEWER, output.getvalue())
        self.assertIn(ROLE_PROJECT_UPDATE_DECIDER, output.getvalue())

    def test_sync_operation_roles_creates_required_groups(self):
        for role_name in [
            ROLE_SIGEDON_ADMIN,
            ROLE_FIELD_OPERATOR,
            ROLE_EXTERNAL_AUDITOR,
            ROLE_PROJECT_COMMITTEE,
            ROLE_PROJECT_UPDATE_REVIEWER,
            ROLE_PROJECT_UPDATE_DECIDER,
        ]:
            with self.subTest(role=role_name):
                self.assertTrue(Group.objects.filter(name=role_name).exists())

    def test_sigedon_admin_can_publish_without_review_or_decision_permissions(self):
        user = self.create_user_for_role('admin-role', ROLE_SIGEDON_ADMIN)
        operations_permissions = Permission.objects.filter(content_type__app_label='operations')
        excluded_codenames = {
            'add_auditlog', 'change_auditlog', 'delete_auditlog',
            'add_projectupdatereview', 'change_projectupdatereview', 'delete_projectupdatereview',
            'add_projectupdatereviewdecision', 'change_projectupdatereviewdecision',
            'delete_projectupdatereviewdecision', 'review_projectupdate', 'decide_projectupdate',
            'resolve_projectupdateremediation',
        }

        for permission in operations_permissions:
            with self.subTest(codename=permission.codename):
                if permission.codename in excluded_codenames:
                    self.assert_lacks_perm(user, permission.codename)
                else:
                    self.assert_has_perm(user, permission.codename)
        self.assert_has_perm(user, 'view_auditlog')
        self.assert_has_perm(user, 'publish_projectupdate')

    def test_field_operator_permission_matrix(self):
        user = self.create_user_for_role('field-role', ROLE_FIELD_OPERATOR)

        self.assert_has_perm(user, 'view_project')
        self.assert_has_perm(user, 'view_projectupdate')
        self.assert_has_perm(user, 'add_projectupdate')
        self.assert_lacks_perm(user, 'add_project')
        self.assert_lacks_perm(user, 'change_donation')
        self.assert_lacks_perm(user, 'change_projectupdate')
        self.assert_lacks_perm(user, 'publish_projectupdate')
        self.assert_lacks_perm(user, 'review_projectupdate')
        self.assert_lacks_perm(user, 'decide_projectupdate')

    def test_external_auditor_permission_matrix(self):
        user = self.create_user_for_role('auditor-role', ROLE_EXTERNAL_AUDITOR)

        self.assert_has_perm(user, 'view_auditlog')
        self.assert_has_perm(user, 'view_expense')
        self.assert_lacks_perm(user, 'add_expense')
        self.assert_lacks_perm(user, 'change_projectupdate')
        self.assert_lacks_perm(user, 'add_auditlog')
        self.assert_lacks_perm(user, 'change_auditlog')
        self.assert_lacks_perm(user, 'delete_auditlog')
        self.assert_lacks_perm(user, 'publish_projectupdate')
        self.assert_lacks_perm(user, 'review_projectupdate')
        self.assert_lacks_perm(user, 'decide_projectupdate')

    def test_legacy_project_committee_permission_matrix_is_read_only(self):
        user = self.create_user_for_role('committee-role', ROLE_PROJECT_COMMITTEE)

        for codename in {
            'view_project',
            'view_projectupdate',
            'view_projectdocument',
            'view_projectupdateattachment',
            'view_projectupdatereview',
            'view_projectupdatereviewdecision',
        }:
            with self.subTest(codename=codename):
                self.assert_has_perm(user, codename)
        for codename in {
            'add_project',
            'change_project',
            'delete_project',
            'add_projectupdate',
            'change_projectupdate',
            'delete_projectupdate',
            'add_projectupdateattachment',
            'change_projectupdateattachment',
            'delete_projectupdateattachment',
            'change_projectupdatereview',
            'delete_projectupdatereview',
            'change_projectupdatereviewdecision',
            'delete_projectupdatereviewdecision',
            'publish_projectupdate',
            'review_projectupdate',
            'decide_projectupdate',
            'add_auditlog',
            'change_auditlog',
            'delete_auditlog',
        }:
            with self.subTest(codename=codename):
                self.assert_lacks_perm(user, codename)

    def test_reviewer_and_decider_permission_matrices_are_separate(self):
        reviewer = self.create_user_for_role('reviewer-role', ROLE_PROJECT_UPDATE_REVIEWER)
        decider = self.create_user_for_role('decider-role', ROLE_PROJECT_UPDATE_DECIDER)

        self.assert_has_perm(reviewer, 'review_projectupdate')
        self.assert_lacks_perm(reviewer, 'publish_projectupdate')
        self.assert_lacks_perm(reviewer, 'decide_projectupdate')
        self.assert_has_perm(decider, 'decide_projectupdate')
        self.assert_lacks_perm(decider, 'publish_projectupdate')
        self.assert_lacks_perm(decider, 'review_projectupdate')
        for user in (reviewer, decider):
            with self.subTest(user=user.username):
                self.assert_lacks_perm(user, 'change_projectupdatereview')
                self.assert_lacks_perm(user, 'delete_projectupdatereview')
                self.assert_lacks_perm(user, 'change_projectupdatereviewdecision')
                self.assert_lacks_perm(user, 'delete_projectupdatereviewdecision')

    def test_resync_removes_legacy_audit_mutation_permissions(self):
        protected_codenames = {'add_auditlog', 'change_auditlog', 'delete_auditlog'}
        legacy_permissions = Permission.objects.filter(
            content_type__app_label='operations', codename__in=protected_codenames
        )
        for group in Group.objects.filter(
            name__in=(
                ROLE_SIGEDON_ADMIN, ROLE_FIELD_OPERATOR, ROLE_EXTERNAL_AUDITOR,
                ROLE_PROJECT_COMMITTEE, ROLE_PROJECT_UPDATE_REVIEWER, ROLE_PROJECT_UPDATE_DECIDER,
            )
        ):
            group.permissions.add(*legacy_permissions)

        sync_operation_roles()

        for group in Group.objects.filter(
            name__in=(
                ROLE_SIGEDON_ADMIN, ROLE_FIELD_OPERATOR, ROLE_EXTERNAL_AUDITOR,
                ROLE_PROJECT_COMMITTEE, ROLE_PROJECT_UPDATE_REVIEWER, ROLE_PROJECT_UPDATE_DECIDER,
            )
        ):
            self.assertFalse(
                group.permissions.filter(codename__in=protected_codenames).exists(),
                group.name,
            )

    def test_resync_removes_incorrect_functional_permissions_from_roles(self):
        functional_codenames = {
            'publish_projectupdate',
            'review_projectupdate',
            'decide_projectupdate',
        }
        review_and_decision_crud_codenames = {
            'add_projectupdatereview',
            'change_projectupdatereview',
            'delete_projectupdatereview',
            'add_projectupdatereviewdecision',
            'change_projectupdatereviewdecision',
            'delete_projectupdatereviewdecision',
        }
        groups = Group.objects.filter(name__in=(
            ROLE_SIGEDON_ADMIN,
            ROLE_PROJECT_COMMITTEE,
            ROLE_PROJECT_UPDATE_REVIEWER,
            ROLE_PROJECT_UPDATE_DECIDER,
        ))
        for group in groups:
            group.permissions.add(*Permission.objects.filter(
                content_type__app_label='operations',
                codename__in=functional_codenames | review_and_decision_crud_codenames,
            ))

        sync_operation_roles()

        expected = {
            ROLE_SIGEDON_ADMIN: {'publish_projectupdate'},
            ROLE_PROJECT_COMMITTEE: set(),
            ROLE_PROJECT_UPDATE_REVIEWER: {'review_projectupdate'},
            ROLE_PROJECT_UPDATE_DECIDER: {'decide_projectupdate'},
        }
        for group in groups:
            self.assertEqual(
                set(group.permissions.filter(codename__in=functional_codenames).values_list('codename', flat=True)),
                expected[group.name],
            )
            self.assertFalse(group.permissions.filter(codename__in=review_and_decision_crud_codenames).exists())

    def test_field_operator_can_open_project_update_create_from_project(self):
        self.client.force_login(self.create_user_for_role('field-create-update', ROLE_FIELD_OPERATOR))

        response = self.client.get(reverse('project_update_create_for_project', args=[self.project.pk]))

        self.assertEqual(response.status_code, 200)

    def test_field_operator_cannot_open_project_update_publish(self):
        self.client.force_login(self.create_user_for_role('field-publish-update', ROLE_FIELD_OPERATOR))

        response = self.client.post(reverse('project_update_publish', args=[self.project_update.pk]))

        self.assertEqual(response.status_code, 403)

    def test_external_auditor_can_open_audit_log_list(self):
        self.client.force_login(self.create_user_for_role('auditor-view-audit', ROLE_EXTERNAL_AUDITOR))

        response = self.client.get(reverse('audit_log_list'))

        self.assertEqual(response.status_code, 200)

    def test_external_auditor_cannot_create_project(self):
        self.client.force_login(self.create_user_for_role('auditor-create-project', ROLE_EXTERNAL_AUDITOR))

        response = self.client.get(reverse('project_create'))

        self.assertEqual(response.status_code, 403)

    def test_project_committee_can_read_projects_and_updates_without_mutating_them(self):
        self.client.force_login(self.create_user_for_role('committee-routes', ROLE_PROJECT_COMMITTEE))

        self.assertEqual(self.client.get(reverse('project_list')).status_code, 200)
        self.assertEqual(self.client.get(reverse('project_update_list')).status_code, 200)
        self.assertEqual(self.client.get(reverse('project_update_detail', args=[self.project_update.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse('project_update_create')).status_code, 403)
        self.assertEqual(self.client.get(reverse('project_update_update', args=[self.project_update.pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse('project_update_delete', args=[self.project_update.pk])).status_code, 403)
        self.assertEqual(self.client.post(reverse('project_update_publish', args=[self.project_update.pk])).status_code, 403)

    def test_existing_role_permission_matrices_remain_unchanged(self):
        expected_permissions = {
            ROLE_FIELD_OPERATOR: {
                'view_project',
                'view_projectupdate',
                'add_projectupdate',
                'view_supportingdocument',
                'add_supportingdocument',
                'view_projectupdateremediation',
                'view_projectupdateremediationattachment',
                'add_projectupdateremediation',
                'change_projectupdateremediation',
                'add_projectupdateremediationattachment',
                'delete_projectupdateremediationattachment',
                'submit_projectupdateremediation',
            },
            ROLE_EXTERNAL_AUDITOR: {
                'view_institution',
                'view_project',
                'view_donation',
                'view_fundallocation',
                'view_expense',
                'view_supportingdocument',
                'view_projectupdate',
                'view_auditlog',
            },
        }

        for role_name, codenames in expected_permissions.items():
            with self.subTest(role=role_name):
                actual_codenames = set(
                    Group.objects.get(name=role_name).permissions.values_list('codename', flat=True)
                )
                self.assertEqual(actual_codenames, codenames)

    def test_sync_operation_roles_is_idempotent(self):
        first_snapshot = {
            group.name: set(group.permissions.values_list('codename', flat=True))
            for group in Group.objects.filter(
                name__in=[
                    ROLE_SIGEDON_ADMIN, ROLE_FIELD_OPERATOR, ROLE_EXTERNAL_AUDITOR,
                    ROLE_PROJECT_COMMITTEE, ROLE_PROJECT_UPDATE_REVIEWER, ROLE_PROJECT_UPDATE_DECIDER,
                ]
            )
        }

        sync_operation_roles()

        second_snapshot = {
            group.name: set(group.permissions.values_list('codename', flat=True))
            for group in Group.objects.filter(
                name__in=[
                    ROLE_SIGEDON_ADMIN, ROLE_FIELD_OPERATOR, ROLE_EXTERNAL_AUDITOR,
                    ROLE_PROJECT_COMMITTEE, ROLE_PROJECT_UPDATE_REVIEWER, ROLE_PROJECT_UPDATE_DECIDER,
                ]
            )
        }
        self.assertEqual(first_snapshot, second_snapshot)
        self.assertEqual(Group.objects.filter(name=ROLE_SIGEDON_ADMIN).count(), 1)
        self.assertEqual(Group.objects.filter(name=ROLE_FIELD_OPERATOR).count(), 1)
        self.assertEqual(Group.objects.filter(name=ROLE_EXTERNAL_AUDITOR).count(), 1)
        self.assertEqual(Group.objects.filter(name=ROLE_PROJECT_COMMITTEE).count(), 1)
        self.assertEqual(Group.objects.filter(name=ROLE_PROJECT_UPDATE_REVIEWER).count(), 1)
        self.assertEqual(Group.objects.filter(name=ROLE_PROJECT_UPDATE_DECIDER).count(), 1)
