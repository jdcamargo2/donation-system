from django.db import transaction

from apps.integrations.kobo.services.common import ProcessingBatchResult, ReviewResult
from apps.integrations.kobo.errors import KoboConfigurationError, KoboPayloadError
from apps.integrations.kobo.models import KoboProcessingEvent, KoboSubmission
from apps.integrations.kobo.processors import PROCESSABLE_STATUSES, process_submission


def process_pending_submissions(
    *,
    limit: int = 100,
    default_timezone,
) -> ProcessingBatchResult:
    """
    PRE: limit is positive and default_timezone is supplied by the caller.
    POST: processes oldest retryable submissions independently up to limit and
    returns aggregate, non-sensitive counts.
    """
    if limit <= 0:
        raise KoboConfigurationError("Kobo processing limit must be positive.")

    submissions = list(
        KoboSubmission.objects.filter(status__in=PROCESSABLE_STATUSES)
        .order_by("received_at", "pk")[:limit]
    )
    processed_count = 0
    ready_count = 0
    validation_failed_count = 0
    processing_failed_count = 0
    skipped_count = 0

    for submission in submissions:
        try:
            outcome = process_submission(
                submission,
                default_timezone=default_timezone,
            )
        except Exception:
            processing_failed_count += 1
            continue
        processed_count += int(outcome.processed)
        skipped_count += int(not outcome.processed)
        ready_count += int(
            outcome.final_status == KoboSubmission.Status.READY_FOR_REVIEW
        )
        validation_failed_count += int(
            outcome.final_status == KoboSubmission.Status.VALIDATION_FAILED
        )
        processing_failed_count += int(
            outcome.final_status == KoboSubmission.Status.PROCESSING_FAILED
        )

    return ProcessingBatchResult(
        selected_count=len(submissions),
        processed_count=processed_count,
        ready_count=ready_count,
        validation_failed_count=validation_failed_count,
        processing_failed_count=processing_failed_count,
        skipped_count=skipped_count,
    )


def review_submission(
    submission: KoboSubmission,
    *,
    decision: str,
    reason: str,
    reviewed_by,
) -> ReviewResult:
    """
    PRE: submission is ready, decision is valid, reviewer is authenticated, and
    rejection includes a reason.
    POST: atomically records the terminal review state and event without payload,
    operations, or publication changes, and returns an explicit result.
    """
    valid_decisions = {
        KoboSubmission.Status.APPROVED_FOR_IMPORT,
        KoboSubmission.Status.REJECTED,
    }
    if decision not in valid_decisions:
        raise KoboPayloadError("Review decision is invalid.")
    if not getattr(reviewed_by, "is_authenticated", False):
        raise KoboConfigurationError("An authenticated reviewer is required.")
    cleaned_reason = reason.strip()
    if decision == KoboSubmission.Status.REJECTED and not cleaned_reason:
        raise KoboPayloadError("A rejection reason is required.")

    event_message = cleaned_reason or "Submission approved for import."
    with transaction.atomic():
        locked_submission = KoboSubmission.objects.select_for_update().get(
            pk=submission.pk
        )
        if locked_submission.status != KoboSubmission.Status.READY_FOR_REVIEW:
            raise KoboPayloadError("Submission is not ready for review.")
        previous_status = locked_submission.status
        locked_submission.status = decision
        locked_submission.save(update_fields=("status",))
        KoboProcessingEvent.objects.create(
            submission=locked_submission,
            stage="review",
            level=KoboProcessingEvent.Level.INFO,
            code=decision,
            message=event_message,
        )
    submission.status = decision
    return ReviewResult(
        submission_id=submission.pk,
        previous_status=previous_status,
        final_status=submission.status,
        reviewed_by_id=reviewed_by.pk,
    )
