"""Isolated tests for SIGEDON private-media settings contract."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

from core.media_paths import (
    is_filesystem_root,
    paths_overlap,
    resolve_media_root,
    validate_media_root_path,
)

BASE_DIR = Path(__file__).resolve().parents[2]
SETTINGS_PROBE = """
import json
from core import settings
print(json.dumps({
    'debug': settings.DEBUG,
    'media_root': str(settings.MEDIA_ROOT),
    'static_root': str(settings.STATIC_ROOT),
}, default=str))
"""


class MediaPathHelperTests(SimpleTestCase):
    def test_paths_overlap_detects_equality_and_nesting(self):
        parent = Path('/var/lib/sigedon')
        media = parent / 'media'
        static = parent / 'static'
        nested = media / 'uploads'

        self.assertTrue(paths_overlap(media, media))
        self.assertTrue(paths_overlap(nested, media))
        self.assertTrue(paths_overlap(media, nested))
        self.assertFalse(paths_overlap(media, static))

    def test_filesystem_root_detection(self):
        self.assertTrue(is_filesystem_root(Path('/')))
        self.assertFalse(is_filesystem_root(Path('/var/lib/sigedon/media')))

    def test_validate_rejects_relative_base_dir_static_overlap_and_root(self):
        static_root = Path('/var/lib/sigedon/static')
        base_dir = Path('/opt/sigedon/app')

        with self.assertRaises(ImproperlyConfigured) as relative_error:
            validate_media_root_path(
                Path('media'),
                static_root=static_root,
                base_dir=base_dir,
            )
        self.assertIn('absolute', str(relative_error.exception))

        with self.assertRaises(ImproperlyConfigured):
            validate_media_root_path(
                Path('/'),
                static_root=static_root,
                base_dir=base_dir,
            )

        with self.assertRaises(ImproperlyConfigured):
            validate_media_root_path(
                base_dir,
                static_root=static_root,
                base_dir=base_dir,
            )

        with self.assertRaises(ImproperlyConfigured):
            validate_media_root_path(
                static_root,
                static_root=static_root,
                base_dir=base_dir,
            )

        with self.assertRaises(ImproperlyConfigured):
            validate_media_root_path(
                static_root / 'nested',
                static_root=static_root,
                base_dir=base_dir,
            )

        with self.assertRaises(ImproperlyConfigured):
            validate_media_root_path(
                Path('/var/lib/sigedon'),
                static_root=Path('/var/lib/sigedon/static'),
                base_dir=base_dir,
            )

    def test_validate_accepts_sibling_paths_without_requiring_existence(self):
        missing = Path('/var/lib/sigedon-test-only/media-does-not-need-to-exist')
        result = validate_media_root_path(
            missing,
            static_root=Path('/var/lib/sigedon-test-only/static'),
            base_dir=Path('/opt/sigedon/app'),
        )
        self.assertEqual(result, missing.resolve(strict=False))

    def test_resolve_development_default_and_override(self):
        base_dir = Path('/opt/sigedon/app')
        static_root = Path('/opt/sigedon/app/staticfiles')

        default_root = resolve_media_root(
            debug=True,
            media_root_raw='',
            base_dir=base_dir,
            static_root=static_root,
        )
        self.assertEqual(default_root, base_dir / 'media')

        override = resolve_media_root(
            debug=True,
            media_root_raw='/tmp/sigedon-dev-media',
            base_dir=base_dir,
            static_root=static_root,
        )
        self.assertEqual(override, Path('/tmp/sigedon-dev-media').resolve(strict=False))

    def test_resolve_production_requires_absolute_outside_repo(self):
        base_dir = Path('/opt/sigedon/app')
        static_root = Path('/opt/sigedon/app/staticfiles')

        with self.assertRaises(ImproperlyConfigured) as missing:
            resolve_media_root(
                debug=False,
                media_root_raw='',
                base_dir=base_dir,
                static_root=static_root,
            )
        self.assertIn('SIGEDON_MEDIA_ROOT is required', str(missing.exception))

        with self.assertRaises(ImproperlyConfigured) as blank:
            resolve_media_root(
                debug=False,
                media_root_raw='   ',
                base_dir=base_dir,
                static_root=static_root,
            )
        self.assertIn('SIGEDON_MEDIA_ROOT is required', str(blank.exception))

        with self.assertRaises(ImproperlyConfigured):
            resolve_media_root(
                debug=False,
                media_root_raw='relative/media',
                base_dir=base_dir,
                static_root=static_root,
            )

        with self.assertRaises(ImproperlyConfigured):
            resolve_media_root(
                debug=False,
                media_root_raw=str(base_dir / 'media'),
                base_dir=base_dir,
                static_root=static_root,
            )

        ok = resolve_media_root(
            debug=False,
            media_root_raw='/var/lib/sigedon/media',
            base_dir=base_dir,
            static_root=static_root,
        )
        self.assertEqual(ok, Path('/var/lib/sigedon/media').resolve(strict=False))


class IsolatedMediaSettingsTests(SimpleTestCase):
    def run_settings(self, **overrides):
        environment = os.environ.copy()
        controlled_names = {
            'DJANGO_DEBUG',
            'DJANGO_SECRET_KEY',
            'ALLOWED_HOSTS',
            'DATABASE_ENGINE',
            'POSTGRES_DB',
            'POSTGRES_USER',
            'POSTGRES_PASSWORD',
            'POSTGRES_HOST',
            'POSTGRES_PORT',
            'DATABASE_CONN_MAX_AGE',
            'SIGEDON_MEDIA_ROOT',
        }
        for name in controlled_names:
            environment[name] = ''
        environment.update(overrides)
        return subprocess.run(
            [sys.executable, '-c', SETTINGS_PROBE],
            cwd=BASE_DIR,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )

    def assert_configuration_error(self, result, expected_fragment):
        self.assertNotEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn(expected_fragment, result.stderr)
        self.assertNotIn('fictitious-password', result.stderr)
        self.assertNotIn('fictitious-secret', result.stderr)

    def production_env(self, **extra):
        values = {
            'DJANGO_DEBUG': 'False',
            'DJANGO_SECRET_KEY': 'fictitious-secret',
            'ALLOWED_HOSTS': 'sigedon.example.test',
            'DATABASE_ENGINE': 'postgresql',
            'POSTGRES_DB': 'sigedon_test',
            'POSTGRES_USER': 'sigedon_test_user',
            'POSTGRES_PASSWORD': 'fictitious-password',
            'POSTGRES_HOST': 'db.example.test',
            'SIGEDON_MEDIA_ROOT': '/var/lib/sigedon/media',
        }
        values.update(extra)
        return values

    def test_debug_default_uses_base_dir_media(self):
        result = self.run_settings(DJANGO_DEBUG='True', DATABASE_ENGINE='sqlite')
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload['media_root'], str(BASE_DIR / 'media'))

    def test_debug_accepts_absolute_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            media = str(Path(tmp) / 'dev-media')
            result = self.run_settings(
                DJANGO_DEBUG='True',
                DATABASE_ENGINE='sqlite',
                SIGEDON_MEDIA_ROOT=media,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload['media_root'], str(Path(media).resolve(strict=False)))

    def test_production_without_env_fails(self):
        result = self.run_settings(**self.production_env(SIGEDON_MEDIA_ROOT=''))
        self.assert_configuration_error(result, 'SIGEDON_MEDIA_ROOT is required')

    def test_production_blank_env_fails(self):
        result = self.run_settings(**self.production_env(SIGEDON_MEDIA_ROOT='   '))
        self.assert_configuration_error(result, 'SIGEDON_MEDIA_ROOT is required')

    def test_relative_path_fails(self):
        result = self.run_settings(**self.production_env(SIGEDON_MEDIA_ROOT='media'))
        self.assert_configuration_error(result, 'absolute')

    def test_filesystem_root_fails(self):
        result = self.run_settings(**self.production_env(SIGEDON_MEDIA_ROOT='/'))
        self.assert_configuration_error(result, 'filesystem root')

    def test_base_dir_fails(self):
        result = self.run_settings(**self.production_env(SIGEDON_MEDIA_ROOT=str(BASE_DIR)))
        self.assert_configuration_error(result, 'repository root')

    def test_media_equal_static_fails(self):
        # STATIC_ROOT is BASE_DIR/staticfiles (inside the repo). Production
        # rejects repository-local media before or via overlap validation.
        static = str(BASE_DIR / 'staticfiles')
        result = self.run_settings(**self.production_env(SIGEDON_MEDIA_ROOT=static))
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(
            'STATIC_ROOT' in result.stderr or 'application repository' in result.stderr,
            msg=result.stderr,
        )
        self.assertNotIn('fictitious-password', result.stderr)

    def test_media_inside_static_fails(self):
        nested = str(BASE_DIR / 'staticfiles' / 'nested-media')
        result = self.run_settings(**self.production_env(SIGEDON_MEDIA_ROOT=nested))
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(
            'STATIC_ROOT' in result.stderr or 'application repository' in result.stderr,
            msg=result.stderr,
        )
    def test_static_inside_media_fails(self):
        # STATIC_ROOT is BASE_DIR/staticfiles; a parent of BASE_DIR nests it.
        result = self.run_settings(
            **self.production_env(SIGEDON_MEDIA_ROOT=str(BASE_DIR.parent))
        )
        self.assert_configuration_error(result, 'STATIC_ROOT')

    def test_valid_absolute_sibling_succeeds_without_existing_path(self):
        missing = '/var/lib/sigedon-media-settings-test/does-not-exist'
        result = self.run_settings(**self.production_env(SIGEDON_MEDIA_ROOT=missing))
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload['media_root'], str(Path(missing).resolve(strict=False)))
        self.assertNotIn('fictitious-password', result.stdout)
        self.assertNotIn('fictitious-secret', result.stdout)

    def test_production_rejects_repository_local_media(self):
        result = self.run_settings(
            **self.production_env(SIGEDON_MEDIA_ROOT=str(BASE_DIR / 'media'))
        )
        self.assert_configuration_error(result, 'application repository')
