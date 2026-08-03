"""
WhiteNoise / production static serving contract (RENDER-1).

PRE: does not write production STATIC_ROOT, serve private media, open ports,
     or require network access.
POST: covers dependency, middleware order, STORAGES, collectstatic+manifest,
      HTTP serving via WhiteNoise, and private-media separation.
"""

from __future__ import annotations

import io
import os
import re
import tempfile
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.test import SimpleTestCase, override_settings


REPO_ROOT = Path(__file__).resolve().parents[2]

COMPRESSED_MANIFEST = (
    'whitenoise.storage.CompressedManifestStaticFilesStorage'
)
FILESYSTEM_DEFAULT = 'django.core.files.storage.FileSystemStorage'
PLAIN_STATIC = 'django.contrib.staticfiles.storage.StaticFilesStorage'

CANONICAL_ASSETS = (
    'web/css/sigedon.css',
    'vendor/bootstrap/5.3.3/css/bootstrap.min.css',
    'vendor/bootstrap/5.3.3/js/bootstrap.bundle.min.js',
    'vendor/bootstrap-icons/1.11.3/font/bootstrap-icons.min.css',
    'vendor/bootstrap-icons/1.11.3/font/fonts/bootstrap-icons.woff2',
    'vendor/sweetalert2/11.26.25/sweetalert2.all.min.js',
)


class WhiteNoiseDependencyTests(SimpleTestCase):
    def test_whitenoise_importable(self):
        import importlib.metadata

        import whitenoise
        from whitenoise.middleware import WhiteNoiseMiddleware
        from whitenoise.storage import CompressedManifestStaticFilesStorage

        self.assertEqual(importlib.metadata.version('whitenoise'), '6.12.0')
        self.assertTrue(whitenoise)
        self.assertTrue(WhiteNoiseMiddleware)
        self.assertTrue(CompressedManifestStaticFilesStorage)


class WhiteNoiseMiddlewareOrderTests(SimpleTestCase):
    def test_whitenoise_immediately_follows_security_middleware(self):
        middleware = list(settings.MIDDLEWARE)
        self.assertIn(
            'django.middleware.security.SecurityMiddleware',
            middleware,
        )
        self.assertIn(
            'whitenoise.middleware.WhiteNoiseMiddleware',
            middleware,
        )
        security_idx = middleware.index(
            'django.middleware.security.SecurityMiddleware'
        )
        whitenoise_idx = middleware.index(
            'whitenoise.middleware.WhiteNoiseMiddleware'
        )
        self.assertEqual(whitenoise_idx, security_idx + 1)
        # WhiteNoise precedes session/auth/CSRF and request-id middleware.
        for later in (
            'core.request_ids.RequestIdMiddleware',
            'django.contrib.sessions.middleware.SessionMiddleware',
            'django.middleware.csrf.CsrfViewMiddleware',
            'django.contrib.auth.middleware.AuthenticationMiddleware',
        ):
            self.assertLess(whitenoise_idx, middleware.index(later))


class WhiteNoiseStorageContractTests(SimpleTestCase):
    def test_default_storage_remains_filesystem(self):
        self.assertEqual(
            settings.STORAGES['default']['BACKEND'],
            FILESYSTEM_DEFAULT,
        )

    def test_staticfiles_backend_follows_debug_conditional_contract(self):
        backend = settings.STORAGES['staticfiles']['BACKEND']
        self.assertIn(backend, {PLAIN_STATIC, COMPRESSED_MANIFEST})
        # STORAGES is evaluated once at import from DEBUG at that moment.
        # Live DEBUG may later differ under the test runner; the source
        # conditional is the production contract (asserted below).
        settings_path = REPO_ROOT / 'core' / 'settings.py'
        text = settings_path.read_text(encoding='utf-8')
        self.assertIn(
            "whitenoise.storage.CompressedManifestStaticFilesStorage'\n"
            "            if not DEBUG\n"
            "            else 'django.contrib.staticfiles.storage.StaticFilesStorage'",
            text,
        )

    def test_production_staticfiles_backend_is_compressed_manifest(self):
        # DEBUG is fixed at settings import; assert the conditional source
        # contract and production-safe WhiteNoise flags on the live settings.
        settings_path = REPO_ROOT / 'core' / 'settings.py'
        text = settings_path.read_text(encoding='utf-8')
        self.assertIn(COMPRESSED_MANIFEST, text)
        self.assertIn('if not DEBUG', text)
        self.assertIn(FILESYSTEM_DEFAULT, text)
        self.assertNotIn('WHITENOISE_USE_FINDERS = True', text)
        self.assertNotIn('WHITENOISE_AUTOREFRESH = True', text)
        self.assertEqual(settings.WHITENOISE_USE_FINDERS, False)
        self.assertEqual(settings.WHITENOISE_AUTOREFRESH, False)
        # No cloud object-storage backend wired (RENDER-2 deferred).
        self.assertNotIn("storages.backends", text)
        self.assertNotIn('boto3', text)
        self.assertNotIn("BACKEND': 'storages", text)
        self.assertNotIn('AWS_STORAGE', text)
        self.assertNotIn("import boto", text)

    def test_static_root_does_not_overlap_source_or_media(self):
        static_root = Path(settings.STATIC_ROOT).resolve()
        source_static = (REPO_ROOT / 'static').resolve()
        media_root = Path(settings.MEDIA_ROOT).resolve()
        self.assertNotEqual(static_root, source_static)
        self.assertFalse(
            str(static_root).startswith(str(source_static) + os.sep)
        )
        self.assertNotEqual(static_root, media_root)
        self.assertFalse(
            str(static_root).startswith(str(media_root) + os.sep)
        )
        self.assertFalse(
            str(media_root).startswith(str(static_root) + os.sep)
        )

    def test_static_url_contract(self):
        self.assertEqual(settings.STATIC_URL, '/static/')

    def test_ci_settings_forces_compressed_manifest(self):
        import json
        import subprocess
        import sys

        probe = (
            'import json; from core import ci_settings; '
            'print(json.dumps({'
            "'static_backend': ci_settings.STORAGES['staticfiles']['BACKEND'],"
            "'default_backend': ci_settings.STORAGES['default']['BACKEND'],"
            "'use_finders': ci_settings.WHITENOISE_USE_FINDERS,"
            "'autorefresh': ci_settings.WHITENOISE_AUTOREFRESH,"
            '}))'
        )
        with tempfile.TemporaryDirectory() as tmp:
            static_root = str(Path(tmp) / 'collected')
            env = os.environ.copy()
            for name in (
                'DJANGO_DEBUG',
                'DJANGO_SECRET_KEY',
                'ALLOWED_HOSTS',
                'DATABASE_ENGINE',
                'SIGEDON_MEDIA_ROOT',
                'SIGEDON_CI_STATIC_ROOT',
                'KOBO_ENABLED',
            ):
                env[name] = ''
            env.update(
                {
                    'DJANGO_DEBUG': 'True',
                    'DJANGO_SECRET_KEY': 'ci-only-secret',
                    'ALLOWED_HOSTS': 'localhost',
                    'DATABASE_ENGINE': 'sqlite',
                    'KOBO_ENABLED': 'False',
                    'SIGEDON_CI_STATIC_ROOT': static_root,
                }
            )
            result = subprocess.run(
                [sys.executable, '-c', probe],
                cwd=str(REPO_ROOT),
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload['static_backend'], COMPRESSED_MANIFEST)
            self.assertEqual(payload['default_backend'], FILESYSTEM_DEFAULT)
            self.assertFalse(payload['use_finders'])
            self.assertFalse(payload['autorefresh'])


class WhiteNoiseCollectstaticManifestTests(SimpleTestCase):
    def test_collectstatic_manifest_and_verifier(self):
        with tempfile.TemporaryDirectory(prefix='sigedon-wn-static-') as tmp:
            root = Path(tmp) / 'staticfiles'
            root.mkdir()
            storages = {
                'default': {'BACKEND': FILESYSTEM_DEFAULT},
                'staticfiles': {'BACKEND': COMPRESSED_MANIFEST},
            }
            with override_settings(
                STATIC_ROOT=str(root),
                STORAGES=storages,
                WHITENOISE_USE_FINDERS=False,
                WHITENOISE_AUTOREFRESH=False,
            ):
                # Re-bind storage after STORAGES override.
                from django.contrib.staticfiles.storage import (
                    staticfiles_storage as storage,
                )

                call_command('collectstatic', interactive=False, verbosity=0)
                manifest = root / 'staticfiles.json'
                self.assertTrue(manifest.is_file(), 'staticfiles.json missing')

                for logical in CANONICAL_ASSETS:
                    with self.subTest(logical=logical):
                        hashed = storage.stored_name(logical)
                        self.assertTrue(storage.exists(hashed))
                        # Hashed CSS/JS names include a content hash segment.
                        if logical.endswith(('.css', '.js')):
                            self.assertNotEqual(hashed, logical)
                        self.assertTrue((root / hashed).is_file())

                icons_css_name = storage.stored_name(
                    'vendor/bootstrap-icons/1.11.3/font/bootstrap-icons.min.css'
                )
                css_text = (root / icons_css_name).read_text(encoding='utf-8')
                urls = re.findall(r'url\(([^)]+)\)', css_text)
                self.assertTrue(urls)
                css_dir = (root / icons_css_name).parent
                for raw in urls:
                    ref = raw.strip('\'"').split('?', 1)[0]
                    self.assertFalse(
                        ref.startswith(('http://', 'https://', '//')),
                        msg=f'external font after hashing: {ref}',
                    )
                    font_path = (css_dir / ref).resolve()
                    self.assertTrue(
                        font_path.is_file(),
                        msg=f'missing rewritten font: {ref}',
                    )

                call_command('verify_deployment_assets')

                # Private media must not be copied into STATIC_ROOT.
                for path in root.rglob('*'):
                    if path.is_file():
                        self.assertNotIn('/media/', str(path).replace('\\', '/'))


class WhiteNoiseHttpServingTests(SimpleTestCase):
    def test_whitenoise_serves_hashed_assets_not_private_media(self):
        from whitenoise import WhiteNoise

        with tempfile.TemporaryDirectory(prefix='sigedon-wn-http-') as tmp:
            static_root = Path(tmp) / 'staticfiles'
            media_root = Path(tmp) / 'media'
            static_root.mkdir()
            media_root.mkdir()
            secret = media_root / 'secret-doc.txt'
            secret.write_bytes(b'private-media-payload')

            storages = {
                'default': {'BACKEND': FILESYSTEM_DEFAULT},
                'staticfiles': {'BACKEND': COMPRESSED_MANIFEST},
            }
            with override_settings(
                STATIC_ROOT=str(static_root),
                MEDIA_ROOT=str(media_root),
                STORAGES=storages,
                WHITENOISE_USE_FINDERS=False,
                WHITENOISE_AUTOREFRESH=False,
            ):
                from django.contrib.staticfiles.storage import (
                    staticfiles_storage as storage,
                )

                call_command('collectstatic', interactive=False, verbosity=0)

                # Fresh WhiteNoise rooted at collected STATIC_ROOT (no Django app).
                # prefix matches STATIC_URL without leading slash (WhiteNoise contract).
                wn = WhiteNoise(
                    application=self._fallback_404_app,
                    root=str(static_root),
                    prefix='static/',
                )

                samples = (
                    'web/css/sigedon.css',
                    'vendor/bootstrap/5.3.3/css/bootstrap.min.css',
                    'vendor/bootstrap/5.3.3/js/bootstrap.bundle.min.js',
                    'vendor/bootstrap-icons/1.11.3/font/fonts/bootstrap-icons.woff2',
                    'vendor/sweetalert2/11.26.25/sweetalert2.all.min.js',
                )
                for logical in samples:
                    with self.subTest(logical=logical):
                        hashed = storage.stored_name(logical)
                        path = '/static/' + hashed.replace('\\', '/')
                        status, headers, body = self._wn_get(wn, path)
                        self.assertEqual(status, 200, msg=logical)
                        self.assertTrue(body)
                        content_type = headers.get(
                            'Content-Type', headers.get('content-type', '')
                        )
                        self.assertTrue(content_type)
                        self.assertNotIn('cdn.jsdelivr.net', body.decode(
                            'utf-8', errors='replace'
                        )[:200] if logical.endswith(('.css', '.js')) else '')
                        # Hashed assets are immutable / long-cache.
                        cache_control = headers.get(
                            'Cache-Control', headers.get('cache-control', '')
                        )
                        self.assertIn('max-age', cache_control.lower())

                missing_status, _, _ = self._wn_get(
                    wn, '/static/does-not-exist-asset.css'
                )
                self.assertEqual(missing_status, 404)

                # Guessed static path for a private media filename must 404.
                media_guess_status, _, media_body = self._wn_get(
                    wn, '/static/secret-doc.txt'
                )
                self.assertEqual(media_guess_status, 404)
                self.assertNotIn(b'private-media-payload', media_body)

                # WhiteNoise must not serve MEDIA_ROOT even if asked via /media/.
                media_status, _, media_body2 = self._wn_get(
                    wn, '/media/secret-doc.txt'
                )
                self.assertEqual(media_status, 404)
                self.assertNotIn(b'private-media-payload', media_body2)

                self.assertEqual(
                    settings.STORAGES['default']['BACKEND'],
                    FILESYSTEM_DEFAULT,
                )
                self.assertNotEqual(
                    settings.STORAGES['default']['BACKEND'],
                    COMPRESSED_MANIFEST,
                )

    @staticmethod
    def _fallback_404_app(environ, start_response):
        start_response('404 Not Found', [('Content-Type', 'text/plain')])
        return [b'not-found']

    @staticmethod
    def _wn_get(wn, path: str):
        status_holder: list[str] = []
        headers_holder: dict[str, str] = {}

        def start_response(status, headers, exc_info=None):
            status_holder.append(status)
            headers_holder.update(headers)
            return None

        environ = {
            'REQUEST_METHOD': 'GET',
            'PATH_INFO': path,
            'SCRIPT_NAME': '',
            'QUERY_STRING': '',
            'SERVER_NAME': 'testserver',
            'SERVER_PORT': '80',
            'wsgi.version': (1, 0),
            'wsgi.url_scheme': 'http',
            'wsgi.input': io.BytesIO(),
            'wsgi.errors': io.StringIO(),
            'wsgi.multithread': False,
            'wsgi.multiprocess': False,
            'wsgi.run_once': False,
        }
        body_iter = wn(environ, start_response)
        body = b''.join(body_iter)
        status_code = int(status_holder[0].split()[0])
        return status_code, headers_holder, body


class WhiteNoisePrivateMediaSeparationTests(SimpleTestCase):
    def test_urls_do_not_mount_media(self):
        from django.urls import get_resolver

        resolver = get_resolver()
        pattern_str = str(resolver.url_patterns)
        self.assertNotIn('MEDIA_URL', pattern_str)
        urls_source = (REPO_ROOT / 'core' / 'urls.py').read_text(encoding='utf-8')
        self.assertNotIn('static(settings.MEDIA_URL', urls_source)
        self.assertIn('never mounted via static()', urls_source)

    def test_no_r2_configuration_in_settings(self):
        text = (REPO_ROOT / 'core' / 'settings.py').read_text(encoding='utf-8')
        self.assertNotIn('storages.backends', text)
        self.assertNotIn('boto3', text)
        self.assertNotIn('AWS_STORAGE', text)
        self.assertNotIn("BACKEND': 'storages", text)
        self.assertNotIn('django_storages', text.lower())
        requirements = (REPO_ROOT / 'requirements.txt').read_text(encoding='utf-8')
        self.assertNotIn('django-storages', requirements)
        self.assertNotIn('boto3', requirements)
