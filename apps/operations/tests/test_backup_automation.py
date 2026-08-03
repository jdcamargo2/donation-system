"""
OPS-BACKUP-AUTOMATION: pruebas de lock, pipeline, retención, markers y drill.

PRE: no se conecta a PostgreSQL real ni se copia media/ del proyecto.
POST: cubre lock exclusivo (FD 9 global + bypass seguro por herencia),
      pipeline programado, status markers, retención solo de verificados,
      rechazo de drill inseguro y alert hook acotado.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from apps.operations.tests.test_backup_scripts import (
    BACKUP_SCRIPT,
    RESTORE_SCRIPT,
    SCRIPTS_DIR,
    VERIFY_SCRIPT,
    _base_backup_env,
    _build_mock_bin,
    _make_valid_backup_dir,
    _run,
    _write_executable,
)

RUNNER_SCRIPT = SCRIPTS_DIR / 'run_scheduled_backup.sh'
RETENTION_SCRIPT = SCRIPTS_DIR / 'apply_retention.sh'
DRILL_SCRIPT = SCRIPTS_DIR / 'run_restore_drill.sh'


def _hold_ops_lock(lock_file: Path, env: dict[str, str]) -> subprocess.Popen:
    """Hold exclusive flock on .sigedon-ops.lock until the process is stopped."""
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    lock_file.touch()
    holder = subprocess.Popen(
        [
            'bash',
            '-c',
            f'exec 9>"{lock_file}"; flock -n 9 || exit 1; sleep 60',
        ],
        env=env,
    )
    time.sleep(0.2)
    if holder.poll() is not None:
        raise RuntimeError('lock holder failed to start')
    return holder


def _lock_is_free(lock_file: Path) -> bool:
    """Return True if a non-blocking exclusive flock on lock_file succeeds."""
    result = subprocess.run(
        [
            'bash',
            '-c',
            f'exec 9>"{lock_file}"; flock -n 9',
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


class BackupAutomationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='sigedon-backup-auto-'))
        self.bin_dir = self.tmp / 'bin'
        self.bin_dir.mkdir()
        _build_mock_bin(self.bin_dir)
        self.env = _base_backup_env(self.tmp)
        self.env['PATH'] = f"{self.bin_dir}:{os.environ.get('PATH', '')}"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_backup_rejects_when_lock_held(self):
        backup_root = Path(self.env['SIGEDON_BACKUP_ROOT'])
        lock_file = backup_root / '.sigedon-ops.lock'
        holder = _hold_ops_lock(lock_file, self.env)
        try:
            result = _run(['bash', str(BACKUP_SCRIPT)], env=self.env)
            self.assertEqual(result.returncode, 8)
            self.assertIn('lock', result.stderr.lower())
        finally:
            holder.terminate()
            holder.wait(timeout=5)

    def test_forged_lock_held_env_does_not_bypass(self):
        """Forged env without inherited FD 9 must not skip flock."""
        backup_root = Path(self.env['SIGEDON_BACKUP_ROOT'])
        lock_file = backup_root / '.sigedon-ops.lock'
        holder = _hold_ops_lock(lock_file, self.env)
        try:
            env = self.env.copy()
            env['SIGEDON_BACKUP_LOCK_HELD'] = 'YES'
            result = _run(['bash', str(BACKUP_SCRIPT)], env=env)
            self.assertEqual(result.returncode, 8)
        finally:
            holder.terminate()
            holder.wait(timeout=5)

    def test_manual_retention_rejects_when_lock_held(self):
        backup_root = Path(self.env['SIGEDON_BACKUP_ROOT'])
        _make_valid_backup_dir(backup_root, '20260801T120000Z')
        lock_file = backup_root / '.sigedon-ops.lock'
        holder = _hold_ops_lock(lock_file, self.env)
        try:
            env = self.env.copy()
            env['SIGEDON_BACKUP_KEEP_COUNT'] = '1'
            result = _run(['bash', str(RETENTION_SCRIPT)], env=env)
            self.assertEqual(result.returncode, 8)
            self.assertIn('lock', result.stderr.lower())
        finally:
            holder.terminate()
            holder.wait(timeout=5)

    def test_scheduled_pipeline_backup_verify_retention_success_marker(self):
        """E2E mocked: backup → verify → retention → success marker."""
        backup_root = Path(self.env['SIGEDON_BACKUP_ROOT'])
        old = _make_valid_backup_dir(backup_root, '20260101T000000Z')
        env = self.env.copy()
        env['SIGEDON_BACKUP_KEEP_COUNT'] = '1'
        result = _run(['bash', str(RUNNER_SCRIPT)], env=env)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        published = Path(result.stdout.strip())
        self.assertTrue(published.is_dir())
        self.assertTrue((published / '.sigedon-verified').is_file())
        self.assertFalse(old.exists(), msg='retention must delete older verified set')
        status_path = backup_root / '.sigedon-backup-status.json'
        status = json.loads(status_path.read_text(encoding='utf-8'))
        self.assertEqual(status['status'], 'success')
        self.assertEqual(status['phase'], 'done')
        self.assertEqual(status['exit_code'], 0)
        self.assertEqual(status['backup_id'], published.name)
        self.assertEqual(status['job'], 'scheduled_backup')
        self.assertNotIn('password', json.dumps(status).lower())
        self.assertTrue(
            _lock_is_free(backup_root / '.sigedon-ops.lock'),
            msg='lock must be released after successful pipeline',
        )

    def test_scheduled_pipeline_writes_success_marker_and_verified(self):
        env = self.env.copy()
        env['SIGEDON_BACKUP_KEEP_COUNT'] = '5'
        result = _run(['bash', str(RUNNER_SCRIPT)], env=env)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        published = Path(result.stdout.strip())
        self.assertTrue(published.is_dir())
        self.assertTrue((published / '.sigedon-verified').is_file())
        status_path = Path(env['SIGEDON_BACKUP_ROOT']) / '.sigedon-backup-status.json'
        status = json.loads(status_path.read_text(encoding='utf-8'))
        self.assertEqual(status['status'], 'success')
        self.assertEqual(status['exit_code'], 0)
        self.assertEqual(status['backup_id'], published.name)
        self.assertEqual(status['job'], 'scheduled_backup')
        self.assertNotIn('password', json.dumps(status).lower())

    def test_concurrent_second_runner_exits_8(self):
        backup_root = Path(self.env['SIGEDON_BACKUP_ROOT'])
        lock_file = backup_root / '.sigedon-ops.lock'
        # Slow first runner holds the global lock while backup runs.
        _write_executable(
            self.bin_dir / 'pg_dump',
            """#!/usr/bin/env bash
set -euo pipefail
out=""
for arg in "$@"; do
  case "$arg" in
    --version) echo "pg_dump (PostgreSQL) 16.14 mock"; exit 0 ;;
    --file=*) out="${arg#--file=}" ;;
  esac
done
sleep 8
printf 'MOCKDUMP' >"$out"
dd if=/dev/zero bs=1 count=64 >>"$out" 2>/dev/null || true
""",
        )
        first = subprocess.Popen(
            ['bash', str(RUNNER_SCRIPT)],
            env=self.env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            deadline = time.time() + 5
            while time.time() < deadline and not lock_file.exists():
                time.sleep(0.05)
            held = False
            while time.time() < deadline:
                probe = subprocess.run(
                    [
                        'bash',
                        '-c',
                        f'exec 9>"{lock_file}"; flock -n 9',
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                if probe.returncode != 0:
                    held = True
                    break
                time.sleep(0.05)
            self.assertTrue(held, msg='first runner did not acquire lock')
            second = _run(['bash', str(RUNNER_SCRIPT)], env=self.env)
            self.assertEqual(second.returncode, 8)
            self.assertIn('lock', second.stderr.lower())
        finally:
            first.terminate()
            try:
                first.wait(timeout=10)
            except subprocess.TimeoutExpired:
                first.kill()
                first.wait(timeout=5)

    def test_failed_backup_skips_retention_and_releases_lock(self):
        backup_root = Path(self.env['SIGEDON_BACKUP_ROOT'])
        old = _make_valid_backup_dir(backup_root, '20260101T000000Z')
        _write_executable(
            self.bin_dir / 'pg_dump',
            """#!/usr/bin/env bash
echo "forced failure" >&2
exit 99
""",
        )
        env = self.env.copy()
        env['SIGEDON_BACKUP_KEEP_COUNT'] = '1'
        result = _run(['bash', str(RUNNER_SCRIPT)], env=env)
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(old.exists(), msg='retention must not run after backup failure')
        status_path = backup_root / '.sigedon-backup-status.json'
        status = json.loads(status_path.read_text(encoding='utf-8'))
        self.assertEqual(status['status'], 'failure')
        self.assertEqual(status['phase'], 'backup')
        self.assertNotEqual(status['exit_code'], 0)
        self.assertTrue(
            _lock_is_free(backup_root / '.sigedon-ops.lock'),
            msg='lock must be released after failed pipeline',
        )

    def test_scheduled_pipeline_writes_failure_marker_on_backup_error(self):
        _write_executable(
            self.bin_dir / 'pg_dump',
            """#!/usr/bin/env bash
echo "forced failure" >&2
exit 99
""",
        )
        result = _run(['bash', str(RUNNER_SCRIPT)], env=self.env)
        self.assertNotEqual(result.returncode, 0)
        status_path = Path(self.env['SIGEDON_BACKUP_ROOT']) / '.sigedon-backup-status.json'
        status = json.loads(status_path.read_text(encoding='utf-8'))
        self.assertEqual(status['status'], 'failure')
        self.assertEqual(status['phase'], 'backup')
        self.assertNotEqual(status['exit_code'], 0)

    def test_retention_keeps_newest_verified_only(self):
        backup_root = Path(self.env['SIGEDON_BACKUP_ROOT'])
        old = _make_valid_backup_dir(backup_root, '20260101T000000Z')
        new = _make_valid_backup_dir(backup_root, '20260801T120000Z')
        # Incomplete / unverified must not be deleted by retention.
        junk = backup_root / 'not-a-backup-id'
        junk.mkdir()
        (junk / 'readme.txt').write_text('x', encoding='utf-8')
        incomplete = backup_root / '.sigedon-backup.partial'
        incomplete.mkdir()

        env = self.env.copy()
        env['SIGEDON_BACKUP_KEEP_COUNT'] = '1'
        result = _run(['bash', str(RETENTION_SCRIPT)], env=env)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertFalse(old.exists())
        self.assertTrue(new.exists())
        self.assertTrue(junk.exists())
        self.assertTrue(incomplete.exists())

    def test_retention_refuses_without_policy(self):
        result = _run(['bash', str(RETENTION_SCRIPT)], env=self.env)
        self.assertEqual(result.returncode, 2)
        self.assertIn('KEEP', result.stderr)

    def test_alert_hook_invoked_on_failure(self):
        hook = self.tmp / 'alert_hook.sh'
        hook_log = self.tmp / 'alert.log'
        _write_executable(
            hook,
            f"""#!/usr/bin/env bash
printf '%s %s %s %s %s\\n' "$1" "$2" "$3" "$4" "$5" >>'{hook_log}'
""",
        )
        _write_executable(
            self.bin_dir / 'pg_dump',
            """#!/usr/bin/env bash
echo "forced failure" >&2
exit 99
""",
        )
        env = self.env.copy()
        env['SIGEDON_BACKUP_ALERT_HOOK'] = str(hook)
        result = _run(['bash', str(RUNNER_SCRIPT)], env=env)
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(hook_log.is_file())
        line = hook_log.read_text(encoding='utf-8').strip()
        self.assertIn('scheduled_backup', line)
        self.assertIn('failure', line)
        self.assertNotIn('password', line.lower())

    def test_restore_drill_rejects_without_enable_flag(self):
        backup_dir = _make_valid_backup_dir(self.tmp / 'drill_src')
        env = self.env.copy()
        env.update(
            {
                'SIGEDON_RESTORE_DB': 'test_restore_isolated',
                'SIGEDON_RESTORE_MEDIA_ROOT': str(self.tmp / 'drill_media'),
                'SIGEDON_RESTORE_CONFIRM': 'YES',
                'POSTGRES_DB': 'source_db_sigedon',
            }
        )
        result = _run(['bash', str(DRILL_SCRIPT), str(backup_dir)], env=env)
        self.assertEqual(result.returncode, 4)
        self.assertIn('SIGEDON_RESTORE_DRILL_ENABLED', result.stderr)

    def test_restore_drill_rejects_active_database(self):
        backup_dir = _make_valid_backup_dir(self.tmp / 'drill_same')
        env = self.env.copy()
        env.update(
            {
                'SIGEDON_RESTORE_DRILL_ENABLED': 'YES',
                'SIGEDON_RESTORE_DB': 'source_db_sigedon',
                'SIGEDON_RESTORE_MEDIA_ROOT': str(self.tmp / 'drill_media_same'),
                'SIGEDON_RESTORE_CONFIRM': 'YES',
                'POSTGRES_DB': 'source_db_sigedon',
            }
        )
        result = _run(['bash', str(DRILL_SCRIPT), str(backup_dir)], env=env)
        self.assertEqual(result.returncode, 4)
        self.assertIn('base activa', result.stderr)

    def test_restore_drill_success_writes_marker(self):
        backup_dir = _make_valid_backup_dir(
            Path(self.env['SIGEDON_BACKUP_ROOT']),
            '20260802T010203Z',
        )
        env = self.env.copy()
        env.update(
            {
                'SIGEDON_RESTORE_DRILL_ENABLED': 'YES',
                'SIGEDON_RESTORE_DB': 'test_restore_isolated',
                'SIGEDON_RESTORE_MEDIA_ROOT': str(self.tmp / 'drill_media_ok'),
                'SIGEDON_RESTORE_CONFIRM': 'YES',
                'POSTGRES_DB': 'source_db_sigedon',
                'SIGEDON_RESTORE_DRILL_RUN_DJANGO': 'NO',
            }
        )
        result = _run(['bash', str(DRILL_SCRIPT), str(backup_dir)], env=env)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        status_path = Path(env['SIGEDON_BACKUP_ROOT']) / '.sigedon-restore-drill-status.json'
        status = json.loads(status_path.read_text(encoding='utf-8'))
        self.assertEqual(status['status'], 'success')
        self.assertEqual(status['job'], 'restore_drill')
        self.assertEqual(status['backup_id'], backup_dir.name)

    def test_examples_and_scripts_exist(self):
        examples = SCRIPTS_DIR / 'examples'
        for name in (
            'sigedon-backup.cron.example',
            'sigedon-backup.service.example',
            'sigedon-backup.timer.example',
            'sigedon-restore-drill.timer.example',
            'sigedon-backup-alert.hook.example',
            'sigedon-backup.env.example',
        ):
            self.assertTrue((examples / name).is_file(), msg=name)
        for script in (RUNNER_SCRIPT, RETENTION_SCRIPT, DRILL_SCRIPT, BACKUP_SCRIPT, VERIFY_SCRIPT, RESTORE_SCRIPT):
            self.assertTrue(script.is_file())
            mode = stat.S_IMODE(script.stat().st_mode)
            self.assertTrue(mode & stat.S_IXUSR, msg=script.name)


if __name__ == '__main__':
    unittest.main()
