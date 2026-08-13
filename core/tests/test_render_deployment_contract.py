"""Contract tests for SIGEDON Render native-Python deployment scripts/runbooks.

PRE: offline only; does not provision Render/Cloudflare, open ports, apply
     production migrations, or print secrets.
POST: asserts build/pre-deploy/post-deploy/start contracts and runbook markers.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
RENDER_DIR = REPO_ROOT / 'deploy' / 'render'
BUILD = RENDER_DIR / 'build.sh'
PRE_DEPLOY = RENDER_DIR / 'pre_deploy.sh'
POST_DEPLOY = RENDER_DIR / 'post_deploy_verify.sh'
START_WEB = REPO_ROOT / 'deploy' / 'start_web.sh'
PYTHON_VERSION = REPO_ROOT / '.python-version'

RUNBOOKS = {
    'first': REPO_ROOT / 'docs' / 'runbooks' / 'RENDER_FIRST_DEPLOY.md',
    'env': REPO_ROOT / 'docs' / 'runbooks' / 'RENDER_ENVIRONMENT.md',
    'staging': REPO_ROOT / 'docs' / 'runbooks' / 'STAGING_ACCEPTANCE.md',
    'gng': REPO_ROOT / 'docs' / 'runbooks' / 'PRODUCTION_GO_NO_GO.md',
    'r2': REPO_ROOT / 'docs' / 'runbooks' / 'CLOUDFLARE_R2.md',
    'render_readme': RENDER_DIR / 'README.md',
}


def _read(path: Path) -> str:
    return path.read_text(encoding='utf-8')


def _code_only(text: str) -> str:
    lines = [
        line
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith('#')
    ]
    return '\n'.join(lines)


def _sys_executable() -> str:
    return shutil.which('python3') or 'python3'


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding='utf-8')
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


class RenderScriptArchitectureTests(unittest.TestCase):
    def test_scripts_exist_and_are_executable(self):
        for path in (BUILD, PRE_DEPLOY, POST_DEPLOY, START_WEB):
            self.assertTrue(path.is_file(), msg=path)
            self.assertTrue(path.stat().st_mode & stat.S_IXUSR, msg=path)

    def test_strict_bash_mode_and_safety(self):
        for path in (BUILD, PRE_DEPLOY, POST_DEPLOY):
            text = _read(path)
            self.assertTrue(text.startswith('#!/usr/bin/env bash'))
            self.assertIn('set -Eeuo pipefail', text)
            code = _code_only(text)
            self.assertNotIn('|| true', code)
            self.assertNotIn('eval ', code)
            self.assertNotIn('docker', code.lower())
            # No credential dumps / echo of secrets (env var *names* may appear).
            self.assertNotIn('printenv', code)
            self.assertNotIn('env |', code)
            self.assertNotRegex(code, r'echo\s+"\$\{?(POSTGRES_PASSWORD|DJANGO_SECRET_KEY|R2_SECRET)')
            self.assertNotRegex(
                text,
                r'(sk_live_|AKIA[0-9A-Z]{16}|password123|BEGIN RSA PRIVATE KEY)',
            )

    def test_build_runs_collectstatic_and_asset_verifier(self):
        text = _read(BUILD)
        self.assertIn('collectstatic --noinput', text)
        self.assertIn('verify_deployment_assets', text)
        self.assertIn('pip check', text)
        code = _code_only(text)
        self.assertNotIn('migrate', code)
        self.assertNotIn('gunicorn', code.lower())
        self.assertNotIn('backup', code.lower())
        self.assertNotIn('restore', code.lower())
        self.assertIn('SIGEDON_PRIVATE_STORAGE=filesystem', code)

    def test_pre_deploy_schema_gates_and_no_server(self):
        text = _read(PRE_DEPLOY)
        for needle in (
            'check --deploy',
            'makemigrations --check --dry-run',
            'migrate --plan',
            'migrate --noinput',
            'migrate --check',
            'sync_sigedon_roles',
            'reconcile_operational_code_sequences',
            './deploy/preflight.sh',
        ):
            self.assertIn(needle, text)
        code = _code_only(text)
        self.assertNotIn('gunicorn', code.lower())
        self.assertNotIn('start_web', code)
        self.assertNotIn('verify_postgres_security', code)
        self.assertNotIn('verify_private_storage --probe', code)
        self.assertNotIn('seed_sigedon_demo', code)
        self.assertNotIn('backup_sigedon', code)

    def test_post_deploy_runtime_verification(self):
        text = _read(POST_DEPLOY)
        for needle in (
            'check --deploy',
            'verify_postgres_security',
            'verify_private_storage --configuration-only',
            'verify_deployment_assets',
            'verify_render_configuration',
            '--probe-private-storage',
        ):
            self.assertIn(needle, text)
        self.assertIn('skipping R2/storage probe', text)
        code = _code_only(text)
        self.assertNotIn('migrate --noinput', code)
        self.assertNotIn('sync_sigedon_roles', code)
        self.assertNotIn('seed_sigedon_demo', code)
        self.assertNotIn('backup_sigedon', code)

    def test_start_command_remains_existing_gunicorn_script(self):
        text = _read(START_WEB)
        self.assertIn('exec gunicorn', text)
        self.assertIn('deploy/gunicorn.conf.py', text)
        self.assertNotEqual(START_WEB.resolve(), BUILD.resolve())

    def test_python_version_declares_312(self):
        self.assertTrue(PYTHON_VERSION.is_file())
        self.assertEqual(_read(PYTHON_VERSION).strip(), '3.12')

    def test_no_render_yaml_blueprint(self):
        self.assertFalse((REPO_ROOT / 'render.yaml').exists())
        self.assertFalse((REPO_ROOT / 'render.yml').exists())


class RenderPostDeployProbeFlagTests(unittest.TestCase):
    def test_probe_requires_explicit_flag(self):
        with tempfile.TemporaryDirectory(prefix='sigedon-post-deploy-') as tmp_raw:
            tmp = Path(tmp_raw)
            deploy = tmp / 'deploy' / 'render'
            deploy.mkdir(parents=True)
            script = deploy / 'post_deploy_verify.sh'
            shutil.copy2(POST_DEPLOY, script)
            log_path = tmp / 'calls.log'
            manage = tmp / 'manage.py'
            manage.write_text(
                f"""#!/usr/bin/env python3
import sys
from pathlib import Path
log = Path(r"{log_path}")
prev = log.read_text() if log.exists() else ""
log.write_text(prev + " ".join(sys.argv[1:]) + "\\n")
raise SystemExit(0)
""",
                encoding='utf-8',
            )
            manage.chmod(manage.stat().st_mode | stat.S_IXUSR)
            # preflight path referenced only by pre_deploy; post_deploy needs repo layout
            (tmp / 'deploy').mkdir(exist_ok=True)
            env = os.environ.copy()
            env['PYTHON_BIN'] = _sys_executable()
            env['DJANGO_SECRET_KEY'] = 'super-secret-value-do-not-echo'
            # Script cds to repo root via BASH_SOURCE; point at isolated tree by
            # adjusting: copy expects ../../ from deploy/render → tmp.
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
            self.assertIn('verify_private_storage --configuration-only', calls)
            self.assertNotIn('verify_private_storage --probe', calls)
            combined = result.stdout + result.stderr
            self.assertNotIn('super-secret-value-do-not-echo', combined)

            log_path.write_text('', encoding='utf-8')
            probed = subprocess.run(
                ['bash', str(script), '--probe-private-storage'],
                cwd=str(tmp),
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(probed.returncode, 0, msg=probed.stderr + probed.stdout)
            calls = log_path.read_text(encoding='utf-8').strip().splitlines()
            self.assertIn('verify_private_storage --probe', calls)


class RenderPreDeployMockOrderTests(unittest.TestCase):
    def test_pre_deploy_mocked_order_without_server(self):
        with tempfile.TemporaryDirectory(prefix='sigedon-pre-deploy-') as tmp_raw:
            tmp = Path(tmp_raw)
            render = tmp / 'deploy' / 'render'
            render.mkdir(parents=True)
            script = render / 'pre_deploy.sh'
            shutil.copy2(PRE_DEPLOY, script)
            preflight = tmp / 'deploy' / 'preflight.sh'
            _write_executable(
                preflight,
                """#!/usr/bin/env bash
set -Eeuo pipefail
echo preflight
""",
            )
            log_path = tmp / 'calls.log'
            manage = tmp / 'manage.py'
            manage.write_text(
                f"""#!/usr/bin/env python3
import sys
from pathlib import Path
log = Path(r"{log_path}")
prev = log.read_text() if log.exists() else ""
log.write_text(prev + " ".join(sys.argv[1:]) + "\\n")
raise SystemExit(0)
""",
                encoding='utf-8',
            )
            manage.chmod(manage.stat().st_mode | stat.S_IXUSR)
            env = os.environ.copy()
            env['PYTHON_BIN'] = _sys_executable()
            env['DJANGO_SECRET_KEY'] = 'owner-migrator-secret-do-not-echo'
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
                    'migrate --plan',
                    'migrate --noinput',
                    'migrate --check',
                    'sync_sigedon_roles',
                    'reconcile_operational_code_sequences',
                ],
            )
            self.assertIn('preflight', result.stdout)
            self.assertNotIn('owner-migrator-secret-do-not-echo', result.stdout)
            self.assertNotIn('owner-migrator-secret-do-not-echo', result.stderr)
            self.assertNotIn('gunicorn', result.stdout.lower())


class RenderEnvironmentRegistryTests(unittest.TestCase):
    def test_registry_documents_consumed_variables(self):
        from core.render_configuration import (
            OBSOLETE_GENERIC_ALIASES,
            RENDER_DOCUMENTED_VARIABLES,
        )

        text = _read(RUNBOOKS['env'])
        for name in sorted(RENDER_DOCUMENTED_VARIABLES):
            self.assertIn(name, text, msg=name)
        for alias in sorted(OBSOLETE_GENERIC_ALIASES):
            # Documented as forbidden, not as supported configuration.
            self.assertTrue(
                re.search(rf'`{re.escape(alias)}`', text),
                msg=f'alias mention missing: {alias}',
            )
        self.assertIn('DATABASE_URL', text)
        self.assertIn('not supported', text.lower())
        self.assertIn('Build vs runtime', text)
        self.assertNotIn('Dockerfile', text)
        # No real-looking credential material.
        self.assertNotRegex(text, r'sk_live_|AKIA[0-9A-Z]{16}')
        self.assertNotIn('password123', text.lower())


class RenderRunbookContractTests(unittest.TestCase):
    def test_runbooks_include_required_markers(self):
        first = _read(RUNBOOKS['first'])
        staging = _read(RUNBOOKS['staging'])
        gng = _read(RUNBOOKS['gng'])
        readme = _read(RUNBOOKS['render_readme'])
        combined = '\n'.join([first, staging, gng, readme])

        for needle in (
            'onrender.com',
            'verify_postgres_security',
            'verify_private_storage --probe',
            'backup',
            'restore',
            'Administrador SIGEDON',
            'Operador de campo',
            'Auditor externo',
            'Comité de proyectos',
            'DNS-only',
            'Full (strict)',
            'rollback',
            '/readyz/',
            './deploy/start_web.sh',
            'NO-GO',
            'go/no-go',
        ):
            self.assertIn(needle, combined, msg=needle)

        self.assertNotIn('Dockerfile', first)
        self.assertIn('No Docker', first)
        self.assertIn('production traffic before go/no-go', gng.lower())
