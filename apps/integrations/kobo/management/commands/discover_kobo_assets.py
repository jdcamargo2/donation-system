from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.integrations.kobo.client import KoboApiClient, KOBO_MAX_ASSET_PAGES
from apps.integrations.kobo.services import discover_assets


class Command(BaseCommand):
    help = "Discover available Kobo assets without configuring integrations."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=100)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        # PRE: command settings and options describe an enabled Kobo connection.
        # POST: prints aggregate discovery counts without remote metadata or secrets.
        if not settings.KOBO_ENABLED:
            raise CommandError("Kobo integration is disabled.")
        if not settings.KOBO_BASE_URL:
            raise CommandError("KOBO_BASE_URL is required.")
        if not settings.KOBO_API_TOKEN:
            raise CommandError("KOBO_API_TOKEN is required.")
        client = KoboApiClient(
            base_url=settings.KOBO_BASE_URL,
            api_token=settings.KOBO_API_TOKEN,
            timeout_seconds=settings.KOBO_REQUEST_TIMEOUT_SECONDS,
            max_asset_pages=getattr(
                settings,
                "KOBO_MAX_ASSET_PAGES",
                KOBO_MAX_ASSET_PAGES,
            ),
        )
        result = discover_assets(
            client,
            limit=options["limit"],
            dry_run=options["dry_run"],
        )
        if options["dry_run"]:
            self.stdout.write(
                "fetched={fetched} would_create={created} "
                "would_update={updated} unchanged={unchanged} failed={failed}".format(
                    fetched=result.fetched_count,
                    created=result.created_count,
                    updated=result.updated_count,
                    unchanged=result.unchanged_count,
                    failed=result.failed_count,
                )
            )
            return
        self.stdout.write(
            "fetched={fetched} created={created} updated={updated} "
            "unchanged={unchanged} unavailable={unavailable} failed={failed}".format(
                fetched=result.fetched_count,
                created=result.created_count,
                updated=result.updated_count,
                unchanged=result.unchanged_count,
                unavailable=result.unavailable_count,
                failed=result.failed_count,
            )
        )
