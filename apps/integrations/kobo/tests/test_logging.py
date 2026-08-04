"""Focused tests for safe Kobo operational logging."""

from __future__ import annotations

import base64
import io
import json
import logging
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from apps.integrations.kobo.errors import KoboPayloadError
from apps.integrations.kobo.mappings.ficha_01 import FICHA_01_FORM_ID, FICHA_01_VERSION
from apps.integrations.kobo.models import (
    KoboAsset,
    KoboFormDefinition,
    KoboProcessingEvent,
    KoboSubmission,
)
from apps.integrations.kobo.processors import process_submission
from apps.integrations.kobo.services import import_kobo_submission
from apps.integrations.kobo.services.importers import ImportOutcome
from core.logging_filters import (
    RequestIdFilter,
    SensitiveDataRedactionFilter,
    SigedonFormatter,
)


def _attach_observability_handler(logger_name: str, level: int = logging.INFO):
    """
    PRE: logger_name identifies a sigedon logger under test.
    POST: attaches a capturing handler with request-id + redaction formatting.
    """
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setLevel(level)
    handler.addFilter(RequestIdFilter())
    handler.addFilter(SensitiveDataRedactionFilter())
    handler.setFormatter(
        SigedonFormatter(
            '{asctime} {levelname} {name} request_id={request_id} {message}',
            style='{',
            datefmt='%Y-%m-%dT%H:%M:%S%z',
        )
    )
    target = logging.getLogger(logger_name)
    previous_handlers = list(target.handlers)
    previous_level = target.level
    previous_propagate = target.propagate
    target.handlers = [handler]
    target.setLevel(level)
    target.propagate = False
    return target, stream, previous_handlers, previous_level, previous_propagate


def _restore_logger(target, previous_handlers, previous_level, previous_propagate):
    target.handlers = previous_handlers
    target.setLevel(previous_level)
    target.propagate = previous_propagate


@override_settings(
    KOBO_ENABLED=True,
    KOBO_WEBHOOK_USERNAME='sigedon-kobo',
    KOBO_WEBHOOK_SECRET='test-webhook-secret-FAKE',
    KOBO_WEBHOOK_MAX_BYTES=1024,
)
class KoboLoggingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.form_definition = KoboFormDefinition.objects.create(
            form_id=FICHA_01_FORM_ID,
            title='Ficha 1 logging',
            version=FICHA_01_VERSION,
        )
        cls.asset = KoboAsset.objects.create(
            asset_uid='logging-asset-uid',
            name='Logging asset',
            form_definition=cls.form_definition,
            form_role=KoboAsset.FormRole.TERRITORIAL_PROFILE,
            is_active=True,
        )
        cls.default_timezone = ZoneInfo('America/Caracas')

    def setUp(self):
        self.client = Client()

    def _auth_headers(self):
        token = base64.b64encode(
            b'sigedon-kobo:test-webhook-secret-FAKE'
        ).decode()
        return {'HTTP_AUTHORIZATION': f'Basic {token}'}

    def create_submission(self, **overrides):
        values = {
            'form_definition': self.form_definition,
            'asset': self.asset,
            'external_id': 'logging-submission',
            'raw_payload': {
                '_uuid': 'logging-submission',
                'sensitive_notes': 'SECRET_MARKER_RAW_PAYLOAD',
                'today': '2026-07-12',
                'nucleo_code': 'NV-001',
                'pastoral_zone': 'catia_la_mar',
                'parish': 'caraballeda',
                'community_sector': 'caraballeda_tanaguarena',
                'location': '10 -66',
                'estimated_households': 10,
                'access_difficulties': 'unknown',
                'initial_priority_perception': 'medium',
                '_attachments': [],
            },
            'status': KoboSubmission.Status.RECEIVED,
        }
        values.update(overrides)
        return KoboSubmission.objects.create(**values)

    def test_normalization_unexpected_exception_logs_safe_error(self):
        submission = self.create_submission()
        target, stream, handlers, level, propagate = _attach_observability_handler(
            'sigedon.kobo.processing',
            logging.ERROR,
        )
        try:
            with patch(
                'apps.integrations.kobo.processors.normalize_submission',
                side_effect=RuntimeError(
                    'token=FAKE_KOBO_TOKEN password=FAKE_PASS '
                    'token=SECRET_MARKER_RAW_PAYLOAD'
                ),
            ):
                outcome = process_submission(
                    submission,
                    default_timezone=self.default_timezone,
                )
        finally:
            _restore_logger(target, handlers, level, propagate)

        output = stream.getvalue()
        self.assertEqual(outcome.final_status, KoboSubmission.Status.PROCESSING_FAILED)
        self.assertIn(f'submission_id={submission.pk}', output)
        self.assertIn('stage=normalize', output)
        self.assertIn('ERROR', output)
        self.assertNotIn('SECRET_MARKER_RAW_PAYLOAD', output)
        self.assertNotIn('FAKE_KOBO_TOKEN', output)
        self.assertNotIn('FAKE_PASS', output)
        self.assertNotIn(submission.raw_payload['sensitive_notes'], output)
        submission.refresh_from_db()
        self.assertEqual(
            submission.processing_events.filter(code='processing_error').count(),
            1,
        )
        self.assertEqual(submission.raw_payload['sensitive_notes'], 'SECRET_MARKER_RAW_PAYLOAD')

    def test_validation_failure_logs_info_without_payload(self):
        submission = self.create_submission()
        target, stream, handlers, level, propagate = _attach_observability_handler(
            'sigedon.kobo.processing',
            logging.INFO,
        )
        try:
            with patch(
                'apps.integrations.kobo.processors.normalize_submission',
                side_effect=KoboPayloadError('invalid'),
            ):
                process_submission(
                    submission,
                    default_timezone=self.default_timezone,
                )
        finally:
            _restore_logger(target, handlers, level, propagate)

        output = stream.getvalue()
        self.assertIn('validation failed', output.lower())
        self.assertNotIn('SECRET_MARKER_RAW_PAYLOAD', output)
        self.assertNotIn('ERROR', output)

    def test_import_failure_emits_one_top_level_traceback(self):
        user = get_user_model().objects.create_user('kobo-logger', password='x')
        submission = self.create_submission(
            external_id='import-logging-submission',
            status=KoboSubmission.Status.READY_FOR_REVIEW,
        )
        target, stream, handlers, level, propagate = _attach_observability_handler(
            'sigedon.kobo.import',
            logging.ERROR,
        )
        try:
            with patch(
                'apps.integrations.kobo.services.importers._validate_common_import_preconditions',
                side_effect=RuntimeError('import boom token=FAKE_IMPORT_TOKEN'),
            ):
                result = import_kobo_submission(submission, actor=user)
        finally:
            _restore_logger(target, handlers, level, propagate)

        self.assertEqual(result.outcome, ImportOutcome.FAILED)
        output = stream.getvalue()
        self.assertEqual(output.count('Kobo import unexpected failure'), 1)
        self.assertIn(f'submission_id={submission.pk}', output)
        self.assertNotIn('FAKE_IMPORT_TOKEN', output)
        submission.refresh_from_db()
        self.assertEqual(submission.status, KoboSubmission.Status.PROCESSING_FAILED)
        self.assertTrue(
            KoboProcessingEvent.objects.filter(
                submission=submission,
                code='MATERIALIZATION_FAILED',
            ).exists()
        )

    def test_webhook_auth_failure_emits_safe_warning(self):
        target, stream, handlers, level, propagate = _attach_observability_handler(
            'sigedon.kobo.webhook',
            logging.WARNING,
        )
        try:
            response = self.client.post(
                reverse('kobo:webhook_submission'),
                data=b'{}',
                content_type='application/json',
            )
        finally:
            _restore_logger(target, handlers, level, propagate)

        self.assertEqual(response.status_code, 401)
        output = stream.getvalue()
        self.assertIn('authentication failed', output.lower())
        self.assertNotIn('test-webhook-secret-FAKE', output)

    def test_webhook_malformed_json_emits_safe_warning(self):
        target, stream, handlers, level, propagate = _attach_observability_handler(
            'sigedon.kobo.webhook',
            logging.WARNING,
        )
        try:
            response = self.client.post(
                reverse('kobo:webhook_submission'),
                data=b'{not-json',
                content_type='application/json',
                **self._auth_headers(),
            )
        finally:
            _restore_logger(target, handlers, level, propagate)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['error'], 'invalid_payload')
        self.assertIn('malformed JSON', stream.getvalue())

    def test_webhook_oversized_body_emits_safe_warning(self):
        body = b'{"_xform_id_string":"x","pad":"' + (b'a' * 2000) + b'"}'
        target, stream, handlers, level, propagate = _attach_observability_handler(
            'sigedon.kobo.webhook',
            logging.WARNING,
        )
        try:
            response = self.client.post(
                reverse('kobo:webhook_submission'),
                data=body,
                content_type='application/json',
                **self._auth_headers(),
            )
        finally:
            _restore_logger(target, handlers, level, propagate)

        self.assertEqual(response.status_code, 413)
        output = stream.getvalue()
        self.assertIn('max_bytes=', output)
        self.assertNotIn('"pad"', output)

    def test_webhook_success_incomplete_and_unexpected_logging(self):
        payload = {
            '_uuid': 'webhook-log-uuid',
            '_xform_id_string': self.asset.asset_uid,
            'today': '2026-07-12',
            'nucleo_code': 'NV-001',
            'pastoral_zone': 'catia_la_mar',
            'parish': 'caraballeda',
            'community_sector': 'caraballeda_tanaguarena',
            'location': '10 -66',
            'estimated_households': 10,
            'access_difficulties': 'unknown',
            'initial_priority_perception': 'medium',
            '_attachments': [],
            'sensitive_notes': 'SECRET_MARKER_RAW_PAYLOAD',
        }

        def fake_receive(*, asset, raw_payload):
            return (
                self.create_submission(
                    external_id=raw_payload['_uuid'],
                    raw_payload=raw_payload,
                ),
                True,
            )

        class SuccessConvergence:
            completed = True
            final_status = KoboSubmission.Status.READY_FOR_REVIEW

            def __init__(self, submission_id):
                self.submission_id = submission_id

        target, stream, handlers, level, propagate = _attach_observability_handler(
            'sigedon.kobo.webhook',
            logging.INFO,
        )
        try:
            with patch(
                'apps.integrations.kobo.views.receive_webhook_submission',
                side_effect=fake_receive,
            ), patch(
                'apps.integrations.kobo.views.converge_webhook_submission',
                side_effect=lambda submission_id, **kwargs: SuccessConvergence(
                    submission_id
                ),
            ):
                response = self.client.post(
                    reverse('kobo:webhook_submission'),
                    data=json.dumps(payload),
                    content_type='application/json',
                    **self._auth_headers(),
                )
            self.assertIn(response.status_code, (200, 201))
            body = response.json()
            self.assertTrue(body['ok'])
            self.assertIn('X-Request-ID', response)
            output = stream.getvalue()
            self.assertIn(f"submission_id={body['submission_id']}", output)
            self.assertNotIn('SECRET_MARKER_RAW_PAYLOAD', output)

            class IncompleteConvergence:
                completed = False
                final_status = KoboSubmission.Status.RECEIVED

                def __init__(self, submission_id):
                    self.submission_id = submission_id

            with patch(
                'apps.integrations.kobo.views.receive_webhook_submission',
                side_effect=fake_receive,
            ), patch(
                'apps.integrations.kobo.views.converge_webhook_submission',
                side_effect=lambda submission_id, **kwargs: IncompleteConvergence(
                    submission_id
                ),
            ):
                incomplete = self.client.post(
                    reverse('kobo:webhook_submission'),
                    data=json.dumps({**payload, '_uuid': 'webhook-log-uuid-2'}),
                    content_type='application/json',
                    **self._auth_headers(),
                )
            self.assertEqual(incomplete.status_code, 422)
            self.assertEqual(incomplete.json()['error'], 'processing_incomplete')
            output = stream.getvalue()
            self.assertIn('processing incomplete', output.lower())
            self.assertIn(f'status={KoboSubmission.Status.RECEIVED}', output)

            with patch(
                'apps.integrations.kobo.views.receive_webhook_submission',
                side_effect=fake_receive,
            ), patch(
                'apps.integrations.kobo.views.converge_webhook_submission',
                side_effect=RuntimeError('boom token=FAKE_WH_TOKEN'),
            ):
                failure = self.client.post(
                    reverse('kobo:webhook_submission'),
                    data=json.dumps({**payload, '_uuid': 'webhook-log-uuid-3'}),
                    content_type='application/json',
                    **self._auth_headers(),
                )
            self.assertEqual(failure.status_code, 500)
            self.assertEqual(failure.json()['error'], 'internal_error')
            output = stream.getvalue()
            self.assertIn('unexpected failure', output.lower())
            self.assertNotIn('FAKE_WH_TOKEN', output)
            self.assertNotIn('SECRET_MARKER_RAW_PAYLOAD', output)
        finally:
            _restore_logger(target, handlers, level, propagate)
