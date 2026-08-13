"""Focused login/logout navigation and open-redirect hardening."""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.operations.tests.helpers import create_user


class LoginNavigationTests(TestCase):
    def setUp(self):
        self.user = create_user(username='login-nav')
        self.password = 'pass-12345'

    def test_login_without_next_redirects_to_panel(self):
        response = self.client.post(
            reverse('login'),
            data={'username': self.user.username, 'password': self.password},
        )
        self.assertRedirects(response, '/panel/')

    def test_safe_internal_next_honored(self):
        destination = reverse('project_list')
        response = self.client.post(
            f"{reverse('login')}?next={destination}",
            data={'username': self.user.username, 'password': self.password},
        )
        self.assertRedirects(response, destination)

    def test_external_next_values_rejected(self):
        rejected = [
            'https://evil.example',
            '//evil.example',
            'https:%2F%2Fevil.example',
            '/\\evil.example',
        ]
        for next_value in rejected:
            with self.subTest(next=next_value):
                client = self.client_class()
                response = client.post(
                    reverse('login'),
                    data={
                        'username': self.user.username,
                        'password': self.password,
                        'next': next_value,
                    },
                )
                self.assertEqual(response.status_code, 302)
                location = response['Location']
                self.assertFalse(location.startswith('https://evil'))
                self.assertFalse(location.startswith('//evil'))
                self.assertNotIn('evil.example', location)
                self.assertEqual(location, '/panel/')

    def test_logout_post_redirects_to_institutional_login(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse('logout'))
        self.assertRedirects(
            response,
            '/accounts/login/',
            fetch_redirect_response=False,
        )
        follow = self.client.get('/panel/')
        self.assertEqual(follow.status_code, 302)
        self.assertIn(reverse('login'), follow['Location'])

    def test_logout_post_ignores_next_parameter(self):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse('logout'),
            data={'next': '/panel/projects/'},
        )
        self.assertRedirects(
            response,
            '/accounts/login/',
            fetch_redirect_response=False,
        )

    def test_logout_get_does_not_end_session(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse('logout'))
        self.assertEqual(response.status_code, 405)
        panel = self.client.get('/panel/')
        self.assertEqual(panel.status_code, 200)

    def test_inactive_user_cannot_login(self):
        self.user.is_active = False
        self.user.save(update_fields=['is_active'])
        response = self.client.post(
            reverse('login'),
            data={'username': self.user.username, 'password': self.password},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['form'].non_field_errors())

    def test_authenticated_root_remains_public(self):
        self.client.force_login(self.user)
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Acceso institucional')
        self.assertNotContains(response, 'Gestión de usuarios')

    def test_login_page_has_no_registration_or_email_reset(self):
        response = self.client.get(reverse('login'))
        self.assertContains(response, 'Acceso institucional')
        self.assertContains(response, 'Volver al portal público')
        self.assertNotContains(response, 'Registrarse')
        self.assertNotContains(response, 'Crear cuenta')
        self.assertNotContains(response, 'password_reset')
        self.assertNotContains(response, '/accounts/password_reset/')

    def test_password_change_available_when_authenticated(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse('password_change'))
        self.assertEqual(response.status_code, 200)
