"""Tests for SIGEDON production media persistence deploy checks."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest import mock

from django.core.checks import Error
from django.test import SimpleTestCase, override_settings

from core.checks import (
    MEDIA_ROOT_MISSING,
    MEDIA_ROOT_NOT_DIRECTORY,
    MEDIA_ROOT_NOT_READABLE,
    MEDIA_ROOT_NOT_WRITABLE,
    MEDIA_ROOT_OVERLAPS_STATIC,
    check_persistent_media_root,
)


class PersistentMediaDeployCheckTests(SimpleTestCase):
    def test_check_is_registered_as_deploy_check(self):
        from django.core.checks.registry import registry

        deploy_checks = registry.get_checks(include_deployment_checks=True)
        self.assertIn(check_persistent_media_root, deploy_checks)
        normal_checks = registry.get_checks(include_deployment_checks=False)
        self.assertNotIn(check_persistent_media_root, normal_checks)

    def test_debug_true_emits_no_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            with override_settings(DEBUG=True, MEDIA_ROOT=tmp, STATIC_ROOT=str(Path(tmp) / 'static')):
                self.assertEqual(check_persistent_media_root(None), [])

    def test_valid_writable_directory_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            media = Path(tmp) / 'media'
            static = Path(tmp) / 'static'
            media.mkdir()
            static.mkdir()
            with override_settings(
                DEBUG=False,
                MEDIA_ROOT=str(media),
                STATIC_ROOT=str(static),
            ):
                self.assertEqual(check_persistent_media_root(None), [])
                remaining = list(media.iterdir())
                self.assertEqual(remaining, [])

    def test_missing_directory_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            media = Path(tmp) / 'missing-media'
            static = Path(tmp) / 'static'
            static.mkdir()
            with override_settings(
                DEBUG=False,
                MEDIA_ROOT=str(media),
                STATIC_ROOT=str(static),
            ):
                errors = check_persistent_media_root(None)
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], Error)
        self.assertEqual(errors[0].id, MEDIA_ROOT_MISSING)
        self.assertNotIn(str(media), errors[0].msg)

    def test_path_is_file_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            media = Path(tmp) / 'media-file'
            static = Path(tmp) / 'static'
            media.write_text('x', encoding='utf-8')
            static.mkdir()
            with override_settings(
                DEBUG=False,
                MEDIA_ROOT=str(media),
                STATIC_ROOT=str(static),
            ):
                errors = check_persistent_media_root(None)
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].id, MEDIA_ROOT_NOT_DIRECTORY)

    def test_overlap_with_static_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            shared = Path(tmp) / 'shared'
            shared.mkdir()
            with override_settings(
                DEBUG=False,
                MEDIA_ROOT=str(shared),
                STATIC_ROOT=str(shared),
            ):
                errors = check_persistent_media_root(None)
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].id, MEDIA_ROOT_OVERLAPS_STATIC)

    def test_write_probe_is_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            media = Path(tmp) / 'media'
            static = Path(tmp) / 'static'
            media.mkdir()
            static.mkdir()
            with override_settings(
                DEBUG=False,
                MEDIA_ROOT=str(media),
                STATIC_ROOT=str(static),
            ):
                self.assertEqual(check_persistent_media_root(None), [])
            self.assertEqual(list(media.iterdir()), [])

    def test_check_never_exposes_directory_contents(self):
        with tempfile.TemporaryDirectory() as tmp:
            media = Path(tmp) / 'media'
            static = Path(tmp) / 'static'
            media.mkdir()
            static.mkdir()
            secret_name = 'secret-evidence-should-not-leak.bin'
            (media / secret_name).write_bytes(b'x')
            with override_settings(
                DEBUG=False,
                MEDIA_ROOT=str(media),
                STATIC_ROOT=str(static),
            ):
                errors = check_persistent_media_root(None)
            self.assertEqual(errors, [])
            serialized = repr(errors)
            self.assertNotIn(secret_name, serialized)

    def test_unreadable_directory_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            media = Path(tmp) / 'media'
            static = Path(tmp) / 'static'
            media.mkdir()
            static.mkdir()
            with override_settings(
                DEBUG=False,
                MEDIA_ROOT=str(media),
                STATIC_ROOT=str(static),
            ):
                with mock.patch('core.checks.os.access', return_value=False):
                    errors = check_persistent_media_root(None)
            self.assertEqual(len(errors), 1)
            self.assertEqual(errors[0].id, MEDIA_ROOT_NOT_READABLE)

    def test_unwritable_directory_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            media = Path(tmp) / 'media'
            static = Path(tmp) / 'static'
            media.mkdir()
            static.mkdir()

            def access_side_effect(path, mode):
                if mode == os.R_OK:
                    return True
                if mode == os.W_OK:
                    return False
                return True

            with override_settings(
                DEBUG=False,
                MEDIA_ROOT=str(media),
                STATIC_ROOT=str(static),
            ):
                with mock.patch('core.checks.os.access', side_effect=access_side_effect):
                    errors = check_persistent_media_root(None)
            self.assertEqual(len(errors), 1)
            self.assertEqual(errors[0].id, MEDIA_ROOT_NOT_WRITABLE)
