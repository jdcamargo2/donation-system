import logging
import re
import uuid
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone

from apps.integrations.kobo.client import KoboApiClient
from apps.integrations.kobo.errors import (
    KoboAttachmentError,
)
from apps.integrations.kobo.models import KoboAttachment, KoboSubmission

logger = logging.getLogger("sigedon.kobo.processing")

ALLOWED_MIME_TYPES = {
    "image/jpeg": ("jpg", b"\xff\xd8\xff"),
    "image/png": ("png", b"\x89PNG\r\n\x1a\n"),
}
PROCESSABLE_ATTACHMENT_STATUSES = (
    KoboAttachment.Status.PENDING,
    KoboAttachment.Status.FAILED,
    KoboAttachment.Status.PROCESSING,
)
_PROCESSING_CLEAR_FIELDS = (
    "processing_started_at",
    "processing_token",
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


@dataclass(frozen=True)
class AttachmentClaim:
    attachment_id: int
    previous_status: str
    processing_token: UUID
    source_url: str
    content_type: str
    privacy_level: str
    external_id: str


def _normalized_mime_type(value: str) -> str:
    # PRE: value is MIME metadata from staging or an HTTP response.
    # POST: returns its normalized media type without parameters.
    return value.partition(";")[0].strip().lower()


def _processing_timeout() -> timedelta:
    # PRE: settings expose a non-negative timeout in seconds.
    # POST: returns the timedelta used to decide claim expiry.
    seconds = max(0, int(settings.KOBO_ATTACHMENT_PROCESSING_TIMEOUT_SECONDS))
    return timedelta(seconds=seconds)


def is_processing_claim_expired(attachment: KoboAttachment, *, now=None) -> bool:
    """
    PRE: attachment is a PROCESSING row or is being evaluated as one.
    POST: True when the reservation has no start time or has exceeded the timeout.
    """
    started_at = attachment.processing_started_at
    if started_at is None:
        return True
    reference = now if now is not None else timezone.now()
    return started_at + _processing_timeout() <= reference


def _is_claimable(attachment: KoboAttachment, *, now=None) -> bool:
    # PRE: attachment row is locked for the claim decision.
    # POST: True only for PENDING, FAILED, or expired PROCESSING reservations.
    if attachment.status in {
        KoboAttachment.Status.PENDING,
        KoboAttachment.Status.FAILED,
    }:
        return True
    if attachment.status == KoboAttachment.Status.PROCESSING:
        return is_processing_claim_expired(attachment, now=now)
    return False


def claim_attachment_processing(attachment_id: int) -> AttachmentClaim | None:
    """
    PRE: attachment_id refers to an existing KoboAttachment row.
    POST: returns a short-lived claim snapshot when the row was reserved, else None.
    Never holds the row lock across network or storage I/O.
    """
    with transaction.atomic():
        try:
            attachment = (
                KoboAttachment.objects.select_for_update()
                .get(pk=attachment_id)
            )
        except KoboAttachment.DoesNotExist:
            return None
        now = timezone.now()
        if not _is_claimable(attachment, now=now):
            return None
        previous_status = attachment.status
        token = uuid.uuid4()
        attachment.status = KoboAttachment.Status.PROCESSING
        attachment.processing_started_at = now
        attachment.processing_token = token
        attachment.error_message = ""
        attachment.save(
            update_fields=(
                "status",
                "processing_started_at",
                "processing_token",
                "error_message",
                "updated_at",
            )
        )
        return AttachmentClaim(
            attachment_id=attachment.pk,
            previous_status=previous_status,
            processing_token=token,
            source_url=attachment.source_url,
            content_type=attachment.content_type,
            privacy_level=attachment.privacy_level,
            external_id=attachment.external_id,
        )


def _owns_processing_claim(attachment: KoboAttachment, token: UUID) -> bool:
    return (
        attachment.status == KoboAttachment.Status.PROCESSING
        and attachment.processing_token == token
    )


def _clear_processing_metadata(attachment: KoboAttachment) -> None:
    attachment.processing_started_at = None
    attachment.processing_token = None


def _compensate_stored_file(storage, stored_name: str | None) -> None:
    # PRE: stored_name is the exclusive object written by this attempt, or None.
    # POST: best-effort deletes that object without replacing a caller exception.
    if not stored_name:
        return
    try:
        storage.delete(stored_name)
    except Exception:
        return


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
    *,
    attempt_token: UUID | None = None,
) -> str:
    """
    PRE: attachment exists and detected_extension comes from validated MIME.
    POST: returns a stable basename without remote paths or separators. When
    attempt_token is provided, the name belongs exclusively to that attempt.
    """
    normalized_extension = detected_extension.lower().lstrip(".")
    if normalized_extension not in {details[0] for details in ALLOWED_MIME_TYPES.values()}:
        raise KoboAttachmentError("Attachment extension is not allowed.")
    identifier = attachment.external_id or f"attachment-{attachment.pk}"
    safe_identifier = re.sub(r"[^A-Za-z0-9_-]+", "-", identifier).strip("-_")
    if not safe_identifier:
        safe_identifier = f"attachment-{attachment.pk}"
    if attempt_token is not None:
        return (
            f"kobo-{safe_identifier}-{attempt_token.hex}.{normalized_extension}"
        )
    return f"kobo-{safe_identifier}.{normalized_extension}"


def _skipped_outcome(
    attachment: KoboAttachment,
    *,
    previous_status: str,
) -> AttachmentOutcome:
    return AttachmentOutcome(
        attachment_id=attachment.pk,
        previous_status=previous_status,
        final_status=attachment.status,
        processed=False,
        error_message=attachment.error_message,
    )


def _finalize_claimed_failure(
    claim: AttachmentClaim,
    *,
    final_status: str,
    error_message: str,
) -> AttachmentOutcome:
    # PRE: claim was obtained by this worker; final_status is INVALID or FAILED.
    # POST: persists failure only while the claim token still owns PROCESSING.
    with transaction.atomic():
        attachment = (
            KoboAttachment.objects.select_for_update()
            .get(pk=claim.attachment_id)
        )
        if not _owns_processing_claim(attachment, claim.processing_token):
            return _skipped_outcome(
                attachment,
                previous_status=claim.previous_status,
            )
        attachment.status = final_status
        attachment.error_message = error_message
        _clear_processing_metadata(attachment)
        attachment.save(
            update_fields=(
                "status",
                "error_message",
                *_PROCESSING_CLEAR_FIELDS,
                "updated_at",
            )
        )
    return AttachmentOutcome(
        attachment_id=claim.attachment_id,
        previous_status=claim.previous_status,
        final_status=final_status,
        processed=True,
        error_message=error_message,
    )


def _confirm_download_success(
    claim: AttachmentClaim,
    *,
    stored_name: str,
    size_bytes: int,
    content_type: str,
) -> AttachmentOutcome | None:
    # PRE: storage already saved stored_name for this claim outside any atomic block.
    # POST: confirms DOWNLOADED only if the claim token still owns PROCESSING.
    with transaction.atomic():
        attachment = (
            KoboAttachment.objects.select_for_update()
            .get(pk=claim.attachment_id)
        )
        if not _owns_processing_claim(attachment, claim.processing_token):
            return None
        attachment.file.name = stored_name
        attachment.status = KoboAttachment.Status.DOWNLOADED
        attachment.error_message = ""
        attachment.size_bytes = size_bytes
        attachment.content_type = content_type
        _clear_processing_metadata(attachment)
        attachment.save(
            update_fields=(
                "file",
                "status",
                "error_message",
                "size_bytes",
                "content_type",
                *_PROCESSING_CLEAR_FIELDS,
                "updated_at",
            )
        )
    return AttachmentOutcome(
        attachment_id=claim.attachment_id,
        previous_status=claim.previous_status,
        final_status=KoboAttachment.Status.DOWNLOADED,
        processed=True,
        error_message="",
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
    POST: claims work transactionally, downloads and stores outside any open
    transaction, then confirms or fails with token checks and orphan compensation.
    """
    previous_status = attachment.status
    claim = claim_attachment_processing(attachment.pk)
    if claim is None:
        attachment.refresh_from_db()
        return _skipped_outcome(attachment, previous_status=previous_status)

    stored_name = None
    try:
        if max_bytes <= 0:
            return _finalize_claimed_failure(
                claim,
                final_status=KoboAttachment.Status.INVALID,
                error_message="Attachment size limit is invalid.",
            )

        unlocked = KoboAttachment.objects.get(pk=claim.attachment_id)
        validate_attachment_metadata(unlocked)
        downloaded = client.download_attachment(claim.source_url)
        if downloaded.content_length is not None and downloaded.content_length > max_bytes:
            raise KoboAttachmentError("Attachment exceeds the allowed size.")
        if len(downloaded.content) > max_bytes:
            raise KoboAttachmentError("Attachment exceeds the allowed size.")

        detected_mime = _normalized_mime_type(downloaded.content_type)
        if detected_mime not in ALLOWED_MIME_TYPES:
            raise KoboAttachmentError("Attachment detected MIME type is not allowed.")
        declared_mime = _normalized_mime_type(claim.content_type)
        if detected_mime != declared_mime:
            raise KoboAttachmentError("Attachment MIME type does not match metadata.")
        detected_extension, signature = ALLOWED_MIME_TYPES[detected_mime]
        if not downloaded.content.startswith(signature):
            raise KoboAttachmentError("Attachment binary signature is invalid.")

        safe_filename = build_safe_filename(
            unlocked,
            detected_extension,
            attempt_token=claim.processing_token,
        )
        stored_name = storage.save(safe_filename, ContentFile(downloaded.content))
    except KoboAttachmentError:
        return _finalize_claimed_failure(
            claim,
            final_status=KoboAttachment.Status.INVALID,
            error_message="Attachment content or metadata is invalid.",
        )
    except Exception:
        logger.exception(
            "Kobo attachment processing failed attachment_id=%s",
            claim.attachment_id,
        )
        return _finalize_claimed_failure(
            claim,
            final_status=KoboAttachment.Status.FAILED,
            error_message="Attachment download or storage failed.",
        )

    try:
        outcome = _confirm_download_success(
            claim,
            stored_name=stored_name,
            size_bytes=len(downloaded.content),
            content_type=detected_mime,
        )
    except Exception:
        _compensate_stored_file(storage, stored_name)
        raise

    if outcome is None:
        _compensate_stored_file(storage, stored_name)
        attachment.refresh_from_db()
        return _skipped_outcome(attachment, previous_status=claim.previous_status)
    return outcome


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
    without changing the submission status. Active PROCESSING claims are skipped
    without counting as failures; expired PROCESSING claims may be recovered.
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
