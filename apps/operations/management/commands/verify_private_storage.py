"""Verify private storage configuration and optional connectivity probe.

PRE: settings loaded; --probe may touch storage but never prints secrets/URLs.
POST: exit 0 when checks pass; non-zero on failure. Default is configuration-only
      for R2 (no network). Filesystem probe is local write/read/delete.
"""

from __future__ import annotations

import uuid

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand, CommandError

from apps.operations.storage_ops import STORAGE_PROBE_PREFIX
from core.private_storage import PRIVATE_STORAGE_FILESYSTEM, PRIVATE_STORAGE_R2


class Command(BaseCommand):
    help = (
        'Verifica la configuración de almacenamiento privado. '
        'Por defecto solo valida configuración (sin red). '
        'Use --probe para un ciclo write/read/delete acotado.'
    )

    def add_arguments(self, parser):
        group = parser.add_mutually_exclusive_group()
        group.add_argument(
            '--configuration-only',
            action='store_true',
            default=True,
            help='Solo validación estructural (default; sin red).',
        )
        group.add_argument(
            '--probe',
            action='store_true',
            help=(
                'Ejecuta un ciclo acotado write/read/delete bajo '
                f'{STORAGE_PROBE_PREFIX}. Requiere infraestructura real en R2.'
            ),
        )

    def handle(self, *args, **options):
        probe = bool(options.get('probe'))
        mode = getattr(settings, 'SIGEDON_PRIVATE_STORAGE', PRIVATE_STORAGE_FILESYSTEM)
        self.stdout.write(f'mode={mode}')
        self._check_configuration(mode)
        if probe:
            self._run_probe(mode)
            self.stdout.write(self.style.SUCCESS('probe=ok'))
        else:
            self.stdout.write(self.style.SUCCESS('configuration=ok'))

    def _check_configuration(self, mode: str) -> None:
        storages = getattr(settings, 'STORAGES', {}) or {}
        default_backend = (storages.get('default') or {}).get('BACKEND', '')
        static_backend = (storages.get('staticfiles') or {}).get('BACKEND', '')

        if mode == PRIVATE_STORAGE_FILESYSTEM:
            if 'FileSystemStorage' not in default_backend:
                raise CommandError('Filesystem mode requires FileSystemStorage.')
            media_root = getattr(settings, 'MEDIA_ROOT', None)
            if not media_root:
                raise CommandError('MEDIA_ROOT ausente en modo filesystem.')
            return

        if mode == PRIVATE_STORAGE_R2:
            if default_backend != 'storages.backends.s3.S3Storage':
                raise CommandError('R2 mode requires S3Storage backend.')
            r2 = getattr(settings, 'SIGEDON_R2_CONFIG', None)
            if r2 is None:
                raise CommandError('R2 configuration missing.')
            options = (storages.get('default') or {}).get('OPTIONS') or {}
            if options.get('querystring_auth') is False:
                raise CommandError('querystring_auth must be True.')
            if options.get('default_acl') in ('public-read', 'public-read-write'):
                raise CommandError('default_acl must not be public-read.')
            if options.get('custom_domain'):
                raise CommandError('custom_domain is not allowed.')
            if options.get('file_overwrite') is not False:
                raise CommandError('file_overwrite must be False.')
            if settings.DEBUG is False and 'whitenoise' not in static_backend.lower():
                raise CommandError('staticfiles must remain WhiteNoise.')
            return

        raise CommandError(f'SIGEDON_PRIVATE_STORAGE desconocido: {mode}')

    def _run_probe(self, mode: str) -> None:
        # Never print endpoint, bucket, keys, signed URLs, or full probe key.
        token = uuid.uuid4().hex
        # Truncate visible token in logs — use only short suffix in messages.
        probe_name = f'{STORAGE_PROBE_PREFIX}probe-{token}.txt'
        payload = b'sigedon-storage-probe'
        stored_name = None
        try:
            stored_name = default_storage.save(probe_name, ContentFile(payload))
            if not default_storage.exists(stored_name):
                raise CommandError('probe: object missing after save')
            with default_storage.open(stored_name, 'rb') as handle:
                read_back = handle.read()
            if read_back != payload:
                raise CommandError('probe: content mismatch')
        except CommandError:
            raise
        except Exception as exc:  # noqa: BLE001 - no provider traceback to stdout
            raise CommandError(f'probe failed: {type(exc).__name__}') from None
        finally:
            if stored_name:
                try:
                    default_storage.delete(stored_name)
                except Exception as exc:  # noqa: BLE001
                    raise CommandError(
                        f'probe cleanup failed: {type(exc).__name__}'
                    ) from None
                if default_storage.exists(stored_name):
                    raise CommandError('probe cleanup failed: object still present')
        # Avoid unused-mode warning; filesystem and r2 share the same storage API.
        _ = mode
