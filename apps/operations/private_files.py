"""Authorized private-file delivery (stream or signed redirect).

PRE: callers completed authentication and domain authorization before invoking
     deliver_private_file. Storage credentials must never reach responses/logs.
POST: returns a hardened HttpResponse, or raises Http404 / returns safe 503
      for distinguishable provider outages. Signed URLs are generated only after
      authorization and are never logged. Inline previews always stream so
      Django can apply CSP; signed redirects require response-parameter support.
"""

from __future__ import annotations

import inspect
import logging
from pathlib import PurePosixPath

from django.conf import settings
from django.core.files.storage import Storage
from django.http import FileResponse, Http404, HttpResponse, HttpResponseRedirect
from django.utils.text import get_valid_filename
from django.utils.translation import gettext as _

from core.private_storage import (
    PRIVATE_FILE_DELIVERY_SIGNED_REDIRECT,
    PRIVATE_FILE_DELIVERY_STREAM,
)

logger = logging.getLogger('sigedon.storage')

PREVIEWABLE_EXTENSION_MIME_TYPES = {
    '.png': 'image/png',
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.webp': 'image/webp',
    '.gif': 'image/gif',
    '.pdf': 'application/pdf',
    '.txt': 'text/plain; charset=utf-8',
}

DISPOSITION_INLINE = 'inline'
DISPOSITION_ATTACHMENT = 'attachment'

_PROTECTED_CACHE_CONTROL = 'private, no-store'
_PUBLIC_CACHE_CONTROL = 'public, max-age=300'
_PROTECTED_NOSNIFF = 'nosniff'
_FALLBACK_DOWNLOAD_MIME = 'application/octet-stream'
_FALLBACK_FILENAME = 'documento'

# Applied to every inline preview response Django controls (stream mode).
# Signed redirects cannot inject CSP into the provider object response, so
# inline disposition always streams through Django.
PROTECTED_PREVIEW_CSP = (
    "default-src 'none'; "
    "sandbox; "
    "img-src 'self' data:; "
    "style-src 'none'; "
    "script-src 'none'; "
    "connect-src 'none'; "
    "form-action 'none'; "
    "base-uri 'none'; "
    "frame-ancestors 'none'"
)

# Provider outages / permission errors that should not become 404.
_PROVIDER_UNAVAILABLE_EXC_NAMES = frozenset(
    {
        'ClientError',
        'BotoCoreError',
        'EndpointConnectionError',
        'ConnectTimeoutError',
        'ReadTimeoutError',
        'ConnectionClosedError',
        'ConnectionError',
        'TimeoutError',
    }
)


class PrivateStorageUnavailable(Exception):
    """Raised when object storage is temporarily unavailable after authorization."""


def storage_supports_response_parameters(storage: Storage) -> bool:
    """
    PRE: storage is the FieldFile's storage backend.
    POST: True only when storage.url explicitly accepts a ``parameters`` argument
          (required for Content-Disposition / Content-Type response overrides).
    """
    url_method = getattr(storage, 'url', None)
    if url_method is None:
        return False
    try:
        signature = inspect.signature(url_method)
    except (TypeError, ValueError):
        return False
    return 'parameters' in signature.parameters


def _normalized_extension(file_field) -> str:
    if not file_field or not getattr(file_field, 'name', None):
        return ''
    name = str(file_field.name).replace('\\', '/')
    return PurePosixPath(name).suffix.lower()


def get_safe_persisted_file_preview_type(file_field) -> str | None:
    return PREVIEWABLE_EXTENSION_MIME_TYPES.get(_normalized_extension(file_field))


def can_preview_persisted_file(file_field) -> bool:
    return get_safe_persisted_file_preview_type(file_field) is not None


def sanitize_download_filename(file_field, *, fallback: str = _FALLBACK_FILENAME) -> str:
    if not file_field or not getattr(file_field, 'name', None):
        return fallback
    stored_basename = str(file_field.name).replace('\\', '/').rsplit('/', 1)[-1]
    cleaned = (
        stored_basename.replace('\r', '')
        .replace('\n', '')
        .replace('"', '')
        .replace("'", '')
    )
    safe_filename = get_valid_filename(cleaned) or fallback
    return safe_filename or fallback


def _is_provider_unavailable(exc: BaseException) -> bool:
    name = type(exc).__name__
    if name in _PROVIDER_UNAVAILABLE_EXC_NAMES:
        return True
    # botocore ClientError with 5xx / SlowDown
    response = getattr(exc, 'response', None)
    if isinstance(response, dict):
        meta = response.get('ResponseMetadata') or {}
        status = meta.get('HTTPStatusCode')
        if isinstance(status, int) and status >= 500:
            return True
        code = (response.get('Error') or {}).get('Code')
        if code in {'SlowDown', 'ServiceUnavailable', 'RequestTimeout'}:
            return True
    return False


def _safe_operational_error_response() -> HttpResponse:
    response = HttpResponse(
        _('Almacenamiento de archivos temporalmente no disponible.'),
        status=503,
        content_type='text/plain; charset=utf-8',
    )
    response['Cache-Control'] = _PROTECTED_CACHE_CONTROL
    response['X-Content-Type-Options'] = _PROTECTED_NOSNIFF
    return response


def _apply_protected_headers(
    response: HttpResponse, *, content_type: str, disposition: str
) -> None:
    response['Content-Type'] = content_type
    response['X-Content-Type-Options'] = _PROTECTED_NOSNIFF
    response['Cache-Control'] = _PROTECTED_CACHE_CONTROL
    if disposition == DISPOSITION_INLINE:
        response['Content-Security-Policy'] = PROTECTED_PREVIEW_CSP


def _resolve_delivery_mode() -> str:
    mode = getattr(
        settings,
        'SIGEDON_PRIVATE_FILE_DELIVERY',
        PRIVATE_FILE_DELIVERY_STREAM,
    )
    if mode not in (PRIVATE_FILE_DELIVERY_STREAM, PRIVATE_FILE_DELIVERY_SIGNED_REDIRECT):
        return PRIVATE_FILE_DELIVERY_STREAM
    return mode


def _content_disposition_header(disposition: str, filename: str) -> str:
    # ASCII-safe quoted filename; CR/LF already stripped by sanitize.
    return f'{disposition}; filename="{filename}"'


def _stream_private_file(
    file_field,
    *,
    disposition: str,
    content_type: str,
    safe_filename: str,
    missing_message,
) -> FileResponse:
    try:
        file_handle = file_field.open('rb')
    except FileNotFoundError as exc:
        raise Http404(missing_message) from exc
    except OSError as exc:
        if _is_provider_unavailable(exc):
            logger.error(
                'private_file_stream_unavailable field=%s category=provider_unavailable',
                getattr(file_field, 'field', None) and file_field.field.name or 'file',
            )
            raise PrivateStorageUnavailable from exc
        raise Http404(missing_message) from exc
    except Exception as exc:  # noqa: BLE001 - map provider exceptions safely
        if _is_provider_unavailable(exc):
            logger.error(
                'private_file_stream_unavailable field=%s category=provider_unavailable',
                getattr(file_field, 'field', None) and file_field.field.name or 'file',
            )
            raise PrivateStorageUnavailable from exc
        logger.error(
            'private_file_stream_error field=%s category=storage_error',
            getattr(file_field, 'field', None) and file_field.field.name or 'file',
        )
        raise Http404(missing_message) from exc

    response = FileResponse(
        file_handle,
        as_attachment=(disposition == DISPOSITION_ATTACHMENT),
        filename=safe_filename,
        content_type=content_type,
    )
    _apply_protected_headers(response, content_type=content_type, disposition=disposition)
    return response


def _signed_redirect_private_file(
    file_field,
    *,
    disposition: str,
    content_type: str,
    safe_filename: str,
    missing_message,
) -> HttpResponse:
    """
    PRE: disposition is attachment; authorization already succeeded.
    POST: redirects to a signed URL with response overrides when the backend
          supports parameters; otherwise streams without generating a bare URL.
    """
    storage: Storage = file_field.storage
    name = file_field.name

    if not storage_supports_response_parameters(storage):
        logger.info(
            'private_file_signed_redirect_fallback category=parameters_unsupported'
        )
        return _stream_private_file(
            file_field,
            disposition=disposition,
            content_type=content_type,
            safe_filename=safe_filename,
            missing_message=missing_message,
        )

    try:
        exists = storage.exists(name)
    except Exception as exc:  # noqa: BLE001
        if _is_provider_unavailable(exc):
            raise PrivateStorageUnavailable from exc
        raise Http404(missing_message) from exc
    if not exists:
        raise Http404(missing_message)

    parameters = {
        'ResponseContentDisposition': _content_disposition_header(
            disposition, safe_filename
        ),
        'ResponseContentType': content_type,
    }
    try:
        signed_url = storage.url(name, parameters=parameters)
    except TypeError:
        # Capability advertised but call rejected — never drop parameters.
        logger.info(
            'private_file_signed_redirect_fallback category=parameters_typeerror'
        )
        return _stream_private_file(
            file_field,
            disposition=disposition,
            content_type=content_type,
            safe_filename=safe_filename,
            missing_message=missing_message,
        )
    except Exception as exc:  # noqa: BLE001
        if _is_provider_unavailable(exc):
            logger.error(
                'private_file_signed_url_unavailable category=provider_unavailable'
            )
            raise PrivateStorageUnavailable from exc
        logger.error('private_file_signed_url_failed category=storage_error')
        raise Http404(missing_message) from exc

    # Never log signed_url. Response must not be cached.
    response = HttpResponseRedirect(signed_url)
    response['Cache-Control'] = _PROTECTED_CACHE_CONTROL
    response['X-Content-Type-Options'] = _PROTECTED_NOSNIFF
    return response


def deliver_private_file(
    request,
    field_file,
    *,
    download_name=None,
    content_type=None,
    disposition: str = DISPOSITION_ATTACHMENT,
    missing_message=None,
):
    """
    PRE: request is authorized for this field_file; disposition is inline|attachment.
    POST: streams via storage API or redirects to a short-lived signed URL.
          Unauthorized callers must never reach this function.
          Inline always streams (CSP control). Signed redirect only for
          attachments when the backend supports response parameters.
    """
    if disposition not in (DISPOSITION_INLINE, DISPOSITION_ATTACHMENT):
        raise ValueError(f'Unsupported disposition: {disposition!r}')

    message = missing_message or _('Archivo no encontrado.')
    if not field_file or not getattr(field_file, 'name', None):
        raise Http404(message)

    safe_filename = download_name or sanitize_download_filename(field_file)
    # Re-sanitize even when download_name is provided (header injection defense).
    safe_filename = (
        get_valid_filename(
            str(safe_filename).replace('\r', '').replace('\n', '').replace('"', '')
        )
        or _FALLBACK_FILENAME
    )
    preview_mime = get_safe_persisted_file_preview_type(field_file)

    if disposition == DISPOSITION_INLINE:
        if preview_mime is None:
            raise Http404(_('Vista previa no disponible para este tipo de archivo.'))
        resolved_type = content_type or preview_mime
    else:
        resolved_type = content_type or preview_mime or _FALLBACK_DOWNLOAD_MIME

    delivery = _resolve_delivery_mode()
    # Inline previews always stream: Django must set CSP; signed redirects
    # cannot inject CSP into the final provider object response.
    use_signed_redirect = (
        delivery == PRIVATE_FILE_DELIVERY_SIGNED_REDIRECT
        and disposition == DISPOSITION_ATTACHMENT
    )
    try:
        if use_signed_redirect:
            return _signed_redirect_private_file(
                field_file,
                disposition=disposition,
                content_type=resolved_type,
                safe_filename=safe_filename,
                missing_message=message,
            )
        return _stream_private_file(
            field_file,
            disposition=disposition,
            content_type=resolved_type,
            safe_filename=safe_filename,
            missing_message=message,
        )
    except PrivateStorageUnavailable:
        return _safe_operational_error_response()


def deliver_public_file(
    field_file,
    *,
    download_name=None,
    content_type=None,
    disposition: str = DISPOSITION_ATTACHMENT,
    missing_message=None,
):
    """
    PRE: caller already confirmed public eligibility for this field_file;
         disposition is inline|attachment.
    POST: streams through Django with sanitized headers and a public cache policy.
          Never logs storage keys or signed URLs. Does not authorize private files.
    """
    if disposition not in (DISPOSITION_INLINE, DISPOSITION_ATTACHMENT):
        raise ValueError(f'Unsupported disposition: {disposition!r}')

    message = missing_message or _('Archivo no encontrado.')
    if not field_file or not getattr(field_file, 'name', None):
        raise Http404(message)

    safe_filename = download_name or sanitize_download_filename(field_file)
    safe_filename = (
        get_valid_filename(
            str(safe_filename).replace('\r', '').replace('\n', '').replace('"', '')
        )
        or _FALLBACK_FILENAME
    )
    preview_mime = get_safe_persisted_file_preview_type(field_file)

    if disposition == DISPOSITION_INLINE:
        if preview_mime is None:
            raise Http404(_('Vista previa no disponible para este tipo de archivo.'))
        resolved_type = content_type or preview_mime
    else:
        resolved_type = content_type or preview_mime or _FALLBACK_DOWNLOAD_MIME

    # Public delivery always streams so Django owns Content-Type / Disposition /
    # nosniff / CSP without relying on provider response overrides.
    try:
        response = _stream_private_file(
            field_file,
            disposition=disposition,
            content_type=resolved_type,
            safe_filename=safe_filename,
            missing_message=message,
        )
    except PrivateStorageUnavailable:
        return _safe_operational_error_response()

    response['Cache-Control'] = _PUBLIC_CACHE_CONTROL
    response['X-Content-Type-Options'] = _PROTECTED_NOSNIFF
    if disposition == DISPOSITION_INLINE:
        response['Content-Security-Policy'] = PROTECTED_PREVIEW_CSP
    return response
