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
        result = self.run_settings(
            DJANGO_DEBUG='False',
            DJANGO_SECRET_KEY='fictitious-secret',
            ALLOWED_HOSTS='sigedon.example.test',
            DATABASE_ENGINE='postgresql',
            POSTGRES_DB='sigedon_test',
            POSTGRES_USER='sigedon_test_user',
            POSTGRES_PASSWORD='fictitious-password',
            POSTGRES_HOST='db.example.test',
            DATABASE_CONN_MAX_AGE='90',
        )

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
        result = self.run_settings(
            DJANGO_DEBUG='False',
            DJANGO_SECRET_KEY='fictitious-secret',
            ALLOWED_HOSTS='sigedon.example.test',
            DATABASE_ENGINE='postgresql',
            POSTGRES_DB='sigedon_test',
            POSTGRES_USER='sigedon_test_user',
            POSTGRES_PASSWORD='fictitious-password',
            POSTGRES_HOST='db.example.test',
        )

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
