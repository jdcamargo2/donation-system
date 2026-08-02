"""
Logging filters and formatters for SIGEDON runtime observability.

PRE: log records may or may not carry a request context.
POST: every formatted record includes request_id; common secret patterns are
      redacted to [REDACTED]; UTC timestamps are used where practical.
"""

from __future__ import annotations

import logging
import re
import time

from core.request_ids import REQUEST_ID_MISSING, get_request_id

REDACTED = '[REDACTED]'

# Defense-in-depth patterns for accidental secret leakage in log text.
# Intentionally narrow: do not redact harmless words into unreadability.
_AUTH_HEADER = re.compile(
    r'(?i)(\bauthorization\s*[:=]\s*)((?:bearer|basic)\s+)?(\S+)'
)
_COOKIE_HEADER = re.compile(
    r'(?i)(\b(?:set-)?cookie\s*[:=]\s*)([^\r\n]+)'
)
_SENSITIVE_KEY_VALUE = re.compile(
    r'(?i)\b('
    r'password|passwd|secret|token|api_token|webhook_secret|'
    r'csrf(?:middlewaretoken)?|database_url|postgres_password|'
    r'authorization|cookie|set-cookie'
    r')\b(\s*[:=]\s*)([^\s&;\'",}]+)'
)
_DATABASE_URL_PASSWORD = re.compile(
    r'(?i)((?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis)://[^:\s/]+:)([^@\s]+)(@)'
)


def redact_sensitive_text(message: str) -> str:
    """
    PRE: message is an arbitrary log string that may contain accidental secrets.
    POST: returns a copy with known sensitive values replaced by [REDACTED].
    """
    if not message:
        return message

    def _replace_auth(match: re.Match[str]) -> str:
        scheme = match.group(2) or ''
        value = match.group(3)
        if value == REDACTED:
            return match.group(0)
        return f'{match.group(1)}{scheme}{REDACTED}'

    def _replace_cookie(match: re.Match[str]) -> str:
        if match.group(2) == REDACTED:
            return match.group(0)
        return f'{match.group(1)}{REDACTED}'

    def _replace_db(match: re.Match[str]) -> str:
        if match.group(2) == REDACTED:
            return match.group(0)
        return f'{match.group(1)}{REDACTED}{match.group(3)}'

    def _replace_kv(match: re.Match[str]) -> str:
        if match.group(3) == REDACTED:
            return match.group(0)
        return f'{match.group(1)}{match.group(2)}{REDACTED}'

    redacted = _AUTH_HEADER.sub(_replace_auth, message)
    redacted = _COOKIE_HEADER.sub(_replace_cookie, redacted)
    redacted = _DATABASE_URL_PASSWORD.sub(_replace_db, redacted)
    redacted = _SENSITIVE_KEY_VALUE.sub(_replace_kv, redacted)
    return redacted


class RequestIdFilter(logging.Filter):
    """
    Injects request_id into every log record.

    Prefers ``record.request.request_id`` (Django log_response extras) so
    django.request lines keep the ID after middleware clears the ContextVar.
    Falls back to the ContextVar, then '-'.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        request = getattr(record, 'request', None)
        request_id = getattr(request, 'request_id', None) if request is not None else None
        if not request_id:
            request_id = get_request_id() or REQUEST_ID_MISSING
        record.request_id = request_id
        return True


class SensitiveDataRedactionFilter(logging.Filter):
    """
    Redacts common secret patterns from log messages and string arguments.

    Does not serialize request objects. Exception formatting is also covered
    when SigedonFormatter formats the final record text.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact_sensitive_text(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {
                    key: (
                        redact_sensitive_text(value)
                        if isinstance(value, str)
                        else value
                    )
                    for key, value in record.args.items()
                }
            elif isinstance(record.args, tuple):
                # When the template already names a sensitive key (token=%s),
                # redact unsubstituted string args defensively.
                template = record.msg if isinstance(record.msg, str) else ''
                redact_all_string_args = bool(
                    re.search(
                        r'(?i)\b(password|passwd|secret|token|api_token|'
                        r'webhook_secret|authorization|cookie|csrf)\b',
                        template,
                    )
                )
                record.args = tuple(
                    (
                        REDACTED
                        if redact_all_string_args and isinstance(arg, str)
                        else (
                            redact_sensitive_text(arg)
                            if isinstance(arg, str)
                            else arg
                        )
                    )
                    for arg in record.args
                )
        return True


class MaxLevelFilter(logging.Filter):
    """Allow only records at or below max_level (inclusive)."""

    def __init__(self, name: str = '', *, max_level: int = logging.INFO):
        super().__init__(name)
        self.max_level = max_level

    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno <= self.max_level


class SigedonFormatter(logging.Formatter):
    """UTC formatter that always exposes request_id and redacts final text."""

    converter = time.gmtime

    def format(self, record: logging.LogRecord) -> str:
        if not hasattr(record, 'request_id'):
            record.request_id = REQUEST_ID_MISSING
        return redact_sensitive_text(super().format(record))
