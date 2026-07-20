import json
import random
import socket
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any, Callable, Iterator, Protocol
from urllib import error, parse, request

from apps.integrations.kobo.errors import (
    KoboAttachmentError,
    KoboAuthenticationError,
    KoboAuthorizationError,
    KoboConfigurationError,
    KoboIntegrationError,
    KoboInvalidResponseError,
    KoboNotFoundError,
    KoboPayloadError,
    KoboPermanentRemoteError,
    KoboRateLimitError,
    KoboTimeoutError,
    KoboTransientRemoteError,
)


@dataclass(frozen=True)
class _HttpResponse:
    status_code: int
    body: bytes
    content_type: str = ""
    content_length: int | None = None
    headers: dict[str, str] | None = None


@dataclass(frozen=True)
class DownloadedContent:
    content: bytes
    content_type: str
    content_length: int | None


@dataclass(frozen=True)
class KoboRemoteAsset:
    asset_uid: str
    name: str
    asset_type: str
    deployment_status: str
    owner_username: str
    created_at: datetime | None
    modified_at: datetime | None
    safe_metadata: dict[str, object]


KOBO_MAX_ASSET_PAGES = 100
SAFE_ASSET_METADATA_FIELDS = (
    "uid",
    "name",
    "asset_type",
    "deployment_status",
    "date_created",
    "date_modified",
    "id_string",
    "version",
)


class _HttpTransport(Protocol):
    def get(
        self,
        url: str,
        *,
        headers: dict[str, str],
        params: dict[str, int],
        timeout: float,
    ) -> _HttpResponse: ...


class _NoRedirectHandler(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # PRE: urllib received an HTTP redirect response.
        # POST: prevents automatic credential forwarding to any redirected host.
        return None


class _UrllibTransport:
    def get(
        self,
        url: str,
        *,
        headers: dict[str, str],
        params: dict[str, int],
        timeout: float,
    ) -> _HttpResponse:
        # PRE: url, headers, params and timeout describe a valid GET request.
        # POST: returns status and body for HTTP responses; network errors propagate.
        query = parse.urlencode(params)
        request_url = url
        if query:
            separator = "&" if parse.urlsplit(url).query else "?"
            request_url = f"{url}{separator}{query}"
        http_request = request.Request(
            request_url,
            headers=headers,
            method="GET",
        )
        try:
            opener = request.build_opener(_NoRedirectHandler())
            with opener.open(http_request, timeout=timeout) as response:
                return _HttpResponse(
                    response.status,
                    response.read(),
                    response.headers.get("Content-Type", ""),
                    _parse_content_length(response.headers.get("Content-Length")),
                    dict(response.headers.items()),
                )
        except error.HTTPError as exc:
            return _HttpResponse(
                exc.code,
                exc.read(),
                exc.headers.get("Content-Type", ""),
                _parse_content_length(exc.headers.get("Content-Length")),
                dict(exc.headers.items()),
            )


def _parse_content_length(value: str | None) -> int | None:
    # PRE: value is an optional HTTP Content-Length header.
    # POST: returns a non-negative integer or None for absent/invalid metadata.
    if value is None:
        return None
    try:
        parsed_value = int(value)
    except ValueError:
        return None
    return parsed_value if parsed_value >= 0 else None


class KoboApiClient:
    def __init__(
        self,
        base_url: str,
        api_token: str,
        timeout_seconds: float,
        transport: _HttpTransport | None = None,
        max_asset_pages: int = KOBO_MAX_ASSET_PAGES,
        *,
        max_attempts: int = 3,
        retry_base_delay: float = 0.5,
        retry_max_delay: float = 8,
        retry_after_max_delay: float = 60,
        sleeper: Callable[[float], None] = time.sleep,
        jitter: Callable[[], float] = random.random,
    ):
        # PRE: constructor arguments come from trusted application configuration.
        # POST: stores validated configuration without exposing credentials.
        if not base_url or not base_url.strip():
            raise KoboConfigurationError("KOBO_BASE_URL is required.")
        if not api_token or not api_token.strip():
            raise KoboConfigurationError("KOBO_API_TOKEN is required.")
        if timeout_seconds <= 0:
            raise KoboConfigurationError("Kobo request timeout must be positive.")
        if max_asset_pages <= 0:
            raise KoboConfigurationError("Kobo asset page limit must be positive.")
        if max_attempts <= 0 or min(retry_base_delay, retry_max_delay, retry_after_max_delay) < 0:
            raise KoboConfigurationError("Kobo retry configuration is invalid.")

        self._base_url = base_url.rstrip("/")
        self._api_token = api_token
        self._timeout_seconds = timeout_seconds
        self._transport = transport or _UrllibTransport()
        self._max_asset_pages = max_asset_pages
        self._max_attempts = max_attempts
        self._retry_base_delay = retry_base_delay
        self._retry_max_delay = retry_max_delay
        self._retry_after_max_delay = retry_after_max_delay
        self._sleeper = sleeper
        self._jitter = jitter

    def list_assets(self, *, limit: int = 100) -> tuple[KoboRemoteAsset, ...]:
        """
        PRE: client configuration is valid and limit is positive.
        POST: returns all validated remote assets across safe API v2 pages as an
        immutable tuple without persistence, submissions, attachments, or secrets.
        """
        if limit <= 0:
            raise KoboConfigurationError("Kobo asset page size must be positive.")

        current_url = f"{self._base_url}/api/v2/assets/"
        current_params = {"limit": limit}
        seen_urls = set()
        remote_assets = []
        page_count = 0
        while current_url is not None:
            if current_url in seen_urls:
                raise KoboPayloadError("Kobo asset pagination cycle detected.")
            if page_count >= self._max_asset_pages:
                raise KoboPayloadError("Kobo asset pagination exceeded maximum pages.")
            seen_urls.add(current_url)
            page_count += 1
            payload = self._request_json(current_url, params=current_params)
            current_params = {}
            results, next_page = self._validate_asset_page(payload)
            remote_assets.extend(self._parse_remote_asset(asset) for asset in results)
            if next_page is not None:
                self._validate_asset_page_url(next_page, field_name="next")
            current_url = next_page
        return tuple(remote_assets)

    def get_submissions(
        self,
        asset_uid: str,
        *,
        limit: int = 100,
    ) -> list[dict]:
        """
        PRE: asset_uid is a non-empty string and limit is positive.
        POST: returns the submission dictionaries reported by Kobo without
        persistence or normalization; integration failures use Kobo exceptions.
        """
        if not asset_uid or not asset_uid.strip():
            raise KoboConfigurationError("Kobo asset_uid is required.")
        if limit <= 0:
            raise KoboConfigurationError("Kobo submission limit must be positive.")

        return list(self.iter_submissions(asset_uid, limit=limit))

    def iter_submissions(self, asset_uid: str, *, limit: int = 100, params: dict[str, str | int] | None = None, max_pages: int | None = None) -> Iterator[dict]:
        """
        PRE: asset_uid and page size are valid configured values.
        POST: yields all validated Kobo submission results without retaining pages.
        """
        if not asset_uid or not asset_uid.strip() or limit <= 0:
            raise KoboConfigurationError("Kobo asset UID and page size must be positive.")
        current_url = f"{self._base_url}/api/v2/assets/{asset_uid}/data/"
        current_params = {"limit": limit, **(params or {})}
        seen_urls: set[str] = set()
        page_count = 0
        page_limit = min(self._max_asset_pages, max_pages) if max_pages else self._max_asset_pages
        while current_url is not None:
            if current_url in seen_urls or page_count >= page_limit:
                raise KoboInvalidResponseError("Kobo submission pagination cycle or page limit detected.")
            self._validate_submission_page_url(current_url, asset_uid)
            seen_urls.add(current_url)
            payload = self._request_json(current_url, params=current_params)
            current_params = {}
            results, current_url = self._validate_submission_page(payload)
            page_count += 1
            yield from results

    def get_asset_detail(self, asset_uid: str) -> dict[str, str | None]:
        """
        PRE: asset_uid is a non-empty Kobo asset UID.
        POST: returns only safe technical form metadata without persistence.
        """
        if not isinstance(asset_uid, str) or not asset_uid.strip():
            raise KoboConfigurationError("Kobo asset_uid is required.")
        payload = self._request_json(
            f"{self._base_url}/api/v2/assets/{asset_uid}/",
            params={},
        )
        if not isinstance(payload, dict):
            raise KoboPayloadError("Kobo asset detail must be an object.")
        content = payload.get("content")
        settings = content.get("settings") if isinstance(content, dict) else {}
        id_string = payload.get("id_string", settings.get("id_string"))
        version = payload.get("version", settings.get("version"))
        if id_string is not None and (not isinstance(id_string, str) or not id_string.strip()):
            raise KoboPayloadError("Kobo asset id_string is invalid.")
        if version is not None and not isinstance(version, str):
            raise KoboPayloadError("Kobo asset version is invalid.")
        return {
            "id_string": id_string.strip() if isinstance(id_string, str) else None,
            "version": version.strip() if isinstance(version, str) else None,
        }

    def download_attachment(self, url: str) -> DownloadedContent:
        """
        PRE: url is non-empty HTTPS on the configured Kobo host; timeout and
        token were validated by construction.
        POST: returns attachment bytes without persistence, redirects to external
        hosts, or credential disclosure; failures use Kobo exceptions.
        """
        if not url or not url.strip():
            raise KoboAttachmentError("Attachment URL is required.")
        attachment_url = parse.urlsplit(url)
        base_url = parse.urlsplit(self._base_url)
        if attachment_url.scheme.lower() != "https":
            raise KoboAttachmentError("Attachment URL must use HTTPS.")
        if not attachment_url.hostname or attachment_url.hostname != base_url.hostname:
            raise KoboAttachmentError("Attachment URL host is not allowed.")

        response = self._request(url, params={})
        if not response.body:
            raise KoboAttachmentError("Kobo attachment body is empty.")
        return DownloadedContent(
            content=response.body,
            content_type=getattr(response, "content_type", ""),
            content_length=getattr(response, "content_length", None),
        )

    def _request_json(self, url: str, *, params: dict[str, int]) -> dict[str, Any]:
        # PRE: url and params identify one validated Kobo API request.
        # POST: returns a JSON object or raises a safe specialized integration error.
        response = self._request(url, params=params)
        return self._decode_payload(response.body)

    def _request(self, url: str, *, params: dict[str, int]) -> _HttpResponse:
        """PRE: URL is Kobo-controlled. POST: returns success or a typed, secret-free error."""
        for attempt in range(1, self._max_attempts + 1):
            response = None
            try:
                response = self._transport.get(url, headers={"Authorization": f"Token {self._api_token}"}, params=params, timeout=self._timeout_seconds)
                failure = self._status_error(response.status_code)
            except (TimeoutError, socket.timeout):
                failure = KoboTimeoutError("Kobo request timed out.")
            except (OSError, error.URLError):
                failure = KoboTransientRemoteError("Kobo network request failed.")
            if failure is None:
                return response
            transient = isinstance(failure, (KoboTimeoutError, KoboTransientRemoteError, KoboRateLimitError))
            if not transient or attempt == self._max_attempts:
                raise failure
            self._sleeper(self._retry_delay(attempt, getattr(response, "headers", None), isinstance(failure, KoboRateLimitError) or (response is not None and response.status_code == 503)))
        raise AssertionError("unreachable Kobo retry state")

    @staticmethod
    def _status_error(status_code: int) -> KoboIntegrationError | None:
        if 200 <= status_code < 300:
            return None
        if status_code == 401:
            return KoboAuthenticationError("Kobo API authentication failed.")
        if status_code == 403:
            return KoboAuthorizationError("Kobo API authorization failed.")
        if status_code == 404:
            return KoboNotFoundError("Kobo API resource was not found.")
        if status_code == 429:
            return KoboRateLimitError("Kobo API rate limit reached.")
        if status_code in (500, 502, 503, 504):
            return KoboTransientRemoteError("Kobo API temporary failure.")
        return KoboPermanentRemoteError(f"Kobo API request failed with status {status_code}.")

    def _retry_delay(self, attempt: int, headers: dict[str, str] | None, allow_retry_after: bool) -> float:
        if allow_retry_after and headers:
            retry_after = self._parse_retry_after(headers.get("Retry-After"))
            if retry_after is not None:
                return min(retry_after, self._retry_after_max_delay)
        return min(self._retry_max_delay, self._retry_base_delay * (2 ** (attempt - 1)) + self._jitter() * self._retry_base_delay)

    @staticmethod
    def _parse_retry_after(value: str | None) -> float | None:
        if not value:
            return None
        try:
            return max(0.0, float(value))
        except ValueError:
            pass
        try:
            return max(0.0, (parsedate_to_datetime(value).astimezone(UTC) - datetime.now(UTC)).total_seconds())
        except (TypeError, ValueError):
            return None

    def _validate_asset_page(
        self,
        payload: dict[str, Any],
    ) -> tuple[list[dict], str | None]:
        # PRE: payload is a decoded candidate API v2 asset envelope.
        # POST: returns validated results/next or raises KoboPayloadError.
        required_fields = {"count", "next", "previous", "results"}
        if not required_fields.issubset(payload):
            raise KoboPayloadError("Kobo asset response envelope is incomplete.")
        if type(payload["count"]) is not int or payload["count"] < 0:
            raise KoboPayloadError("Kobo asset count must be a non-negative integer.")
        if not isinstance(payload["results"], list) or not all(
            isinstance(asset, dict) for asset in payload["results"]
        ):
            raise KoboPayloadError("Kobo asset results must be a list of objects.")
        for field_name in ("next", "previous"):
            page_url = payload[field_name]
            if page_url is not None:
                if not isinstance(page_url, str):
                    raise KoboPayloadError(
                        f"Kobo asset {field_name} must be an HTTPS URL or null."
                    )
                parsed_url = parse.urlsplit(page_url)
                if parsed_url.scheme.lower() != "https" or not parsed_url.hostname:
                    raise KoboPayloadError(
                        f"Kobo asset {field_name} must be an HTTPS URL or null."
                    )
        return payload["results"], payload["next"]

    def _validate_asset_page_url(self, url: str, *, field_name: str) -> None:
        # PRE: url is a structurally valid HTTPS pagination URL.
        # POST: permits only the configured host and API v2 assets path.
        parsed_url = parse.urlsplit(url)
        base_url = parse.urlsplit(self._base_url)
        if parsed_url.hostname != base_url.hostname:
            raise KoboPayloadError(f"Kobo asset {field_name} host is not allowed.")
        if not parsed_url.path.startswith("/api/v2/assets"):
            raise KoboPayloadError(f"Kobo asset {field_name} path is not allowed.")

    def _validate_submission_page_url(self, url: str, asset_uid: str) -> None:
        # PRE: url is a Kobo pagination candidate.
        # POST: accepts only HTTPS pagination on the configured asset endpoint.
        parsed_url = parse.urlsplit(url)
        base_url = parse.urlsplit(self._base_url)
        if parsed_url.scheme.lower() != "https" or parsed_url.hostname != base_url.hostname:
            raise KoboInvalidResponseError("Kobo submission pagination host is not allowed.")
        if not parsed_url.path.startswith(f"/api/v2/assets/{asset_uid}/data"):
            raise KoboInvalidResponseError("Kobo submission pagination path is not allowed.")

    def _validate_submission_page(self, payload: dict[str, Any]) -> tuple[list[dict], str | None]:
        # PRE: payload is decoded JSON from a Kobo submissions response.
        # POST: returns a validated page envelope or raises a typed response error.
        required_fields = {"count", "next", "previous", "results"}
        if not required_fields.issubset(payload) or type(payload["count"]) is not int or payload["count"] < 0:
            raise KoboInvalidResponseError("Kobo submission response envelope is invalid.")
        results = payload["results"]
        next_url = payload["next"]
        if not isinstance(results, list) or not all(isinstance(item, dict) for item in results):
            raise KoboInvalidResponseError("Kobo submission results must be objects.")
        if next_url is not None and not isinstance(next_url, str):
            raise KoboInvalidResponseError("Kobo submission next must be a URL or null.")
        return results, next_url

    @staticmethod
    def _parse_remote_asset(asset: dict[str, Any]) -> KoboRemoteAsset:
        # PRE: asset is one API result object.
        # POST: returns a safe immutable projection or raises KoboPayloadError.
        asset_uid = asset.get("uid")
        if not isinstance(asset_uid, str) or not asset_uid.strip():
            raise KoboPayloadError("Kobo asset uid must be a non-empty string.")

        def optional_text(field_name: str) -> str:
            value = asset.get(field_name)
            if value is None:
                return ""
            if not isinstance(value, str):
                raise KoboPayloadError(f"Kobo asset {field_name} must be text.")
            return value.strip()

        owner = asset.get("owner")
        owner_username = ""
        if isinstance(owner, dict) and isinstance(owner.get("username"), str):
            owner_username = owner["username"].strip()
        elif isinstance(asset.get("owner_username"), str):
            owner_username = asset["owner_username"].strip()

        safe_metadata = {
            field_name: asset[field_name]
            for field_name in SAFE_ASSET_METADATA_FIELDS
            if field_name in asset
        }
        return KoboRemoteAsset(
            asset_uid=asset_uid.strip(),
            name=optional_text("name"),
            asset_type=optional_text("asset_type"),
            deployment_status=optional_text("deployment_status"),
            owner_username=owner_username,
            created_at=KoboApiClient._parse_remote_datetime(
                asset.get("date_created"),
                field_name="date_created",
            ),
            modified_at=KoboApiClient._parse_remote_datetime(
                asset.get("date_modified"),
                field_name="date_modified",
            ),
            safe_metadata=safe_metadata,
        )

    @staticmethod
    def _parse_remote_datetime(value: object, *, field_name: str) -> datetime | None:
        # PRE: value is optional remote ISO 8601 datetime data.
        # POST: returns an aware datetime/None or raises KoboPayloadError.
        if value in (None, ""):
            return None
        if not isinstance(value, str):
            raise KoboPayloadError(f"Kobo asset {field_name} must be ISO 8601 text.")
        try:
            parsed_value = datetime.fromisoformat(value)
        except ValueError as exc:
            raise KoboPayloadError(
                f"Kobo asset {field_name} must be valid ISO 8601."
            ) from exc
        if parsed_value.tzinfo is None or parsed_value.utcoffset() is None:
            raise KoboPayloadError(f"Kobo asset {field_name} must include an offset.")
        return parsed_value

    @staticmethod
    def _decode_payload(body: bytes) -> dict[str, Any]:
        # PRE: body is the raw body of a successful Kobo response.
        # POST: returns a JSON object or raises KoboPayloadError.
        try:
            payload = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise KoboPayloadError("Kobo API returned invalid JSON.") from exc

        if not isinstance(payload, dict):
            raise KoboPayloadError("Kobo API response must be a JSON object.")
        return payload
