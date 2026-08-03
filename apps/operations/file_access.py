"""Protected persisted-file preview and download helpers.

PRE: callers authorize the parent object and model permission before invoking
     response builders.
POST: streams or signed-redirects only authorized storage objects with hardened
      headers; never exposes absolute paths or trusts upload MIME for inline
      safety. Storage backends without .path are supported.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.urls import reverse

from apps.operations.private_files import (
    DISPOSITION_ATTACHMENT,
    DISPOSITION_INLINE,
    PREVIEWABLE_EXTENSION_MIME_TYPES,
    can_preview_persisted_file,
    deliver_private_file,
    get_safe_persisted_file_preview_type,
    sanitize_download_filename,
)

# Re-export for existing imports.
__all__ = [
    'DISPOSITION_ATTACHMENT',
    'DISPOSITION_INLINE',
    'PREVIEWABLE_EXTENSION_MIME_TYPES',
    'ProtectedFileActions',
    'build_protected_file_actions',
    'can_preview_persisted_file',
    'get_safe_persisted_file_preview_type',
    'protected_file_response',
    'sanitize_download_filename',
    'user_can_access_project_supporting_document',
]

_FALLBACK_FILENAME = 'documento'


def protected_file_response(
    file_field,
    *,
    disposition: str,
    missing_message,
    request=None,
):
    """
    PRE: caller authorized the parent object; disposition is inline|attachment;
         missing_message is a safe user-facing string.
    POST: delivers via deliver_private_file (stream or signed_redirect per
          SIGEDON_PRIVATE_FILE_DELIVERY). Inline refuses non-whitelist types.
    """
    return deliver_private_file(
        request,
        file_field,
        disposition=disposition,
        missing_message=missing_message,
    )


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
    file_name = sanitize_download_filename(
        file_field, fallback=file_label or _FALLBACK_FILENAME
    )
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
