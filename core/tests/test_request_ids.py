"""Focused tests for X-Request-ID middleware and context helpers."""

from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor

from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, override_settings
from django.urls import path

from core.logging_filters import RequestIdFilter
from core.request_ids import (
    REQUEST_ID_HEADER,
    REQUEST_ID_MISSING,
    RequestIdMiddleware,
    get_request_id,
    normalize_or_generate_request_id,
    reset_request_id,
    set_request_id,
)


def _ok_view(request):
    return HttpResponse('ok', status=200)


def _forbidden_view(request):
    return HttpResponse('forbidden', status=403)


def _not_found_view(request):
    return HttpResponse('missing', status=404)


def _boom_view(request):
    raise RuntimeError('controlled-500-for-request-id')


urlpatterns = [
    path('rid-ok/', _ok_view),
    path('rid-403/', _forbidden_view),
    path('rid-404/', _not_found_view),
    path('rid-500/', _boom_view),
]


@override_settings(ROOT_URLCONF=__name__)
class RequestIdContractTests(SimpleTestCase):
    def test_missing_inbound_id_generates_32_char_hex(self):
        generated = normalize_or_generate_request_id(None)
        self.assertEqual(len(generated), 32)
        self.assertTrue(all(ch in '0123456789abcdef' for ch in generated))

    def test_valid_inbound_id_is_preserved(self):
        value = 'client-req_01.ABC-xyz'
        self.assertEqual(normalize_or_generate_request_id(value), value)

    def test_invalid_characters_cause_replacement(self):
        replaced = normalize_or_generate_request_id('bad/id/value')
        self.assertNotEqual(replaced, 'bad/id/value')
        self.assertEqual(len(replaced), 32)

    def test_whitespace_causes_replacement(self):
        inbound = '  validid1  '
        replaced = normalize_or_generate_request_id(inbound)
        self.assertNotEqual(replaced, inbound.strip())
        self.assertEqual(len(replaced), 32)

    def test_too_short_id_causes_replacement(self):
        replaced = normalize_or_generate_request_id('short')
        self.assertEqual(len(replaced), 32)

    def test_too_long_id_causes_replacement(self):
        inbound = 'a' * 65
        replaced = normalize_or_generate_request_id(inbound)
        self.assertNotEqual(replaced, inbound)
        self.assertEqual(len(replaced), 32)

    def test_non_ascii_id_causes_replacement(self):
        inbound = 'café-req1'
        replaced = normalize_or_generate_request_id(inbound)
        self.assertNotEqual(replaced, inbound)
        self.assertEqual(len(replaced), 32)

    def test_response_contains_header_and_request_attribute(self):
        response = self.client.get('/rid-ok/')
        self.assertEqual(response.status_code, 200)
        request_id = response[REQUEST_ID_HEADER]
        self.assertEqual(len(request_id), 32)
        self.assertEqual(get_request_id(), REQUEST_ID_MISSING)

    def test_valid_inbound_header_preserved_on_response(self):
        inbound = 'proxy-trace-01'
        response = self.client.get(
            '/rid-ok/',
            HTTP_X_REQUEST_ID=inbound,
        )
        self.assertEqual(response[REQUEST_ID_HEADER], inbound)

    def test_invalid_inbound_header_not_reflected(self):
        inbound = 'bad id with spaces!!'
        response = self.client.get(
            '/rid-ok/',
            HTTP_X_REQUEST_ID=inbound,
        )
        self.assertNotEqual(response[REQUEST_ID_HEADER], inbound)
        self.assertNotIn(inbound, response[REQUEST_ID_HEADER])

    def test_404_and_403_receive_header(self):
        for path_name in ('/rid-404/', '/rid-403/'):
            response = self.client.get(path_name)
            self.assertIn(REQUEST_ID_HEADER, response)
            self.assertGreaterEqual(len(response[REQUEST_ID_HEADER]), 8)

    def test_controlled_500_logs_same_request_id(self):
        client = self.client_class(raise_request_exception=False)
        with self.assertLogs('django.request', level='ERROR') as captured:
            response = client.get('/rid-500/')
        self.assertEqual(response.status_code, 500)
        request_id = response[REQUEST_ID_HEADER]
        self.assertEqual(len(request_id), 32)
        self.assertEqual(get_request_id(), REQUEST_ID_MISSING)
        joined = '\n'.join(captured.output)
        self.assertIn('Internal Server Error', joined)
        record = logging.LogRecord(
            name='django.request',
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg='probe',
            args=(),
            exc_info=None,
        )
        record.request = type('R', (), {'request_id': request_id})()
        RequestIdFilter().filter(record)
        self.assertEqual(record.request_id, request_id)

    def test_middleware_clears_context_after_response(self):
        factory = RequestFactory()
        request = factory.get('/rid-ok/')

        def get_response(req):
            self.assertNotEqual(get_request_id(), REQUEST_ID_MISSING)
            return HttpResponse('ok')

        middleware = RequestIdMiddleware(get_response)
        middleware(request)
        self.assertEqual(get_request_id(), REQUEST_ID_MISSING)

    def test_middleware_clears_context_after_exception(self):
        factory = RequestFactory()
        request = factory.get('/rid-ok/')

        def get_response(req):
            raise RuntimeError('boom')

        middleware = RequestIdMiddleware(get_response)
        with self.assertRaises(RuntimeError):
            middleware(request)
        self.assertEqual(get_request_id(), REQUEST_ID_MISSING)

    def test_threaded_contexts_do_not_leak_ids(self):
        barriers = threading.Barrier(2)
        results = {}

        def worker(name, value):
            token = set_request_id(value)
            try:
                barriers.wait(timeout=2)
                results[name] = get_request_id()
                barriers.wait(timeout=2)
            finally:
                reset_request_id(token)

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(worker, 'a', 'thread-aaa1'),
                pool.submit(worker, 'b', 'thread-bbb2'),
            ]
            for future in futures:
                future.result(timeout=5)

        self.assertEqual(results['a'], 'thread-aaa1')
        self.assertEqual(results['b'], 'thread-bbb2')
        self.assertEqual(get_request_id(), REQUEST_ID_MISSING)

    def test_logs_outside_requests_use_placeholder(self):
        record = logging.LogRecord(
            name='sigedon',
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg='outside',
            args=(),
            exc_info=None,
        )
        RequestIdFilter().filter(record)
        self.assertEqual(record.request_id, REQUEST_ID_MISSING)

    def test_request_object_receives_request_id(self):
        factory = RequestFactory()
        request = factory.get('/rid-ok/')
        seen = {}

        def get_response(req):
            seen['request_id'] = req.request_id
            return HttpResponse('ok')

        response = RequestIdMiddleware(get_response)(request)
        self.assertEqual(seen['request_id'], response[REQUEST_ID_HEADER])
