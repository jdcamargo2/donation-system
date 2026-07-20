from django.conf import settings
from django.core.management.base import BaseCommand

from apps.integrations.kobo.client import KoboApiClient
from apps.integrations.kobo.models import KoboAsset
from apps.integrations.kobo.services import sync_asset_submissions


class Command(BaseCommand):
    help = "Synchronize one configured Kobo asset through the incremental service."

    def add_arguments(self, parser):
        parser.add_argument("--asset-uid", required=True)
        parser.add_argument("--full", action="store_true")
        parser.add_argument("--max-pages", type=int)

    def handle(self, *args, **options):
        # PRE: the selected local asset is active and supported.
        # POST: delegates exclusively to the hardened incremental service.
        if options["max_pages"] is not None and options["max_pages"] <= 0:
            from django.core.management.base import CommandError
            raise CommandError("--max-pages must be a positive integer.")
        try:
            asset = KoboAsset.objects.get(asset_uid=options["asset_uid"], is_active=True)
        except KoboAsset.DoesNotExist:
            from django.core.management.base import CommandError
            raise CommandError("Active supported Kobo asset was not found.")
        client = KoboApiClient(
            base_url=settings.KOBO_BASE_URL,
            api_token=settings.KOBO_API_TOKEN,
            timeout_seconds=settings.KOBO_REQUEST_TIMEOUT_SECONDS,
        )
        result = sync_asset_submissions(asset=asset, client=client, full=options["full"], max_pages=options["max_pages"])
        self.stdout.write(" ".join((f"asset={asset.asset_uid}", f"mode={result.mode}", f"status={result.status}", f"pages_fetched={result.pages_fetched}", f"created={result.created}", f"updated={result.updated}", f"unchanged={result.unchanged}", f"remote_updates_detected={result.remote_updates_detected}", f"failed={result.failed}", f"partial={result.partial}", f"cursor_advanced={result.cursor_after is not None and result.cursor_after != result.cursor_before}", f"watermark_before={result.watermark_before or ''}", f"watermark_after={result.watermark_after or ''}")))
        if result.status == "FAILED":
            raise SystemExit(1)
        if result.status in ("PARTIAL", "SYNC_ALREADY_RUNNING"):
            raise SystemExit(2)
