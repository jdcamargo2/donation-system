"""Verify Render native-Python runtime configuration (network-free).

PRE: Django settings loaded for the candidate Render runtime environment.
POST: exits 0 when all Render configuration guarantees pass; otherwise
      CommandError. Never prints secret values. Never opens network sockets.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from core.render_configuration import (
    configuration_is_healthy,
    verify_render_configuration,
)


class Command(BaseCommand):
    help = (
        'Valida la configuración de runtime Render de SIGEDON sin red ni '
        'mutaciones. No imprime secretos.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--allow-development-hosts',
            action='store_true',
            help=(
                'Permite localhost/127.0.0.1 en ALLOWED_HOSTS '
                '(solo harnesses locales; no usar en staging/producción).'
            ),
        )

    def handle(self, *args, **options):
        findings = verify_render_configuration(
            allow_development_hosts=bool(options.get('allow_development_hosts')),
        )
        failures = [item for item in findings if not item.ok]
        for item in findings:
            style = self.style.SUCCESS if item.ok else self.style.ERROR
            self.stdout.write(style(f'{item.code}: {item.message}'))

        if not configuration_is_healthy(findings):
            raise CommandError(
                f'Render configuration failed ({len(failures)} guarantee(s)).'
            )

        self.stdout.write(self.style.SUCCESS('verify_render_configuration=ok'))
