"""
Focused tests for SIGEDON liveness (/healthz/) and readiness (/readyz/) probes.

PRE: disposable test database available for real readiness/migration checks.
POST: covers anonymous access, hardening headers, request IDs, zero-query
      liveness, DB/migration readiness failures, logging safety, and isolation
      from Kobo/cache/media/session side effects.
"""

from __future__ import annotations

import inspect
import json
import logging
from unittest import mock

from django.core.cache import cache
from django.db.utils import OperationalError
from django.test import TestCase, override_settings
from django.urls import reverse

from core import health as health_module
from core.logging_filters import (
    REDACTED,
    RequestIdFilter,
    SensitiveDataRedactionFilter,
    SigedonFormatter,
    redact_sensitive_text,
)
from core.request_ids import REQUEST_ID_HEADER, reset_request_id, set_request_id


def _assert_probe_hardening(response):
    cache_control = response['Cache-Control']
    assert 'no-store' in cache_control
    assert 'max-age=0' in cache_control
    assert response['Pragma'] == 'no-cache'
    assert response['Expires'] == '0'
    assert response['X-Content-Type-Options'] == 'nosniff'
    assert REQUEST_ID_HEADER in response
    assert 'sessionid' not in response.cookies


def _assert_no_sensitive_fields(payload: dict, body: str):
    forbidden_keys = {
        'version',
        'environment',
        'host',
        'hostname',
        'database',
        'db',
        'migration',
        'migrations',
        'pid',
        'uptime',
        'detail',
        'error',
        'exception',
        'traceback',
        'message',
    }
    assert set(payload.keys()).isdisjoint(forbidden_keys)
    lowered = body.lower()
    for token in (
        'traceback',
        'operationalerror',
        'password',
        'postgres',
        'localhost',
        'sigedon_app',
        'migration',
        'django_migrations',
    ):
        assert token not in lowered


class HealthzLivenessTests(TestCase):
    def test_reverse_healthz(self):
        self.assertEqual(reverse('healthz'), '/healthz/')

    def test_anonymous_get_returns_minimal_ok(self):
        with self.assertNumQueries(0):
            response = self.client.get(reverse('healthz'))
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload, {'status': 'ok'})
        _assert_probe_hardening(response)
        _assert_no_sensitive_fields(payload, response.content.decode())

    def test_valid_inbound_request_id_preserved(self):
        inbound = 'probe-live-01'
        response = self.client.get(
            reverse('healthz'),
            HTTP_X_REQUEST_ID=inbound,
        )
        self.assertEqual(response[REQUEST_ID_HEADER], inbound)

    def test_invalid_inbound_request_id_replaced(self):
        inbound = 'bad id!!'
        response = self.client.get(
            reverse('healthz'),
            HTTP_X_REQUEST_ID=inbound,
        )
        self.assertNotEqual(response[REQUEST_ID_HEADER], inbound)
        self.assertGreaterEqual(len(response[REQUEST_ID_HEADER]), 8)

    def test_head_supported(self):
        with self.assertNumQueries(0):
            response = self.client.head(reverse('healthz'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b'')
        _assert_probe_hardening(response)

    def test_post_returns_405(self):
        response = self.client.post(reverse('healthz'))
        self.assertEqual(response.status_code, 405)
        self.assertIn(REQUEST_ID_HEADER, response)

    def test_liveness_stays_200_when_database_unavailable(self):
        with mock.patch.object(
            health_module,
            'check_database_connection',
            side_effect=OperationalError('simulated-db-down'),
        ):
            # Liveness must not invoke DB checks; assert via zero queries.
            with self.assertNumQueries(0):
                response = self.client.get(reverse('healthz'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'status': 'ok'})

    def test_security_middleware_headers_present(self):
        response = self.client.get(reverse('healthz'))
        self.assertEqual(response['X-Content-Type-Options'], 'nosniff')
        # XFrameOptionsMiddleware still applies.
        self.assertIn('X-Frame-Options', response)


class ReadyzReadinessTests(TestCase):
    def setUp(self):
        # Reset process-local log suppression and migration cache between tests.
        health_module._last_failure_log_at = 0.0
        health_module.reset_migration_readiness_cache()

    def test_reverse_readyz(self):
        self.assertEqual(reverse('readyz'), '/readyz/')

    def test_anonymous_get_ready_against_real_test_database(self):
        """Uses the disposable test DB and the real migration graph."""
        response = self.client.get(reverse('readyz'))
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload, {'status': 'ready'})
        _assert_probe_hardening(response)
        _assert_no_sensitive_fields(payload, response.content.decode())

    def test_database_unavailability_returns_503_without_details(self):
        with mock.patch.object(
            health_module,
            'check_database_connection',
            side_effect=OperationalError(
                'could not connect to server password=fake-db-secret '
                'user=sigedon_app host=db.internal.example'
            ),
        ):
            response = self.client.get(reverse('readyz'))
        self.assertEqual(response.status_code, 503)
        payload = response.json()
        self.assertEqual(payload, {'status': 'not_ready'})
        body = response.content.decode()
        self.assertNotIn('fake-db-secret', body)
        self.assertNotIn('sigedon_app', body)
        self.assertNotIn('db.internal.example', body)
        self.assertNotIn('OperationalError', body)
        _assert_probe_hardening(response)
        _assert_no_sensitive_fields(payload, body)

    def test_pending_migrations_return_503_without_names(self):
        with mock.patch.object(
            health_module,
            'check_migrations_applied',
            return_value=False,
        ):
            response = self.client.get(reverse('readyz'))
        self.assertEqual(response.status_code, 503)
        payload = response.json()
        self.assertEqual(payload, {'status': 'not_ready'})
        body = response.content.decode()
        self.assertNotIn('operations.', body)
        self.assertNotIn('0001', body)
        _assert_no_sensitive_fields(payload, body)

    def test_migration_plan_mock_returns_503(self):
        fake_plan = [('operations', '0099_fake_pending')]
        with mock.patch(
            'core.health.MigrationExecutor'
        ) as executor_cls:
            executor = executor_cls.return_value
            executor.loader.graph.leaf_nodes.return_value = []
            executor.migration_plan.return_value = fake_plan
            response = self.client.get(reverse('readyz'))
        self.assertEqual(response.status_code, 503)
        body = response.content.decode()
        self.assertEqual(response.json(), {'status': 'not_ready'})
        self.assertNotIn('0099_fake_pending', body)
        self.assertNotIn('operations', body)

    def test_unexpected_migration_exception_returns_503(self):
        with mock.patch.object(
            health_module,
            'check_migrations_applied',
            side_effect=RuntimeError('unexpected-migration-graph-failure'),
        ):
            response = self.client.get(reverse('readyz'))
        self.assertEqual(response.status_code, 503)
        body = response.content.decode()
        self.assertEqual(response.json(), {'status': 'not_ready'})
        self.assertNotIn('unexpected-migration-graph-failure', body)

    def test_post_returns_405(self):
        response = self.client.post(reverse('readyz'))
        self.assertEqual(response.status_code, 405)

    def test_no_login_redirect(self):
        response = self.client.get(reverse('readyz'))
        self.assertNotEqual(response.status_code, 302)
        self.assertEqual(response.status_code, 200)

    def test_head_supported_when_ready(self):
        response = self.client.head(reverse('readyz'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b'')
        _assert_probe_hardening(response)

    def test_kobo_cache_and_media_are_not_touched(self):
        with (
            mock.patch(
                'apps.integrations.kobo.client.KoboApiClient',
                autospec=True,
            ) as kobo_cls,
            mock.patch.object(cache, 'get', wraps=cache.get) as cache_get,
            mock.patch.object(cache, 'set', wraps=cache.set) as cache_set,
            mock.patch(
                'django.core.files.storage.FileSystemStorage.save',
                autospec=True,
            ) as media_save,
        ):
            response = self.client.get(reverse('readyz'))
        self.assertEqual(response.status_code, 200)
        kobo_cls.assert_not_called()
        cache_get.assert_not_called()
        cache_set.assert_not_called()
        media_save.assert_not_called()
        # Health module must stay independent of business / Kobo packages.
        source = inspect.getsource(health_module)
        self.assertNotIn('apps.operations', source)
        self.assertNotIn('apps.integrations', source)
        self.assertNotIn('apps.public_portal', source)

    def test_request_id_present_on_ready_and_not_ready(self):
        inbound = 'ready-corr-01'
        ok = self.client.get(reverse('readyz'), HTTP_X_REQUEST_ID=inbound)
        self.assertEqual(ok[REQUEST_ID_HEADER], inbound)

        with mock.patch.object(
            health_module,
            'check_database_connection',
            side_effect=OperationalError('down'),
        ):
            failed = self.client.get(
                reverse('readyz'),
                HTTP_X_REQUEST_ID=inbound,
            )
        self.assertEqual(failed.status_code, 503)
        self.assertEqual(failed[REQUEST_ID_HEADER], inbound)


@override_settings(SIGEDON_READINESS_MIGRATION_CACHE_SECONDS=15)
class ReadyzMigrationCacheTests(TestCase):
    def setUp(self):
        health_module._last_failure_log_at = 0.0
        health_module.reset_migration_readiness_cache()

    def test_second_request_reuses_migration_plan_within_ttl(self):
        with mock.patch.object(
            health_module,
            'check_migrations_applied',
            wraps=health_module.check_migrations_applied,
        ) as plan_probe:
            first = self.client.get(reverse('readyz'))
            second = self.client.get(reverse('readyz'))
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(plan_probe.call_count, 1)

    def test_db_probe_still_runs_on_cached_migration_hit(self):
        # Prime cache.
        self.assertEqual(self.client.get(reverse('readyz')).status_code, 200)
        with mock.patch.object(
            health_module,
            'check_database_connection',
            wraps=health_module.check_database_connection,
        ) as db_probe:
            response = self.client.get(reverse('readyz'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(db_probe.call_count, 1)

    def test_ttl_zero_always_recomputes(self):
        with override_settings(SIGEDON_READINESS_MIGRATION_CACHE_SECONDS=0):
            health_module.reset_migration_readiness_cache()
            with mock.patch.object(
                health_module,
                'check_migrations_applied',
                wraps=health_module.check_migrations_applied,
            ) as plan_probe:
                self.client.get(reverse('readyz'))
                self.client.get(reverse('readyz'))
            self.assertEqual(plan_probe.call_count, 2)

    def test_after_ttl_plan_recomputes(self):
        with override_settings(SIGEDON_READINESS_MIGRATION_CACHE_SECONDS=30):
            health_module.reset_migration_readiness_cache()
            with mock.patch.object(
                health_module,
                'check_migrations_applied',
                wraps=health_module.check_migrations_applied,
            ) as plan_probe:
                self.client.get(reverse('readyz'))
                # Expire cache by rewinding stored timestamp.
                with health_module._migration_cache_lock:
                    cached = health_module._migration_cache
                    self.assertIsNotNone(cached)
                    cached['at'] = cached['at'] - 31
                self.client.get(reverse('readyz'))
            self.assertEqual(plan_probe.call_count, 2)

    def test_db_failure_overrides_cached_migration_ready(self):
        self.assertEqual(self.client.get(reverse('readyz')).status_code, 200)
        with mock.patch.object(
            health_module,
            'check_database_connection',
            side_effect=OperationalError('down'),
        ):
            response = self.client.get(reverse('readyz'))
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {'status': 'not_ready'})

    def test_pending_migration_cached_within_ttl(self):
        with mock.patch.object(
            health_module,
            'check_migrations_applied',
            return_value=False,
        ) as plan_probe:
            first = self.client.get(reverse('readyz'))
            second = self.client.get(reverse('readyz'))
        self.assertEqual(first.status_code, 503)
        self.assertEqual(second.status_code, 503)
        self.assertEqual(plan_probe.call_count, 1)
        with health_module._migration_cache_lock:
            cached = health_module._migration_cache
        self.assertEqual(cached['category'], 'pending')
        self.assertFalse(cached['ready'])
        self.assertNotIn('exception', cached)
        self.assertNotIn('password', str(cached).lower())

    def test_cache_contains_no_secrets(self):
        self.client.get(reverse('readyz'))
        with health_module._migration_cache_lock:
            cached = dict(health_module._migration_cache or {})
        self.assertEqual(set(cached.keys()), {'at', 'ttl', 'ready', 'category', 'alias'})
        self.assertIn(cached['category'], {'ready', 'pending'})

    def test_success_emits_no_health_log(self):
        with self.assertNoLogs('sigedon.health', level='WARNING'):
            response = self.client.get(reverse('readyz'))
        self.assertEqual(response.status_code, 200)

        with self.assertNoLogs('sigedon.health', level='INFO'):
            response = self.client.get(reverse('healthz'))
        self.assertEqual(response.status_code, 200)

    def test_database_failure_logs_warning_with_request_id_and_redaction(self):
        inbound = 'ready-log-42'
        with self.assertLogs('sigedon.health', level='WARNING') as captured:
            with mock.patch.object(
                health_module,
                'check_database_connection',
                side_effect=OperationalError(
                    'connection failed password=fake-db-secret '
                    'postgres://sigedon:fake-db-password@db.example.test/sigedon'
                ),
            ):
                response = self.client.get(
                    reverse('readyz'),
                    HTTP_X_REQUEST_ID=inbound,
                )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {'status': 'not_ready'})
        self.assertNotIn('fake-db-secret', response.content.decode())
        self.assertNotIn('fake-db-password', response.content.decode())

        joined = '\n'.join(captured.output)
        self.assertIn('database unavailable', joined)
        record = captured.records[0]
        # assertLogs does not run project LOGGING filters; verify message safety
        # and that RequestIdFilter would bind the inbound ID from context.
        self.assertIn('database unavailable', record.getMessage())
        self.assertNotIn('fake-db-secret', response.content.decode())

        token = set_request_id(inbound)
        try:
            probe = logging.LogRecord(
                name='sigedon.health',
                level=logging.WARNING,
                pathname=__file__,
                lineno=1,
                msg='readiness check failed: database unavailable',
                args=(),
                exc_info=None,
            )
            RequestIdFilter().filter(probe)
            self.assertEqual(probe.request_id, inbound)
            SensitiveDataRedactionFilter().filter(probe)
            formatted = SigedonFormatter(
                '{levelname} {name} request_id={request_id} {message}',
                style='{',
            ).format(probe)
            self.assertIn(inbound, formatted)
        finally:
            reset_request_id(token)

        leaked = (
            'postgres://sigedon:fake-db-password@db.example.test/sigedon'
        )
        self.assertIn(REDACTED, redact_sensitive_text(leaked))
        self.assertNotIn('fake-db-password', redact_sensitive_text(leaked))

    def test_repeated_failures_suppress_duplicate_warning_noise(self):
        with mock.patch.object(
            health_module,
            'check_database_connection',
            side_effect=OperationalError('down'),
        ):
            with self.assertLogs('sigedon.health', level='WARNING') as first:
                self.client.get(reverse('readyz'))
            # Immediate repeat should be suppressed.
            with self.assertNoLogs('sigedon.health', level='WARNING'):
                self.client.get(reverse('readyz'))
        self.assertEqual(len(first.records), 1)

    def test_unexpected_failure_logs_exception_once(self):
        with mock.patch.object(
            health_module,
            'check_migrations_applied',
            side_effect=RuntimeError('graph-boom'),
        ):
            with self.assertLogs('sigedon.health', level='ERROR') as captured:
                response = self.client.get(reverse('readyz'))
        self.assertEqual(response.status_code, 503)
        self.assertEqual(len(captured.records), 1)
        self.assertIn('unexpected error', captured.records[0].getMessage())
        self.assertNotIn('graph-boom', response.content.decode())


@override_settings(
    CACHES={
        'default': {
            'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
            'LOCATION': 'health-probe-isolation',
        }
    }
)
class HealthProbeIsolationRegressionTests(TestCase):
    def test_probe_responses_are_not_session_creating(self):
        for name in ('healthz', 'readyz'):
            with self.subTest(name=name):
                client = self.client_class()
                response = client.get(reverse(name))
                self.assertIn(response.status_code, (200, 503))
                self.assertNotIn('sessionid', response.cookies)

    def test_json_bodies_are_exactly_approved_payloads(self):
        live = self.client.get(reverse('healthz'))
        self.assertEqual(json.loads(live.content), {'status': 'ok'})
        ready = self.client.get(reverse('readyz'))
        self.assertEqual(json.loads(ready.content), {'status': 'ready'})
