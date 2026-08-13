"""Focused tests for defensive sensitive-data log redaction."""

from __future__ import annotations

import io
import logging

from django.test import SimpleTestCase

from core.logging_filters import (
    REDACTED,
    RequestIdFilter,
    SensitiveDataRedactionFilter,
    SigedonFormatter,
    redact_sensitive_text,
)
from core.request_ids import set_request_id, reset_request_id


class LogRedactionTests(SimpleTestCase):
    def test_authorization_bearer_redacted(self):
        text = 'Authorization: Bearer FAKESECRET_w3x4y5z6a7b8c9d0e1f2'
        result = redact_sensitive_text(text)
        self.assertIn(REDACTED, result)
        self.assertNotIn('sk-fake-bearer-token-value', result)

    def test_authorization_basic_redacted(self):
        text = 'Authorization: Basic dXNlcjpwYXNz'
        result = redact_sensitive_text(text)
        self.assertIn(REDACTED, result)
        self.assertNotIn('dXNlcjpwYXNz', result)

    def test_cookie_and_set_cookie_redacted(self):
        cookie = 'Cookie: sessionid=abc123; csrftoken=tok'
        set_cookie = 'Set-Cookie: sessionid=abc123; Path=/'
        self.assertNotIn('abc123', redact_sensitive_text(cookie))
        self.assertNotIn('abc123', redact_sensitive_text(set_cookie))

    def test_password_secret_and_tokens_redacted(self):
        samples = (
            'password: super-secret-pass',
            'PASSWORD=super-secret-pass',
            'secret=top-secret-value',
            'api_token=api-token-value-99',
            'token=plain-token-value',
            'webhook_secret=whsec_fake_value',
        )
        for sample in samples:
            with self.subTest(sample=sample):
                result = redact_sensitive_text(sample)
                self.assertIn(REDACTED, result)
                self.assertNotIn('super-secret-pass', result)
                self.assertNotIn('top-secret-value', result)
                self.assertNotIn('api-token-value-99', result)
                self.assertNotIn('plain-token-value', result)
                self.assertNotIn('whsec_fake_value', result)

    def test_database_url_password_redacted(self):
        url = 'postgres://sigedon:fake-db-password@db.example.test:5432/sigedon'
        result = redact_sensitive_text(url)
        self.assertIn(REDACTED, result)
        self.assertNotIn('fake-db-password', result)
        self.assertIn('sigedon', result)
        self.assertIn('db.example.test', result)

    def test_query_like_and_multiline(self):
        message = (
            'failed auth\n'
            'password=multiline-secret-one\n'
            'token=multiline-secret-two&csrfmiddlewaretoken=csrf-secret'
        )
        result = redact_sensitive_text(message)
        self.assertNotIn('multiline-secret-one', result)
        self.assertNotIn('multiline-secret-two', result)
        self.assertNotIn('csrf-secret', result)
        self.assertIn('failed auth', result)

    def test_safe_ids_remain_readable(self):
        message = 'Kobo normalization failed submission_id=42 stage=normalize'
        self.assertEqual(redact_sensitive_text(message), message)

    def test_x_amz_credential_redacted(self):
        text = (
            'https://bucket.example.test/obj?'
            'X-Amz-Credential=FAKEACCESSKEY%2F20260803%2Fauto%2Fs3%2Faws4_request'
        )
        result = redact_sensitive_text(text)
        self.assertIn(REDACTED, result)
        self.assertNotIn('FAKEACCESSKEY', result)

    def test_x_amz_signature_redacted(self):
        text = 'download X-Amz-Signature=abcdef0123456789deadbeef'
        result = redact_sensitive_text(text)
        self.assertIn(REDACTED, result)
        self.assertNotIn('abcdef0123456789deadbeef', result)

    def test_x_amz_security_token_redacted(self):
        text = 'qs&X-Amz-Security-Token=FwoGZXIvYXdzEJr%2F%2F%2F%2F%2F%2F%2F%2F%2F%2FwEa'
        result = redact_sensitive_text(text)
        self.assertIn(REDACTED, result)
        self.assertNotIn('FwoGZXIvYXdzEJr', result)

    def test_aws_access_key_id_query_redacted(self):
        text = 'https://example.test/file?AWSAccessKeyId=AKIATESTFAKEKEY99&Expires=1'
        result = redact_sensitive_text(text)
        self.assertIn(REDACTED, result)
        self.assertNotIn('AKIATESTFAKEKEY99', result)

    def test_presigned_url_query_strings_redacted(self):
        url = (
            'https://fictitiousaccount01.r2.cloudflarestorage.com/bucket/path/doc.pdf'
            '?X-Amz-Algorithm=AWS4-HMAC-SHA256'
            '&X-Amz-Credential=AKIAPRESIGN%2F20260803%2Fauto%2Fs3%2Faws4_request'
            '&X-Amz-Date=20260803T120000Z'
            '&X-Amz-Expires=300'
            '&X-Amz-SignedHeaders=host'
            '&X-Amz-Signature=presigned-signature-value-should-vanish'
        )
        result = redact_sensitive_text(url)
        self.assertNotIn('AKIAPRESIGN', result)
        self.assertNotIn('presigned-signature-value-should-vanish', result)
        self.assertIn(REDACTED, result)
        self.assertIn('cloudflarestorage.com', result)

    def test_r2_secret_access_key_assignment_redacted(self):
        text = 'R2_SECRET_ACCESS_KEY=fictitious-r2-secret-value-99'
        result = redact_sensitive_text(text)
        self.assertIn(REDACTED, result)
        self.assertNotIn('fictitious-r2-secret-value-99', result)
        self.assertIn('R2_SECRET_ACCESS_KEY', result)

    def test_filter_redacts_message_and_args(self):
        record = logging.LogRecord(
            name='sigedon',
            level=logging.WARNING,
            pathname=__file__,
            lineno=1,
            msg='token=%s',
            args=('leaked-token-arg',),
            exc_info=None,
        )
        SensitiveDataRedactionFilter().filter(record)
        self.assertEqual(record.args, (REDACTED,))

    def test_exception_traceback_metadata_preserved_and_secrets_redacted(self):
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(
            SigedonFormatter(
                '{asctime} {levelname} {name} request_id={request_id} {message}',
                style='{',
                datefmt='%Y-%m-%dT%H:%M:%S%z',
            )
        )
        handler.addFilter(RequestIdFilter())
        handler.addFilter(SensitiveDataRedactionFilter())
        test_logger = logging.getLogger('sigedon.redaction_test')
        test_logger.handlers = [handler]
        test_logger.setLevel(logging.ERROR)
        test_logger.propagate = False

        token = set_request_id('redact-test-01')
        try:
            try:
                raise RuntimeError('password=boom-secret Authorization: Bearer leak-token')
            except RuntimeError:
                test_logger.exception('handled unexpected failure submission_id=%s', 7)
        finally:
            reset_request_id(token)

        output = stream.getvalue()
        self.assertIn('ERROR', output)
        self.assertIn('request_id=redact-test-01', output)
        self.assertIn('submission_id=7', output)
        self.assertIn('Traceback', output)
        self.assertIn('RuntimeError', output)
        self.assertNotIn('boom-secret', output)
        self.assertNotIn('leak-token', output)
        self.assertIn(REDACTED, output)
