import re
from dataclasses import dataclass

from django.core.files.base import ContentFile

from apps.integrations.kobo.client import KoboApiClient
from apps.integrations.kobo.errors import (
    KoboAttachmentError,
)
from apps.integrations.kobo.models import KoboAttachment, KoboSubmission


ALLOWED_MIME_TYPES = {
    "image/jpeg": ("jpg", b"\xff\xd8\xff"),
    "image/png": ("png", b"\x89PNG\r\n\x1a\n"),
}
PROCESSABLE_ATTACHMENT_STATUSES = (
    KoboAttachment.Status.PENDING,
    KoboAttachment.Status.FAILED,
)


@dataclass(frozen=True)
class AttachmentOutcome:
    attachment_id: int
    previous_status: str
    final_status: str
    processed: bool
    error_message: str


@dataclass(frozen=True)
class AttachmentBatchResult:
    selected: int
    downloaded: int
    invalid: int
    failed: int
    skipped: int


def _normalized_mime_type(value: str) -> str:
    # PRE: value is MIME metadata from staging or an HTTP response.
    # POST: returns its normalized media type without parameters.
    return value.partition(";")[0].strip().lower()


def validate_attachment_metadata(attachment: KoboAttachment) -> None:
    """
    PRE: attachment exists.
    POST: validates source URL, declared MIME and privacy or raises a safe
    KoboAttachmentError without network or persistence.
    """
    if not attachment.source_url or not attachment.source_url.strip():
        raise KoboAttachmentError("Attachment source URL is required.")
    declared_mime = _normalized_mime_type(attachment.content_type)
    if declared_mime not in ALLOWED_MIME_TYPES:
        raise KoboAttachmentError("Attachment declared MIME type is not allowed.")
    valid_privacy_levels = {choice.value for choice in KoboAttachment.PrivacyLevel}
    if attachment.privacy_level not in valid_privacy_levels:
        raise KoboAttachmentError("Attachment privacy level is invalid.")


def build_safe_filename(
    attachment: KoboAttachment,
    detected_extension: str,
) -> str:
    """
    PRE: attachment exists and detected_extension comes from validated MIME.
    POST: returns a stable basename without remote paths or separators.
    """
    normalized_extension = detected_extension.lower().lstrip(".")
    if normalized_extension not in {details[0] for details in ALLOWED_MIME_TYPES.values()}:
        raise KoboAttachmentError("Attachment extension is not allowed.")
    identifier = attachment.external_id or f"attachment-{attachment.pk}"
    safe_identifier = re.sub(r"[^A-Za-z0-9_-]+", "-", identifier).strip("-_")
    if not safe_identifier:
        safe_identifier = f"attachment-{attachment.pk}"
    return f"kobo-{safe_identifier}.{normalized_extension}"


def _save_failure(
    attachment: KoboAttachment,
    *,
    previous_status: str,
    final_status: str,
    error_message: str,
) -> AttachmentOutcome:
    # PRE: attachment processing failed and error_message contains no source data.
    # POST: persists the safe failure and returns its explicit outcome.
    attachment.status = final_status
    attachment.error_message = error_message
    attachment.save(update_fields=("status", "error_message", "updated_at"))
    return AttachmentOutcome(
        attachment_id=attachment.pk,
        previous_status=previous_status,
        final_status=final_status,
        processed=True,
        error_message=error_message,
    )


def download_and_store_attachment(
    attachment: KoboAttachment,
    *,
    client: KoboApiClient,
    storage,
    max_bytes: int,
) -> AttachmentOutcome:
    """
    PRE: attachment exists; client, storage and a positive max_bytes are injected.
    POST: skips terminal states, or safely downloads, validates and privately
    stores content while persisting an explicit downloaded/invalid/failed outcome.
    """
    previous_status = attachment.status
    if previous_status not in PROCESSABLE_ATTACHMENT_STATUSES:
        return AttachmentOutcome(
            attachment_id=attachment.pk,
            previous_status=previous_status,
            final_status=previous_status,
            processed=False,
            error_message=attachment.error_message,
        )
    if max_bytes <= 0:
        return _save_failure(
            attachment,
            previous_status=previous_status,
            final_status=KoboAttachment.Status.INVALID,
            error_message="Attachment size limit is invalid.",
        )

    try:
        validate_attachment_metadata(attachment)
        downloaded = client.download_attachment(attachment.source_url)
        if downloaded.content_length is not None and downloaded.content_length > max_bytes:
            raise KoboAttachmentError("Attachment exceeds the allowed size.")
        if len(downloaded.content) > max_bytes:
            raise KoboAttachmentError("Attachment exceeds the allowed size.")

        detected_mime = _normalized_mime_type(downloaded.content_type)
        if detected_mime not in ALLOWED_MIME_TYPES:
            raise KoboAttachmentError("Attachment detected MIME type is not allowed.")
        declared_mime = _normalized_mime_type(attachment.content_type)
        if detected_mime != declared_mime:
            raise KoboAttachmentError("Attachment MIME type does not match metadata.")
        detected_extension, signature = ALLOWED_MIME_TYPES[detected_mime]
        if not downloaded.content.startswith(signature):
            raise KoboAttachmentError("Attachment binary signature is invalid.")

        safe_filename = build_safe_filename(attachment, detected_extension)
        stored_name = storage.save(safe_filename, ContentFile(downloaded.content))
    except KoboAttachmentError:
        return _save_failure(
            attachment,
            previous_status=previous_status,
            final_status=KoboAttachment.Status.INVALID,
            error_message="Attachment content or metadata is invalid.",
        )
    except Exception:
        return _save_failure(
            attachment,
            previous_status=previous_status,
            final_status=KoboAttachment.Status.FAILED,
            error_message="Attachment download or storage failed.",
        )

    attachment.file.name = stored_name
    attachment.status = KoboAttachment.Status.DOWNLOADED
    attachment.error_message = ""
    attachment.size_bytes = len(downloaded.content)
    attachment.content_type = detected_mime
    attachment.save(
        update_fields=(
            "file",
            "status",
            "error_message",
            "size_bytes",
            "content_type",
            "updated_at",
        )
    )
    return AttachmentOutcome(
        attachment_id=attachment.pk,
        previous_status=previous_status,
        final_status=attachment.status,
        processed=True,
        error_message="",
    )


def process_pending_attachments(
    submission: KoboSubmission,
    *,
    client: KoboApiClient,
    storage,
    max_bytes: int,
) -> AttachmentBatchResult:
    """
    PRE: submission exists and download dependencies are explicitly injected.
    POST: processes each attachment independently and returns aggregate counts
    without changing the submission status.
    """
    attachments = list(submission.attachments.order_by("pk"))
    downloaded = 0
    invalid = 0
    failed = 0
    skipped = 0
    for attachment in attachments:
        outcome = download_and_store_attachment(
            attachment,
            client=client,
            storage=storage,
            max_bytes=max_bytes,
        )
        downloaded += int(
            outcome.processed
            and outcome.final_status == KoboAttachment.Status.DOWNLOADED
        )
        invalid += int(
            outcome.processed and outcome.final_status == KoboAttachment.Status.INVALID
        )
        failed += int(
            outcome.processed and outcome.final_status == KoboAttachment.Status.FAILED
        )
        skipped += int(not outcome.processed)
    return AttachmentBatchResult(
        selected=len(attachments),
        downloaded=downloaded,
        invalid=invalid,
        failed=failed,
        skipped=skipped,
    )
