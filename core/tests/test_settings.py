import json
import os
import subprocess
import sys
from pathlib import Path

from django.test import SimpleTestCase


BASE_DIR = Path(__file__).resolve().parents[2]
SETTINGS_PROBE = """
import json
from core import settings
database = settings.DATABASES['default'].copy()
database.pop('PASSWORD', None)
print(json.dumps({
    'debug': settings.DEBUG,
    'database': database,
    'allowed_hosts': settings.ALLOWED_HOSTS,
    'session_cookie_secure': settings.SESSION_COOKIE_SECURE,
    'csrf_cookie_secure': settings.CSRF_COOKIE_SECURE,
    'media_root': str(settings.MEDIA_ROOT),
    'kobo_http_connect_timeout': settings.KOBO_HTTP_CONNECT_TIMEOUT,
    'kobo_http_read_timeout': settings.KOBO_HTTP_READ_TIMEOUT,
    'kobo_http_max_attempts': settings.KOBO_HTTP_MAX_ATTEMPTS,
    'kobo_http_retry_base_delay': settings.KOBO_HTTP_RETRY_BASE_DELAY,
    'kobo_http_retry_max_delay': settings.KOBO_HTTP_RETRY_MAX_DELAY,
    'kobo_max_attachment_bytes': settings.KOBO_MAX_ATTACHMENT_BYTES,
    'kobo_webhook_max_bytes': settings.KOBO_WEBHOOK_MAX_BYTES,
    'max_private_upload_bytes': settings.SIGEDON_MAX_PRIVATE_UPLOAD_BYTES,
    'data_upload_max_memory_size': settings.DATA_UPLOAD_MAX_MEMORY_SIZE,
}, default=str))
"""


class IsolatedSettingsTests(SimpleTestCase):
    def run_settings(self, **overrides):
        """
        PRE: overrides contains only fictitious environment values for an isolated settings import.
        POST: returns the completed subprocess without exposing configured secrets in output.
        """
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
            'SIGEDON_MAX_PRIVATE_UPLOAD_BYTES',
            'KOBO_HTTP_CONNECT_TIMEOUT',
            'KOBO_HTTP_READ_TIMEOUT',
            'KOBO_HTTP_MAX_ATTEMPTS',
            'KOBO_HTTP_RETRY_BASE_DELAY',
            'KOBO_HTTP_RETRY_MAX_DELAY',
            'KOBO_HTTP_RETRY_AFTER_MAX_DELAY',
            'KOBO_HTTP_MAX_PAGES',
            'KOBO_SYNC_OVERLAP_SECONDS',
            'KOBO_SYNC_LEASE_SECONDS',
            'KOBO_MAX_ATTACHMENT_BYTES',
            'KOBO_ATTACHMENT_PROCESSING_TIMEOUT_SECONDS',
            'KOBO_WEBHOOK_MAX_BYTES',
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

    def assert_configuration_error(self, result, expected_message):
        """
        PRE: result is a failed isolated settings import.
        POST: verifies a safe expected error without credential values in output.
        """
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(expected_message, result.stderr)
        self.assertNotIn('fictitious-password', result.stderr)
        self.assertNotIn('fictitious-secret', result.stderr)

    def test_development_allows_sqlite(self):
        result = self.run_settings(DJANGO_DEBUG='True', DATABASE_ENGINE='sqlite')

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload['database']['ENGINE'], 'django.db.backends.sqlite3')
        self.assertEqual(payload['database']['NAME'], str(BASE_DIR / 'db.sqlite3'))

    def test_production_rejects_sqlite(self):
        result = self.run_settings(
            DJANGO_DEBUG='False',
            DJANGO_SECRET_KEY='fictitious-secret',
            ALLOWED_HOSTS='sigedon.example.test',
            DATABASE_ENGINE='sqlite',
        )

        self.assert_configuration_error(result, 'SQLite solo está permitido')

    def test_postgresql_configuration_is_built_without_exposing_password(self):
        result = self.run_settings(**self.production_env(DATABASE_CONN_MAX_AGE='90'))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('fictitious-password', result.stdout)
        payload = json.loads(result.stdout)
        database = payload['database']
        self.assertEqual(database['ENGINE'], 'django.db.backends.postgresql')
        self.assertEqual(database['PORT'], '5432')
        self.assertEqual(database['CONN_MAX_AGE'], 90)
        self.assertTrue(database['CONN_HEALTH_CHECKS'])
        self.assertTrue(payload['session_cookie_secure'])
        self.assertTrue(payload['csrf_cookie_secure'])
        self.assertEqual(payload['media_root'], '/var/lib/sigedon/media')

    def test_postgresql_requires_password(self):
        result = self.run_settings(
            DJANGO_DEBUG='True',
            DATABASE_ENGINE='postgresql',
            POSTGRES_DB='sigedon_test',
            POSTGRES_USER='sigedon_test_user',
            POSTGRES_HOST='db.example.test',
        )

        self.assert_configuration_error(result, 'POSTGRES_PASSWORD')

    def test_production_enables_secure_cookies(self):
        result = self.run_settings(**self.production_env())

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload['session_cookie_secure'])
        self.assertTrue(payload['csrf_cookie_secure'])

    def test_production_requires_secret_key(self):
        result = self.run_settings(
            DJANGO_DEBUG='False',
            ALLOWED_HOSTS='sigedon.example.test',
            DATABASE_ENGINE='postgresql',
        )

        self.assert_configuration_error(result, 'DJANGO_SECRET_KEY')

    def test_production_requires_allowed_hosts(self):
        result = self.run_settings(
            DJANGO_DEBUG='False',
            DJANGO_SECRET_KEY='fictitious-secret',
            DATABASE_ENGINE='postgresql',
        )

        self.assert_configuration_error(result, 'ALLOWED_HOSTS')

    def test_production_requires_media_root(self):
        result = self.run_settings(**self.production_env(SIGEDON_MEDIA_ROOT=''))

        self.assert_configuration_error(result, 'SIGEDON_MEDIA_ROOT is required')

    def test_kobo_numeric_defaults(self):
        result = self.run_settings(DJANGO_DEBUG='True', DATABASE_ENGINE='sqlite')
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload['kobo_http_connect_timeout'], 5)
        self.assertEqual(payload['kobo_http_read_timeout'], 15)
        self.assertEqual(payload['kobo_http_max_attempts'], 3)
        self.assertEqual(payload['kobo_max_attachment_bytes'], 10485760)
        self.assertEqual(payload['max_private_upload_bytes'], 10485760)

    def test_kobo_bounds_accept_min_and_max(self):
        for overrides in (
            {'KOBO_HTTP_CONNECT_TIMEOUT': '0.1'},
            {'KOBO_HTTP_CONNECT_TIMEOUT': '60'},
            {'KOBO_HTTP_MAX_ATTEMPTS': '1'},
            {'KOBO_HTTP_MAX_ATTEMPTS': '10'},
            {'KOBO_MAX_ATTACHMENT_BYTES': '1'},
            {'KOBO_MAX_ATTACHMENT_BYTES': '104857600'},
            {'SIGEDON_MAX_PRIVATE_UPLOAD_BYTES': '1'},
            {'SIGEDON_MAX_PRIVATE_UPLOAD_BYTES': '104857600'},
        ):
            with self.subTest(overrides=overrides):
                result = self.run_settings(
                    DJANGO_DEBUG='True',
                    DATABASE_ENGINE='sqlite',
                    **overrides,
                )
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_kobo_rejects_out_of_range_and_malformed(self):
        cases = (
            ({'KOBO_HTTP_CONNECT_TIMEOUT': '0.09'}, 'KOBO_HTTP_CONNECT_TIMEOUT'),
            ({'KOBO_HTTP_CONNECT_TIMEOUT': '61'}, 'KOBO_HTTP_CONNECT_TIMEOUT'),
            ({'KOBO_HTTP_CONNECT_TIMEOUT': 'abc'}, 'KOBO_HTTP_CONNECT_TIMEOUT'),
            ({'KOBO_HTTP_CONNECT_TIMEOUT': 'nan'}, 'KOBO_HTTP_CONNECT_TIMEOUT'),
            ({'KOBO_HTTP_CONNECT_TIMEOUT': 'inf'}, 'KOBO_HTTP_CONNECT_TIMEOUT'),
            ({'KOBO_HTTP_MAX_ATTEMPTS': '0'}, 'KOBO_HTTP_MAX_ATTEMPTS'),
            ({'KOBO_HTTP_MAX_ATTEMPTS': '11'}, 'KOBO_HTTP_MAX_ATTEMPTS'),
            ({'KOBO_HTTP_MAX_ATTEMPTS': '1.5'}, 'KOBO_HTTP_MAX_ATTEMPTS'),
            ({'KOBO_HTTP_RETRY_BASE_DELAY': '2', 'KOBO_HTTP_RETRY_MAX_DELAY': '1'}, 'KOBO_HTTP_RETRY_MAX_DELAY'),
            ({'KOBO_SYNC_LEASE_SECONDS': '0'}, 'KOBO_SYNC_LEASE_SECONDS'),
            ({'KOBO_MAX_ATTACHMENT_BYTES': '0'}, 'KOBO_MAX_ATTACHMENT_BYTES'),
            ({'KOBO_WEBHOOK_MAX_BYTES': '-1'}, 'KOBO_WEBHOOK_MAX_BYTES'),
            ({'SIGEDON_MAX_PRIVATE_UPLOAD_BYTES': '0'}, 'SIGEDON_MAX_PRIVATE_UPLOAD_BYTES'),
            ({'SIGEDON_MAX_PRIVATE_UPLOAD_BYTES': '104857601'}, 'SIGEDON_MAX_PRIVATE_UPLOAD_BYTES'),
        )
        for overrides, needle in cases:
            with self.subTest(overrides=overrides):
                result = self.run_settings(
                    DJANGO_DEBUG='True',
                    DATABASE_ENGINE='sqlite',
                    **overrides,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(needle, result.stderr)
                message_lines = [
                    line
                    for line in result.stderr.splitlines()
                    if 'ImproperlyConfigured:' in line
                ]
                self.assertTrue(message_lines)
                message = message_lines[-1]
                # Distinctive malformed tokens must not appear in the raised message.
                for raw in overrides.values():
                    if raw in {'abc', 'nan', 'inf', '1.5', '-1', '104857601'}:
                        self.assertNotIn(raw, message)
                self.assertNotIn('fictitious-password', result.stderr)
                self.assertNotIn('fictitious-secret', result.stderr)
