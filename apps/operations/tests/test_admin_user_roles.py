from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import Client, TestCase
from django.urls import reverse

from apps.operations.admin import SigedonUserAdmin
from apps.operations.forms import SigedonAdminUserCreationForm, SigedonUserChangeForm
from apps.operations.role_services import (
    get_user_functional_role,
    get_user_functional_roles,
    operation_role_names,
    set_user_functional_role,
    sync_operation_roles,
)
from apps.operations.roles import (
    ROLE_EXTERNAL_AUDITOR,
    ROLE_FIELD_OPERATOR,
    ROLE_PROJECT_COMMITTEE,
    ROLE_SIGEDON_ADMIN,
)


User = get_user_model()


class SigedonUserAdminRoleTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        sync_operation_roles()
        cls.role_admin = Group.objects.get(name=ROLE_SIGEDON_ADMIN)
        cls.role_operator = Group.objects.get(name=ROLE_FIELD_OPERATOR)
        cls.role_auditor = Group.objects.get(name=ROLE_EXTERNAL_AUDITOR)
        cls.role_committee = Group.objects.get(name=ROLE_PROJECT_COMMITTEE)
        cls.technical_a = Group.objects.create(name='Technical Group A')
        cls.technical_b = Group.objects.create(name='Technical Group B')
        cls.view_project = Permission.objects.get(
            content_type__app_label='operations',
            codename='view_project',
        )
        cls.view_donation = Permission.objects.get(
            content_type__app_label='operations',
            codename='view_donation',
        )

    def functional_names(self, user):
        return set(get_user_functional_roles(user).values_list('name', flat=True))

    def group_names(self, user):
        return set(user.groups.values_list('name', flat=True))

    def permission_codenames(self, user):
        return set(user.user_permissions.values_list('codename', flat=True))

    def change_form_payload(self, user, **overrides):
        """
        PRE: user is persisted; overrides supply admin POST field values.
        POST: returns a minimal valid UserChangeForm payload.
        """
        payload = {
            'username': user.username,
            'password': '!',  # ReadOnlyPasswordHashField accepts the hash marker.
            'date_joined': user.date_joined.strftime('%Y-%m-%d %H:%M:%S'),
            'is_active': 'on' if user.is_active else '',
            'is_staff': 'on' if user.is_staff else '',
            'is_superuser': 'on' if user.is_superuser else '',
            'first_name': user.first_name,
            'last_name': user.last_name,
            'email': user.email,
            'functional_role': '',
            'groups': [],
            'user_permissions': [],
            'last_login': '',
        }
        # Drop empty checkbox fields Django treats as absent.
        for checkbox in ('is_active', 'is_staff', 'is_superuser'):
            if not payload[checkbox]:
                payload.pop(checkbox)
        payload.update(overrides)
        return payload

    def test_admin_registration_replaced_stock_user_admin(self):
        registered = admin.site._registry[User]
        self.assertIsInstance(registered, SigedonUserAdmin)
        self.assertIs(type(registered), SigedonUserAdmin)

    def test_save_with_no_functional_role(self):
        user = User.objects.create_user(username='no-role', password='pass-12345')
        form = SigedonUserChangeForm(
            data=self.change_form_payload(user, functional_role=''),
            instance=user,
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        user.refresh_from_db()
        self.assertIsNone(get_user_functional_role(user))
        self.assertEqual(self.functional_names(user), set())

    def test_assign_one_functional_role(self):
        user = User.objects.create_user(username='one-role', password='pass-12345')
        form = SigedonUserChangeForm(
            data=self.change_form_payload(
                user,
                functional_role=str(self.role_operator.pk),
            ),
            instance=user,
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        user.refresh_from_db()
        self.assertEqual(get_user_functional_role(user), self.role_operator)
        self.assertEqual(self.functional_names(user), {ROLE_FIELD_OPERATOR})

    def test_change_functional_role_replaces_previous(self):
        user = User.objects.create_user(username='change-role', password='pass-12345')
        set_user_functional_role(user, self.role_operator)
        form = SigedonUserChangeForm(
            data=self.change_form_payload(
                user,
                functional_role=str(self.role_auditor.pk),
            ),
            instance=user,
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        user.refresh_from_db()
        self.assertEqual(self.functional_names(user), {ROLE_EXTERNAL_AUDITOR})
        self.assertEqual(get_user_functional_roles(user).count(), 1)

    def test_clear_functional_role(self):
        user = User.objects.create_user(username='clear-role', password='pass-12345')
        set_user_functional_role(user, self.role_committee)
        form = SigedonUserChangeForm(
            data=self.change_form_payload(user, functional_role=''),
            instance=user,
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        user.refresh_from_db()
        self.assertEqual(self.functional_names(user), set())

    def test_preserve_technical_groups_across_role_change(self):
        user = User.objects.create_user(username='keep-tech', password='pass-12345')
        set_user_functional_role(user, self.role_operator)
        user.groups.add(self.technical_a, self.technical_b)
        form = SigedonUserChangeForm(
            data=self.change_form_payload(
                user,
                functional_role=str(self.role_auditor.pk),
                groups=[str(self.technical_a.pk), str(self.technical_b.pk)],
            ),
            instance=user,
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        user.refresh_from_db()
        self.assertEqual(
            self.group_names(user),
            {ROLE_EXTERNAL_AUDITOR, 'Technical Group A', 'Technical Group B'},
        )

    def test_deselecting_technical_group_removes_it(self):
        """Technical groups field is authoritative: deselect removes membership."""
        user = User.objects.create_user(username='drop-tech', password='pass-12345')
        set_user_functional_role(user, self.role_operator)
        user.groups.add(self.technical_a, self.technical_b)
        form = SigedonUserChangeForm(
            data=self.change_form_payload(
                user,
                functional_role=str(self.role_operator.pk),
                groups=[str(self.technical_a.pk)],
            ),
            instance=user,
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        user.refresh_from_db()
        self.assertEqual(
            self.group_names(user),
            {ROLE_FIELD_OPERATOR, 'Technical Group A'},
        )

    def test_preserve_direct_permissions_across_role_change(self):
        user = User.objects.create_user(username='keep-perms', password='pass-12345')
        set_user_functional_role(user, self.role_operator)
        user.user_permissions.add(self.view_project)
        form = SigedonUserChangeForm(
            data=self.change_form_payload(
                user,
                functional_role=str(self.role_auditor.pk),
                user_permissions=[str(self.view_project.pk)],
            ),
            instance=user,
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        user.refresh_from_db()
        self.assertEqual(self.permission_codenames(user), {'view_project'})
        self.assertEqual(self.functional_names(user), {ROLE_EXTERNAL_AUDITOR})

    def test_canonical_groups_excluded_from_technical_selector(self):
        user = User.objects.create_user(username='selector', password='pass-12345')
        form = SigedonUserChangeForm(instance=user)
        technical_names = set(
            form.fields['groups'].queryset.values_list('name', flat=True)
        )
        functional_names = set(
            form.fields['functional_role'].queryset.values_list('name', flat=True)
        )
        for role_name in operation_role_names():
            self.assertNotIn(role_name, technical_names)
            self.assertIn(role_name, functional_names)
        self.assertIn('Technical Group A', technical_names)
        self.assertIn('Technical Group B', technical_names)
        self.assertEqual(functional_names, set(operation_role_names()))

    def test_repair_inconsistent_multi_role_user(self):
        user = User.objects.create_user(username='multi-role', password='pass-12345')
        # Bypass helper to create the inconsistent fixture.
        user.groups.add(self.role_operator, self.role_auditor)

        unbound = SigedonUserChangeForm(instance=user)
        self.assertIsNone(unbound.fields['functional_role'].initial)
        self.assertTrue(unbound.non_field_errors())

        form = SigedonUserChangeForm(
            data=self.change_form_payload(
                user,
                functional_role=str(self.role_committee.pk),
            ),
            instance=user,
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        user.refresh_from_db()
        self.assertEqual(self.functional_names(user), {ROLE_PROJECT_COMMITTEE})
        self.assertEqual(get_user_functional_roles(user).count(), 1)

    def test_superuser_with_no_role_is_valid(self):
        user = User.objects.create_superuser(
            username='super-no-role',
            email='super@example.com',
            password='pass-12345',
        )
        form = SigedonUserChangeForm(
            data=self.change_form_payload(
                user,
                is_superuser='on',
                is_staff='on',
                is_active='on',
                functional_role='',
            ),
            instance=user,
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        user.refresh_from_db()
        self.assertTrue(user.is_superuser)
        self.assertIsNone(get_user_functional_role(user))

    def test_service_style_user_preserves_direct_permission_without_groups(self):
        user = User.objects.create_user(username='kobo.system', password='pass-12345')
        user.user_permissions.add(self.view_donation)
        form = SigedonUserChangeForm(
            data=self.change_form_payload(
                user,
                functional_role='',
                groups=[],
                user_permissions=[str(self.view_donation.pk)],
            ),
            instance=user,
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        user.refresh_from_db()
        self.assertEqual(self.group_names(user), set())
        self.assertEqual(self.permission_codenames(user), {'view_donation'})

    def test_add_form_creates_user_with_selected_role(self):
        form = SigedonAdminUserCreationForm(
            data={
                'username': 'new-with-role',
                'password1': 'ComplexPass-12345',
                'password2': 'ComplexPass-12345',
                'usable_password': 'true',
                'functional_role': str(self.role_admin.pk),
                'groups': [],
                'user_permissions': [],
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
        user = form.save()
        self.assertEqual(get_user_functional_role(user), self.role_admin)

    def test_add_form_creates_user_with_no_role(self):
        form = SigedonAdminUserCreationForm(
            data={
                'username': 'new-no-role',
                'password1': 'ComplexPass-12345',
                'password2': 'ComplexPass-12345',
                'usable_password': 'true',
                'functional_role': '',
                'groups': [],
                'user_permissions': [],
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
        user = form.save()
        self.assertIsNone(get_user_functional_role(user))
        self.assertEqual(self.group_names(user), set())

    def test_role_change_alone_does_not_drop_selected_technical_groups(self):
        user = User.objects.create_user(username='role-only', password='pass-12345')
        set_user_functional_role(user, self.role_operator)
        user.groups.add(self.technical_a)
        form = SigedonUserChangeForm(
            data=self.change_form_payload(
                user,
                functional_role=str(self.role_admin.pk),
                groups=[str(self.technical_a.pk)],
            ),
            instance=user,
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        user.refresh_from_db()
        self.assertIn('Technical Group A', self.group_names(user))
        self.assertEqual(self.functional_names(user), {ROLE_SIGEDON_ADMIN})


class SigedonUserAdminSmokeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        sync_operation_roles()
        Group.objects.create(name='Technical Smoke Group')
        cls.superuser = User.objects.create_superuser(
            username='admin-smoke',
            email='admin-smoke@example.com',
            password='pass-12345',
        )
        cls.target = User.objects.create_user(
            username='target-smoke',
            password='pass-12345',
        )

    def setUp(self):
        self.client = Client()
        self.client.force_login(self.superuser)

    def test_change_form_loads_with_split_role_widgets(self):
        url = reverse('admin:auth_user_change', args=[self.target.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn('Rol funcional SIGEDON', content)
        self.assertIn('Grupos técnicos adicionales', content)
        self.assertIn('user_permissions', content)
        # Canonical roles appear as options of the single-select, not as a
        # duplicate block beside technical groups only.
        for role_name in operation_role_names():
            self.assertContains(response, role_name)
        form = response.context['adminform'].form
        technical_names = set(
            form.fields['groups'].queryset.values_list('name', flat=True)
        )
        for role_name in operation_role_names():
            self.assertNotIn(role_name, technical_names)
        self.assertIn('Technical Smoke Group', technical_names)

    def test_add_form_loads(self):
        url = reverse('admin:auth_user_add')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Rol funcional SIGEDON')
        self.assertContains(response, 'Grupos técnicos adicionales')
