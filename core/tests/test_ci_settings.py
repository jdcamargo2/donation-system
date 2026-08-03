"""Tests for core.ci_settings STATIC_ROOT isolation."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from django.test import SimpleTestCase


REPO_ROOT = Path(__file__).resolve().parents[2]

CI_SETTINGS_PROBE = """
import json
from pathlib import Path
from core import ci_settings
print(json.dumps({'static_root': str(ci_settings.STATIC_ROOT)}))
"""


class CiSettingsTests(SimpleTestCase):
    def _run(self, env_extra: dict[str, str]) -> subprocess.CompletedProcess:
        environment = os.environ.copy()
        for name in (
            'DJANGO_DEBUG',
            'DJANGO_SECRET_KEY',
            'ALLOWED_HOSTS',
            'DATABASE_ENGINE',
            'SIGEDON_MEDIA_ROOT',
            'SIGEDON_CI_STATIC_ROOT',
            'KOBO_ENABLED',
        ):
            environment[name] = ''
        environment.update(
            {
                'DJANGO_DEBUG': 'True',
                'DJANGO_SECRET_KEY': 'ci-only-secret',
                'ALLOWED_HOSTS': 'localhost',
                'DATABASE_ENGINE': 'sqlite',
                'KOBO_ENABLED': 'False',
            }
        )
        environment.update(env_extra)
        return subprocess.run(
            [sys.executable, '-c', CI_SETTINGS_PROBE],
            cwd=REPO_ROOT,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_requires_absolute_static_root(self):
        result = self._run({})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('SIGEDON_CI_STATIC_ROOT', result.stderr)

        result = self._run({'SIGEDON_CI_STATIC_ROOT': 'relative/static'})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('absolute', result.stderr)

    def test_overrides_static_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            static_root = str(Path(tmp) / 'collected')
            result = self._run({'SIGEDON_CI_STATIC_ROOT': static_root})
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload['static_root'], static_root)
