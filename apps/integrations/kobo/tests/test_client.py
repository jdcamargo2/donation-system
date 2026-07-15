from apps.integrations.kobo.client import KoboApiClient
from apps.integrations.kobo.errors import KoboAttachmentError
from apps.integrations.kobo.errors import KoboAuthenticationError
from apps.integrations.kobo.errors import KoboConfigurationError
from apps.integrations.kobo.errors import KoboIntegrationError
from apps.integrations.kobo.errors import KoboPayloadError
from apps.integrations.kobo.mappings.ficha_01 import FICHA_01_FORM_ID
from apps.integrations.kobo.mappings.ficha_01 import FICHA_01_VERSION
from apps.integrations.kobo.mappings.ficha_10 import FICHA_10_FORM_ID
from django.test import SimpleTestCase
from types import SimpleNamespace
import json


class StubHttpTransport:
    def __init__(
        self,
        *,
        status_code=200,
        body=b'{"count": 0, "next": null, "previous": null, "results": []}',
        content_type="",
        content_length=None,
        exception=None,
    ):
        self.status_code = status_code
        self.body = body
        self.content_type = content_type
        self.content_length = content_length
        self.exception = exception
        self.calls = []

    def get(self, url, *, headers, params, timeout):
        # PRE: the client supplies a complete simulated GET request.
        # POST: records the request and returns or raises the configured outcome.
        self.calls.append(
            {
                "url": url,
                "headers": headers,
                "params": params,
                "timeout": timeout,
            }
        )
        if self.exception is not None:
            raise self.exception
        return SimpleNamespace(
            status_code=self.status_code,
            body=self.body,
            content_type=self.content_type,
            content_length=self.content_length,
        )


class SequenceHttpTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, *, headers, params, timeout):
        # PRE: one configured response exists for the paginated request.
        # POST: records safe request structure and returns/raises the next response.
        self.calls.append({"url": url, "params": params, "timeout": timeout})
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return SimpleNamespace(
            status_code=200,
            body=json.dumps(response).encode(),
            content_type="application/json",
            content_length=None,
        )


class KoboApiClientTests(SimpleTestCase):
    def create_client(self, transport, **overrides):
        # PRE: transport implements get and overrides contains constructor values.
        # POST: returns a configured client that cannot perform a real HTTP request.
        values = {
            "base_url": "https://kf.example.test",
            "api_token": "top-secret-token",
            "timeout_seconds": 15,
            "transport": transport,
        }
        values.update(overrides)
        return KoboApiClient(**values)

    def test_successful_response_returns_submissions(self):
        transport = StubHttpTransport(
            body=(
                b'{"count": 1, "next": null, "previous": null, '
                b'"results": [{"_uuid": "submission-001"}]}'
            )
        )
        client = self.create_client(transport)

        submissions = client.get_submissions("asset-01", limit=25)

        self.assertEqual(submissions, [{"_uuid": "submission-001"}])
        self.assertEqual(
            transport.calls[0]["headers"]["Authorization"],
            "Token top-secret-token",
        )
        self.assertEqual(transport.calls[0]["params"], {"limit": 25})
        self.assertEqual(transport.calls[0]["timeout"], 15)

    def test_asset_detail_extracts_safe_technical_contract_metadata(self):
        client = self.create_client(
            StubHttpTransport(
                body=(
                    b'{"uid":"asset-1","content":{"settings":'
                    b'{"id_string":"ficha_1_identificacion_territorial_depurada",'
                    b'"version":"2026-07-12-depurada"}},"url":"private"}'
                )
            )
        )

        detail = client.get_asset_detail("asset-1")

        self.assertEqual(detail["id_string"], FICHA_01_FORM_ID)
        self.assertEqual(detail["version"], FICHA_01_VERSION)

    def test_asset_detail_allows_missing_version_without_guessing(self):
        client = self.create_client(
            StubHttpTransport(
                body=b'{"id_string":"ficha_10_microproyecto_priorizado_depurada"}'
            )
        )

        self.assertEqual(
            client.get_asset_detail("asset-10"),
            {"id_string": FICHA_10_FORM_ID, "version": None},
        )

    def test_missing_or_non_list_results_uses_payload_error(self):
        bodies = (
            b'{"count": 0, "next": null, "previous": null}',
            b'{"count": 1, "next": null, "previous": null, "results": {}}',
        )

        for body in bodies:
            with self.subTest(body=body):
                client = self.create_client(StubHttpTransport(body=body))
                with self.assertRaises(KoboPayloadError):
                    client.get_submissions("asset-01")

    def test_non_object_result_uses_payload_error(self):
        client = self.create_client(
            StubHttpTransport(
                body=(
                    b'{"count": 1, "next": null, "previous": null, '
                    b'"results": ["invalid"]}'
                )
            )
        )

        with self.assertRaises(KoboPayloadError):
            client.get_submissions("asset-01")

    def test_invalid_v2_envelope_metadata_uses_payload_error(self):
        bodies = (
            b'{"count": -1, "next": null, "previous": null, "results": []}',
            b'{"count": 0, "next": 7, "previous": null, "results": []}',
            b'{"count": 0, "next": null, "previous": [], "results": []}',
        )

        for body in bodies:
            with self.subTest(body=body):
                client = self.create_client(StubHttpTransport(body=body))
                with self.assertRaises(KoboPayloadError):
                    client.get_submissions("asset-01")

    def test_missing_configuration_fails_before_transport(self):
        transport = StubHttpTransport()

        for override in ({"base_url": ""}, {"api_token": ""}):
            with self.subTest(override=override):
                with self.assertRaises(KoboConfigurationError):
                    self.create_client(transport, **override)

        self.assertEqual(transport.calls, [])

    def test_invalid_request_arguments_fail_before_transport(self):
        transport = StubHttpTransport()
        client = self.create_client(transport)

        for asset_uid, limit in (("", 100), ("asset-01", 0)):
            with self.subTest(asset_uid=asset_uid, limit=limit):
                with self.assertRaises(KoboConfigurationError):
                    client.get_submissions(asset_uid, limit=limit)

        self.assertEqual(transport.calls, [])

    def test_authentication_failure_uses_specialized_error(self):
        client = self.create_client(StubHttpTransport(status_code=401))

        with self.assertRaises(KoboAuthenticationError):
            client.get_submissions("asset-01")

    def test_server_failure_uses_integration_error(self):
        client = self.create_client(StubHttpTransport(status_code=500))

        with self.assertRaises(KoboIntegrationError):
            client.get_submissions("asset-01")

    def test_network_failure_uses_integration_error(self):
        transport = StubHttpTransport(exception=OSError("connection failed"))
        client = self.create_client(transport)

        with self.assertRaises(KoboIntegrationError):
            client.get_submissions("asset-01")

    def test_invalid_json_uses_payload_error(self):
        client = self.create_client(StubHttpTransport(body=b"not-json"))

        with self.assertRaises(KoboPayloadError):
            client.get_submissions("asset-01")

    def test_token_does_not_appear_in_exceptions(self):
        token = "token-that-must-stay-secret"
        scenarios = (
            StubHttpTransport(status_code=403),
            StubHttpTransport(status_code=500),
            StubHttpTransport(exception=OSError(token)),
        )

        for transport in scenarios:
            with self.subTest(transport=transport):
                client = self.create_client(transport, api_token=token)
                with self.assertRaises(KoboIntegrationError) as context:
                    client.get_submissions("asset-01")
                self.assertNotIn(token, str(context.exception))

    def test_downloads_valid_jpeg_and_png_content(self):
        scenarios = (
            (b"\xff\xd8\xffjpeg", "image/jpeg"),
            (b"\x89PNG\r\n\x1a\npng", "image/png"),
        )

        for content, content_type in scenarios:
            with self.subTest(content_type=content_type):
                transport = StubHttpTransport(
                    body=content,
                    content_type=content_type,
                    content_length=len(content),
                )
                client = self.create_client(transport)
                downloaded = client.download_attachment(
                    "https://kf.example.test/api/attachment/1"
                )
                self.assertEqual(downloaded.content, content)
                self.assertEqual(downloaded.content_type, content_type)
                self.assertEqual(downloaded.content_length, len(content))

    def test_download_rejects_http_and_external_hosts_before_transport(self):
        transport = StubHttpTransport(body=b"content")
        client = self.create_client(transport)
        urls = (
            "http://kf.example.test/api/attachment/1",
            "https://external.example.test/api/attachment/1",
        )

        for url in urls:
            with self.subTest(url=url):
                with self.assertRaises(KoboAttachmentError):
                    client.download_attachment(url)
        self.assertEqual(transport.calls, [])

    def test_download_errors_do_not_expose_token_or_full_url(self):
        token = "download-secret-token"
        full_url = "https://kf.example.test/private/sensitive/attachment.jpg"
        client = self.create_client(
            StubHttpTransport(exception=OSError(f"failed {full_url} {token}")),
            api_token=token,
        )

        with self.assertRaises(KoboIntegrationError) as context:
            client.download_attachment(full_url)

        self.assertNotIn(token, str(context.exception))
        self.assertNotIn(full_url, str(context.exception))

    def asset_result(self, uid="asset-1", **overrides):
        # PRE: uid identifies a simulated remote Kobo asset.
        # POST: returns API metadata containing safe and intentionally unsafe fields.
        asset = {
            "uid": uid,
            "name": f"Asset {uid}",
            "asset_type": "survey",
            "deployment_status": "deployed",
            "date_created": "2026-07-10T10:00:00Z",
            "date_modified": "2026-07-11T10:00:00+00:00",
            "owner": {"username": "owner-user", "permissions": ["secret"]},
            "permissions": ["secret"],
            "url": "https://signed.example.test/secret",
            "submissions": [{"sensitive": True}],
        }
        asset.update(overrides)
        return asset

    def asset_page(self, results, *, next_page=None, previous=None):
        # PRE: results and links represent one simulated API v2 page.
        # POST: returns a complete asset pagination envelope.
        return {
            "count": len(results),
            "next": next_page,
            "previous": previous,
            "results": results,
        }

    def test_list_assets_returns_safe_single_page(self):
        transport = SequenceHttpTransport(
            [self.asset_page([self.asset_result()])]
        )
        client = self.create_client(transport)

        assets = client.list_assets(limit=25)

        self.assertEqual(len(assets), 1)
        self.assertEqual(assets[0].asset_uid, "asset-1")
        self.assertEqual(assets[0].owner_username, "owner-user")
        self.assertIsNotNone(assets[0].created_at.utcoffset())
        self.assertEqual(
            set(assets[0].safe_metadata),
            {
                "uid",
                "name",
                "asset_type",
                "deployment_status",
                "date_created",
                "date_modified",
            },
        )
        self.assertNotIn("permissions", assets[0].safe_metadata)
        self.assertEqual(transport.calls[0]["params"], {"limit": 25})

    def test_list_assets_follows_multiple_pages(self):
        next_page = "https://kf.example.test/api/v2/assets/?page=2"
        transport = SequenceHttpTransport(
            [
                self.asset_page([self.asset_result("asset-1")], next_page=next_page),
                self.asset_page(
                    [self.asset_result("asset-2")],
                    previous="https://kf.example.test/api/v2/assets/?page=1",
                ),
            ]
        )
        client = self.create_client(transport)

        assets = client.list_assets()

        self.assertEqual(tuple(asset.asset_uid for asset in assets), ("asset-1", "asset-2"))
        self.assertEqual(transport.calls[1]["url"], next_page)
        self.assertEqual(transport.calls[1]["params"], {})

    def test_list_assets_rejects_external_or_wrong_path_next(self):
        invalid_urls = (
            "https://external.example.test/api/v2/assets/?page=2",
            "https://kf.example.test/api/v2/users/?page=2",
        )

        for invalid_url in invalid_urls:
            with self.subTest(invalid_url=invalid_url):
                client = self.create_client(
                    SequenceHttpTransport(
                        [self.asset_page([], next_page=invalid_url)]
                    )
                )
                with self.assertRaises(KoboPayloadError):
                    client.list_assets()

    def test_list_assets_detects_cycle(self):
        repeated_url = "https://kf.example.test/api/v2/assets/?page=2"
        client = self.create_client(
            SequenceHttpTransport(
                [
                    self.asset_page([], next_page=repeated_url),
                    self.asset_page([], next_page=repeated_url),
                ]
            )
        )

        with self.assertRaisesMessage(KoboPayloadError, "cycle"):
            client.list_assets()

    def test_list_assets_respects_maximum_pages(self):
        client = self.create_client(
            SequenceHttpTransport(
                [
                    self.asset_page(
                        [],
                        next_page="https://kf.example.test/api/v2/assets/?page=2",
                    )
                ]
            ),
            max_asset_pages=1,
        )

        with self.assertRaisesMessage(KoboPayloadError, "maximum"):
            client.list_assets()

    def test_list_assets_rejects_missing_uid_and_invalid_envelope(self):
        pages = (
            self.asset_page([{"name": "No uid"}]),
            {"count": -1, "next": None, "previous": None, "results": []},
            {"count": 0, "next": None, "previous": None, "results": {}},
            {
                "count": 0,
                "next": None,
                "previous": "http://kf.example.test/api/v2/assets/",
                "results": [],
            },
        )

        for page in pages:
            with self.subTest(page=page):
                client = self.create_client(SequenceHttpTransport([page]))
                with self.assertRaises(KoboPayloadError):
                    client.list_assets()

    def test_list_asset_error_does_not_expose_token(self):
        token = "asset-discovery-secret-token"
        client = self.create_client(
            SequenceHttpTransport([OSError(token)]),
            api_token=token,
        )

        with self.assertRaises(KoboIntegrationError) as context:
            client.list_assets()

        self.assertNotIn(token, str(context.exception))
