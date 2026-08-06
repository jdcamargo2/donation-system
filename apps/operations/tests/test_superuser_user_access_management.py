"""Superuser-only institutional user management — focused production tests."""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.contrib.sessions.models import Session
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from apps.operations.models import AuditLog, UserAccessProfile
from apps.operations.role_services import (
    get_user_functional_role,
    set_user_functional_role,
    sync_operation_roles,
)
from apps.operations.roles import (
    ROLE_EXTERNAL_AUDITOR,
    ROLE_FIELD_OPERATOR,
    ROLE_PROJECT_COMMITTEE,
    ROLE_SIGEDON_ADMIN,
)
from apps.operations.user_access_services import user_requires_password_change

User = get_user_model()

STRONG_PASSWORD = 'Temporal-Segura-9x!'
CHANGED_PASSWORD = 'Definitiva-Segura-9x!'


@override_settings(SESSION_ENGINE='django.contrib.sessions.backends.db')
class SuperuserUserAccessManagementTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        sync_operation_roles()
        cls.role_admin = Group.objects.get(name=ROLE_SIGEDON_ADMIN)
        cls.role_operator = Group.objects.get(name=ROLE_FIELD_OPERATOR)
        cls.role_committee = Group.objects.get(name=ROLE_PROJECT_COMMITTEE)
        cls.role_auditor = Group.objects.get(name=ROLE_EXTERNAL_AUDITOR)

    def setUp(self):
        self.superuser = User.objects.create_superuser(
            username='access-super',
            email='super@example.com',
            password=STRONG_PASSWORD,
        )
        self.admin_role_user = self._role_user('access-admin', self.role_admin, is_staff=True)
        self.operator = self._role_user('access-operator', self.role_operator)
        self.committee = self._role_user('access-committee', self.role_committee)
        self.auditor = self._role_user('access-auditor', self.role_auditor)
        self.staff_only = User.objects.create_user(
            username='access-staff',
            password=STRONG_PASSWORD,
            is_staff=True,
        )

    def _role_user(self, username, role, *, is_staff=False):
        user = User.objects.create_user(
            username=username,
            password=STRONG_PASSWORD,
            is_staff=is_staff,
        )
        set_user_functional_role(user, role)
        return user

    def _login_super(self):
        self.client.force_login(self.superuser)

    def test_sidebar_visible_only_for_superuser(self):
        self._login_super()
        response = self.client.get(reverse('dashboard'))
        self.assertContains(response, 'Gestión de usuarios')
        self.assertContains(response, reverse('user_access_list'))

        for user in (self.admin_role_user, self.operator, self.committee, self.auditor):
            with self.subTest(user=user.username):
                client = Client()
                client.force_login(user)
                page = client.get(reverse('dashboard'))
                self.assertEqual(page.status_code, 200)
                self.assertNotContains(page, 'Gestión de usuarios')
                self.assertNotContains(page, reverse('user_access_list'))

    def test_public_templates_never_show_user_management(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Acceso institucional')
        self.assertNotContains(response, 'Gestión de usuarios')
        self.assertNotContains(response, reverse('user_access_list'))

    def test_authorization_matrix(self):
        url = reverse('user_access_list')
        self._login_super()
        self.assertEqual(self.client.get(url).status_code, 200)

        for user in (
            self.admin_role_user,
            self.staff_only,
            self.operator,
            self.committee,
            self.auditor,
        ):
            with self.subTest(user=user.username):
                client = Client()
                client.force_login(user)
                self.assertEqual(client.get(url).status_code, 403)
                self.assertEqual(
                    client.post(reverse('user_access_activate', args=[self.operator.pk])).status_code,
                    403,
                )

        anon = Client()
        response = anon.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('login'), response['Location'])

    def test_superuser_creates_institutional_user(self):
        self._login_super()
        before = User.objects.count()
        response = self.client.post(
            reverse('user_access_create'),
            data={
                'username': 'nuevo-operador',
                'first_name': 'Nueva',
                'last_name': 'Persona',
                'email': 'nueva@example.com',
                'functional_role': self.role_operator.pk,
                'temporary_password': STRONG_PASSWORD,
                'temporary_password_confirmation': STRONG_PASSWORD,
                'is_active': 'on',
                'is_superuser': 'on',
                'is_staff': 'on',
                'groups': [self.role_admin.pk],
                'user_permissions': list(
                    Permission.objects.filter(codename='add_user').values_list('pk', flat=True)
                ),
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(User.objects.count(), before + 1)
        created = User.objects.get(username='nuevo-operador')
        self.assertFalse(created.is_superuser)
        self.assertFalse(created.is_staff)
        self.assertEqual(get_user_functional_role(created), self.role_operator)
        self.assertTrue(user_requires_password_change(created))
        self.assertNotContains(
            self.client.get(response['Location']),
            STRONG_PASSWORD,
        )
        audit = AuditLog.objects.filter(
            entity_id=str(created.pk),
            summary__startswith='Usuario institucional creado',
        ).latest('created_at')
        self.assertNotIn(STRONG_PASSWORD, audit.summary)
        self.assertNotIn(created.password, audit.summary)

    def test_weak_and_mismatched_passwords_rejected(self):
        self._login_super()
        weak = self.client.post(
            reverse('user_access_create'),
            data={
                'username': 'weak-user',
                'first_name': '',
                'last_name': '',
                'email': '',
                'functional_role': self.role_operator.pk,
                'temporary_password': '123',
                'temporary_password_confirmation': '123',
                'is_active': 'on',
            },
        )
        self.assertEqual(weak.status_code, 200)
        self.assertFalse(User.objects.filter(username='weak-user').exists())

        mismatch = self.client.post(
            reverse('user_access_create'),
            data={
                'username': 'mismatch-user',
                'first_name': '',
                'last_name': '',
                'email': '',
                'functional_role': self.role_operator.pk,
                'temporary_password': STRONG_PASSWORD,
                'temporary_password_confirmation': CHANGED_PASSWORD,
                'is_active': 'on',
            },
        )
        self.assertEqual(mismatch.status_code, 200)
        self.assertFalse(User.objects.filter(username='mismatch-user').exists())

    def test_duplicate_username_rejected(self):
        self._login_super()
        response = self.client.post(
            reverse('user_access_create'),
            data={
                'username': self.operator.username,
                'first_name': '',
                'last_name': '',
                'email': '',
                'functional_role': self.role_operator.pk,
                'temporary_password': STRONG_PASSWORD,
                'temporary_password_confirmation': STRONG_PASSWORD,
                'is_active': 'on',
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(User.objects.filter(username=self.operator.username).count(), 1)

    def test_cannot_edit_or_reset_or_deactivate_superuser(self):
        other_super = User.objects.create_superuser(
            username='other-super',
            password=STRONG_PASSWORD,
        )
        self._login_super()
        self.assertEqual(
            self.client.get(reverse('user_access_update', args=[other_super.pk])).status_code,
            403,
        )
        self.assertEqual(
            self.client.get(
                reverse('user_access_reset_password', args=[other_super.pk])
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.post(
                reverse('user_access_deactivate', args=[other_super.pk])
            ).status_code,
            403,
        )
        other_super.refresh_from_db()
        self.assertTrue(other_super.is_active)

    def test_cannot_deactivate_self(self):
        self._login_super()
        response = self.client.post(
            reverse('user_access_deactivate', args=[self.superuser.pk])
        )
        self.assertEqual(response.status_code, 302)
        self.superuser.refresh_from_db()
        self.assertTrue(self.superuser.is_active)

    def test_edit_role_and_profile_fields(self):
        self._login_super()
        response = self.client.post(
            reverse('user_access_update', args=[self.operator.pk]),
            data={
                'first_name': 'Oper',
                'last_name': 'Actualizado',
                'email': 'oper@example.com',
                'functional_role': self.role_auditor.pk,
                'is_active': 'on',
                'is_superuser': 'on',
                'is_staff': 'on',
            },
        )
        self.assertEqual(response.status_code, 302)
        self.operator.refresh_from_db()
        self.assertEqual(self.operator.first_name, 'Oper')
        self.assertEqual(self.operator.email, 'oper@example.com')
        self.assertFalse(self.operator.is_superuser)
        self.assertFalse(self.operator.is_staff)
        self.assertEqual(get_user_functional_role(self.operator), self.role_auditor)
        self.assertTrue(
            AuditLog.objects.filter(
                entity_id=str(self.operator.pk),
                summary__startswith='Rol funcional actualizado',
            ).exists()
        )

    def test_activation_deactivation_invalidates_target_sessions(self):
        target_client = Client()
        self.assertTrue(
            target_client.login(username=self.operator.username, password=STRONG_PASSWORD)
        )
        self.assertEqual(target_client.get(reverse('dashboard')).status_code, 200)
        target_sessions_before = Session.objects.count()
        self.assertGreaterEqual(target_sessions_before, 1)

        self._login_super()
        actor_key = self.client.session.session_key
        response = self.client.post(
            reverse('user_access_deactivate', args=[self.operator.pk])
        )
        self.assertEqual(response.status_code, 302)
        self.operator.refresh_from_db()
        self.assertFalse(self.operator.is_active)
        self.assertTrue(
            Session.objects.filter(session_key=actor_key).exists()
        )
        # Target session invalidated: subsequent request is anonymous.
        follow = target_client.get(reverse('dashboard'))
        self.assertEqual(follow.status_code, 302)
        self.assertIn(reverse('login'), follow['Location'])

        reactivate = self.client.post(
            reverse('user_access_activate', args=[self.operator.pk])
        )
        self.assertEqual(reactivate.status_code, 302)
        self.operator.refresh_from_db()
        self.assertTrue(self.operator.is_active)

    def test_password_reset_sets_flag_and_invalidates_sessions(self):
        target_client = Client()
        self.assertTrue(
            target_client.login(username=self.operator.username, password=STRONG_PASSWORD)
        )
        self._login_super()
        actor_key = self.client.session.session_key
        response = self.client.post(
            reverse('user_access_reset_password', args=[self.operator.pk]),
            data={
                'temporary_password': CHANGED_PASSWORD,
                'temporary_password_confirmation': CHANGED_PASSWORD,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.operator.refresh_from_db()
        self.assertTrue(user_requires_password_change(self.operator))
        self.assertTrue(self.operator.check_password(CHANGED_PASSWORD))
        self.assertTrue(Session.objects.filter(session_key=actor_key).exists())
        self.assertEqual(target_client.get(reverse('dashboard')).status_code, 302)
        detail = self.client.get(reverse('user_access_detail', args=[self.operator.pk]))
        self.assertNotContains(detail, CHANGED_PASSWORD)
        self.assertNotContains(detail, self.operator.password)
        audit = AuditLog.objects.filter(
            entity_id=str(self.operator.pk),
            summary__startswith='Contraseña temporal restablecida',
        ).latest('created_at')
        self.assertNotIn(CHANGED_PASSWORD, audit.summary)

    def test_forced_password_change_flow(self):
        profile = UserAccessProfile.objects.create(
            user=self.operator,
            must_change_password=True,
        )
        client = Client()
        self.assertTrue(
            client.login(username=self.operator.username, password=STRONG_PASSWORD)
        )
        blocked = client.get(reverse('dashboard'))
        self.assertEqual(blocked.status_code, 302)
        self.assertEqual(blocked['Location'], reverse('password_change'))

        public = client.get('/')
        self.assertEqual(public.status_code, 200)

        change = client.post(
            reverse('password_change'),
            data={
                'old_password': STRONG_PASSWORD,
                'new_password1': CHANGED_PASSWORD,
                'new_password2': CHANGED_PASSWORD,
            },
        )
        self.assertEqual(change.status_code, 302)
        profile.refresh_from_db()
        self.assertFalse(profile.must_change_password)
        panel = client.get(reverse('dashboard'))
        self.assertEqual(panel.status_code, 200)
        self.assertTrue(
            AuditLog.objects.filter(
                entity_id=str(self.operator.pk),
                summary__startswith='Cambio obligatorio de contraseña completado',
            ).exists()
        )

    def test_existing_user_without_profile_not_blocked(self):
        self.assertFalse(UserAccessProfile.objects.filter(user=self.operator).exists())
        self.client.force_login(self.operator)
        self.assertEqual(self.client.get(reverse('dashboard')).status_code, 200)

    def test_admin_user_access_requires_superuser(self):
        self._login_super()
        self.assertEqual(self.client.get('/admin/auth/user/').status_code, 200)

        for user in (self.admin_role_user, self.staff_only):
            with self.subTest(user=user.username):
                client = Client()
                client.force_login(user)
                # Staff may reach admin index depending on perms, but User module is denied.
                response = client.get('/admin/auth/user/')
                self.assertIn(response.status_code, (403, 302))
