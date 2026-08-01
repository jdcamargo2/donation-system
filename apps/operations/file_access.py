"""Protected persisted-file preview and download helpers.

PRE: callers authorize the parent object and model permission before invoking
     response builders.
POST: streams only authorized storage objects with hardened headers; never
     exposes absolute paths or trusts upload MIME for inline safety.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath

from django.http import FileResponse, Http404
from django.urls import reverse
from django.utils.text import get_valid_filename
from django.utils.translation import gettext as _

# Strict server-owned whitelist. Extension alone decides inline safety.
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
_PROTECTED_NOSNIFF = 'nosniff'
_FALLBACK_DOWNLOAD_MIME = 'application/octet-stream'
_FALLBACK_FILENAME = 'documento'


def _normalized_extension(file_field) -> str:
    """
    PRE: file_field may be empty or point at a stored relative path.
    POST: returns the lowercase suffix including the leading dot, or ''.
    """
    if not file_field or not getattr(file_field, 'name', None):
        return ''
    name = str(file_field.name).replace('\\', '/')
    return PurePosixPath(name).suffix.lower()


def get_safe_persisted_file_preview_type(file_field) -> str | None:
    """
    PRE: file_field is a Django FileField/FieldFile or None.
    POST: returns the controlled MIME for whitelist extensions, else None.
    Does not trust browser-provided upload MIME types.
    """
    return PREVIEWABLE_EXTENSION_MIME_TYPES.get(_normalized_extension(file_field))


def can_preview_persisted_file(file_field) -> bool:
    """
    PRE: file_field is a Django FileField/FieldFile or None.
    POST: True only when the stored basename extension is in the whitelist.
    """
    return get_safe_persisted_file_preview_type(file_field) is not None


def sanitize_download_filename(file_field, *, fallback: str = _FALLBACK_FILENAME) -> str:
    """
    PRE: file_field may contain path separators, quotes, or empty basenames.
    POST: returns a single-segment filename safe for Content-Disposition.
    """
    if not file_field or not getattr(file_field, 'name', None):
        return fallback
    stored_basename = str(file_field.name).replace('\\', '/').rsplit('/', 1)[-1]
    # Strip CR/LF and quotes before Django's validator.
    cleaned = (
        stored_basename.replace('\r', '')
        .replace('\n', '')
        .replace('"', '')
        .replace("'", '')
    )
    safe_filename = get_valid_filename(cleaned) or fallback
    return safe_filename or fallback


def protected_file_response(
    file_field,
    *,
    disposition: str,
    missing_message,
):
    """
    PRE: caller authorized the parent object; disposition is inline|attachment;
         missing_message is a safe user-facing string.
    POST: streams the storage object with hardened headers, or raises Http404
          without leaking paths. Inline disposition refuses non-whitelist types.
    """
    if disposition not in (DISPOSITION_INLINE, DISPOSITION_ATTACHMENT):
        raise ValueError(f'Unsupported disposition: {disposition!r}')

    if not file_field or not getattr(file_field, 'name', None):
        raise Http404(missing_message)

    safe_filename = sanitize_download_filename(file_field)
    preview_mime = get_safe_persisted_file_preview_type(file_field)

    if disposition == DISPOSITION_INLINE:
        if preview_mime is None:
            raise Http404(_('Vista previa no disponible para este tipo de archivo.'))
        content_type = preview_mime
    else:
        content_type = preview_mime or _FALLBACK_DOWNLOAD_MIME

    try:
        file_handle = file_field.open('rb')
    except (FileNotFoundError, OSError) as exc:
        raise Http404(missing_message) from exc

    response = FileResponse(
        file_handle,
        as_attachment=(disposition == DISPOSITION_ATTACHMENT),
        filename=safe_filename,
        content_type=content_type,
    )
    response['Content-Type'] = content_type
    response['X-Content-Type-Options'] = _PROTECTED_NOSNIFF
    response['Cache-Control'] = _PROTECTED_CACHE_CONTROL
    if disposition == DISPOSITION_INLINE and content_type.startswith('image/'):
        # Narrow CSP for images only; omit for PDF to preserve native viewers.
        response['Content-Security-Policy'] = "default-src 'none'; img-src 'self' data:; sandbox"
    return response


@dataclass(frozen=True)
class ProtectedFileActions:
    """Template-safe action contract for one persisted file row."""

    file_name: str
    uploaded_at: object | None
    file_size: int | None
    preview_url: str | None
    download_url: str | None
    can_preview: bool
    can_download: bool
    delete_url: str | None
    can_delete: bool
    file_label: str


def build_protected_file_actions(
    *,
    file_field,
    file_label: str,
    uploaded_at=None,
    can_download: bool,
    preview_url_name: str | None = None,
    download_url_name: str | None = None,
    url_args: tuple = (),
    delete_url: str | None = None,
    can_delete: bool = False,
) -> ProtectedFileActions:
    """
    PRE: URL names/args resolve only when can_download is True; file_field may
         be empty. Preview safety is decided server-side from the extension.
    POST: returns explicit action flags/URLs without storage URLs or path leaks.
    """
    file_name = sanitize_download_filename(file_field, fallback=file_label or _FALLBACK_FILENAME)
    file_size = None
    if file_field and getattr(file_field, 'name', None):
        try:
            file_size = file_field.size
        except (FileNotFoundError, OSError, ValueError):
            file_size = None

    preview_allowed = bool(can_download and can_preview_persisted_file(file_field))
    preview_url = None
    download_url = None
    if can_download and download_url_name:
        download_url = reverse(download_url_name, args=url_args)
    if preview_allowed and preview_url_name:
        preview_url = reverse(preview_url_name, args=url_args)

    return ProtectedFileActions(
        file_name=file_name,
        uploaded_at=uploaded_at,
        file_size=file_size,
        preview_url=preview_url,
        download_url=download_url,
        can_preview=preview_allowed,
        can_download=bool(can_download and download_url),
        delete_url=delete_url if can_delete else None,
        can_delete=bool(can_delete and delete_url),
        file_label=file_label,
    )


def user_can_access_project_supporting_document(user, document) -> bool:
    """
    Narrow supporting-document access without granting view_expense.

    Policy: a user with operations.view_supportingdocument may preview/download
    a support file when they also have operations.view_project for the related
    project (expense → allocation → project). This does not authorize opening
    ExpenseDetailView or reading financial fields.

    PRE: document is a SupportingDocument with expense/allocation/project loaded
         or resolvable; user is authenticated.
    POST: True only when both view_supportingdocument and view_project hold.
    """
    if not user.is_authenticated:
        return False
    if not user.has_perm('operations.view_supportingdocument'):
        return False
    if not user.has_perm('operations.view_project'):
        return False
    project_id = getattr(
        getattr(getattr(document, 'expense', None), 'allocation', None),
        'project_id',
        None,
    )
    return project_id is not None
