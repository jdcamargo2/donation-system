"""Focused routing architecture for public root + /panel/ internal app."""

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from apps.operations.tests.helpers import create_user


class AccessUrlArchitectureTests(TestCase):
    def setUp(self):
        self.user = create_user(username='panel-user')

    def test_operational_reverses_live_under_panel(self):
        self.assertEqual(reverse('dashboard'), '/panel/')
        self.assertEqual(reverse('project_list'), '/panel/projects/')
        self.assertEqual(reverse('expense_request_list'), '/panel/expense-requests/')
        self.assertEqual(reverse('donation_list'), '/panel/donations/')
        self.assertEqual(reverse('allocation_list'), '/panel/allocations/')
        self.assertEqual(reverse('expense_list'), '/panel/expenses/')
        self.assertEqual(reverse('audit_log_list'), '/panel/audit/')
        self.assertEqual(reverse('user_access_list'), '/panel/usuarios/')
        self.assertTrue(reverse('kobo:hub').startswith('/panel/integrations/kobo/'))

    def test_public_canonical_spanish_paths(self):
        self.assertEqual(reverse('public_portal:public_home'), '/')
        self.assertEqual(reverse('public_portal:public_project_list'), '/proyectos/')
        self.assertEqual(reverse('public_portal:public_updates_feed'), '/avances/')
        self.assertEqual(reverse('public_portal:public_projects_json'), '/datos/proyectos.json')
        self.assertEqual(reverse('public_portal:public_metrics_json'), '/datos/metricas.json')

    def test_anonymous_panel_redirects_to_login_with_safe_next(self):
        response = self.client.get('/panel/')
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('login'), response['Location'])
        self.assertIn('next=/panel/', response['Location'])

    def test_authenticated_user_reaches_panel(self):
        self.client.force_login(self.user)
        response = self.client.get('/panel/')
        self.assertEqual(response.status_code, 200)

    def test_old_internal_root_paths_return_404(self):
        self.client.force_login(self.user)
        for path in (
            '/projects/',
            '/donations/',
            '/allocations/',
            '/expenses/',
            '/expense-requests/',
            '/updates/',
            '/institutions/',
        ):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 404)

    def test_old_internal_post_returns_404_without_mutation(self):
        self.client.force_login(self.user)
        before = get_user_model().objects.count()
        response = self.client.post('/projects/new/', data={'name': 'x'})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(get_user_model().objects.count(), before)

    def test_legacy_transparency_permanent_redirects(self):
        cases = [
            ('/transparency/', '/'),
            ('/transparency/projects/', '/proyectos/'),
            ('/transparency/updates/', '/avances/'),
            ('/transparency/data/projects.json', '/datos/proyectos.json'),
            ('/transparency/data/metrics.json', '/datos/metricas.json'),
        ]
        for legacy, canonical in cases:
            with self.subTest(legacy=legacy):
                response = self.client.get(legacy)
                self.assertEqual(response.status_code, 301)
                self.assertEqual(response['Location'], canonical)
                follow = self.client.get(legacy, follow=False)
                self.assertEqual(follow.status_code, 301)

    def test_legacy_redirect_preserves_safe_page_query(self):
        response = self.client.get('/transparency/projects/?page=2&evil=1')
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response['Location'], '/proyectos/?page=2')

    def test_legacy_head_redirects(self):
        response = self.client.head('/transparency/')
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response['Location'], '/')

    def test_kobo_management_under_panel_and_old_path_404(self):
        self.client.force_login(self.user)
        self.assertEqual(reverse('kobo:hub'), '/panel/integrations/kobo/')
        self.assertEqual(self.client.get('/integrations/kobo/').status_code, 404)
        self.assertEqual(reverse('kobo_webhook'), '/integrations/kobo/webhook/')

    def test_password_reset_routes_disabled(self):
        for path in (
            '/accounts/password_reset/',
            '/accounts/password_reset/done/',
            '/accounts/reset/uid/token/',
            '/accounts/reset/done/',
        ):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 404)

    def test_web_urls_not_mounted(self):
        # Empty web.urls must not be remounted at root.
        from web.urls import urlpatterns as web_patterns

        self.assertEqual(web_patterns, [])
