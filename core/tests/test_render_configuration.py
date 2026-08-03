"""Tests for verify_render_configuration (Render runtime contract).

PRE: uses override_settings / fictitious env only; no network.
POST: healthy fictional Render config passes; known misconfigurations fail
      without printing secrets.
"""

from __future__ import annotations

import io
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings

from core.render_configuration import (
    configuration_is_healthy,
    verify_render_configuration,
)
from core.private_storage import R2StorageConfig


def _fictitious_r2() -> R2StorageConfig:
    return R2StorageConfig(
        account_id='fictitiousaccount01',
        access_key_id='fictitious-access-key',
        secret_access_key='fictitious-r2-secret',
        bucket_name='sigedon-private-test',
        endpoint_url='https://fictitiousaccount01.r2.cloudflarestorage.com',
        region_name='auto',
        signed_url_expiry_seconds=300,
        addressing_style='path',
    )


HEALTHY_STORAGES = {
    'default': {
        'BACKEND': 'storages.backends.s3.S3Storage',
        'OPTIONS': {
            'default_acl': None,
            'querystring_auth': True,
            'custom_domain': None,
        },
    },
    'staticfiles': {
        'BACKEND': 'whitenoise.storage.CompressedManifestStaticFilesStorage',
    },
}


@override_settings(
    DEBUG=False,
    DATABASE_ENGINE='postgresql',
    ALLOWED_HOSTS=['sigedon-staging.onrender.com'],
    CSRF_TRUSTED_ORIGINS=['https://sigedon-staging.onrender.com'],
    SESSION_COOKIE_SECURE=True,
    CSRF_COOKIE_SECURE=True,
    SECURE_PROXY_SSL_HEADER=('HTTP_X_FORWARDED_PROTO', 'https'),
    SECURE_HSTS_PRELOAD=False,
    SIGEDON_PRIVATE_STORAGE='r2',
    SIGEDON_R2_CONFIG=_fictitious_r2(),
    STORAGES=HEALTHY_STORAGES,
    KOBO_ENABLED=False,
    KOBO_BASE_URL='',
    KOBO_API_TOKEN='',
    KOBO_WEBHOOK_USERNAME='',
    KOBO_WEBHOOK_SECRET='',
)
class VerifyRenderConfigurationHealthyTests(SimpleTestCase):
    def test_healthy_fictional_configuration_succeeds(self):
        findings = verify_render_configuration(environ={})
        self.assertTrue(configuration_is_healthy(findings), msg=findings)
        stdout = io.StringIO()
        call_command('verify_render_configuration', stdout=stdout)
        output = stdout.getvalue()
        self.assertIn('verify_render_configuration=ok', output)
        self.assertNotIn('fictitious-r2-secret', output)
        self.assertNotIn('fictitious-access-key', output)


@override_settings(
    DEBUG=True,
    DATABASE_ENGINE='postgresql',
    ALLOWED_HOSTS=['sigedon-staging.onrender.com'],
    CSRF_TRUSTED_ORIGINS=['https://sigedon-staging.onrender.com'],
    SESSION_COOKIE_SECURE=True,
    CSRF_COOKIE_SECURE=True,
    SECURE_PROXY_SSL_HEADER=('HTTP_X_FORWARDED_PROTO', 'https'),
    SIGEDON_PRIVATE_STORAGE='r2',
    SIGEDON_R2_CONFIG=_fictitious_r2(),
    STORAGES=HEALTHY_STORAGES,
    KOBO_ENABLED=False,
)
class VerifyRenderDebugFailsTests(SimpleTestCase):
    def test_debug_true_fails(self):
        findings = verify_render_configuration(environ={})
        self.assertFalse(configuration_is_healthy(findings))
        codes = {item.code for item in findings if not item.ok}
        self.assertIn('debug_enabled', codes)


@override_settings(
    DEBUG=False,
    DATABASE_ENGINE='sqlite',
    ALLOWED_HOSTS=['sigedon-staging.onrender.com'],
    CSRF_TRUSTED_ORIGINS=['https://sigedon-staging.onrender.com'],
    SESSION_COOKIE_SECURE=True,
    CSRF_COOKIE_SECURE=True,
    SECURE_PROXY_SSL_HEADER=('HTTP_X_FORWARDED_PROTO', 'https'),
    SIGEDON_PRIVATE_STORAGE='r2',
    SIGEDON_R2_CONFIG=_fictitious_r2(),
    STORAGES=HEALTHY_STORAGES,
    KOBO_ENABLED=False,
)
class VerifyRenderSqliteFailsTests(SimpleTestCase):
    def test_sqlite_fails(self):
        findings = verify_render_configuration(environ={})
        self.assertFalse(configuration_is_healthy(findings))
        self.assertTrue(
            any(item.code == 'database_engine' and not item.ok for item in findings)
        )


@override_settings(
    DEBUG=False,
    DATABASE_ENGINE='postgresql',
    ALLOWED_HOSTS=['sigedon-staging.onrender.com'],
    CSRF_TRUSTED_ORIGINS=['https://sigedon-staging.onrender.com'],
    SESSION_COOKIE_SECURE=True,
    CSRF_COOKIE_SECURE=True,
    SECURE_PROXY_SSL_HEADER=('HTTP_X_FORWARDED_PROTO', 'https'),
    SIGEDON_PRIVATE_STORAGE='filesystem',
    SIGEDON_R2_CONFIG=None,
    STORAGES={
        'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
        'staticfiles': {
            'BACKEND': 'whitenoise.storage.CompressedManifestStaticFilesStorage',
        },
    },
    KOBO_ENABLED=False,
)
class VerifyRenderFilesystemFailsTests(SimpleTestCase):
    def test_filesystem_final_mode_fails(self):
        findings = verify_render_configuration(environ={})
        self.assertFalse(configuration_is_healthy(findings))
        self.assertTrue(
            any(
                item.code == 'private_storage_mode' and not item.ok
                for item in findings
            )
        )


@override_settings(
    DEBUG=False,
    DATABASE_ENGINE='postgresql',
    ALLOWED_HOSTS=[],
    CSRF_TRUSTED_ORIGINS=['https://sigedon-staging.onrender.com'],
    SESSION_COOKIE_SECURE=True,
    CSRF_COOKIE_SECURE=True,
    SECURE_PROXY_SSL_HEADER=('HTTP_X_FORWARDED_PROTO', 'https'),
    SIGEDON_PRIVATE_STORAGE='r2',
    SIGEDON_R2_CONFIG=_fictitious_r2(),
    STORAGES=HEALTHY_STORAGES,
    KOBO_ENABLED=False,
)
class VerifyRenderMissingHostTests(SimpleTestCase):
    def test_missing_host_fails(self):
        findings = verify_render_configuration(environ={})
        self.assertFalse(configuration_is_healthy(findings))
        self.assertTrue(
            any(item.code == 'allowed_hosts' and not item.ok for item in findings)
        )


@override_settings(
    DEBUG=False,
    DATABASE_ENGINE='postgresql',
    ALLOWED_HOSTS=['sigedon-staging.onrender.com'],
    CSRF_TRUSTED_ORIGINS=['http://sigedon-staging.onrender.com'],
    SESSION_COOKIE_SECURE=True,
    CSRF_COOKIE_SECURE=True,
    SECURE_PROXY_SSL_HEADER=('HTTP_X_FORWARDED_PROTO', 'https'),
    SIGEDON_PRIVATE_STORAGE='r2',
    SIGEDON_R2_CONFIG=_fictitious_r2(),
    STORAGES=HEALTHY_STORAGES,
    KOBO_ENABLED=False,
)
class VerifyRenderNonHttpsOriginTests(SimpleTestCase):
    def test_non_https_trusted_origin_fails(self):
        findings = verify_render_configuration(environ={})
        self.assertFalse(configuration_is_healthy(findings))
        self.assertTrue(
            any(item.code == 'csrf_origins' and not item.ok for item in findings)
        )


@override_settings(
    DEBUG=False,
    DATABASE_ENGINE='postgresql',
    ALLOWED_HOSTS=['sigedon-staging.onrender.com'],
    CSRF_TRUSTED_ORIGINS=['https://sigedon-staging.onrender.com'],
    SESSION_COOKIE_SECURE=False,
    CSRF_COOKIE_SECURE=False,
    SECURE_PROXY_SSL_HEADER=('HTTP_X_FORWARDED_PROTO', 'https'),
    SIGEDON_PRIVATE_STORAGE='r2',
    SIGEDON_R2_CONFIG=_fictitious_r2(),
    STORAGES=HEALTHY_STORAGES,
    KOBO_ENABLED=False,
)
class VerifyRenderInsecureCookiesTests(SimpleTestCase):
    def test_insecure_cookies_fail(self):
        findings = verify_render_configuration(environ={})
        self.assertFalse(configuration_is_healthy(findings))
        codes = {item.code for item in findings if not item.ok}
        self.assertIn('session_cookie_secure', codes)
        self.assertIn('csrf_cookie_secure', codes)


@override_settings(
    DEBUG=False,
    DATABASE_ENGINE='postgresql',
    ALLOWED_HOSTS=['sigedon-staging.onrender.com'],
    CSRF_TRUSTED_ORIGINS=['https://sigedon-staging.onrender.com'],
    SESSION_COOKIE_SECURE=True,
    CSRF_COOKIE_SECURE=True,
    SECURE_PROXY_SSL_HEADER=('HTTP_X_FORWARDED_PROTO', 'https'),
    SIGEDON_PRIVATE_STORAGE='r2',
    SIGEDON_R2_CONFIG=None,
    STORAGES=HEALTHY_STORAGES,
    KOBO_ENABLED=False,
)
class VerifyRenderMissingR2Tests(SimpleTestCase):
    def test_missing_r2_configuration_fails(self):
        findings = verify_render_configuration(environ={})
        self.assertFalse(configuration_is_healthy(findings))
        self.assertTrue(
            any(item.code == 'r2_config' and not item.ok for item in findings)
        )


@override_settings(
    DEBUG=False,
    DATABASE_ENGINE='postgresql',
    ALLOWED_HOSTS=['sigedon-staging.onrender.com'],
    CSRF_TRUSTED_ORIGINS=['https://sigedon-staging.onrender.com'],
    SESSION_COOKIE_SECURE=True,
    CSRF_COOKIE_SECURE=True,
    SECURE_PROXY_SSL_HEADER=('HTTP_X_FORWARDED_PROTO', 'https'),
    SIGEDON_PRIVATE_STORAGE='r2',
    SIGEDON_R2_CONFIG=_fictitious_r2(),
    STORAGES={
        'default': {
            'BACKEND': 'storages.backends.s3.S3Storage',
            'OPTIONS': {
                'default_acl': 'public-read',
                'querystring_auth': True,
            },
        },
        'staticfiles': {
            'BACKEND': 'whitenoise.storage.CompressedManifestStaticFilesStorage',
        },
    },
    KOBO_ENABLED=False,
)
class VerifyRenderPublicR2Tests(SimpleTestCase):
    def test_public_r2_configuration_fails(self):
        findings = verify_render_configuration(
            environ={'R2_PUBLIC_URL': 'https://pub.example.test'}
        )
        self.assertFalse(configuration_is_healthy(findings))
        codes = {item.code for item in findings if not item.ok}
        self.assertIn('r2_public_acl', codes)
        self.assertIn('r2_public_env', codes)


@override_settings(
    DEBUG=False,
    DATABASE_ENGINE='postgresql',
    ALLOWED_HOSTS=['sigedon-staging.onrender.com'],
    CSRF_TRUSTED_ORIGINS=['https://sigedon-staging.onrender.com'],
    SESSION_COOKIE_SECURE=True,
    CSRF_COOKIE_SECURE=True,
    SECURE_PROXY_SSL_HEADER=('HTTP_X_FORWARDED_PROTO', 'https'),
    SIGEDON_PRIVATE_STORAGE='r2',
    SIGEDON_R2_CONFIG=_fictitious_r2(),
    STORAGES=HEALTHY_STORAGES,
    KOBO_ENABLED=True,
    KOBO_BASE_URL='https://kobo.example.test',
    KOBO_API_TOKEN='',
    KOBO_WEBHOOK_USERNAME='sigedon-kobo',
    KOBO_WEBHOOK_SECRET='',
)
class VerifyRenderKoboEnabledMissingSecretTests(SimpleTestCase):
    def test_missing_kobo_secret_fails_when_enabled(self):
        findings = verify_render_configuration(environ={})
        self.assertFalse(configuration_is_healthy(findings))
        failed = [item for item in findings if item.code == 'kobo_required']
        self.assertTrue(failed and not failed[0].ok)
        self.assertNotIn('fictitious', failed[0].message)


@override_settings(
    DEBUG=False,
    DATABASE_ENGINE='postgresql',
    ALLOWED_HOSTS=['sigedon-staging.onrender.com'],
    CSRF_TRUSTED_ORIGINS=['https://sigedon-staging.onrender.com'],
    SESSION_COOKIE_SECURE=True,
    CSRF_COOKIE_SECURE=True,
    SECURE_PROXY_SSL_HEADER=('HTTP_X_FORWARDED_PROTO', 'https'),
    SIGEDON_PRIVATE_STORAGE='r2',
    SIGEDON_R2_CONFIG=_fictitious_r2(),
    STORAGES={
        'default': HEALTHY_STORAGES['default'],
        'staticfiles': {
            'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage',
        },
    },
    KOBO_ENABLED=False,
)
class VerifyRenderWhiteNoiseRequiredTests(SimpleTestCase):
    def test_whitenoise_static_backend_required(self):
        findings = verify_render_configuration(environ={})
        self.assertFalse(configuration_is_healthy(findings))
        self.assertTrue(
            any(item.code == 'whitenoise' and not item.ok for item in findings)
        )


class VerifyRenderCommandNetworkFreeTests(SimpleTestCase):
    @override_settings(
        DEBUG=False,
        DATABASE_ENGINE='postgresql',
        ALLOWED_HOSTS=['sigedon-staging.onrender.com'],
        CSRF_TRUSTED_ORIGINS=['https://sigedon-staging.onrender.com'],
        SESSION_COOKIE_SECURE=True,
        CSRF_COOKIE_SECURE=True,
        SECURE_PROXY_SSL_HEADER=('HTTP_X_FORWARDED_PROTO', 'https'),
        SIGEDON_PRIVATE_STORAGE='r2',
        SIGEDON_R2_CONFIG=_fictitious_r2(),
        STORAGES=HEALTHY_STORAGES,
        KOBO_ENABLED=False,
    )
    def test_no_network_calls(self):
        with mock.patch('socket.socket') as sock:
            call_command('verify_render_configuration', stdout=io.StringIO())
            sock.assert_not_called()

    @override_settings(
        DEBUG=True,
        DATABASE_ENGINE='postgresql',
        ALLOWED_HOSTS=['x.onrender.com'],
        CSRF_TRUSTED_ORIGINS=['https://x.onrender.com'],
        SESSION_COOKIE_SECURE=True,
        CSRF_COOKIE_SECURE=True,
        SECURE_PROXY_SSL_HEADER=('HTTP_X_FORWARDED_PROTO', 'https'),
        SIGEDON_PRIVATE_STORAGE='r2',
        SIGEDON_R2_CONFIG=_fictitious_r2(),
        STORAGES=HEALTHY_STORAGES,
        KOBO_ENABLED=False,
    )
    def test_command_raises_without_echoing_secrets(self):
        stdout = io.StringIO()
        with self.assertRaises(CommandError) as ctx:
            call_command('verify_render_configuration', stdout=stdout)
        combined = stdout.getvalue() + str(ctx.exception)
        self.assertNotIn('fictitious-r2-secret', combined)
