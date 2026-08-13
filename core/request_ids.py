"""
Safe HTTP request/correlation identifiers for SIGEDON runtime logs.

PRE: callers pass optional inbound header values without trusting them blindly.
POST: every request gets a validated or generated ID bound via contextvars;
      invalid inbound values are replaced and never logged or reflected.
"""

from __future__ import annotations

import re
import uuid
from contextvars import ContextVar, Token

REQUEST_ID_HEADER = 'X-Request-ID'
REQUEST_ID_MISSING = '-'
_SAFE_PATTERN = re.compile(r'^[A-Za-z0-9._-]{8,64}$')

_request_id_var: ContextVar[str] = ContextVar('sigedon_request_id', default=REQUEST_ID_MISSING)


def get_request_id() -> str:
    """
    PRE: none.
    POST: returns the current request ID or '-' outside an HTTP request.
    """
    return _request_id_var.get()


def set_request_id(value: str) -> Token:
    """
    PRE: value is a safe request ID previously validated or generated.
    POST: binds value for the current context and returns a reset token.
    """
    return _request_id_var.set(value)


def reset_request_id(token: Token) -> None:
    """
    PRE: token was returned by set_request_id for the current context.
    POST: restores the previous request-ID context value.
    """
    _request_id_var.reset(token)


def normalize_or_generate_request_id(value: str | None) -> str:
    """
    PRE: value is an optional inbound X-Request-ID candidate.
    POST: returns value when it matches the safe format; otherwise a new hex UUID.
    """
    if value is None:
        return uuid.uuid4().hex
    if not isinstance(value, str):
        return uuid.uuid4().hex
    candidate = value.strip()
    # Reject values that needed trimming (whitespace) or fail the strict pattern.
    if candidate != value or not _SAFE_PATTERN.fullmatch(candidate):
        return uuid.uuid4().hex
    return candidate


class RequestIdMiddleware:
    """
    Early middleware that assigns a safe X-Request-ID to each HTTP request.

    Placement: immediately after SecurityMiddleware. Does not depend on
    sessions or authentication. Clears contextvars in finally.

    django.request logs still receive the ID via RequestIdFilter reading
    ``record.request.request_id`` from Django's log_response extras.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        inbound = request.headers.get(REQUEST_ID_HEADER)
        request_id = normalize_or_generate_request_id(inbound)
        request.request_id = request_id
        token = set_request_id(request_id)
        try:
            response = self.get_response(request)
            response[REQUEST_ID_HEADER] = request_id
            return response
        finally:
            reset_request_id(token)
