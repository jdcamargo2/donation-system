"""Deploy / compatibility checks for private storage (offline, no network)."""

from __future__ import annotations

import socket
import tempfile
from pathlib import Path
from unittest import mock

from django.core.checks import Error
from django.test import SimpleTestCase, override_settings

from core.checks import (
    MEDIA_ROOT_MISSING,
    R2_STORAGE_NOT_CONFIGURED,
    check_persistent_media_root,
    check_private_storage_mode,
)
from core.private_storage import R2StorageConfig, build_r2_storages_default

FICTITIOUS_R2 = R2StorageConfig(
    account_id='fictitiousaccount01',
    access_key_id='fictitious-access-key',
    secret_access_key='fictitious-r2-secret',
    bucket_name='sigedon-private-test',
    endpoint_url='https://fictitiousaccount01.r2.cloudflarestorage.com',
    region_name='auto',
    signed_url_expiry_seconds=300,
    addressing_style='path',
)

WHITENOISE_STATIC = {
    'BACKEND': 'whitenoise.storage.CompressedManifestStaticFilesStorage',
}


def _r2_storages():
    return {
        'default': build_r2_storages_default(FICTITIOUS_R2),
        'staticfiles': WHITENOISE_STATIC,
    }


class PrivateStorageDeployCheckTests(SimpleTestCase):
    def test_filesystem_deploy_checks_still_work(self):
        with tempfile.TemporaryDirectory() as tmp:
            media = Path(tmp) / 'media'
            static = Path(tmp) / 'static'
            missing = Path(tmp) / 'gone-media'
            media.mkdir()
            static.mkdir()
            with override_settings(
                DEBUG=False,
                SIGEDON_PRIVATE_STORAGE='filesystem',
                MEDIA_ROOT=str(media),
                STATIC_ROOT=str(static),
                STORAGES={
                    'default': {
                        'BACKEND': 'django.core.files.storage.FileSystemStorage',
                    },
                    'staticfiles': WHITENOISE_STATIC,
                },
            ):
                self.assertEqual(check_persistent_media_root(None), [])
                self.assertEqual(check_private_storage_mode(None), [])

            with override_settings(
                DEBUG=False,
                SIGEDON_PRIVATE_STORAGE='filesystem',
                MEDIA_ROOT=str(missing),
                STATIC_ROOT=str(static),
            ):
                errors = check_persistent_media_root(None)
            self.assertEqual(len(errors), 1)
            self.assertEqual(errors[0].id, MEDIA_ROOT_MISSING)

    def test_r2_mode_deploy_checks_pass_without_network(self):
        missing_media = '/var/lib/sigedon-r2-check-test/does-not-exist'
        with override_settings(
            DEBUG=False,
            SIGEDON_PRIVATE_STORAGE='r2',
            SIGEDON_R2_CONFIG=FICTITIOUS_R2,
            MEDIA_ROOT=missing_media,
            STORAGES=_r2_storages(),
        ):
            self.assertEqual(check_persistent_media_root(None), [])
            self.assertEqual(check_private_storage_mode(None), [])

    def test_r2_mode_does_not_require_media_root_existence(self):
        missing_media = '/tmp/sigedon-r2-unused-media-does-not-exist'
        self.assertFalse(Path(missing_media).exists())
        with override_settings(
            DEBUG=False,
            SIGEDON_PRIVATE_STORAGE='r2',
            SIGEDON_R2_CONFIG=FICTITIOUS_R2,
            MEDIA_ROOT=missing_media,
            STORAGES=_r2_storages(),
        ):
            errors = check_persistent_media_root(None)
        self.assertEqual(errors, [])
        for err in errors:
            self.assertNotEqual(getattr(err, 'id', None), MEDIA_ROOT_MISSING)

    def test_checks_perform_no_network(self):
        def fail_connect(*_args, **_kwargs):
            raise AssertionError('deploy checks must not open network sockets')

        missing_media = '/tmp/sigedon-r2-no-network-media'
        with mock.patch.object(socket.socket, 'connect', side_effect=fail_connect):
            with override_settings(
                DEBUG=False,
                SIGEDON_PRIVATE_STORAGE='r2',
                SIGEDON_R2_CONFIG=FICTITIOUS_R2,
                MEDIA_ROOT=missing_media,
                STORAGES=_r2_storages(),
            ):
                self.assertEqual(check_persistent_media_root(None), [])
                self.assertEqual(check_private_storage_mode(None), [])

    def test_r2_mode_without_config_errors(self):
        with override_settings(
            DEBUG=False,
            SIGEDON_PRIVATE_STORAGE='r2',
            SIGEDON_R2_CONFIG=None,
            STORAGES={
                'default': {'BACKEND': 'storages.backends.s3.S3Storage'},
                'staticfiles': WHITENOISE_STATIC,
            },
        ):
            errors = check_persistent_media_root(None)
        self.assertTrue(errors)
        self.assertTrue(all(isinstance(err, Error) for err in errors))
        self.assertEqual(errors[0].id, R2_STORAGE_NOT_CONFIGURED)
        serialized = repr(errors)
        self.assertNotIn('fictitious-r2-secret', serialized)
