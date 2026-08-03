"""Offline migrate_private_media tests (FS source → NoPathPrivateStorage dest)."""

from __future__ import annotations

import hashlib
import io
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from apps.operations.storage_ops import stream_sha256
from apps.operations.tests.helpers import create_institution
from core.tests.storage_backends import NoPathPrivateStorage

PDF_BYTES = b'%PDF-1.1\nmigrate-private-media\n%%EOF\n'
PDF_BYTES_OTHER = b'%PDF-1.1\nother-content\n%%EOF\n'

NOPATH_BACKEND = 'core.tests.storage_backends.NoPathPrivateStorage'
FS_BACKEND = 'django.core.files.storage.FileSystemStorage'


def _fs_storages(location: str) -> dict:
    return {
        'default': {
            'BACKEND': FS_BACKEND,
            'OPTIONS': {'location': location},
        },
        'staticfiles': {
            'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage',
        },
    }


def _nopath_storages(location: str) -> dict:
    return {
        'default': {
            'BACKEND': NOPATH_BACKEND,
            'OPTIONS': {
                'location': location,
                'base_url': 'https://fake-private-storage.test/',
            },
        },
        'staticfiles': {
            'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage',
        },
    }


def _assert_no_secrets(text: str) -> None:
    for fragment in (
        'fictitious-r2-secret',
        'fake-test-signature',
        'X-Amz-Signature',
        'fake-private-storage.test',
        'R2_SECRET_ACCESS_KEY',
    ):
        assert fragment not in text, f'secret/url leaked: {fragment}'


class MigratePrivateMediaTests(TestCase):
    def setUp(self):
        self.source_dir = TemporaryDirectory()
        self.dest_dir = TemporaryDirectory()
        self.addCleanup(self.source_dir.cleanup)
        self.addCleanup(self.dest_dir.cleanup)

    def _create_institution_with_source_file(self, content=PDF_BYTES, filename='legal.pdf'):
        with override_settings(
            MEDIA_ROOT=self.source_dir.name,
            STORAGES=_fs_storages(self.source_dir.name),
        ):
            institution = create_institution(name='Migrate Institution')
            institution.legal_document = SimpleUploadedFile(
                filename, content, content_type='application/pdf'
            )
            institution.save(update_fields=('legal_document', 'updated_at'))
            name = institution.legal_document.name
            self.assertTrue(Path(self.source_dir.name, name).is_file())
            return institution, name

    def _call(self, *args, apply=False, delete_source=False, expect_error=False):
        stdout = StringIO()
        stderr = StringIO()
        cmd_args = list(args)
        if apply:
            cmd_args.append('--apply')
        else:
            cmd_args.append('--dry-run')
        if delete_source:
            cmd_args.append('--delete-source')
        cmd_args.append(f'--source-media-root={self.source_dir.name}')
        with override_settings(
            MEDIA_ROOT=self.source_dir.name,
            STORAGES=_nopath_storages(self.dest_dir.name),
        ):
            if expect_error:
                with self.assertRaises(CommandError):
                    call_command(
                        'migrate_private_media',
                        *cmd_args,
                        stdout=stdout,
                        stderr=stderr,
                        verbosity=1,
                    )
            else:
                call_command(
                    'migrate_private_media',
                    *cmd_args,
                    stdout=stdout,
                    stderr=stderr,
                    verbosity=1,
                )
        combined = stdout.getvalue() + stderr.getvalue()
        _assert_no_secrets(combined)
        return combined

    def _dest_storage(self) -> NoPathPrivateStorage:
        return NoPathPrivateStorage(
            location=self.dest_dir.name,
            base_url='https://fake-private-storage.test/',
        )

    def test_dry_run_uploads_nothing(self):
        _institution, name = self._create_institution_with_source_file()
        output = self._call()
        self.assertIn('uploaded=0', output)
        self.assertFalse(self._dest_storage().exists(name))

    def test_apply_uploads_missing(self):
        _institution, name = self._create_institution_with_source_file()
        output = self._call(apply=True)
        self.assertIn('uploaded=1', output)
        dest = self._dest_storage()
        self.assertTrue(dest.exists(name))
        with dest.open(name, 'rb') as handle:
            self.assertEqual(handle.read(), PDF_BYTES)

    def test_matching_skipped(self):
        _institution, name = self._create_institution_with_source_file()
        dest = self._dest_storage()
        dest.save_bytes(name, PDF_BYTES)
        output = self._call(apply=True)
        self.assertIn('already_present=1', output)
        self.assertIn('uploaded=0', output)

    def test_differing_rejected(self):
        _institution, name = self._create_institution_with_source_file()
        dest = self._dest_storage()
        dest.save_bytes(name, PDF_BYTES_OTHER)
        output = self._call(apply=True, expect_error=True)
        self.assertIn('mismatched=1', output)
        with dest.open(name, 'rb') as handle:
            self.assertEqual(handle.read(), PDF_BYTES_OTHER)

    def test_missing_source_reported(self):
        institution, name = self._create_institution_with_source_file()
        Path(self.source_dir.name, name).unlink()
        output = self._call(apply=True, expect_error=True)
        self.assertIn('missing=1', output)
        self.assertIn('failed=', output)
        _ = institution

    def test_remote_only_classified(self):
        institution, name = self._create_institution_with_source_file()
        Path(self.source_dir.name, name).unlink()
        dest = self._dest_storage()
        dest.save_bytes(name, PDF_BYTES)
        output = self._call(apply=True)
        self.assertIn('remote_only=1', output)
        self.assertIn('uploaded=0', output)
        _ = institution

    def test_source_retained_by_default(self):
        _institution, name = self._create_institution_with_source_file()
        self._call(apply=True)
        self.assertTrue(Path(self.source_dir.name, name).is_file())
        self.assertTrue(self._dest_storage().exists(name))

    def test_delete_source_requires_flag(self):
        _institution, name = self._create_institution_with_source_file()
        with override_settings(
            MEDIA_ROOT=self.source_dir.name,
            STORAGES=_nopath_storages(self.dest_dir.name),
        ):
            with self.assertRaises(CommandError):
                call_command(
                    'migrate_private_media',
                    '--delete-source',
                    f'--source-media-root={self.source_dir.name}',
                    verbosity=0,
                )
        self._call(apply=True, delete_source=True)
        self.assertFalse(Path(self.source_dir.name, name).is_file())
        self.assertTrue(self._dest_storage().exists(name))

    def test_failure_exits_non_zero(self):
        _institution, name = self._create_institution_with_source_file()
        self._dest_storage().save_bytes(name, PDF_BYTES_OTHER)
        self._call(apply=True, expect_error=True)

    def test_idempotent_rerun(self):
        _institution, name = self._create_institution_with_source_file()
        first = self._call(apply=True)
        self.assertIn('uploaded=1', first)
        second = self._call(apply=True)
        self.assertIn('already_present=1', second)
        self.assertIn('uploaded=0', second)

    def test_streaming_sha256(self):
        payload = b'x' * (1024 * 1024 + 17)
        digest, size = stream_sha256(io.BytesIO(payload), chunk_size=64 * 1024)
        self.assertEqual(size, len(payload))
        self.assertEqual(digest, hashlib.sha256(payload).hexdigest())

        source = FileSystemStorage(location=self.source_dir.name)
        name = 'institution_documents/2026/08/stream-hash.pdf'
        source.save(name, ContentFile(payload))
        with source.open(name, 'rb') as handle:
            digest2, size2 = stream_sha256(handle)
        self.assertEqual(digest2, digest)
        self.assertEqual(size2, size)

    def test_no_secrets_or_urls_printed(self):
        _institution, name = self._create_institution_with_source_file()
        # Seed dest so matching path also runs hash code.
        self._dest_storage().save_bytes(name, PDF_BYTES)
        output = self._call(apply=True)
        _assert_no_secrets(output)
        self.assertNotIn('https://', output)
