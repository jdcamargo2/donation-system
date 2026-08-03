"""
Rendered base-template coverage for local core UI static assets.
"""

from __future__ import annotations

from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse


BOOTSTRAP_CSS = 'vendor/bootstrap/5.3.3/css/bootstrap.min.css'
BOOTSTRAP_JS = 'vendor/bootstrap/5.3.3/js/bootstrap.bundle.min.js'
ICONS_CSS = 'vendor/bootstrap-icons/1.11.3/font/bootstrap-icons.min.css'
SWAL_JS = 'vendor/sweetalert2/11.26.25/sweetalert2.all.min.js'


class BaseTemplateLocalAssetsTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username='base-template-assets',
            password='pass-12345',
        )

    def test_base_source_has_no_cdn_for_core_ui(self):
        source = Path('templates/base.html').read_text(encoding='utf-8')
        self.assertIn(BOOTSTRAP_CSS, source)
        self.assertIn(ICONS_CSS, source)
        self.assertIn(BOOTSTRAP_JS, source)
        self.assertIn(SWAL_JS, source)
        self.assertNotIn('cdn.jsdelivr.net', source)
        self.assertNotIn('cdnjs', source)
        self.assertNotIn('unpkg', source)

    def test_authenticated_dashboard_inherits_local_assets(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'base.html')
        html = response.content.decode()
        self.assertIn(BOOTSTRAP_CSS, html)
        self.assertIn(ICONS_CSS, html)
        self.assertIn(BOOTSTRAP_JS, html)
        self.assertIn(SWAL_JS, html)
        self.assertNotIn('cdn.jsdelivr.net', html)
