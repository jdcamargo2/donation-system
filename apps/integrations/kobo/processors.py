from dataclasses import dataclass
from datetime import tzinfo

from django.db import transaction
from django.utils import timezone

from apps.integrations.kobo.attachments import (
    AttachmentBatchResult,
    process_pending_attachments,
)
from apps.integrations.kobo.client import KoboApiClient
from apps.integrations.kobo.errors import KoboPayloadError
from apps.integrations.kobo.models import (
    KoboAttachment,
    KoboProcessingEvent,
    KoboSubmission,
)
from apps.integrations.kobo.normalizers import normalize_submission


PROCESSABLE_STATUSES = (
    KoboSubmission.Status.RECEIVED,
    KoboSubmission.Status.VALIDATION_FAILED,
    KoboSubmission.Status.PROCESSING_FAILED,
)


@dataclass(frozen=True)
class ProcessingOutcome:
    submission_id: int
    previous_status: str
    final_status: str
    processed: bool
    attachment_count: int
    error_code: str
    error_message: str


@dataclass(frozen=True)
class ProcessingAggregateResult:
    selected: int
    processed: int
    ready: int
    validation_failed: int
    processing_failed: int
    skipped: int
    attachments_selected: int
    attachments_downloaded: int
    attachments_invalid: int
    attachments_failed: int
    attachments_skipped: int


def _failure_outcome(
    submission: KoboSubmission,
    *,
    previous_status: str,
    final_status: str,
    error_code: str,
    error_message: str,
    stage: str,
) -> ProcessingOutcome:
    # PRE: submission normalization or processing failed with safe metadata.
    # POST: persists the failure and event atomically, then returns its outcome.
    with transaction.atomic():
        submission.status = final_status
        submission.error_code = error_code
        submission.error_message = error_message
        submission.save(update_fields=("status", "error_code", "error_message"))
        KoboProcessingEvent.objects.create(
            submission=submission,
            stage=stage,
            level=KoboProcessingEvent.Level.ERROR,
            code=error_code,
            message=error_message,
        )
    return ProcessingOutcome(
        submission_id=submission.pk,
        previous_status=previous_status,
        final_status=final_status,
        processed=True,
        attachment_count=submission.attachments.count(),
        error_code=error_code,
        error_message=error_message,
    )


def process_submission(
    submission: KoboSubmission,
    *,
    default_timezone: tzinfo,
) -> ProcessingOutcome:
    """
    PRE: submission exists with form_definition, dict raw_payload and an injected
    default_timezone.
    POST: normalizes retryable staging atomically, records a safe event, creates
    pending descriptors without downloading them, and returns an outcome.
    """
    previous_status = submission.status
    if previous_status not in PROCESSABLE_STATUSES:
        return ProcessingOutcome(
            submission_id=submission.pk,
            previous_status=previous_status,
            final_status=previous_status,
            processed=False,
            attachment_count=submission.attachments.count(),
            error_code=submission.error_code,
            error_message=submission.error_message,
        )

    try:
        normalized = normalize_submission(
            submission.raw_payload,
            form_id=submission.form_definition.form_id,
            form_version=submission.form_definition.version,
            default_timezone=default_timezone,
        )
    except KoboPayloadError:
        return _failure_outcome(
            submission,
            previous_status=previous_status,
            final_status=KoboSubmission.Status.VALIDATION_FAILED,
            error_code="invalid_payload",
            error_message="Submission payload failed normalization.",
            stage="normalization",
        )
    except Exception:
        return _failure_outcome(
            submission,
            previous_status=previous_status,
            final_status=KoboSubmission.Status.PROCESSING_FAILED,
            error_code="processing_error",
            error_message="Submission processing failed unexpectedly.",
            stage="processing",
        )

    try:
        with transaction.atomic():
            submission.normalized_payload = normalized.normalized_payload
            submission.pastoral_zone = normalized.pastoral_zone
            submission.parish = normalized.parish
            submission.primary_community = normalized.primary_community or ""
            submission.assessment_date = normalized.assessment_date
            submission.normalized_at = timezone.now()
            submission.status = KoboSubmission.Status.READY_FOR_REVIEW
            submission.error_code = ""
            submission.error_message = ""
            submission.save(
                update_fields=(
                    "normalized_payload",
                    "pastoral_zone",
                    "parish",
                    "primary_community",
                    "assessment_date",
                    "normalized_at",
                    "status",
                    "error_code",
                    "error_message",
                )
            )
            for attachment in normalized.attachments:
                KoboAttachment.objects.get_or_create(
                    submission=submission,
                    field_name=attachment.field_name,
                    source_url=attachment.source_url,
                    defaults={
                        "original_filename": attachment.filename or "",
                        "content_type": attachment.content_type or "",
                        "privacy_level": attachment.privacy_level,
                        "status": KoboAttachment.Status.PENDING,
                    },
                )
            KoboProcessingEvent.objects.create(
                submission=submission,
                stage="normalization",
                level=KoboProcessingEvent.Level.INFO,
                code="normalized",
                message="Submission normalized and ready for review.",
            )
    except Exception:
        return _failure_outcome(
            submission,
            previous_status=previous_status,
            final_status=KoboSubmission.Status.PROCESSING_FAILED,
            error_code="processing_error",
            error_message="Submission processing failed unexpectedly.",
            stage="processing",
        )

    return ProcessingOutcome(
        submission_id=submission.pk,
        previous_status=previous_status,
        final_status=submission.status,
        processed=True,
        attachment_count=len(normalized.attachments),
        error_code="",
        error_message="",
    )


def process_submission_attachments(
    submission: KoboSubmission,
    *,
    client: KoboApiClient,
    storage,
    max_bytes: int,
) -> AttachmentBatchResult:
    """
    PRE: submission exists and attachment dependencies are injected explicitly.
    POST: processes its attachments independently without normalizing, changing
    submission status, or creating normalization events.
    """
    return process_pending_attachments(
        submission,
        client=client,
        storage=storage,
        max_bytes=max_bytes,
    )
