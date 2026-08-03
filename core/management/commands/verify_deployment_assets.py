"""
Verify collected static deployment assets under settings.STATIC_ROOT.

PRE: collectstatic has already run during the release/build phase when the
     deployment serves files from STATIC_ROOT (WhiteNoise or equivalent).
POST: exits 0 when STATIC_ROOT exists and required sentinel assets resolve
      through Django's staticfiles storage (including hashed manifest names)
      and are non-empty; otherwise raises CommandError. Never collects or
      mutates files. Error messages report logical paths only.
"""

from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.contrib.staticfiles.storage import staticfiles_storage
from django.core.management.base import BaseCommand, CommandError


# Application CSS, ILDE brand logos, and vendored core UI libraries expected
# after collectstatic. Paths are logical static names (not hashed filenames).
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


def resolve_collected_name(logical: str) -> str | None:
    """
    PRE: logical is a staticfiles logical path; collectstatic already ran
         (or tests planted collected sentinels under STATIC_ROOT).
    POST: returns the storage name to open (hashed when using manifest storage)
          or None when the asset cannot be resolved.
    """
    # Django 6 FileSystemStorage/StaticFilesStorage has no stored_name;
    # Manifest/WhiteNoise hashed backends do (HashedFilesMixin).
    stored_name = getattr(staticfiles_storage, 'stored_name', None)
    if callable(stored_name):
        try:
            name = stored_name(logical)
        except ValueError:
            name = None
        else:
            if staticfiles_storage.exists(name):
                return name
            if name != logical and staticfiles_storage.exists(logical):
                return logical
    # Plain storage, or unhashed sentinels under STATIC_ROOT (tests / non-hash).
    if staticfiles_storage.exists(logical):
        return logical
    return None


def asset_is_nonempty(storage_name: str) -> bool:
    """
    PRE: storage_name resolves under the active staticfiles storage.
    POST: True when at least one byte can be read.
    """
    with staticfiles_storage.open(storage_name, 'rb') as handle:
        return bool(handle.read(1))


class Command(BaseCommand):
    help = (
        'Verifica que STATIC_ROOT exista y contenga activos locales canónicos '
        'tras collectstatic (CSS de aplicación, logos ILDE y vendor UI: '
        'Bootstrap, Bootstrap Icons, SweetAlert2). Resuelve rutas lógicas vía '
        'staticfiles storage (compatible con manifest hashed). '
        'No ejecuta collectstatic.'
    )

    def handle(self, *args, **options):
        """
        PRE: Django settings expose STATIC_ROOT and staticfiles storage.
        POST: succeeds when the directory and sentinel assets resolve; else
              CommandError with logical paths only (no absolute filesystem leaks).
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
            resolved = resolve_collected_name(relative)
            if resolved is None:
                missing.append(relative)
                continue
            try:
                if not asset_is_nonempty(resolved):
                    empty.append(relative)
            except (OSError, ValueError, FileNotFoundError):
                missing.append(relative)

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
