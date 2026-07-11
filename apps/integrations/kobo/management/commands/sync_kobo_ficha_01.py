from django.conf import settings
from django.core.management.base import BaseCommand

from apps.integrations.kobo.client import KoboApiClient
from apps.integrations.kobo.services import sync_ficha_01_submissions


class Command(BaseCommand):
    help = "Synchronize Kobo Ficha 1 submissions into staging."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=100)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        # PRE: Kobo settings and the registered Ficha 1 definition are available.
        # POST: synchronizes only Ficha 1 and prints non-sensitive aggregate counts.
        client = KoboApiClient(
            base_url=settings.KOBO_BASE_URL,
            api_token=settings.KOBO_API_TOKEN,
            timeout_seconds=settings.KOBO_REQUEST_TIMEOUT_SECONDS,
        )
        result = sync_ficha_01_submissions(
            client,
            settings.KOBO_FICHA_01_ASSET_UID,
            limit=options["limit"],
            dry_run=options["dry_run"],
        )
        if options["dry_run"]:
            output_template = (
                "fetched={fetched} would_create={created} "
                "would_exist={existing} failed={failed}"
            )
        else:
            output_template = (
                "fetched={fetched} created={created} "
                "existing={existing} failed={failed}"
            )
        self.stdout.write(
            output_template.format(
                fetched=result.fetched_count,
                created=result.created_count,
                existing=result.existing_count,
                failed=result.failed_count,
            )
        )
