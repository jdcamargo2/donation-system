"""Offline export/restore private object backup tests (NoPathPrivateStorage)."""

from __future__ import annotations

import json
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from apps.operations.models import Institution
from apps.operations.tests.helpers import create_institution
from core.tests.storage_backends import NoPathPrivateStorage

PDF_BYTES = b'%PDF-1.1\nobject-backup\n%%EOF\n'
PDF_BYTES_OTHER = b'%PDF-1.1\ndiffering\n%%EOF\n'

NOPATH_BACKEND = 'core.tests.storage_backends.NoPathPrivateStorage'


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


def _assert_manifest_has_no_credentials(manifest: dict) -> None:
    blob = json.dumps(manifest).lower()
    for forbidden in (
        'password',
        'secret',
        'access_key',
        'secret_key',
        'endpoint',
        'token',
        'connection_url',
        'fake-private-storage',
        'x-amz-signature',
        'fictitious',
    ):
        # consistency.note must not introduce credential-like fields.
        if forbidden == 'endpoint':
            # Guard only top-level / object entry keys, not free-form notes.
            for key in manifest.keys():
                assert 'endpoint' not in key.lower()
            for entry in manifest.get('objects') or []:
                for key in entry.keys():
                    assert 'endpoint' not in key.lower()
            continue
        assert forbidden not in blob, f'forbidden fragment in manifest: {forbidden}'


class PrivateObjectBackupTests(TestCase):
    def setUp(self):
        self.storage_dir = TemporaryDirectory()
        self.backup_dir = TemporaryDirectory()
        self.addCleanup(self.storage_dir.cleanup)
        self.addCleanup(self.backup_dir.cleanup)
        self.override = override_settings(
            MEDIA_ROOT=self.storage_dir.name,
            STORAGES=_nopath_storages(self.storage_dir.name),
        )
        self.override.enable()
        self.addCleanup(self.override.disable)

    def _storage(self) -> NoPathPrivateStorage:
        from django.core.files.storage import default_storage

        storage = default_storage
        if hasattr(storage, '_wrapped'):
            storage = storage._wrapped
        self.assertIsInstance(storage, NoPathPrivateStorage)
        return storage

    def _create_with_document(self, name='Backup Institution', content=PDF_BYTES):
        institution = create_institution(name=name)
        institution.legal_document = SimpleUploadedFile(
            'legal.pdf', content, content_type='application/pdf'
        )
        institution.save(update_fields=('legal_document', 'updated_at'))
        return institution

    def _export(self, expect_error=False):
        stdout = StringIO()
        stderr = StringIO()
        if expect_error:
            with self.assertRaises(CommandError):
                call_command(
                    'export_private_objects',
                    f'--output-directory={self.backup_dir.name}',
                    stdout=stdout,
                    stderr=stderr,
                    verbosity=1,
                )
        else:
            call_command(
                'export_private_objects',
                f'--output-directory={self.backup_dir.name}',
                stdout=stdout,
                stderr=stderr,
                verbosity=1,
            )
        return stdout.getvalue() + stderr.getvalue()

    def _manifest_path(self) -> Path:
        return Path(self.backup_dir.name) / 'object-manifest.json'

    def _read_manifest(self) -> dict:
        return json.loads(self._manifest_path().read_text(encoding='utf-8'))

    def test_export_referenced_objects(self):
        institution = self._create_with_document()
        output = self._export()
        self.assertIn('export_private_objects: ok', output)
        manifest = self._read_manifest()
        self.assertEqual(manifest['format_version'], 1)
        self.assertEqual(manifest['object_count'], 1)
        self.assertEqual(manifest['private_storage']['mode'], 'object')
        entry = manifest['objects'][0]
        self.assertEqual(entry['key'], institution.legal_document.name)
        self.assertEqual(entry['size_bytes'], len(PDF_BYTES))
        self.assertTrue((Path(self.backup_dir.name) / entry['relative_path']).is_file())
        _assert_manifest_has_no_credentials(manifest)

    def test_duplicate_keys_deduplicated(self):
        first = self._create_with_document(name='Dup A')
        key = first.legal_document.name
        second = create_institution(name='Dup B')
        Institution.objects.filter(pk=second.pk).update(legal_document=key)
        output = self._export()
        self.assertIn('objects=1', output)
        manifest = self._read_manifest()
        self.assertEqual(manifest['object_count'], 1)
        self.assertEqual(len(manifest['objects']), 1)

    def test_missing_object_fails(self):
        institution = self._create_with_document()
        self._storage().delete(institution.legal_document.name)
        output = self._export(expect_error=True)
        self.assertIn('missing=', output)

    def test_unsafe_key_rejected(self):
        institution = self._create_with_document()
        Institution.objects.filter(pk=institution.pk).update(
            legal_document='../../etc/passwd'
        )
        output = self._export(expect_error=True)
        self.assertIn('unsafe_key', output)

    def test_restore_matching_skipped_differing_rejected(self):
        institution = self._create_with_document()
        key = institution.legal_document.name
        self._export()

        # Matching existing destination object is skipped.
        stdout = StringIO()
        call_command(
            'restore_private_objects',
            f'--input-directory={self.backup_dir.name}',
            '--apply',
            stdout=stdout,
            verbosity=1,
        )
        skipped_report = stdout.getvalue()
        self.assertIn('skipped=1', skipped_report)
        self.assertIn('uploaded=0', skipped_report)

        # Differing destination content is rejected (no overwrite).
        self._storage().delete(key)
        self._storage().save_bytes(key, PDF_BYTES_OTHER)
        with self.assertRaises(CommandError):
            call_command(
                'restore_private_objects',
                f'--input-directory={self.backup_dir.name}',
                '--apply',
                verbosity=0,
            )
        with self._storage().open(key, 'rb') as handle:
            self.assertEqual(handle.read(), PDF_BYTES_OTHER)

    def test_no_overwrite_by_default(self):
        institution = self._create_with_document()
        key = institution.legal_document.name
        self._export()
        self._storage().delete(key)
        self._storage().save_bytes(key, PDF_BYTES_OTHER)

        stdout = StringIO()
        with self.assertRaises(CommandError):
            call_command(
                'restore_private_objects',
                f'--input-directory={self.backup_dir.name}',
                '--apply',
                stdout=stdout,
                verbosity=1,
            )
        with self._storage().open(key, 'rb') as handle:
            self.assertEqual(handle.read(), PDF_BYTES_OTHER)

    def test_no_credentials_endpoint_in_manifests(self):
        self._create_with_document()
        self._export()
        manifest = self._read_manifest()
        _assert_manifest_has_no_credentials(manifest)
        text = self._manifest_path().read_text(encoding='utf-8')
        self.assertNotIn('cloudflarestorage', text)
        self.assertNotIn('access_key', text.lower())
        self.assertNotIn('endpoint', text.lower())

    def test_object_manifest_format_v1_structure(self):
        """
        Object backup format_version=1 mirrors the conceptual versioning used by
        filesystem shell backups (see apps/operations/tests/test_backup_scripts.py
        and deploy/backups/* for media.tar.gz + manifest.json format_version=1).
        """
        self._create_with_document()
        self._export()
        manifest = self._read_manifest()
        self.assertEqual(manifest['format_version'], 1)
        self.assertIn('private_storage', manifest)
        self.assertEqual(manifest['private_storage']['mode'], 'object')
        self.assertIn('consistency', manifest)
        self.assertEqual(
            manifest['consistency']['scope'], 'filefield_referenced'
        )
        self.assertEqual(
            manifest['consistency']['orphan_detection'], 'not_performed'
        )
        required_entry_keys = {'key', 'relative_path', 'size_bytes', 'sha256', 'references'}
        for entry in manifest['objects']:
            self.assertTrue(required_entry_keys.issubset(entry.keys()))
            self.assertTrue(entry['relative_path'].startswith('objects/'))
            self.assertEqual(len(entry['sha256']), 64)
