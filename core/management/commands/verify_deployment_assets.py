"""
Verify collected static deployment assets under settings.STATIC_ROOT.

PRE: collectstatic has already run during the release phase when the deployment
     serves files from STATIC_ROOT (proxy or local).
POST: exits 0 when STATIC_ROOT exists and required sentinel assets are present
      and non-empty; otherwise raises CommandError. Never collects or mutates files.
"""

from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


# Application CSS, ILDE brand logos, and vendored core UI libraries expected
# after collectstatic. Paths are relative to STATIC_ROOT / static finders.
REQUIRED_RELATIVE_ASSETS = (
    'web/css/sigedon.css',
    'web/img/logo_ilde.png',
    'web/img/logo_ilde_short.png',
    'vendor/bootstrap/5.3.3/css/bootstrap.min.css',
    'vendor/bootstrap/5.3.3/js/bootstrap.bundle.min.js',
    'vendor/bootstrap-icons/1.11.3/font/bootstrap-icons.min.css',
    'vendor/bootstrap-icons/1.11.3/font/fonts/bootstrap-icons.woff',
    'vendor/bootstrap-icons/1.11.3/font/fonts/bootstrap-icons.woff2',
    'vendor/sweetalert2/11.26.25/sweetalert2.min.css',
    'vendor/sweetalert2/11.26.25/sweetalert2.all.min.js',
)


class Command(BaseCommand):
    help = (
        'Verifica que STATIC_ROOT exista y contenga activos locales canónicos '
        'tras collectstatic (CSS de aplicación, logos ILDE y vendor UI: '
        'Bootstrap, Bootstrap Icons, SweetAlert2). No ejecuta collectstatic.'
    )

    def handle(self, *args, **options):
        """
        PRE: Django settings expose STATIC_ROOT.
        POST: succeeds when the directory and sentinel files exist; else CommandError.
        """
        static_root = Path(settings.STATIC_ROOT)
        if not static_root.exists():
            raise CommandError(
                'STATIC_ROOT does not exist. Run collectstatic during the release '
                'phase before preflight or opening traffic.'
            )
        if not static_root.is_dir():
            raise CommandError('STATIC_ROOT exists but is not a directory.')

        missing: list[str] = []
        empty: list[str] = []
        for relative in REQUIRED_RELATIVE_ASSETS:
            path = static_root / relative
            if not path.is_file():
                missing.append(relative)
                continue
            if path.stat().st_size <= 0:
                empty.append(relative)

        if missing or empty:
            parts: list[str] = []
            if missing:
                parts.append('missing: ' + ', '.join(missing))
            if empty:
                parts.append('empty: ' + ', '.join(empty))
            raise CommandError(
                'Collected static assets incomplete under STATIC_ROOT ('
                + '; '.join(parts)
                + '). Re-run collectstatic in the release phase.'
            )

        self.stdout.write(
            self.style.SUCCESS(
                f'STATIC_ROOT assets OK ({len(REQUIRED_RELATIVE_ASSETS)} sentinels).'
            )
        )
