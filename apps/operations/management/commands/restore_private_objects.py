"""Restore private objects from a portable object backup set.

PRE: object-manifest.json + objects/ exist; default is --dry-run.
POST: uploads through configured target storage; skips matching objects;
      rejects differing objects; never overwrites by default; verifies destination.
"""

from __future__ import annotations

import json
from pathlib import Path

from django.core.files.base import File
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand, CommandError

from apps.operations.storage_ops import (
    is_safe_relative_object_key,
    storage_object_sha256,
    stream_sha256,
)

OBJECT_MANIFEST_NAME = 'object-manifest.json'


class Command(BaseCommand):
    help = (
        'Restaura objetos privados desde objects/ + object-manifest.json hacia '
        'el storage configurado. Default: --dry-run.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--input-directory', required=True)
        parser.add_argument(
            '--manifest-path',
            default='',
            help='Ruta del object-manifest.json.',
        )
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument('--apply', action='store_true')
        parser.add_argument(
            '--verify',
            action='store_true',
            help='Verifica checksums (automático con --apply).',
        )

    def handle(self, *args, **options):
        apply = bool(options['apply'])
        dry_run = bool(options['dry_run']) or not apply
        verify = bool(options['verify']) or apply

        input_dir = Path(options['input_directory']).resolve()
        manifest_path = Path(
            options['manifest_path'] or (input_dir / OBJECT_MANIFEST_NAME)
        ).resolve()
        if not manifest_path.is_file():
            raise CommandError('object-manifest.json ausente')

        with manifest_path.open('r', encoding='utf-8') as handle:
            manifest = json.load(handle)

        if int(manifest.get('format_version', 0)) != 1:
            raise CommandError('object-manifest format_version no soportado')

        objects = manifest.get('objects') or []
        uploaded = 0
        skipped = 0
        mismatched = 0
        failed = 0
        missing_source = 0

        for entry in objects:
            key = entry.get('key') or ''
            rel = entry.get('relative_path') or ''
            expected_sha = entry.get('sha256') or ''
            expected_size = int(entry.get('size_bytes') or -1)
            if not is_safe_relative_object_key(key):
                failed += 1
                self.stderr.write(f'unsafe_key')
                continue
            source_path = input_dir / rel
            if not source_path.is_file():
                # Fallback: objects/<key>
                source_path = input_dir / 'objects' / key
            if not source_path.is_file():
                missing_source += 1
                failed += 1
                continue

            with source_path.open('rb') as handle:
                source_sha, source_size = stream_sha256(handle)
            if source_sha != expected_sha or source_size != expected_size:
                mismatched += 1
                failed += 1
                continue

            if default_storage.exists(key):
                try:
                    dest_sha, dest_size = storage_object_sha256(default_storage, key)
                except Exception:  # noqa: BLE001
                    failed += 1
                    continue
                if dest_sha == expected_sha and dest_size == expected_size:
                    skipped += 1
                    continue
                mismatched += 1
                failed += 1
                continue

            if dry_run or not apply:
                continue

            try:
                with source_path.open('rb') as handle:
                    default_storage.save(key, File(handle, name=key))
                if verify:
                    dest_sha, dest_size = storage_object_sha256(default_storage, key)
                    if dest_sha != expected_sha or dest_size != expected_size:
                        mismatched += 1
                        failed += 1
                        continue
                uploaded += 1
            except Exception:  # noqa: BLE001
                failed += 1

        self.stdout.write(
            'restore_private_objects report: '
            f'uploaded={uploaded} skipped={skipped} mismatched={mismatched} '
            f'missing_source={missing_source} failed={failed}'
        )
        if failed or mismatched or missing_source:
            raise CommandError('restore_private_objects terminó con fallos')
        self.stdout.write(self.style.SUCCESS('restore_private_objects: ok'))
