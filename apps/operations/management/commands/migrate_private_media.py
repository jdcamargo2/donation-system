"""Idempotent migration of private media from filesystem source to target storage.

PRE: source files live under a local filesystem storage; target is the configured
     default storage (often a no-path / R2 backend). Default is --dry-run.
POST: reports bounded counts; --apply uploads missing matching objects; never
      overwrites differing remote content; never deletes source unless
      --delete-source after verified upload. No secrets/URLs printed.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from django.core.files.base import File
from django.core.files.storage import FileSystemStorage, default_storage
from django.core.management.base import BaseCommand, CommandError

from apps.operations.private_file_fields import (
    PRIVATE_FILE_FIELD_SPECS,
    get_model_for_spec,
)
from apps.operations.storage_ops import (
    is_safe_relative_object_key,
    storage_object_sha256,
    stream_sha256,
)


@dataclass
class MigrationReport:
    scanned: int = 0
    eligible: int = 0
    uploaded: int = 0
    already_present: int = 0
    remote_only: int = 0
    missing: int = 0
    mismatched: int = 0
    failed: int = 0
    deleted_source: int = 0
    skipped_duplicate_keys: int = 0
    errors: list[str] = field(default_factory=list)


class Command(BaseCommand):
    help = (
        'Migra media privada desde almacenamiento filesystem local hacia el '
        'storage privado configurado. Default: --dry-run.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Inventario y plan sin subir (default si no hay --apply).',
        )
        parser.add_argument(
            '--apply',
            action='store_true',
            help='Sube objetos faltantes y verifica checksums.',
        )
        parser.add_argument(
            '--verify',
            action='store_true',
            help='Verifica existencia/tamaño/hash en destino.',
        )
        parser.add_argument(
            '--delete-source',
            action='store_true',
            help=(
                'Elimina fuente local solo tras verificación exitosa del destino. '
                'Requiere --apply.'
            ),
        )
        parser.add_argument(
            '--source-media-root',
            default='',
            help=(
                'Raíz filesystem de origen. Por defecto usa MEDIA_ROOT actual '
                'cuando el default storage es FileSystemStorage.'
            ),
        )

    def handle(self, *args, **options):
        apply = bool(options['apply'])
        dry_run = bool(options['dry_run']) or not apply
        verify = bool(options['verify']) or apply
        delete_source = bool(options['delete_source'])
        if delete_source and not apply:
            raise CommandError('--delete-source requiere --apply.')

        source_root = (options.get('source_media_root') or '').strip()
        if source_root:
            source_storage = FileSystemStorage(location=source_root)
        elif isinstance(default_storage, FileSystemStorage):
            source_storage = default_storage
        else:
            # Migrating into R2: source must be an explicit local root.
            from django.conf import settings

            media_root = getattr(settings, 'MEDIA_ROOT', None)
            if not media_root:
                raise CommandError(
                    'Defina --source-media-root cuando el storage destino no es filesystem.'
                )
            source_storage = FileSystemStorage(location=str(media_root))

        target_storage = default_storage
        report = MigrationReport()
        seen_keys: set[str] = set()

        for spec in PRIVATE_FILE_FIELD_SPECS:
            model = get_model_for_spec(spec)
            queryset = model.objects.exclude(
                **{f'{spec.field_name}__isnull': True}
            ).exclude(**{f'{spec.field_name}': ''})
            for instance in queryset.iterator():
                report.scanned += 1
                field_file = getattr(instance, spec.field_name)
                name = getattr(field_file, 'name', '') or ''
                if not name:
                    continue
                if not is_safe_relative_object_key(name):
                    report.failed += 1
                    report.errors.append(f'unsafe_key:{spec.label}:{instance.pk}')
                    continue
                if name in seen_keys:
                    report.skipped_duplicate_keys += 1
                    continue
                seen_keys.add(name)
                report.eligible += 1
                self._migrate_one(
                    source_storage=source_storage,
                    target_storage=target_storage,
                    name=name,
                    report=report,
                    dry_run=dry_run,
                    apply=apply,
                    verify=verify,
                    delete_source=delete_source,
                    verbose=options['verbosity'] >= 2,
                    label=f'{spec.label}:{instance.pk}',
                )

        self._print_report(report, verbosity=options['verbosity'])
        if report.failed or report.mismatched or report.missing:
            if apply or report.failed or report.mismatched:
                raise CommandError(
                    'migrate_private_media terminó con fallos '
                    f'(failed={report.failed} mismatched={report.mismatched} '
                    f'missing={report.missing}).'
                )
        self.stdout.write(self.style.SUCCESS('migrate_private_media: done'))

    def _migrate_one(
        self,
        *,
        source_storage,
        target_storage,
        name: str,
        report: MigrationReport,
        dry_run: bool,
        apply: bool,
        verify: bool,
        delete_source: bool,
        verbose: bool,
        label: str,
    ) -> None:
        source_exists = source_storage.exists(name)
        try:
            target_exists = target_storage.exists(name)
        except Exception:  # noqa: BLE001
            report.failed += 1
            report.errors.append(f'target_exists_error:{label}')
            return

        if not source_exists and target_exists:
            report.remote_only += 1
            if verbose:
                self.stdout.write(f'remote_only {label}')
            return
        if not source_exists and not target_exists:
            report.missing += 1
            report.failed += 1
            report.errors.append(f'source_and_target_missing:{label}')
            return

        # Source exists.
        try:
            with source_storage.open(name, 'rb') as handle:
                source_sha, source_size = stream_sha256(handle)
        except Exception:  # noqa: BLE001
            report.failed += 1
            report.errors.append(f'source_read_error:{label}')
            return

        if target_exists:
            try:
                dest_sha, dest_size = storage_object_sha256(target_storage, name)
            except Exception:  # noqa: BLE001
                report.failed += 1
                report.errors.append(f'target_read_error:{label}')
                return
            if dest_sha == source_sha and dest_size == source_size:
                report.already_present += 1
                if verbose:
                    self.stdout.write(f'already_present {label}')
                return
            report.mismatched += 1
            report.failed += 1
            report.errors.append(f'mismatched:{label}')
            return

        if dry_run or not apply:
            if verbose:
                self.stdout.write(f'would_upload {label}')
            return

        try:
            with source_storage.open(name, 'rb') as handle:
                target_storage.save(name, File(handle, name=name))
            if verify:
                dest_sha, dest_size = storage_object_sha256(target_storage, name)
                if dest_sha != source_sha or dest_size != source_size:
                    report.mismatched += 1
                    report.failed += 1
                    report.errors.append(f'verify_mismatch:{label}')
                    return
            report.uploaded += 1
            if delete_source:
                source_storage.delete(name)
                report.deleted_source += 1
        except Exception:  # noqa: BLE001
            report.failed += 1
            report.errors.append(f'upload_error:{label}')

    def _print_report(self, report: MigrationReport, *, verbosity: int = 1) -> None:
        self.stdout.write('migrate_private_media report:')
        for key in (
            'scanned',
            'eligible',
            'uploaded',
            'already_present',
            'remote_only',
            'missing',
            'mismatched',
            'failed',
            'deleted_source',
            'skipped_duplicate_keys',
        ):
            self.stdout.write(f'  {key}={getattr(report, key)}')
        if report.errors and verbosity >= 2:
            for err in report.errors[:50]:
                self.stdout.write(f'  error={err}')
