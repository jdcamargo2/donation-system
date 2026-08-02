import logging

from apps.integrations.kobo.services.common import ProcessingBatchResult
from apps.integrations.kobo.errors import KoboConfigurationError
from apps.integrations.kobo.models import KoboSubmission
from apps.integrations.kobo.processors import PROCESSABLE_STATUSES, process_submission
from apps.integrations.kobo.services.territorial_routing import route_normalized_submission

logger = logging.getLogger("sigedon.kobo.processing")


def process_pending_submissions(
    *,
    limit: int = 100,
    default_timezone,
) -> ProcessingBatchResult:
    """
    PRE: limit is positive and default_timezone is supplied by the caller.
    POST: processes oldest retryable submissions independently up to limit,
    auto-imports eligible ones, and returns aggregate, non-sensitive counts.
    """
    from apps.integrations.kobo.services.automation import auto_import_if_eligible

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
            logger.exception(
                "Kobo batch processing unexpected failure submission_id=%s",
                submission.pk,
            )
            processing_failed_count += 1
            continue
        processed_count += int(outcome.processed)
        if outcome.final_status == KoboSubmission.Status.READY_FOR_REVIEW:
            route_normalized_submission(submission)
            auto_import_if_eligible(submission)
            submission.refresh_from_db()
        skipped_count += int(not outcome.processed)
        ready_count += int(
            submission.status
            in {
                KoboSubmission.Status.READY_FOR_REVIEW,
                KoboSubmission.Status.APPROVED_FOR_IMPORT,
                KoboSubmission.Status.IMPORTED,
            }
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
