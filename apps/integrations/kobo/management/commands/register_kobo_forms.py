from django.core.management.base import BaseCommand

from apps.integrations.kobo.services import sync_registered_forms


class Command(BaseCommand):
    help = "Create or update the versioned Kobo form registry."

    def handle(self, *args, **options):
        # PRE: Django is configured and Kobo migrations are applied.
        # POST: registered forms are synchronized and their count is printed.
        synchronized_count = sync_registered_forms()
        self.stdout.write(
            self.style.SUCCESS(
                f"Synchronized {synchronized_count} Kobo form definitions."
            )
        )
