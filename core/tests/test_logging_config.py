"""Focused tests for Django LOGGING configuration."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase


BASE_DIR = Path(__file__).resolve().parents[2]

LOGGING_PROBE = """
import json
import sys
from django.core.exceptions import ImproperlyConfigured
try:
    from core import settings as s
except ImproperlyConfigured as exc:
    print(json.dumps({'ok': False, 'error': str(exc)}))
    sys.exit(1)
logging_config = s.LOGGING
handlers = logging_config.get('handlers', {})
formatters = logging_config.get('formatters', {})
filters = logging_config.get('filters', {})
loggers = logging_config.get('loggers', {})
print(json.dumps({
    'ok': True,
    'django_level': s.DJANGO_LOG_LEVEL,
    'sigedon_level': s.SIGEDON_LOG_LEVEL,
    'kobo_level': s.KOBO_LOG_LEVEL,
    'has_request_id_filter': 'request_id' in filters,
    'has_redact_filter': 'redact_sensitive' in filters,
    'formatter_format': formatters.get('sigedon', {}).get('format', ''),
    'formatter_class': formatters.get('sigedon', {}).get('()', ''),
    'handler_names': sorted(handlers),
    'stdout_stream': handlers.get('stdout', {}).get('stream'),
    'stderr_stream': handlers.get('stderr', {}).get('stream'),
    'handler_classes': sorted({h.get('class') for h in handlers.values()}),
    'django_request_level': loggers.get('django.request', {}).get('level'),
    'django_request_propagate': loggers.get('django.request', {}).get('propagate'),
    'sigedon_exists': 'sigedon' in loggers,
    'sigedon_kobo_exists': 'sigedon.kobo' in loggers,
    'db_backends_level': loggers.get('django.db.backends', {}).get('level'),
    'secret_in_logging': 'fictitious-secret' in json.dumps(logging_config),
    'password_in_logging': 'fictitious-password' in json.dumps(logging_config),
}))
"""


class LoggingConfigTests(SimpleTestCase):
    def test_logging_exists_in_settings(self):
        self.assertIsInstance(settings.LOGGING, dict)
        self.assertIn('version', settings.LOGGING)
        self.assertIn('request_id', settings.LOGGING['filters'])
        formatter = settings.LOGGING['formatters']['sigedon']
        self.assertIn('request_id={request_id}', formatter['format'])
        self.assertEqual(formatter['style'], '{')
        self.assertIn('sigedon', settings.LOGGING['loggers'])
        self.assertIn('sigedon.kobo', settings.LOGGING['loggers'])

    def test_django_request_avoids_ordinary_access_duplication(self):
        request_logger = settings.LOGGING['loggers']['django.request']
        self.assertEqual(request_logger['level'], 'WARNING')
        self.assertFalse(request_logger['propagate'])

    def test_handlers_target_stdout_stderr_without_files(self):
        handlers = settings.LOGGING['handlers']
        self.assertEqual(handlers['stdout']['stream'], 'ext://sys.stdout')
        self.assertEqual(handlers['stderr']['stream'], 'ext://sys.stderr')
        for handler in handlers.values():
            self.assertEqual(handler['class'], 'logging.StreamHandler')
            self.assertNotIn('filename', handler)
            self.assertNotIn('Host', handler.get('class', ''))

    def test_sql_backend_not_debug(self):
        level = settings.LOGGING['loggers']['django.db.backends']['level']
        self.assertNotEqual(level, 'DEBUG')
        self.assertEqual(level, 'WARNING')

    def test_no_external_network_handler(self):
        for handler in settings.LOGGING['handlers'].values():
            class_name = handler['class']
            self.assertNotIn('Socket', class_name)
            self.assertNotIn('HTTP', class_name)
            self.assertNotIn('SysLog', class_name)
            self.assertNotIn('SMTP', class_name)

    def run_probe(self, **overrides):
        environment = os.environ.copy()
        for name in (
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
            'DJANGO_LOG_LEVEL',
            'SIGEDON_LOG_LEVEL',
            'KOBO_LOG_LEVEL',
        ):
            environment[name] = ''
        environment.update(
            {
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
        )
        environment.update(overrides)
        return subprocess.run(
            [sys.executable, '-c', LOGGING_PROBE],
            cwd=BASE_DIR,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_valid_environment_levels_parse(self):
        result = self.run_probe(
            DJANGO_LOG_LEVEL='WARNING',
            SIGEDON_LOG_LEVEL='DEBUG',
            KOBO_LOG_LEVEL='ERROR',
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload['ok'])
        self.assertEqual(payload['django_level'], 'WARNING')
        self.assertEqual(payload['sigedon_level'], 'DEBUG')
        self.assertEqual(payload['kobo_level'], 'ERROR')
        self.assertTrue(payload['has_request_id_filter'])
        self.assertTrue(payload['has_redact_filter'])
        self.assertIn('request_id={request_id}', payload['formatter_format'])
        self.assertTrue(payload['sigedon_exists'])
        self.assertTrue(payload['sigedon_kobo_exists'])
        self.assertEqual(payload['django_request_level'], 'WARNING')
        self.assertFalse(payload['django_request_propagate'])
        self.assertEqual(payload['db_backends_level'], 'WARNING')
        self.assertEqual(payload['stdout_stream'], 'ext://sys.stdout')
        self.assertEqual(payload['stderr_stream'], 'ext://sys.stderr')
        self.assertEqual(payload['handler_classes'], ['logging.StreamHandler'])
        self.assertFalse(payload['secret_in_logging'])
        self.assertFalse(payload['password_in_logging'])

    def test_invalid_level_fails_clearly(self):
        result = self.run_probe(SIGEDON_LOG_LEVEL='VERBOSE')
        self.assertNotEqual(result.returncode, 0)
        combined = result.stdout + result.stderr
        self.assertIn('SIGEDON_LOG_LEVEL', combined)
        self.assertNotIn('fictitious-password', combined)
        self.assertNotIn('fictitious-secret', combined)

    def test_request_id_filter_injects_placeholder(self):
        from core.logging_filters import RequestIdFilter
        from core.request_ids import REQUEST_ID_MISSING

        record = logging.LogRecord(
            name='sigedon',
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg='probe',
            args=(),
            exc_info=None,
        )
        RequestIdFilter().filter(record)
        self.assertEqual(record.request_id, REQUEST_ID_MISSING)
