"""
Static resilience: vendored Bootstrap, Bootstrap Icons, and SweetAlert2.

PRE: repository static/vendor contains committed distributables; no network.
POST: finders resolve required assets; templates reference local paths; verifier
      sentinels align; provenance documents exact versions.
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.contrib.staticfiles import finders
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from core.management.commands.verify_deployment_assets import (
    REQUIRED_RELATIVE_ASSETS,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
VENDOR_ROOT = REPO_ROOT / 'static' / 'vendor'
PROVENANCE = VENDOR_ROOT / 'THIRD_PARTY_ASSETS.md'

BOOTSTRAP_CSS = 'vendor/bootstrap/5.3.3/css/bootstrap.min.css'
BOOTSTRAP_JS = 'vendor/bootstrap/5.3.3/js/bootstrap.bundle.min.js'
ICONS_CSS = 'vendor/bootstrap-icons/1.11.3/font/bootstrap-icons.min.css'
ICONS_WOFF = 'vendor/bootstrap-icons/1.11.3/font/fonts/bootstrap-icons.woff'
ICONS_WOFF2 = 'vendor/bootstrap-icons/1.11.3/font/fonts/bootstrap-icons.woff2'
SWAL_CSS = 'vendor/sweetalert2/11.26.25/sweetalert2.min.css'
SWAL_JS = 'vendor/sweetalert2/11.26.25/sweetalert2.all.min.js'

REQUIRED_LOGICAL = (
    BOOTSTRAP_CSS,
    BOOTSTRAP_JS,
    ICONS_CSS,
    ICONS_WOFF,
    ICONS_WOFF2,
    SWAL_CSS,
    SWAL_JS,
)

CDN_MARKERS = (
    'cdn.jsdelivr.net',
    'cdnjs',
    'unpkg.com',
)


def _finder_path(logical: str) -> Path:
    found = finders.find(logical)
    assert found is not None, f'findstatic miss: {logical}'
    path = Path(found)
    assert path.is_file(), f'not a file: {logical}'
    assert not path.is_symlink(), f'symlink not allowed: {logical}'
    assert path.stat().st_size > 0, f'empty: {logical}'
    return path


class StaticResilienceFinderTests(SimpleTestCase):
    def test_required_logical_files_resolve_nonempty(self):
        for logical in REQUIRED_LOGICAL:
            with self.subTest(logical=logical):
                _finder_path(logical)

    def test_bootstrap_bundle_contains_api_markers(self):
        text = _finder_path(BOOTSTRAP_JS).read_text(encoding='utf-8', errors='replace')
        self.assertIn('bootstrap', text.lower())
        self.assertIn('Dropdown', text)

    def test_sweetalert_contains_swal_marker(self):
        text = _finder_path(SWAL_JS).read_text(encoding='utf-8', errors='replace')
        self.assertIn('Swal', text)

    def test_icon_css_references_local_fonts_only(self):
        css_path = _finder_path(ICONS_CSS)
        css = css_path.read_text(encoding='utf-8')
        urls = re.findall(r'url\(([^)]+)\)', css)
        self.assertTrue(urls)
        base = css_path.parent
        for raw in urls:
            ref = raw.strip('\'"').split('?', 1)[0]
            self.assertFalse(
                ref.startswith(('http://', 'https://', '//')),
                msg=f'external font url: {ref}',
            )
            font = (base / ref).resolve()
            self.assertTrue(font.is_file(), msg=ref)
            self.assertGreater(font.stat().st_size, 0)

    def test_vendor_assets_have_no_unresolved_sourcemap(self):
        for logical in (BOOTSTRAP_CSS, BOOTSTRAP_JS, ICONS_CSS, SWAL_CSS, SWAL_JS):
            with self.subTest(logical=logical):
                text = _finder_path(logical).read_text(encoding='utf-8', errors='replace')
                self.assertNotIn('sourceMappingURL', text)

    def test_verifier_sentinels_include_vendor_assets(self):
        for logical in REQUIRED_LOGICAL:
            self.assertIn(logical, REQUIRED_RELATIVE_ASSETS)

    def test_templates_reference_verifier_vendor_paths(self):
        base = (REPO_ROOT / 'templates' / 'base.html').read_text(encoding='utf-8')
        auth = (
            REPO_ROOT / 'templates' / 'registration' / 'auth_base.html'
        ).read_text(encoding='utf-8')
        for logical in (BOOTSTRAP_CSS, BOOTSTRAP_JS, ICONS_CSS, SWAL_JS):
            self.assertIn(logical, base)
        for logical in (BOOTSTRAP_CSS, ICONS_CSS):
            self.assertIn(logical, auth)
        for marker in CDN_MARKERS:
            self.assertNotIn(marker, base)
            self.assertNotIn(marker, auth)

    def test_provenance_lists_exact_versions(self):
        text = PROVENANCE.read_text(encoding='utf-8')
        self.assertIn('5.3.3', text)
        self.assertIn('1.11.3', text)
        self.assertIn('11.26.25', text)
        self.assertIn('MIT', text)

    def test_verifier_succeeds_when_assets_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'staticfiles'
            for relative in REQUIRED_RELATIVE_ASSETS:
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'x')
            with override_settings(STATIC_ROOT=str(root)):
                call_command('verify_deployment_assets')

    def test_verifier_fails_when_vendor_sentinel_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'staticfiles'
            for relative in REQUIRED_RELATIVE_ASSETS:
                if relative == SWAL_JS:
                    continue
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'x')
            with override_settings(STATIC_ROOT=str(root)):
                with self.assertRaises(CommandError) as ctx:
                    call_command('verify_deployment_assets')
            self.assertIn(SWAL_JS, str(ctx.exception))


class StaticResilienceRenderedTemplateTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username='static-resilience',
            password='pass-12345',
        )
        self.client.force_login(self.user)

    def test_dashboard_uses_local_core_ui_assets(self):
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn(BOOTSTRAP_CSS, html)
        self.assertIn(ICONS_CSS, html)
        self.assertIn(BOOTSTRAP_JS, html)
        self.assertIn(SWAL_JS, html)
        for marker in CDN_MARKERS:
            self.assertNotIn(marker, html)
        # CSS order: Bootstrap before application overrides.
        self.assertLess(html.index(BOOTSTRAP_CSS), html.index('web/css/sigedon.css'))
        # Script order: Bootstrap before SweetAlert (and before Swal.fire usage).
        self.assertLess(html.index(BOOTSTRAP_JS), html.index(SWAL_JS))
        self.assertLess(html.index(SWAL_JS), html.index('Swal.fire'))

    def test_login_page_uses_local_bootstrap_without_cdn(self):
        self.client.logout()
        response = self.client.get(reverse('login'))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn(BOOTSTRAP_CSS, html)
        self.assertIn(ICONS_CSS, html)
        for marker in CDN_MARKERS:
            self.assertNotIn(marker, html)
        self.assertNotIn('cdn.jsdelivr.net', html)
