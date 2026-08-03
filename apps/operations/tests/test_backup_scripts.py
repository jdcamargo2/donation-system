"""
Pruebas de los scripts Bash de backup/restore/verify con mocks en PATH.

PRE: no se conecta a PostgreSQL real ni se copia media/ del proyecto.
POST: cubre variables obligatorias, mantenimiento, checksums, corrupcion,
      cleanup de temporales, no sobrescritura, rechazo de restore inseguro
      y media destino no vacia.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = REPO_ROOT / 'deploy' / 'backups'
BACKUP_SCRIPT = SCRIPTS_DIR / 'backup_sigedon.sh'
VERIFY_SCRIPT = SCRIPTS_DIR / 'verify_backup.sh'
RESTORE_SCRIPT = SCRIPTS_DIR / 'restore_sigedon.sh'


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding='utf-8')
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _build_mock_bin(bin_dir: Path) -> None:
    """
    PRE: bin_dir exists and is empty enough to hold mock tools.
    POST: installs pg_dump/pg_restore/tar/sha256sum/psql/date mocks usable via PATH.
    """
    # pg_dump writes a non-empty custom-like blob when --file= is present.
    _write_executable(
        bin_dir / 'pg_dump',
        """#!/usr/bin/env bash
set -euo pipefail
out=""
version_only=0
for arg in "$@"; do
  case "$arg" in
    --version) version_only=1 ;;
    --file=*) out="${arg#--file=}" ;;
  esac
done
if [[ "$version_only" -eq 1 ]]; then
  echo "pg_dump (PostgreSQL) 16.14 mock"
  exit 0
fi
if [[ -z "$out" ]]; then
  echo "pg_dump mock: missing --file" >&2
  exit 1
fi
printf 'MOCKDUMP' >"$out"
# pad to look non-trivial
dd if=/dev/zero bs=1 count=64 >>"$out" 2>/dev/null || true
""",
    )
    _write_executable(
        bin_dir / 'pg_restore',
        """#!/usr/bin/env bash
set -euo pipefail
list_only=0
file=""
for arg in "$@"; do
  case "$arg" in
    --list) list_only=1 ;;
    --*) ;;
    *) file="$arg" ;;
  esac
done
if [[ "$list_only" -eq 1 ]]; then
  if [[ ! -s "${file:-}" ]]; then
    echo "pg_restore mock: empty dump" >&2
    exit 1
  fi
  # Detect intentional corruption marker.
  if grep -q 'CORRUPT' "$file" 2>/dev/null; then
    echo "pg_restore mock: corrupt" >&2
    exit 1
  fi
  echo "; Archive created at mock"
  exit 0
fi
# restore path used by restore_sigedon.sh
exit 0
""",
    )
    _write_executable(
        bin_dir / 'sha256sum',
        """#!/usr/bin/env bash
set -euo pipefail
# Prefer real sha256sum if available outside mock PATH parent.
if command -v /usr/bin/sha256sum >/dev/null 2>&1; then
  exec /usr/bin/sha256sum "$@"
fi
if command -v /bin/sha256sum >/dev/null 2>&1; then
  exec /bin/sha256sum "$@"
fi
python3 - <<'PY' "$@"
import hashlib, sys
for path in sys.argv[1:]:
    data = open(path, "rb").read()
    print(f"{hashlib.sha256(data).hexdigest()}  {path}")
PY
""",
    )
    # Real tar is required for correctness of archives; use system tar.
    system_tar = shutil.which('tar')
    if not system_tar:
        raise RuntimeError('system tar is required for backup script tests')
    os.symlink(system_tar, bin_dir / 'tar')

    _write_executable(
        bin_dir / 'psql',
        """#!/usr/bin/env bash
set -euo pipefail
# Record invocations; SELECT EXISTS returns f by default.
if printf '%s\n' "$*" | grep -q 'SELECT EXISTS'; then
  echo f
  exit 0
fi
exit 0
""",
    )
    _write_executable(
        bin_dir / 'mktemp',
        """#!/usr/bin/env bash
set -euo pipefail
# Delegate to system mktemp.
exec /usr/bin/mktemp "$@"
""",
    )


def _base_backup_env(tmp: Path, *, maintenance: str = 'YES') -> dict[str, str]:
    backup_root = tmp / 'backup_root'
    media_root = tmp / 'media_root'
    backup_root.mkdir(parents=True, exist_ok=True)
    media_root.mkdir(parents=True, exist_ok=True)
    (media_root / 'docs').mkdir(exist_ok=True)
    (media_root / 'docs' / 'sample.txt').write_text('hello', encoding='utf-8')
    (media_root / 'staticfiles').mkdir(exist_ok=True)
    (media_root / 'staticfiles' / 'should_skip.txt').write_text('nope', encoding='utf-8')
    env = os.environ.copy()
    env.update(
        {
            'SIGEDON_MAINTENANCE_CONFIRMED': maintenance,
            'SIGEDON_BACKUP_ROOT': str(backup_root),
            'SIGEDON_MEDIA_ROOT': str(media_root),
            'POSTGRES_DB': 'source_db_sigedon',
            'POSTGRES_USER': 'sigedon_owner',
            'POSTGRES_HOST': '127.0.0.1',
            'POSTGRES_PORT': '5432',
            'PGPASSWORD': '',  # empty; scripts must not require printing it
        }
    )
    return env


def _run(cmd, *, env, cwd=None):
    return subprocess.run(
        cmd,
        env=env,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


def _make_valid_backup_dir(parent: Path, backup_id: str = '20260714T120000Z') -> Path:
    backup_dir = parent / backup_id
    backup_dir.mkdir(parents=True)
    dump = backup_dir / 'database.dump'
    dump.write_bytes(b'MOCKDUMP' + b'\0' * 64)
    media = backup_dir / 'media.tar.gz'
    # Build a tiny tar.gz with one file using system tar.
    media_src = parent / 'media_src'
    media_src.mkdir(exist_ok=True)
    (media_src / 'a.txt').write_text('x', encoding='utf-8')
    subprocess.run(
        ['tar', '-C', str(media_src), '-czf', str(media), '.'],
        check=True,
        capture_output=True,
    )
    dump_sha = subprocess.check_output(['sha256sum', str(dump)], text=True).split()[0]
    media_sha = subprocess.check_output(['sha256sum', str(media)], text=True).split()[0]
    manifest = {
        'format_version': 1,
        'backup_id': backup_id,
        'created_at_utc': '2026-07-14T12:00:00Z',
        'database': {
            'filename': 'database.dump',
            'sha256': dump_sha,
            'size_bytes': dump.stat().st_size,
            'postgres_client_version': 'pg_dump (PostgreSQL) 16.14 mock',
        },
        'media': {
            'filename': 'media.tar.gz',
            'sha256': media_sha,
            'size_bytes': media.stat().st_size,
            'file_count': 1,
        },
        'application': {
            'git_commit': 'abc',
            'git_branch': 'feature/test',
            'django_version': '6.0.6',
            'python_version': '3.12.3',
        },
        'consistency': {
            'maintenance_confirmed': True,
            'strategy': 'maintenance_window',
        },
    }
    (backup_dir / 'manifest.json').write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + '\n',
        encoding='utf-8',
    )
    return backup_dir


class BackupScriptsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='sigedon-backup-tests-'))
        self.bin_dir = self.tmp / 'bin'
        self.bin_dir.mkdir()
        _build_mock_bin(self.bin_dir)
        self.env = _base_backup_env(self.tmp)
        # Prefer mocks, but keep system dirs for bash/python3/find/grep/date/git.
        self.env['PATH'] = f"{self.bin_dir}:{os.environ.get('PATH', '')}"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_backup_rejects_missing_required_variables(self):
        env = self.env.copy()
        del env['SIGEDON_BACKUP_ROOT']
        result = _run(['bash', str(BACKUP_SCRIPT)], env=env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('SIGEDON_BACKUP_ROOT', result.stderr)

    def test_backup_rejects_without_maintenance_confirmation(self):
        alt = self.tmp / 'maintenance_case'
        alt.mkdir()
        env = _base_backup_env(alt, maintenance='NO')
        env['PATH'] = self.env['PATH']
        result = _run(['bash', str(BACKUP_SCRIPT)], env=env)
        self.assertEqual(result.returncode, 4)
        self.assertIn('SIGEDON_MAINTENANCE_CONFIRMED', result.stderr)

    def test_backup_rejects_relative_media_root(self):
        env = self.env.copy()
        env['SIGEDON_MEDIA_ROOT'] = 'relative/media'
        result = _run(['bash', str(BACKUP_SCRIPT)], env=env)
        self.assertEqual(result.returncode, 3)
        self.assertIn('absoluta', result.stderr)

    def test_backup_rejects_filesystem_root_media(self):
        env = self.env.copy()
        env['SIGEDON_MEDIA_ROOT'] = '/'
        result = _run(['bash', str(BACKUP_SCRIPT)], env=env)
        self.assertEqual(result.returncode, 3)
        self.assertIn('raiz', result.stderr)

    def test_backup_creates_missing_backup_root_and_continues(self):
        alt = self.tmp / 'missing_root_case'
        alt.mkdir()
        env = _base_backup_env(alt)
        env['PATH'] = self.env['PATH']
        missing_root = alt / 'nested' / 'new_backups'
        self.assertFalse(missing_root.exists())
        env['SIGEDON_BACKUP_ROOT'] = str(missing_root)

        result = _run(['bash', str(BACKUP_SCRIPT)], env=env)

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertTrue(missing_root.is_dir())
        published = Path(result.stdout.strip())
        self.assertTrue(published.is_dir())
        self.assertEqual(published.parent, missing_root.resolve())

    def test_backup_root_created_with_restrictive_permissions(self):
        alt = self.tmp / 'perms_case'
        alt.mkdir()
        env = _base_backup_env(alt)
        env['PATH'] = self.env['PATH']
        missing_root = alt / 'secure_backups'
        env['SIGEDON_BACKUP_ROOT'] = str(missing_root)

        result = _run(['bash', str(BACKUP_SCRIPT)], env=env)

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        mode = stat.S_IMODE(missing_root.stat().st_mode)
        self.assertEqual(mode, 0o700)

    def test_backup_rejects_backup_root_that_is_a_file(self):
        alt = self.tmp / 'file_root_case'
        alt.mkdir()
        env = _base_backup_env(alt)
        env['PATH'] = self.env['PATH']
        file_path = alt / 'not_a_directory'
        file_path.write_text('x', encoding='utf-8')
        env['SIGEDON_BACKUP_ROOT'] = str(file_path)

        result = _run(['bash', str(BACKUP_SCRIPT)], env=env)

        self.assertEqual(result.returncode, 3)
        self.assertIn('no es un directorio', result.stderr)

    def test_backup_rejects_non_writable_backup_root(self):
        alt = self.tmp / 'ro_root_case'
        alt.mkdir()
        env = _base_backup_env(alt)
        env['PATH'] = self.env['PATH']
        ro_root = alt / 'readonly_backups'
        ro_root.mkdir(mode=0o500)
        env['SIGEDON_BACKUP_ROOT'] = str(ro_root)

        result = _run(['bash', str(BACKUP_SCRIPT)], env=env)

        self.assertEqual(result.returncode, 3)
        self.assertIn('no es escribible', result.stderr)
        # Restore perms so tearDown can delete.
        ro_root.chmod(0o700)

    def test_backup_cleans_temp_when_backup_root_was_auto_created(self):
        alt = self.tmp / 'auto_root_cleanup'
        alt.mkdir()
        env = _base_backup_env(alt)
        env['PATH'] = self.env['PATH']
        missing_root = alt / 'auto_created_backups'
        env['SIGEDON_BACKUP_ROOT'] = str(missing_root)
        _write_executable(
            self.bin_dir / 'pg_dump',
            """#!/usr/bin/env bash
echo "forced failure after root creation" >&2
exit 99
""",
        )

        result = _run(['bash', str(BACKUP_SCRIPT)], env=env)

        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(missing_root.is_dir())
        leftovers = [
            path
            for path in missing_root.glob('.sigedon-backup.*')
            if path.name != '.sigedon-ops.lock'
        ]
        self.assertEqual(leftovers, [])

    def test_backup_creates_manifest_and_artifacts(self):
        result = _run(['bash', str(BACKUP_SCRIPT)], env=self.env)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        published = Path(result.stdout.strip())
        self.assertTrue(published.is_dir())
        self.assertTrue((published / 'database.dump').is_file())
        self.assertTrue((published / 'media.tar.gz').is_file())
        manifest = json.loads((published / 'manifest.json').read_text(encoding='utf-8'))
        self.assertEqual(manifest['format_version'], 1)
        self.assertEqual(manifest['backup_id'], published.name)
        self.assertTrue(manifest['consistency']['maintenance_confirmed'])
        self.assertEqual(manifest['consistency']['strategy'], 'maintenance_window')
        self.assertNotIn('password', json.dumps(manifest).lower())
        # staticfiles must not appear in archive listing
        listing = subprocess.check_output(
            ['tar', '-tzf', str(published / 'media.tar.gz')],
            text=True,
        )
        self.assertNotIn('staticfiles', listing)
        self.assertIn('docs/sample.txt', listing)

    def test_backup_cleans_temp_on_failure_and_keeps_previous(self):
        # First successful backup
        first = _run(['bash', str(BACKUP_SCRIPT)], env=self.env)
        self.assertEqual(first.returncode, 0, msg=first.stderr)
        first_dir = Path(first.stdout.strip())
        self.assertTrue(first_dir.is_dir())

        # Break pg_dump for second attempt
        _write_executable(
            self.bin_dir / 'pg_dump',
            """#!/usr/bin/env bash
echo "forced failure" >&2
exit 99
""",
        )
        second = _run(['bash', str(BACKUP_SCRIPT)], env=self.env)
        self.assertNotEqual(second.returncode, 0)
        backup_root = Path(self.env['SIGEDON_BACKUP_ROOT'])
        leftovers = [
            path
            for path in backup_root.glob('.sigedon-backup.*')
            if path.name != '.sigedon-ops.lock'
        ]
        self.assertEqual(leftovers, [])
        self.assertTrue(first_dir.is_dir())

    def test_backup_does_not_overwrite_existing_id(self):
        # Freeze date so backup_id collides.
        _write_executable(
            self.bin_dir / 'date',
            """#!/usr/bin/env bash
if [[ "$*" == *'%Y%m%dT%H%M%SZ'* ]]; then
  echo '20260714T999999Z'
  exit 0
fi
exec /usr/bin/date "$@"
""",
        )
        first = _run(['bash', str(BACKUP_SCRIPT)], env=self.env)
        self.assertEqual(first.returncode, 0, msg=first.stderr)
        second = _run(['bash', str(BACKUP_SCRIPT)], env=self.env)
        self.assertEqual(second.returncode, 5)
        self.assertIn('no se sobrescribe', second.stderr)

    def test_verify_accepts_valid_manifest(self):
        backup_dir = _make_valid_backup_dir(self.tmp / 'verified')
        result = _run(['bash', str(VERIFY_SCRIPT), str(backup_dir)], env=self.env)
        self.assertEqual(result.returncode, 0, msg=result.stderr)

    def test_verify_detects_checksum_mismatch(self):
        backup_dir = _make_valid_backup_dir(self.tmp / 'badsum')
        dump = backup_dir / 'database.dump'
        dump.write_bytes(dump.read_bytes() + b'X')
        result = _run(['bash', str(VERIFY_SCRIPT), str(backup_dir)], env=self.env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('checksum', result.stderr.lower())

    def test_verify_detects_corrupt_dump(self):
        backup_dir = _make_valid_backup_dir(self.tmp / 'corrupt')
        (backup_dir / 'database.dump').write_bytes(b'CORRUPT')
        # Fix manifest sizes/checksums to force pg_restore failure path after checksum update
        dump = backup_dir / 'database.dump'
        media = backup_dir / 'media.tar.gz'
        dump_sha = subprocess.check_output(['sha256sum', str(dump)], text=True).split()[0]
        media_sha = subprocess.check_output(['sha256sum', str(media)], text=True).split()[0]
        manifest = json.loads((backup_dir / 'manifest.json').read_text(encoding='utf-8'))
        manifest['database']['sha256'] = dump_sha
        manifest['database']['size_bytes'] = dump.stat().st_size
        manifest['media']['sha256'] = media_sha
        manifest['media']['size_bytes'] = media.stat().st_size
        (backup_dir / 'manifest.json').write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + '\n',
            encoding='utf-8',
        )
        result = _run(['bash', str(VERIFY_SCRIPT), str(backup_dir)], env=self.env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('corrupt', result.stderr.lower())

    def test_verify_detects_incomplete_manifest(self):
        backup_dir = _make_valid_backup_dir(self.tmp / 'incomplete')
        (backup_dir / 'manifest.json').write_text('{"format_version": 1}\n', encoding='utf-8')
        result = _run(['bash', str(VERIFY_SCRIPT), str(backup_dir)], env=self.env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('incompleto', result.stderr.lower())

    def test_verify_detects_missing_file(self):
        backup_dir = _make_valid_backup_dir(self.tmp / 'missing')
        (backup_dir / 'media.tar.gz').unlink()
        result = _run(['bash', str(VERIFY_SCRIPT), str(backup_dir)], env=self.env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('media.tar.gz', result.stderr)

    def test_restore_rejects_same_database_as_active(self):
        backup_dir = _make_valid_backup_dir(self.tmp / 'restore_same')
        env = self.env.copy()
        env.update(
            {
                'SIGEDON_RESTORE_DB': 'source_db_sigedon',
                'SIGEDON_RESTORE_MEDIA_ROOT': str(self.tmp / 'restore_media'),
                'SIGEDON_RESTORE_CONFIRM': 'YES',
                'POSTGRES_DB': 'source_db_sigedon',
            }
        )
        result = _run(['bash', str(RESTORE_SCRIPT), str(backup_dir)], env=env)
        self.assertEqual(result.returncode, 4)
        self.assertIn('coincide', result.stderr)

    def test_restore_rejects_non_empty_media_destination(self):
        backup_dir = _make_valid_backup_dir(self.tmp / 'restore_media_full')
        dest = self.tmp / 'restore_media_full_dest'
        dest.mkdir()
        (dest / 'existing.txt').write_text('keep', encoding='utf-8')
        env = self.env.copy()
        env.update(
            {
                'SIGEDON_RESTORE_DB': 'test_restore_isolated',
                'SIGEDON_RESTORE_MEDIA_ROOT': str(dest),
                'SIGEDON_RESTORE_CONFIRM': 'YES',
                'POSTGRES_DB': 'source_db_sigedon',
            }
        )
        result = _run(['bash', str(RESTORE_SCRIPT), str(backup_dir)], env=env)
        self.assertEqual(result.returncode, 4)
        self.assertIn('no esta vacio', result.stderr)

    def test_restore_rejects_unsafe_prefix(self):
        backup_dir = _make_valid_backup_dir(self.tmp / 'restore_prefix')
        env = self.env.copy()
        env.update(
            {
                'SIGEDON_RESTORE_DB': 'db_sigedon',
                'SIGEDON_RESTORE_MEDIA_ROOT': str(self.tmp / 'restore_media_prefix'),
                'SIGEDON_RESTORE_CONFIRM': 'YES',
                'POSTGRES_DB': 'source_db_sigedon',
            }
        )
        result = _run(['bash', str(RESTORE_SCRIPT), str(backup_dir)], env=env)
        self.assertEqual(result.returncode, 4)
        self.assertIn('prefijo seguro', result.stderr)


if __name__ == '__main__':
    unittest.main()
