"""Isolated settings probes for SIGEDON private storage (filesystem | R2).

PRE: subprocess imports core.settings with fictitious env only; no network.
POST: asserts mode selection, WhiteNoise staticfiles, private bucket OPTIONS,
      and fail-closed R2 validation without printing secrets.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

from core.private_storage import (
    DEFAULT_SIGNED_URL_EXPIRY_SECONDS,
    R2_UNUSED_MEDIA_DIRNAME,
    build_filesystem_storages_default,
    build_r2_storage_config,
    build_r2_storages_default,
    resolve_private_storage_mode,
    validate_r2_endpoint_url,
    validate_signed_url_expiry,
)

BASE_DIR = Path(__file__).resolve().parents[2]

SETTINGS_PROBE = """
import json
from core import settings

default = settings.STORAGES['default']
options = dict(default.get('OPTIONS') or {})
# Never echo secrets into the probe payload.
for secret_key in ('access_key', 'secret_key', 'secret_access_key'):
    options.pop(secret_key, None)

r2 = settings.SIGEDON_R2_CONFIG
print(json.dumps({
    'debug': settings.DEBUG,
    'private_storage': settings.SIGEDON_PRIVATE_STORAGE,
    'delivery': settings.SIGEDON_PRIVATE_FILE_DELIVERY,
    'media_root': str(settings.MEDIA_ROOT),
    'default_backend': default.get('BACKEND'),
    'default_options': options,
    'static_backend': settings.STORAGES['staticfiles']['BACKEND'],
    'has_r2_config': r2 is not None,
    'r2_bucket': getattr(r2, 'bucket_name', None),
    'r2_endpoint': getattr(r2, 'endpoint_url', None),
    'r2_expiry': getattr(r2, 'signed_url_expiry_seconds', None),
}, default=str))
"""

FICTITIOUS_R2 = {
    'R2_ACCOUNT_ID': 'fictitiousaccount01',
    'R2_ACCESS_KEY_ID': 'fictitious-access-key',
    'R2_SECRET_ACCESS_KEY': 'fictitious-r2-secret',
    'R2_BUCKET_NAME': 'sigedon-private-test',
}


class PrivateStorageHelperUnitTests(SimpleTestCase):
    def test_filesystem_mode_default(self):
        self.assertEqual(resolve_private_storage_mode(None), 'filesystem')
        self.assertEqual(resolve_private_storage_mode(''), 'filesystem')
        self.assertEqual(resolve_private_storage_mode('  filesystem '), 'filesystem')

    def test_unknown_storage_mode_fail(self):
        with self.assertRaises(ImproperlyConfigured) as err:
            resolve_private_storage_mode('s3')
        self.assertIn('filesystem o r2', str(err.exception))

    def test_invalid_endpoint_and_http_fail(self):
        with self.assertRaises(ImproperlyConfigured) as http_err:
            validate_r2_endpoint_url('http://example.test/r2')
        self.assertIn('HTTPS', str(http_err.exception))

        with self.assertRaises(ImproperlyConfigured):
            validate_r2_endpoint_url('not-a-url')

        with self.assertRaises(ImproperlyConfigured) as cred_err:
            validate_r2_endpoint_url('https://user:pass@example.test')
        self.assertIn('usuario ni contraseña', str(cred_err.exception))

    def test_invalid_expiry_fail(self):
        with self.assertRaises(ImproperlyConfigured):
            validate_signed_url_expiry('nope')
        with self.assertRaises(ImproperlyConfigured):
            validate_signed_url_expiry('30')
        with self.assertRaises(ImproperlyConfigured):
            validate_signed_url_expiry('901')
        self.assertEqual(validate_signed_url_expiry(''), DEFAULT_SIGNED_URL_EXPIRY_SECONDS)

    def test_incomplete_credentials_fail(self):
        incomplete = dict(FICTITIOUS_R2)
        incomplete.pop('R2_SECRET_ACCESS_KEY')
        with self.assertRaises(ImproperlyConfigured) as err:
            build_r2_storage_config(incomplete)
        self.assertIn('R2_SECRET_ACCESS_KEY', str(err.exception))
        self.assertNotIn('fictitious-r2-secret', str(err.exception))

    def test_no_custom_public_domain(self):
        env = dict(FICTITIOUS_R2)
        env['R2_PUBLIC_URL'] = 'https://cdn.example.test'
        with self.assertRaises(ImproperlyConfigured) as err:
            build_r2_storage_config(env)
        self.assertIn('R2_PUBLIC_URL', str(err.exception))

        env = dict(FICTITIOUS_R2)
        env['AWS_S3_CUSTOM_DOMAIN'] = 'cdn.example.test'
        with self.assertRaises(ImproperlyConfigured):
            build_r2_storage_config(env)

    def test_bucket_private_config(self):
        config = build_r2_storage_config(FICTITIOUS_R2)
        options = build_r2_storages_default(config)['OPTIONS']
        self.assertIsNone(options['default_acl'])
        self.assertTrue(options['querystring_auth'])
        self.assertFalse(options['file_overwrite'])
        self.assertNotIn('custom_domain', options)

    def test_filesystem_storages_default_shape(self):
        self.assertEqual(
            build_filesystem_storages_default()['BACKEND'],
            'django.core.files.storage.FileSystemStorage',
        )


class IsolatedPrivateStorageSettingsTests(SimpleTestCase):
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
            'SIGEDON_PRIVATE_STORAGE',
            'SIGEDON_PRIVATE_FILE_DELIVERY',
            'R2_ACCOUNT_ID',
            'R2_ACCESS_KEY_ID',
            'R2_SECRET_ACCESS_KEY',
            'R2_BUCKET_NAME',
            'R2_ENDPOINT_URL',
            'R2_REGION_NAME',
            'R2_ADDRESSING_STYLE',
            'R2_SIGNED_URL_EXPIRY_SECONDS',
            'R2_PUBLIC_URL',
            'AWS_S3_CUSTOM_DOMAIN',
            'AWS_S3_URL_PROTOCOL',
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
        self.assertNotIn('fictitious-r2-secret', result.stderr)
        self.assertNotIn('fictitious-access-key', result.stderr)

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

    def test_filesystem_mode_default(self):
        result = self.run_settings(DJANGO_DEBUG='True', DATABASE_ENGINE='sqlite')
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload['private_storage'], 'filesystem')
        self.assertEqual(
            payload['default_backend'],
            'django.core.files.storage.FileSystemStorage',
        )
        self.assertFalse(payload['has_r2_config'])

    def test_filesystem_preserves_filesystem_storage(self):
        result = self.run_settings(
            **self.production_env(SIGEDON_PRIVATE_STORAGE='filesystem')
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload['private_storage'], 'filesystem')
        self.assertEqual(
            payload['default_backend'],
            'django.core.files.storage.FileSystemStorage',
        )
        self.assertEqual(payload['media_root'], '/var/lib/sigedon/media')

    def test_r2_mode_selects_s3_storage(self):
        result = self.run_settings(
            **self.production_env(
                SIGEDON_PRIVATE_STORAGE='r2',
                SIGEDON_MEDIA_ROOT='',
                **FICTITIOUS_R2,
            )
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload['private_storage'], 'r2')
        self.assertEqual(payload['default_backend'], 'storages.backends.s3.S3Storage')
        self.assertTrue(payload['has_r2_config'])
        self.assertEqual(payload['r2_bucket'], 'sigedon-private-test')
        self.assertTrue(str(payload['media_root']).endswith(R2_UNUSED_MEDIA_DIRNAME))
        self.assertNotIn('fictitious-r2-secret', result.stdout)
        self.assertNotIn('fictitious-access-key', result.stdout)

    def test_staticfiles_remain_whitenoise_in_production(self):
        result = self.run_settings(
            **self.production_env(
                SIGEDON_PRIVATE_STORAGE='r2',
                SIGEDON_MEDIA_ROOT='',
                **FICTITIOUS_R2,
            )
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertIn('whitenoise', payload['static_backend'].lower())
        self.assertIn('CompressedManifest', payload['static_backend'])

    def test_missing_r2_vars_fail_in_production(self):
        result = self.run_settings(
            **self.production_env(
                SIGEDON_PRIVATE_STORAGE='r2',
                SIGEDON_MEDIA_ROOT='',
            )
        )
        self.assert_configuration_error(result, 'Faltan variables R2 obligatorias')

    def test_incomplete_credentials_fail(self):
        incomplete = dict(FICTITIOUS_R2)
        del incomplete['R2_ACCESS_KEY_ID']
        result = self.run_settings(
            **self.production_env(
                SIGEDON_PRIVATE_STORAGE='r2',
                SIGEDON_MEDIA_ROOT='',
                **incomplete,
            )
        )
        self.assert_configuration_error(result, 'R2_ACCESS_KEY_ID')

    def test_invalid_endpoint_http_fail(self):
        result = self.run_settings(
            **self.production_env(
                SIGEDON_PRIVATE_STORAGE='r2',
                SIGEDON_MEDIA_ROOT='',
                R2_ENDPOINT_URL='http://fictitious.r2.example.test',
                **FICTITIOUS_R2,
            )
        )
        self.assert_configuration_error(result, 'HTTPS')

    def test_invalid_expiry_fail(self):
        result = self.run_settings(
            **self.production_env(
                SIGEDON_PRIVATE_STORAGE='r2',
                SIGEDON_MEDIA_ROOT='',
                R2_SIGNED_URL_EXPIRY_SECONDS='15',
                **FICTITIOUS_R2,
            )
        )
        self.assert_configuration_error(result, 'R2_SIGNED_URL_EXPIRY_SECONDS')

    def test_unknown_storage_mode_fail(self):
        result = self.run_settings(
            **self.production_env(SIGEDON_PRIVATE_STORAGE='minio')
        )
        self.assert_configuration_error(result, 'filesystem o r2')

    def test_no_automatic_fallback_to_filesystem(self):
        # Incomplete R2 must fail closed — never silently become filesystem.
        result = self.run_settings(
            **self.production_env(
                SIGEDON_PRIVATE_STORAGE='r2',
                SIGEDON_MEDIA_ROOT='',
                R2_ACCOUNT_ID='fictitiousaccount01',
                R2_BUCKET_NAME='sigedon-private-test',
            )
        )
        self.assertNotEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn('Faltan variables R2', result.stderr)

        # Presence of R2_* alone never selects R2.
        result_fs = self.run_settings(
            **self.production_env(
                SIGEDON_PRIVATE_STORAGE='',
                **FICTITIOUS_R2,
            )
        )
        self.assertEqual(result_fs.returncode, 0, result_fs.stderr)
        payload = json.loads(result_fs.stdout)
        self.assertEqual(payload['private_storage'], 'filesystem')
        self.assertEqual(
            payload['default_backend'],
            'django.core.files.storage.FileSystemStorage',
        )

    def test_bucket_private_config_in_settings(self):
        result = self.run_settings(
            **self.production_env(
                SIGEDON_PRIVATE_STORAGE='r2',
                SIGEDON_MEDIA_ROOT='',
                **FICTITIOUS_R2,
            )
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        options = payload['default_options']
        self.assertIsNone(options.get('default_acl'))
        self.assertTrue(options.get('querystring_auth'))
        self.assertFalse(options.get('file_overwrite'))
        self.assertFalse(options.get('custom_domain'))

    def test_no_custom_public_domain_in_settings(self):
        result = self.run_settings(
            **self.production_env(
                SIGEDON_PRIVATE_STORAGE='r2',
                SIGEDON_MEDIA_ROOT='',
                R2_PUBLIC_URL='https://files.example.test',
                **FICTITIOUS_R2,
            )
        )
        self.assert_configuration_error(result, 'R2_PUBLIC_URL')
