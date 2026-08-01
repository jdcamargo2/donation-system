from django.contrib.auth.models import Group, Permission
from django.test import TestCase

from apps.operations.role_services import sync_operation_roles
from apps.operations.roles import (
    ROLE_EXTERNAL_AUDITOR,
    ROLE_FIELD_OPERATOR,
    ROLE_PROJECT_COMMITTEE,
    ROLE_SIGEDON_ADMIN,
)


EVENT_MUTATION_CODENAMES = {
    'add_expenserequestevent',
    'change_expenserequestevent',
    'delete_expenserequestevent',
}


class ExpenseRequestRoleTests(TestCase):
    def setUp(self):
        sync_operation_roles()

    def create_user_for_role(self, username, role_name):
        from django.contrib.auth import get_user_model

        user = get_user_model().objects.create_user(username=username, password='pass-12345')
        user.groups.add(Group.objects.get(name=role_name))
        return user

    def assert_has_perm(self, user, codename):
        self.assertTrue(user.has_perm(f'operations.{codename}'), codename)

    def assert_lacks_perm(self, user, codename):
        self.assertFalse(user.has_perm(f'operations.{codename}'), codename)

    def test_admin_has_create_view_change_fulfill_withdraw_annul(self):
        user = self.create_user_for_role('er-admin', ROLE_SIGEDON_ADMIN)

        for codename in {
            'view_expenserequest',
            'add_expenserequest',
            'change_expenserequest',
            'fulfill_expenserequest',
            'withdraw_expenserequest',
            'annul_expenserequest',
            'view_expenserequestattachment',
            'add_expenserequestattachment',
            'delete_expenserequestattachment',
            'view_expenserequestevent',
        }:
            with self.subTest(codename=codename):
                self.assert_has_perm(user, codename)

    def test_admin_does_not_decide_or_hard_delete_or_mutate_events(self):
        user = self.create_user_for_role('er-admin-exclusions', ROLE_SIGEDON_ADMIN)

        for codename in {
            'decide_expenserequest',
            'delete_expenserequest',
            *EVENT_MUTATION_CODENAMES,
        }:
            with self.subTest(codename=codename):
                self.assert_lacks_perm(user, codename)

    def test_operator_create_view_change_withdraw_and_evidence(self):
        user = self.create_user_for_role('er-operator', ROLE_FIELD_OPERATOR)

        for codename in {
            'view_expenserequest',
            'add_expenserequest',
            'change_expenserequest',
            'withdraw_expenserequest',
            'view_expenserequestattachment',
            'add_expenserequestattachment',
            'delete_expenserequestattachment',
            'view_expenserequestevent',
        }:
            with self.subTest(codename=codename):
                self.assert_has_perm(user, codename)

        for codename in {
            'decide_expenserequest',
            'fulfill_expenserequest',
            'annul_expenserequest',
            'delete_expenserequest',
            *EVENT_MUTATION_CODENAMES,
        }:
            with self.subTest(codename=codename):
                self.assert_lacks_perm(user, codename)

    def test_committee_can_view_and_decide_only(self):
        user = self.create_user_for_role('er-committee', ROLE_PROJECT_COMMITTEE)

        for codename in {
            'view_expenserequest',
            'decide_expenserequest',
            'view_expenserequestattachment',
            'view_expenserequestevent',
        }:
            with self.subTest(codename=codename):
                self.assert_has_perm(user, codename)

        for codename in {
            'add_expenserequest',
            'change_expenserequest',
            'fulfill_expenserequest',
            'withdraw_expenserequest',
            'annul_expenserequest',
            'delete_expenserequest',
            'add_expenserequestattachment',
            'delete_expenserequestattachment',
            *EVENT_MUTATION_CODENAMES,
        }:
            with self.subTest(codename=codename):
                self.assert_lacks_perm(user, codename)

    def test_auditor_is_view_only(self):
        user = self.create_user_for_role('er-auditor', ROLE_EXTERNAL_AUDITOR)

        for codename in {
            'view_expenserequest',
            'view_expenserequestattachment',
            'view_expenserequestevent',
        }:
            with self.subTest(codename=codename):
                self.assert_has_perm(user, codename)

        for codename in {
            'add_expenserequest',
            'change_expenserequest',
            'decide_expenserequest',
            'fulfill_expenserequest',
            'withdraw_expenserequest',
            'annul_expenserequest',
            'delete_expenserequest',
            'add_expenserequestattachment',
            'delete_expenserequestattachment',
            *EVENT_MUTATION_CODENAMES,
        }:
            with self.subTest(codename=codename):
                self.assert_lacks_perm(user, codename)

    def test_no_canonical_role_receives_event_mutation_permissions(self):
        for role_name in {
            ROLE_SIGEDON_ADMIN,
            ROLE_FIELD_OPERATOR,
            ROLE_EXTERNAL_AUDITOR,
            ROLE_PROJECT_COMMITTEE,
        }:
            group = Group.objects.get(name=role_name)
            with self.subTest(role=role_name):
                self.assertFalse(
                    group.permissions.filter(codename__in=EVENT_MUTATION_CODENAMES).exists()
                )

    def test_no_canonical_role_receives_delete_expenserequest(self):
        for role_name in {
            ROLE_SIGEDON_ADMIN,
            ROLE_FIELD_OPERATOR,
            ROLE_EXTERNAL_AUDITOR,
            ROLE_PROJECT_COMMITTEE,
        }:
            with self.subTest(role=role_name):
                self.assertFalse(
                    Group.objects.get(name=role_name).permissions.filter(
                        codename='delete_expenserequest'
                    ).exists()
                )

    def test_expense_request_permission_resync_is_idempotent(self):
        first = {
            group.name: set(group.permissions.values_list('codename', flat=True))
            for group in Group.objects.filter(
                name__in={
                    ROLE_SIGEDON_ADMIN,
                    ROLE_FIELD_OPERATOR,
                    ROLE_EXTERNAL_AUDITOR,
                    ROLE_PROJECT_COMMITTEE,
                }
            )
        }

        sync_operation_roles()

        second = {
            group.name: set(group.permissions.values_list('codename', flat=True))
            for group in Group.objects.filter(
                name__in={
                    ROLE_SIGEDON_ADMIN,
                    ROLE_FIELD_OPERATOR,
                    ROLE_EXTERNAL_AUDITOR,
                    ROLE_PROJECT_COMMITTEE,
                }
            )
        }
        self.assertEqual(first, second)
        self.assertTrue(
            Permission.objects.filter(
                content_type__app_label='operations',
                codename='decide_expenserequest',
            ).exists()
        )
