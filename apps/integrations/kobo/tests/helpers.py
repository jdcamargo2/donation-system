from django.core.files.storage import InMemoryStorage
from django.db import connection
from dataclasses import dataclass
import json


class StubAttachmentClient:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []
        self.in_atomic_flags = []

    def download_attachment(self, url, *, max_bytes=None):
        # PRE: a pending attachment supplies its source URL and size cap.
        # POST: records the URL and returns or raises the next configured outcome.
        self.calls.append(url)
        self.in_atomic_flags.append(connection.in_atomic_block)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@dataclass(frozen=True)
class FakeResponse:
    """Minimal transport response matching KoboApiClient's adapter contract."""

    status_code: int = 200
    body: bytes = b'{"count": 0, "next": null, "previous": null, "results": []}'
    content_type: str = "application/json"
    content_length: int | None = None
    headers: dict[str, str] | None = None

    @classmethod
    def json(cls, payload, *, status_code=200, headers=None):
        # PRE: payload is JSON-serializable remote response data.
        # POST: returns an immutable response with the adapter's byte-body contract.
        body = json.dumps(payload).encode()
        return cls(status_code=status_code, body=body, headers=headers)


class SequenceTransport:
    """Deterministic adapter fake for retries and pagination."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def get(self, url, *, headers, params, timeout, max_bytes=None):
        # PRE: one configured response or exception exists for this request.
        # POST: records the request and returns or raises exactly one outcome.
        self.calls.append(
            {
                "url": url,
                "headers": headers,
                "params": params,
                "timeout": timeout,
                "max_bytes": max_bytes,
            }
        )
        if not self.outcomes:
            raise AssertionError("Unexpected Kobo transport request.")
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        if isinstance(outcome, dict):
            return FakeResponse.json(outcome)
        return outcome


class RecordingSleeper:
    def __init__(self):
        self.delays = []

    def __call__(self, delay):
        # PRE: delay was calculated by the client retry policy.
        # POST: records it without sleeping.
        self.delays.append(delay)


class RecordingAttachmentStorage(InMemoryStorage):
    def __init__(self, *, fail_delete=False):
        super().__init__()
        self.fail_delete = fail_delete
        self.saved = []
        self.deleted = []

    def save(self, name, content, max_length=None):
        self.saved.append((name, connection.in_atomic_block))
        return super().save(name, content, max_length)

    def delete(self, name):
        self.deleted.append((name, connection.in_atomic_block))
        if self.fail_delete:
            raise OSError("storage delete failed")
        return super().delete(name)
