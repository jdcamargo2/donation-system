from django.conf import settings
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q
from django.utils import timezone

from apps.integrations.kobo.attachments import (
    PROCESSABLE_ATTACHMENT_STATUSES,
)
from apps.integrations.kobo.client import build_kobo_api_client
from apps.integrations.kobo.models import KoboSubmission
from apps.integrations.kobo.processors import (
    PROCESSABLE_STATUSES,
    ProcessingAggregateResult,
    process_submission,
    process_submission_attachments,
)
from apps.integrations.kobo.services import (
    process_pending_submissions,
    route_normalized_submission,
)


def _aggregate_outcomes(
    outcomes,
    attachment_results,
) -> ProcessingAggregateResult:
    # PRE: outcomes and attachment_results belong to the same command run.
    # POST: returns separated submission and attachment counters.
    return ProcessingAggregateResult(
        selected=len(outcomes),
        processed=sum(outcome.processed for outcome in outcomes),
        ready=sum(
            outcome.final_status == KoboSubmission.Status.READY_FOR_REVIEW
            for outcome in outcomes
        ),
        validation_failed=sum(
            outcome.final_status == KoboSubmission.Status.VALIDATION_FAILED
            for outcome in outcomes
        ),
        processing_failed=sum(
            outcome.final_status == KoboSubmission.Status.PROCESSING_FAILED
            for outcome in outcomes
        ),
        skipped=sum(not outcome.processed for outcome in outcomes),
        attachments_selected=sum(result.selected for result in attachment_results),
        attachments_downloaded=sum(
            result.downloaded for result in attachment_results
        ),
        attachments_invalid=sum(result.invalid for result in attachment_results),
        attachments_failed=sum(result.failed for result in attachment_results),
        attachments_skipped=sum(result.skipped for result in attachment_results),
    )


class Command(BaseCommand):
    help = "Normalize pending Kobo submissions into review-ready staging."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=100)
        parser.add_argument("--submission-id", type=int)
        parser.add_argument("--download-attachments", action="store_true")

    def handle(self, *args, **options):
        # PRE: Kobo migrations are applied and command options are valid.
        # POST: processes the requested scope independently per record, prints
        # aggregate counts, and raises CommandError when failed > 0 so shells
        # receive a non-zero exit without rolling back prior successes.
        default_timezone = timezone.get_current_timezone()
        submission_id = options["submission_id"]
        download_attachments = options["download_attachments"]
        client = None
        if download_attachments:
            client = build_kobo_api_client()
        if submission_id is None:
            if download_attachments:
                if options["limit"] <= 0:
                    raise CommandError("Limit must be positive.")
                submissions = list(
                    KoboSubmission.objects.filter(
                        Q(status__in=PROCESSABLE_STATUSES)
                        | Q(
                            status=KoboSubmission.Status.READY_FOR_REVIEW,
                            attachments__status__in=PROCESSABLE_ATTACHMENT_STATUSES,
                        )
                    )
                    .select_related("form_definition")
                    .distinct()
                    .order_by("received_at", "pk")[: options["limit"]]
                )
                outcomes = []
                attachment_results = []
                for submission in submissions:
                    outcome = process_submission(
                        submission,
                        default_timezone=default_timezone,
                    )
                    outcomes.append(outcome)
                    if outcome.final_status == KoboSubmission.Status.READY_FOR_REVIEW:
                        if outcome.processed:
                            route_normalized_submission(submission)
                        attachment_results.append(
                            process_submission_attachments(
                                submission,
                                client=client,
                                storage=default_storage,
                                max_bytes=settings.KOBO_MAX_ATTACHMENT_BYTES,
                            )
                        )
                result = _aggregate_outcomes(outcomes, attachment_results)
            else:
                batch_result = process_pending_submissions(
                    limit=options["limit"],
                    default_timezone=default_timezone,
                )
                result = ProcessingAggregateResult(
                    selected=batch_result.selected_count,
                    processed=batch_result.processed_count,
                    ready=batch_result.ready_count,
                    validation_failed=batch_result.validation_failed_count,
                    processing_failed=batch_result.processing_failed_count,
                    skipped=batch_result.skipped_count,
                    attachments_selected=0,
                    attachments_downloaded=0,
                    attachments_invalid=0,
                    attachments_failed=0,
                    attachments_skipped=0,
                )
        else:
            try:
                submission = KoboSubmission.objects.select_related(
                    "form_definition"
                ).get(pk=submission_id)
            except KoboSubmission.DoesNotExist as exc:
                raise CommandError("Kobo submission does not exist.") from exc
            outcome = process_submission(
                submission,
                default_timezone=default_timezone,
            )
            if (
                outcome.processed
                and outcome.final_status == KoboSubmission.Status.READY_FOR_REVIEW
            ):
                route_normalized_submission(submission)
            attachment_results = []
            if (
                download_attachments
                and outcome.final_status == KoboSubmission.Status.READY_FOR_REVIEW
            ):
                attachment_results.append(
                    process_submission_attachments(
                        submission,
                        client=client,
                        storage=default_storage,
                        max_bytes=settings.KOBO_MAX_ATTACHMENT_BYTES,
                    )
                )
            result = _aggregate_outcomes([outcome], attachment_results)

        # Operational outcome: per-record failures are persisted independently;
        # successful records already committed. Non-zero exit when any failed.
        failed = (
            result.validation_failed
            + result.processing_failed
            + result.attachments_failed
        )
        succeeded = result.ready
        self.stdout.write(
            "Kobo processing summary: selected={selected} "
            "processed={processed} succeeded={succeeded} failed={failed} "
            "skipped={skipped} ready={ready} "
            "validation_failed={validation_failed} "
            "processing_failed={processing_failed} "
            "attachments_selected={attachments_selected} "
            "attachments_downloaded={attachments_downloaded} "
            "attachments_invalid={attachments_invalid} "
            "attachments_failed={attachments_failed} "
            "attachments_skipped={attachments_skipped}".format(
                selected=result.selected,
                processed=result.processed,
                succeeded=succeeded,
                failed=failed,
                skipped=result.skipped,
                ready=result.ready,
                validation_failed=result.validation_failed,
                processing_failed=result.processing_failed,
                attachments_selected=result.attachments_selected,
                attachments_downloaded=result.attachments_downloaded,
                attachments_invalid=result.attachments_invalid,
                attachments_failed=result.attachments_failed,
                attachments_skipped=result.attachments_skipped,
            )
        )
        if failed > 0:
            raise CommandError(
                f"Processing completed with {failed} error(s)."
            )
