from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.integrations.kobo.client import KoboApiClient
from apps.integrations.kobo.errors import KoboIntegrationError, KoboPayloadError
from apps.integrations.kobo.models import (
    KoboAsset,
    KoboProcessingEvent,
    KoboSubmission,
)
from apps.integrations.kobo.processors import process_submission
from apps.integrations.kobo.services import (
    assign_normalized_submission_to_direct_project,
    receive_webhook_submission,
)


def _is_retryable_normalization_failure(submission: KoboSubmission) -> bool:
    """
    PRE: submission is an existing Kobo staging record.
    POST: returns True for one prior invalid-payload normalization failure only,
    preventing reconciliation from retrying permanent failures indefinitely.
    """
    return (
        submission.status == KoboSubmission.Status.VALIDATION_FAILED
        and submission.error_code == "invalid_payload"
        and isinstance(submission.raw_payload, dict)
        and bool(submission.raw_payload)
        and submission.processing_events.filter(
            stage="normalization",
            code="invalid_payload",
        ).count()
        == 1
    )


class Command(BaseCommand):
    help = "Reconcile active Kobo assets without importing submissions."

    def add_arguments(self, parser):
        parser.add_argument("--asset-uid")
        parser.add_argument("--limit", type=int, default=100)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        # PRE: Kobo is enabled, credentials exist, and limit is positive.
        # POST: retries eligible local payloads independently, then stages/processes
        # missing remote submissions without importing them.
        if not settings.KOBO_ENABLED:
            raise CommandError("Kobo integration is disabled.")
        if options["limit"] <= 0:
            raise CommandError("--limit must be positive.")
        client = KoboApiClient(
            base_url=settings.KOBO_BASE_URL,
            api_token=settings.KOBO_API_TOKEN,
            timeout_seconds=settings.KOBO_REQUEST_TIMEOUT_SECONDS,
        )
        assets = KoboAsset.objects.filter(is_active=True).select_related("form_definition")
        if options["asset_uid"]:
            assets = assets.filter(asset_uid=options["asset_uid"])
        created = existing = failed_assets = 0
        local_reprocessed = local_failed = local_would_reprocess = 0
        local_submissions = KoboSubmission.objects.filter(
            asset__in=assets,
            asset__is_active=True,
            form_definition__is_active=True,
            project__isnull=True,
            status__in=(
                KoboSubmission.Status.VALIDATION_FAILED,
                KoboSubmission.Status.READY_FOR_REVIEW,
            ),
        ).select_related("asset", "form_definition")
        for submission in local_submissions:
            needs_normalization = submission.status == KoboSubmission.Status.VALIDATION_FAILED
            if needs_normalization and not _is_retryable_normalization_failure(submission):
                continue
            if options["dry_run"]:
                local_would_reprocess += 1
                continue
            if needs_normalization:
                outcome = process_submission(
                    submission,
                    default_timezone=timezone.get_current_timezone(),
                )
                if outcome.final_status != KoboSubmission.Status.READY_FOR_REVIEW:
                    local_failed += 1
                    KoboProcessingEvent.objects.create(
                        submission=submission,
                        stage="reconciliation",
                        level=KoboProcessingEvent.Level.WARNING,
                        code="local_failed",
                        message="Local Kobo submission could not be reprocessed.",
                    )
                    continue
            if not assign_normalized_submission_to_direct_project(submission):
                local_failed += 1
                KoboProcessingEvent.objects.create(
                    submission=submission,
                    stage="reconciliation",
                    level=KoboProcessingEvent.Level.WARNING,
                    code="local_failed",
                    message="Local Kobo submission could not resolve its project.",
                )
            else:
                local_reprocessed += 1
                KoboProcessingEvent.objects.create(
                    submission=submission,
                    stage="reconciliation",
                    level=KoboProcessingEvent.Level.INFO,
                    code="local_reprocessed",
                    message="Local Kobo submission reprocessed from stored payload.",
                )
        for asset in assets:
            try:
                payloads = client.get_submissions(asset.asset_uid, limit=options["limit"])
                for payload in payloads:
                    if options["dry_run"]:
                        external_id = payload.get("_uuid") if isinstance(payload, dict) else None
                        exists = isinstance(external_id, str) and KoboSubmission.objects.filter(
                            form_definition=asset.form_definition, external_id=external_id
                        ).exists()
                        existing += int(exists)
                        created += int(not exists)
                        continue
                    submission, was_created = receive_webhook_submission(
                        asset=asset, raw_payload=payload
                    )
                    created += int(was_created)
                    existing += int(not was_created)
                    if was_created or _is_retryable_normalization_failure(submission):
                        outcome = process_submission(
                            submission,
                            default_timezone=timezone.get_current_timezone(),
                        )
                        if outcome.final_status == KoboSubmission.Status.READY_FOR_REVIEW:
                            assign_normalized_submission_to_direct_project(submission)
            except (KoboIntegrationError, KoboPayloadError):
                failed_assets += 1
        self.stdout.write(
            self.style.SUCCESS(
                "local_reprocessed={local_reprocessed} local_failed={local_failed} "
                "local_would_reprocess={local_would_reprocess} created={created} "
                "existing={existing} failed_assets={failed_assets} "
                "dry_run={dry_run}".format(
                    local_reprocessed=local_reprocessed,
                    local_failed=local_failed,
                    local_would_reprocess=local_would_reprocess,
                    created=created,
                    existing=existing,
                    failed_assets=failed_assets,
                    dry_run=options["dry_run"],
                )
            )
        )
