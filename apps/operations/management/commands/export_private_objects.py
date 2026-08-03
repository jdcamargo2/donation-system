"""Export referenced private objects into a portable backup set.

PRE: --output-directory is writable; default storage holds private objects.
POST: writes objects/ tree + object-manifest.json with path/size/sha256.
      Fails non-zero on missing/unreadable/unsafe keys. No credentials in
      manifests. No provider endpoints. No network listing of the whole bucket —
      only FileField-referenced keys.
"""

from __future__ import annotations

import json
from pathlib import Path

from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand, CommandError

from apps.operations.private_file_fields import PRIVATE_FILE_FIELD_SPECS, get_model_for_spec
from apps.operations.storage_ops import is_safe_relative_object_key

OBJECT_MANIFEST_NAME = 'object-manifest.json'
OBJECTS_DIRNAME = 'objects'
FORMAT_VERSION = 1


class Command(BaseCommand):
    help = (
        'Exporta objetos privados referenciados por FileFields a un directorio '
        'portable (objects/ + object-manifest.json).'
    )

    def add_arguments(self, parser):
        parser.add_argument('--output-directory', required=True)
        parser.add_argument(
            '--manifest-path',
            default='',
            help='Ruta del object-manifest.json (default: <output>/object-manifest.json).',
        )

    def handle(self, *args, **options):
        output = Path(options['output_directory']).resolve()
        output.mkdir(parents=True, exist_ok=True)
        objects_dir = output / OBJECTS_DIRNAME
        objects_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = Path(
            options['manifest_path'] or (output / OBJECT_MANIFEST_NAME)
        ).resolve()

        entries = []
        seen: set[str] = set()
        missing = 0
        failed = 0

        for spec in PRIVATE_FILE_FIELD_SPECS:
            model = get_model_for_spec(spec)
            queryset = model.objects.exclude(
                **{f'{spec.field_name}__isnull': True}
            ).exclude(**{f'{spec.field_name}': ''})
            for instance in queryset.iterator():
                field_file = getattr(instance, spec.field_name)
                name = getattr(field_file, 'name', '') or ''
                if not name:
                    continue
                if name in seen:
                    continue
                seen.add(name)
                if not is_safe_relative_object_key(name):
                    failed += 1
                    self.stderr.write(f'unsafe_key model={spec.label} id={instance.pk}')
                    continue
                if not default_storage.exists(name):
                    missing += 1
                    self.stderr.write(f'missing model={spec.label} id={instance.pk}')
                    continue
                try:
                    dest = objects_dir / name
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    with default_storage.open(name, 'rb') as src, dest.open('wb') as out:
                        digest = __import__('hashlib').sha256()
                        size = 0
                        while True:
                            chunk = src.read(1024 * 1024)
                            if not chunk:
                                break
                            out.write(chunk)
                            digest.update(chunk)
                            size += len(chunk)
                    entries.append(
                        {
                            'key': name,
                            'relative_path': f'{OBJECTS_DIRNAME}/{name}',
                            'size_bytes': size,
                            'sha256': digest.hexdigest(),
                            'references': [f'{spec.label}:{instance.pk}'],
                        }
                    )
                except Exception:  # noqa: BLE001
                    failed += 1
                    self.stderr.write(f'read_error model={spec.label} id={instance.pk}')

        # Attach additional references for duplicate keys already exported.
        # (Dedup already handled; references list keeps first occurrence.)

        manifest = {
            'format_version': FORMAT_VERSION,
            'private_storage': {'mode': 'object'},
            'object_count': len(entries),
            'objects': sorted(entries, key=lambda item: item['key']),
            'consistency': {
                'scope': 'filefield_referenced',
                'orphan_detection': 'not_performed',
                'note': (
                    'Backup includes objects referenced by canonical FileFields. '
                    'Unreferenced/orphan bucket objects require separate lifecycle '
                    'management and are not deleted by this command.'
                ),
            },
        }

        with manifest_path.open('w', encoding='utf-8') as handle:
            json.dump(manifest, handle, indent=2, sort_keys=True)
            handle.write('\n')

        self.stdout.write(
            f'export_private_objects: objects={len(entries)} '
            f'missing={missing} failed={failed}'
        )
        if missing or failed:
            raise CommandError(
                f'export_private_objects failed missing={missing} failed={failed}'
            )
        self.stdout.write(self.style.SUCCESS('export_private_objects: ok'))
