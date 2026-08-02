"""
Focused tests for SIGEDON production Gunicorn startup and preflight contract.

PRE: does not bind ports, start Gunicorn as a server, apply migrations,
     run collectstatic against production paths, or sync roles.
POST: covers runtime_config parsers, gunicorn.conf.py shape, start_web.sh,
      preflight.sh architecture, and verify_deployment_assets.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings


REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_DIR = REPO_ROOT / 'deploy'
GUNICORN_CONF = DEPLOY_DIR / 'gunicorn.conf.py'
RUNTIME_CONFIG = DEPLOY_DIR / 'runtime_config.py'
START_WEB = DEPLOY_DIR / 'start_web.sh'
PREFLIGHT = DEPLOY_DIR / 'preflight.sh'


def _load_runtime_config():
    spec = importlib.util.spec_from_file_location(
        'sigedon_runtime_config',
        RUNTIME_CONFIG,
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _load_gunicorn_conf(environ: dict[str, str]):
    """
    PRE: environ contains only the Gunicorn-related keys under test.
    POST: returns a freshly loaded gunicorn.conf module reflecting environ.
    """
    # Isolate from the developer machine environment for deterministic defaults.
    clean = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith('GUNICORN_') and key != 'PORT'
    }
    clean.update(environ)
    with mock.patch.dict(os.environ, clean, clear=True):
        spec = importlib.util.spec_from_file_location(
            'sigedon_gunicorn_conf',
            GUNICORN_CONF,
        )
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        return module


def _read(path: Path) -> str:
    return path.read_text(encoding='utf-8')


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding='utf-8')
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


class RuntimeConfigParserTests(unittest.TestCase):
    def setUp(self):
        self.rt = _load_runtime_config()

    def test_default_bind_uses_port_8000(self):
        self.assertEqual(self.rt.resolve_bind(), '0.0.0.0:8000')

    def test_port_overrides_default_bind(self):
        self.assertEqual(
            self.rt.resolve_bind(port_raw='9000'),
            '0.0.0.0:9000',
        )

    def test_explicit_bind_overrides_port(self):
        self.assertEqual(
            self.rt.resolve_bind(bind_raw='127.0.0.1:8080', port_raw='9000'),
            '127.0.0.1:8080',
        )

    def test_workers_parse_positive_integer(self):
        self.assertEqual(
            self.rt.parse_positive_int('GUNICORN_WORKERS', '3', 2),
            3,
        )

    def test_zero_workers_rejected(self):
        with self.assertRaises(self.rt.RuntimeConfigError):
            self.rt.parse_positive_int('GUNICORN_WORKERS', '0', 2)

    def test_negative_workers_rejected(self):
        with self.assertRaises(self.rt.RuntimeConfigError):
            self.rt.parse_positive_int('GUNICORN_WORKERS', '-1', 2)

    def test_invalid_integer_fails_clearly(self):
        with self.assertRaises(self.rt.RuntimeConfigError) as ctx:
            self.rt.parse_positive_int('GUNICORN_TIMEOUT', 'abc', 60)
        self.assertIn('GUNICORN_TIMEOUT', str(ctx.exception))
        self.assertNotIn('abc', str(ctx.exception))

    def test_threads_and_timeouts_parse(self):
        settings = self.rt.load_from_environ(
            {
                'GUNICORN_THREADS': '2',
                'GUNICORN_TIMEOUT': '45',
                'GUNICORN_GRACEFUL_TIMEOUT': '15',
                'GUNICORN_KEEPALIVE': '3',
            }
        )
        self.assertEqual(settings['threads'], 2)
        self.assertEqual(settings['timeout'], 45)
        self.assertEqual(settings['graceful_timeout'], 15)
        self.assertEqual(settings['keepalive'], 3)
        self.assertEqual(settings['potential_db_connections'], 2 * 2)

    def test_load_defaults_are_conservative(self):
        settings = self.rt.load_from_environ({})
        self.assertEqual(settings['workers'], 2)
        self.assertEqual(settings['threads'], 1)
        self.assertEqual(settings['worker_class'], 'sync')
        self.assertEqual(settings['timeout'], 60)
        self.assertEqual(settings['graceful_timeout'], 30)
        self.assertEqual(settings['keepalive'], 5)
        self.assertFalse(settings['daemon'])
        self.assertFalse(settings['reload'])
        self.assertFalse(settings['preload_app'])
        self.assertEqual(settings['accesslog'], '-')
        self.assertEqual(settings['errorlog'], '-')


class GunicornConfModuleTests(unittest.TestCase):
    def test_config_imports_with_defaults(self):
        conf = _load_gunicorn_conf({})
        self.assertEqual(conf.bind, '0.0.0.0:8000')
        self.assertEqual(conf.workers, 2)
        self.assertEqual(conf.threads, 1)
        self.assertEqual(conf.worker_class, 'sync')
        self.assertFalse(conf.daemon)
        self.assertFalse(conf.reload)
        self.assertFalse(conf.preload_app)
        self.assertEqual(conf.accesslog, '-')
        self.assertEqual(conf.errorlog, '-')
        self.assertTrue(conf.capture_output)
        self.assertIsNone(conf.pidfile)

    def test_port_and_bind_overrides(self):
        conf_port = _load_gunicorn_conf({'PORT': '8123'})
        self.assertEqual(conf_port.bind, '0.0.0.0:8123')
        conf_bind = _load_gunicorn_conf(
            {'PORT': '8123', 'GUNICORN_BIND': '127.0.0.1:7999'}
        )
        self.assertEqual(conf_bind.bind, '127.0.0.1:7999')

    def test_invalid_workers_fail_on_import(self):
        with self.assertRaises(Exception):
            _load_gunicorn_conf({'GUNICORN_WORKERS': '0'})

    def test_config_source_has_no_secret_literals(self):
        text = _read(GUNICORN_CONF) + _read(RUNTIME_CONFIG)
        for needle in ('PASSWORD', 'SECRET_KEY', 'TOKEN', 'API_TOKEN'):
            self.assertNotIn(needle, text)


class StartWebWrapperTests(unittest.TestCase):
    def test_wrapper_architecture(self):
        text = _read(START_WEB)
        self.assertIn('set -Eeuo pipefail', text)
        self.assertIn('exec gunicorn', text)
        self.assertIn('core.wsgi:application', text)
        self.assertIn('deploy/gunicorn.conf.py', text)
        code_lines = [
            line for line in text.splitlines()
            if line.strip() and not line.lstrip().startswith('#')
        ]
        code = '\n'.join(code_lines)
        self.assertNotIn('runserver', code)
        self.assertNotIn('migrate', code)
        self.assertNotIn('collectstatic', code)
        self.assertNotIn('sync_sigedon_roles', code)
        self.assertNotIn('&', code.replace('&&', ''))
        self.assertTrue(START_WEB.stat().st_mode & stat.S_IXUSR)

    def test_wrapper_preserves_arguments_and_exit_code(self):
        with tempfile.TemporaryDirectory(prefix='sigedon-start-web-') as tmp:
            bin_dir = Path(tmp) / 'bin'
            bin_dir.mkdir()
            log_path = Path(tmp) / 'gunicorn.log'
            _write_executable(
                bin_dir / 'gunicorn',
                f"""#!/usr/bin/env bash
set -Eeuo pipefail
printf '%s\\n' "$*" >"{log_path}"
exit 42
""",
            )
            env = os.environ.copy()
            env['PATH'] = f'{bin_dir}:{env.get("PATH", "")}'
            result = subprocess.run(
                ['bash', str(START_WEB), '--check-config'],
                cwd=str(REPO_ROOT),
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 42)
            logged = log_path.read_text(encoding='utf-8')
            self.assertIn('core.wsgi:application', logged)
            self.assertIn('--config', logged)
            self.assertIn('deploy/gunicorn.conf.py', logged)
            self.assertIn('--check-config', logged)


class PreflightScriptTests(unittest.TestCase):
    def test_preflight_architecture(self):
        text = _read(PREFLIGHT)
        self.assertIn('set -Eeuo pipefail', text)
        self.assertIn('check --deploy', text)
        self.assertIn('makemigrations --check --dry-run', text)
        self.assertIn('migrate --check', text)
        self.assertIn('verify_deployment_assets', text)
        self.assertIn('PYTHON_BIN', text)
        code_lines = [
            line for line in text.splitlines()
            if line.strip() and not line.lstrip().startswith('#')
        ]
        code = '\n'.join(code_lines)
        self.assertNotIn('collectstatic', code)
        self.assertNotIn('sync_sigedon_roles', code)
        self.assertNotIn('gunicorn', code.lower())
        self.assertNotIn('exec ', code)
        # Must not apply migrations: only migrate --check / optional --plan.
        for line in code_lines:
            stripped = line.strip()
            if 'manage.py migrate' in stripped:
                self.assertTrue(
                    'migrate --check' in stripped or 'migrate --plan' in stripped,
                    msg=f'unexpected migrate invocation: {stripped}',
                )
        self.assertTrue(PREFLIGHT.stat().st_mode & stat.S_IXUSR)

    def test_preflight_mocked_success_and_failure_order(self):
        with tempfile.TemporaryDirectory(prefix='sigedon-preflight-ok-') as tmp_raw:
            tmp = Path(tmp_raw)
            # Isolated tree mimicking <root>/deploy/preflight.sh and <root>/manage.py.
            deploy = tmp / 'deploy'
            deploy.mkdir()
            script = deploy / 'preflight.sh'
            shutil.copy2(PREFLIGHT, script)
            log_path = tmp / 'calls.log'
            manage = tmp / 'manage.py'
            manage.write_text(
                f"""#!/usr/bin/env python3
import sys
from pathlib import Path
log = Path(r"{log_path}")
prev = log.read_text() if log.exists() else ""
log.write_text(prev + " ".join(sys.argv[1:]) + "\\n")
args = " ".join(sys.argv[1:])
if args == "check --deploy":
    raise SystemExit(0)
if args == "makemigrations --check --dry-run":
    raise SystemExit(0)
if args == "migrate --check":
    raise SystemExit(0)
if args == "migrate --plan":
    raise SystemExit(0)
if args == "verify_deployment_assets":
    raise SystemExit(0)
raise SystemExit(1)
""",
                encoding='utf-8',
            )
            manage.chmod(manage.stat().st_mode | stat.S_IXUSR)
            env = os.environ.copy()
            env['PYTHON_BIN'] = _sys_executable()
            env['SIGEDON_PREFLIGHT_SHOW_MIGRATE_PLAN'] = 'YES'
            env['DJANGO_SECRET_KEY'] = 'super-secret-value-do-not-echo'
            result = subprocess.run(
                ['bash', str(script)],
                cwd=str(tmp),
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
            calls = log_path.read_text(encoding='utf-8').strip().splitlines()
            self.assertEqual(
                calls,
                [
                    'check --deploy',
                    'makemigrations --check --dry-run',
                    'migrate --check',
                    'migrate --plan',
                    'verify_deployment_assets',
                ],
            )
            combined = result.stdout + result.stderr
            self.assertNotIn('super-secret-value-do-not-echo', combined)

            log_path.write_text('', encoding='utf-8')
            manage.write_text(
                f"""#!/usr/bin/env python3
import sys
from pathlib import Path
log = Path(r"{log_path}")
prev = log.read_text() if log.exists() else ""
log.write_text(prev + " ".join(sys.argv[1:]) + "\\n")
args = " ".join(sys.argv[1:])
if args == "check --deploy":
    raise SystemExit(9)
raise SystemExit(0)
""",
                encoding='utf-8',
            )
            manage.chmod(manage.stat().st_mode | stat.S_IXUSR)
            failed = subprocess.run(
                ['bash', str(script)],
                cwd=str(tmp),
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(failed.returncode, 9)
            calls = log_path.read_text(encoding='utf-8').strip().splitlines()
            self.assertEqual(calls, ['check --deploy'])


def _sys_executable() -> str:
    return shutil.which('python3') or 'python3'


class VerifyDeploymentAssetsCommandTests(SimpleTestCase):
    def test_missing_static_root_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / 'staticfiles-missing'
            with override_settings(STATIC_ROOT=str(missing)):
                with self.assertRaises(CommandError):
                    call_command('verify_deployment_assets')

    def test_sentinels_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'staticfiles'
            root.mkdir()
            with override_settings(STATIC_ROOT=str(root)):
                with self.assertRaises(CommandError) as ctx:
                    call_command('verify_deployment_assets')
            self.assertIn('missing', str(ctx.exception).lower())

    def test_complete_sentinels_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'staticfiles'
            assets = [
                root / 'web' / 'css' / 'sigedon.css',
                root / 'web' / 'img' / 'logo_ilde.png',
                root / 'web' / 'img' / 'logo_ilde_short.png',
            ]
            for path in assets:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'x')
            with override_settings(STATIC_ROOT=str(root)):
                call_command('verify_deployment_assets')
