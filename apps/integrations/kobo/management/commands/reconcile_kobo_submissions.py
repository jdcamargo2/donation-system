from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.integrations.kobo.client import KoboApiClient
from apps.integrations.kobo.errors import KoboIntegrationError, KoboPayloadError
from apps.integrations.kobo.models import KoboAsset, KoboSubmission
from apps.integrations.kobo.processors import process_submission
from apps.integrations.kobo.services import receive_webhook_submission


class Command(BaseCommand):
    help = "Reconcile active Kobo assets without importing submissions."

    def add_arguments(self, parser):
        parser.add_argument("--asset-uid")
        parser.add_argument("--limit", type=int, default=100)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        # PRE: Kobo is enabled, credentials exist, and limit is positive.
        # POST: stages/processes missing remote submissions without importing them.
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
        created = existing = failed = 0
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
                    if was_created:
                        process_submission(
                            submission,
                            default_timezone=timezone.get_current_timezone(),
                        )
            except (KoboIntegrationError, KoboPayloadError):
                failed += 1
        self.stdout.write(
            self.style.SUCCESS(
                f"created={created} existing={existing} failed_assets={failed} dry_run={options['dry_run']}"
            )
        )
