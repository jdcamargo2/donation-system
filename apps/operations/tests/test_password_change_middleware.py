"""Focused query-scope coverage for MustChangePasswordMiddleware.

Broad forced-password behaviour remains in test_superuser_user_access_management.
This module asserts when the middleware does or does not hit
operations_useraccessprofile, and the thin redirect outcomes that depend on it.
"""

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import Client, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.operations.models import UserAccessProfile

User = get_user_model()

PROFILE_TABLE = 'operations_useraccessprofile'
STRONG_PASSWORD = 'Temporal-Segura-9x!'
CHANGED_PASSWORD = 'Definitiva-Segura-9x!'


def _profile_queries(captured):
    """
    PRE: captured is a CaptureQueriesContext after a request.
    POST: returns SQL statements that touch operations_useraccessprofile.
    """
    return [
        query['sql']
        for query in captured.captured_queries
        if PROFILE_TABLE in query['sql']
    ]


@override_settings(SESSION_ENGINE='django.contrib.sessions.backends.db')
class MustChangePasswordMiddlewareQueryScopeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='mw-password-user',
            password=STRONG_PASSWORD,
        )
        self.client = Client()
        self.assertTrue(
            self.client.login(username=self.user.username, password=STRONG_PASSWORD)
        )

    def test_panel_request_performs_exactly_one_profile_check_query(self):
        with CaptureQueriesContext(connection) as captured:
            response = self.client.get(reverse('dashboard'))

        self.assertEqual(response.status_code, 200)
        profile_sql = _profile_queries(captured)
        self.assertEqual(
            len(profile_sql),
            1,
            msg=f'Expected one {PROFILE_TABLE} check; got {profile_sql!r}',
        )

    def test_public_home_performs_no_profile_check_query(self):
        with CaptureQueriesContext(connection) as captured:
            response = self.client.get('/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            _profile_queries(captured),
            [],
            msg='Public / must not run the mandatory-password profile check',
        )

    def test_password_change_path_is_exempt_from_profile_check_query(self):
        with CaptureQueriesContext(connection) as captured:
            response = self.client.get(reverse('password_change'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            _profile_queries(captured),
            [],
            msg='Exempt /accounts/password_change/ must skip middleware profile lookup',
        )

    def test_flagged_user_requesting_panel_is_redirected_to_password_change(self):
        UserAccessProfile.objects.create(user=self.user, must_change_password=True)

        response = self.client.get(reverse('dashboard'))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], reverse('password_change'))

    def test_unflagged_user_requesting_panel_is_not_redirected(self):
        UserAccessProfile.objects.create(user=self.user, must_change_password=False)

        response = self.client.get(reverse('dashboard'))

        self.assertEqual(response.status_code, 200)

    def test_existing_user_without_profile_is_not_blocked(self):
        self.assertFalse(UserAccessProfile.objects.filter(user=self.user).exists())

        response = self.client.get(reverse('dashboard'))

        self.assertEqual(response.status_code, 200)

    def test_successful_password_change_clears_flag_and_allows_panel(self):
        profile = UserAccessProfile.objects.create(
            user=self.user,
            must_change_password=True,
        )
        blocked = self.client.get(reverse('dashboard'))
        self.assertEqual(blocked.status_code, 302)
        self.assertEqual(blocked['Location'], reverse('password_change'))

        change = self.client.post(
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

        panel = self.client.get(reverse('dashboard'))
        self.assertEqual(panel.status_code, 200)
