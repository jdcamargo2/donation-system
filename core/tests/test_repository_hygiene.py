"""Tests for deploy/ci/check_repository_hygiene.sh (OPS-CI-GATES).

PRE: hygiene script exists and bash/git are available.
POST: validates allow/deny rules on temporary Git repositories without
      scanning the developer home directory or deleting files.
"""

from __future__ import annotations

import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
HYGIENE_SCRIPT = REPO_ROOT / 'deploy' / 'ci' / 'check_repository_hygiene.sh'


class RepositoryHygieneScriptTests(unittest.TestCase):
    def _git(self, repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        return subprocess.run(
            ['git', *args],
            cwd=repo,
            text=True,
            capture_output=True,
            check=check,
        )

    def _init_repo(self, repo: Path) -> None:
        self._git(repo, 'init')
        self._git(repo, 'config', 'user.email', 'ci@example.test')
        self._git(repo, 'config', 'user.name', 'CI Test')
        # Minimal tracked file so the repo is non-empty.
        (repo / 'README.md').write_text('ok\n', encoding='utf-8')
        self._git(repo, 'add', 'README.md')
        self._git(repo, 'commit', '-m', 'init')

    def _run_hygiene(self, repo: Path) -> subprocess.CompletedProcess:
        env = os.environ.copy()
        env['GIT_DIR'] = str(repo / '.git')
        env['GIT_WORK_TREE'] = str(repo)
        return subprocess.run(
            ['bash', str(HYGIENE_SCRIPT)],
            cwd=repo,
            text=True,
            capture_output=True,
            check=False,
            env=env,
        )

    def test_script_is_executable_tracked_shell(self):
        self.assertTrue(HYGIENE_SCRIPT.is_file())
        mode = HYGIENE_SCRIPT.stat().st_mode
        self.assertTrue(mode & stat.S_IXUSR)

    def test_clean_repository_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            self._init_repo(repo)
            result = self._run_hygiene(repo)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            self.assertIn('hygiene: OK', result.stdout)

    def test_env_example_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            self._init_repo(repo)
            (repo / '.env.example').write_text('DJANGO_DEBUG=True\n', encoding='utf-8')
            self._git(repo, 'add', '.env.example')
            self._git(repo, 'commit', '-m', 'env example')
            result = self._run_hygiene(repo)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

    def test_env_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            self._init_repo(repo)
            (repo / '.env').write_text('SECRET=should-not-print\n', encoding='utf-8')
            self._git(repo, 'add', '-f', '.env')
            self._git(repo, 'commit', '-m', 'bad env')
            result = self._run_hygiene(repo)
            self.assertNotEqual(result.returncode, 0)
            combined = result.stdout + result.stderr
            self.assertIn('.env', combined)
            self.assertNotIn('SECRET=should-not-print', combined)

    def test_sqlite_dump_backup_and_key_rejected(self):
        cases = (
            ('db.sqlite3', 'x'),
            ('backup.dump', 'x'),
            ('nightly.backup', 'x'),
            ('tls.pem', '-----BEGIN CERTIFICATE-----\n'),
            ('id_rsa.key', '-----BEGIN PRIVATE KEY-----\nx\n-----END PRIVATE KEY-----\n'),
        )
        for name, content in cases:
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as tmp:
                    repo = Path(tmp)
                    self._init_repo(repo)
                    (repo / name).write_text(content, encoding='utf-8')
                    self._git(repo, 'add', '-f', name)
                    self._git(repo, 'commit', '-m', f'add {name}')
                    result = self._run_hygiene(repo)
                    self.assertNotEqual(result.returncode, 0)
                    combined = result.stdout + result.stderr
                    self.assertIn(name, combined)
                    self.assertNotIn('PRIVATE KEY', combined)
                    self.assertNotIn('CERTIFICATE', combined)

    def test_runtime_media_and_staticfiles_rejected(self):
        for relative in ('media/secret.bin', 'staticfiles/app.css'):
            with self.subTest(path=relative):
                with tempfile.TemporaryDirectory() as tmp:
                    repo = Path(tmp)
                    self._init_repo(repo)
                    path = repo / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text('payload-must-not-appear\n', encoding='utf-8')
                    self._git(repo, 'add', '-f', relative)
                    self._git(repo, 'commit', '-m', f'add {relative}')
                    result = self._run_hygiene(repo)
                    self.assertNotEqual(result.returncode, 0)
                    combined = result.stdout + result.stderr
                    self.assertIn(relative.split('/', 1)[0], combined)
                    self.assertNotIn('payload-must-not-appear', combined)

    def test_static_vendor_assets_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            self._init_repo(repo)
            path = repo / 'static' / 'vendor' / 'bootstrap' / 'bootstrap.min.css'
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('/* vendor */\n', encoding='utf-8')
            self._git(repo, 'add', 'static')
            self._git(repo, 'commit', '-m', 'vendor')
            result = self._run_hygiene(repo)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

    def test_backup_status_markers_rejected(self):
        for name in (
            '.sigedon-backup-status.json',
            '.sigedon-restore-drill-status.json',
        ):
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as tmp:
                    repo = Path(tmp)
                    self._init_repo(repo)
                    (repo / name).write_text('{"status":"success"}\n', encoding='utf-8')
                    self._git(repo, 'add', '-f', name)
                    self._git(repo, 'commit', '-m', f'add {name}')
                    result = self._run_hygiene(repo)
                    self.assertNotEqual(result.returncode, 0)
                    combined = result.stdout + result.stderr
                    self.assertIn(name, combined)
                    self.assertNotIn('"status"', combined)

    def test_merge_conflict_markers_rejected_without_emitting_contents(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            self._init_repo(repo)
            conflicted = repo / 'conflicted.py'
            conflicted.write_text(
                'a = 1\n<<<<<<< HEAD\nsecret_value = "must-not-print"\n=======\nb = 2\n>>>>>>> branch\n',
                encoding='utf-8',
            )
            self._git(repo, 'add', 'conflicted.py')
            self._git(repo, 'commit', '-m', 'conflict')
            result = self._run_hygiene(repo)
            self.assertNotEqual(result.returncode, 0)
            combined = result.stdout + result.stderr
            self.assertIn('conflicted.py', combined)
            self.assertNotIn('must-not-print', combined)
            self.assertNotIn('secret_value', combined)

    def test_hygiene_does_not_delete_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            self._init_repo(repo)
            bad = repo / '.env'
            bad.write_text('keep-me\n', encoding='utf-8')
            self._git(repo, 'add', '-f', '.env')
            self._git(repo, 'commit', '-m', 'bad')
            result = self._run_hygiene(repo)
            self.assertNotEqual(result.returncode, 0)
            self.assertTrue(bad.is_file())
            self.assertEqual(bad.read_text(encoding='utf-8'), 'keep-me\n')
