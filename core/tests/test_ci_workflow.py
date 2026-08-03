"""Contract tests for the GitHub Actions CI workflow (OPS-CI-GATES).

PRE: .github/workflows/ci.yml is present in the repository.
POST: asserts structural CI gates without brittle whitespace coupling.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from django.test import SimpleTestCase


REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = REPO_ROOT / '.github' / 'workflows' / 'ci.yml'


def _workflow_on(data: dict) -> dict:
    """PyYAML 1.1 may parse the key ``on`` as boolean ``True``."""
    value = data.get('on', data.get(True))
    if value is None:
        return {}
    return value


class CiWorkflowContractTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.raw = WORKFLOW_PATH.read_text(encoding='utf-8')
        cls.data = yaml.safe_load(cls.raw)
        cls.on = _workflow_on(cls.data)

    def test_workflow_file_exists(self):
        self.assertTrue(WORKFLOW_PATH.is_file())

    def test_triggers_include_pr_push_and_dispatch(self):
        on = self.on
        self.assertIn('pull_request', on)
        self.assertIn('push', on)
        self.assertIn('workflow_dispatch', on)
        self.assertIn('main', on['push']['branches'])

    def test_contents_permission_is_read_only(self):
        self.assertEqual(self.data['permissions']['contents'], 'read')
        # No broad write grants at workflow level.
        for key, value in self.data['permissions'].items():
            self.assertNotEqual(value, 'write', msg=key)

    def test_no_pull_request_target(self):
        self.assertNotIn('pull_request_target', self.on)
        self.assertNotIn('pull_request_target', self.raw)

    def test_concurrency_cancels_obsolete_runs(self):
        concurrency = self.data['concurrency']
        self.assertTrue(concurrency['cancel-in-progress'])
        self.assertIn('github.ref', concurrency['group'])

    def test_python_312_configured(self):
        self.assertIn('3.12', self.raw)
        for job in self.data['jobs'].values():
            steps = job.get('steps', [])
            setup = [
                s for s in steps
                if s.get('uses', '').startswith('actions/setup-python@')
            ]
            self.assertTrue(setup, msg=job.get('name'))
            self.assertEqual(setup[0]['with']['python-version'], '3.12')

    def test_postgresql_16_service_on_integration_jobs(self):
        for job_id in ('postgres-migrations', 'critical-tests', 'full-suite'):
            job = self.data['jobs'][job_id]
            postgres = job['services']['postgres']
            self.assertEqual(postgres['image'], 'postgres:16')
            self.assertIn('pg_isready', postgres['options'])
            self.assertEqual(job['env']['DATABASE_ENGINE'], 'postgresql')
            self.assertNotIn('sqlite', job['env']['DATABASE_ENGINE'])

    def test_static_job_runs_required_checks(self):
        job = self.data['jobs']['static']
        joined = '\n'.join(
            step.get('run', '') for step in job['steps'] if 'run' in step
        )
        self.assertIn('run_static_checks.sh', joined)
        # Script contract covers git diff, hygiene, pip check, bash -n, check,
        # makemigrations --check.
        script = (REPO_ROOT / 'deploy' / 'ci' / 'run_static_checks.sh').read_text(
            encoding='utf-8'
        )
        for needle in (
            'git diff --check',
            'check_repository_hygiene.sh',
            'pip check',
            'bash -n',
            'manage.py check',
            'makemigrations --check --dry-run',
        ):
            self.assertIn(needle, script)

    def test_migration_job_runs_from_zero_and_artifacts(self):
        job = self.data['jobs']['postgres-migrations']
        joined = '\n'.join(
            step.get('run', '') for step in job['steps'] if 'run' in step
        )
        self.assertIn('run_migration_gates.sh', joined)
        script = (REPO_ROOT / 'deploy' / 'ci' / 'run_migration_gates.sh').read_text(
            encoding='utf-8'
        )
        for needle in (
            'migrate --plan',
            'migrate --noinput',
            'migrate --check',
            'check --deploy',
            'collectstatic',
            'verify_deployment_assets',
            'reconcile_operational_code_sequences',
            'core.ci_settings',
            'SIGEDON_CI_STATIC_ROOT',
        ):
            self.assertIn(needle, script)
        # Owner-role verify_postgres_security is intentionally not a CI gate.
        self.assertNotIn('verify_postgres_security\n', script)
        self.assertNotIn('manage.py verify_postgres_security', script)
        # WhiteNoise is a runtime dependency; CI collectstatic uses ci_settings
        # which forces CompressedManifestStaticFilesStorage.
        requirements = (REPO_ROOT / 'requirements.txt').read_text(encoding='utf-8')
        self.assertIn('whitenoise==', requirements)
        ci_settings = (REPO_ROOT / 'core' / 'ci_settings.py').read_text(
            encoding='utf-8'
        )
        self.assertIn(
            'whitenoise.storage.CompressedManifestStaticFilesStorage',
            ci_settings,
        )
        # RENDER-2: private media may use django-storages/boto3; static stays WhiteNoise.
        self.assertIn('django-storages', requirements)
        self.assertIn('boto3==', requirements)
        self.assertIn('whitenoise==', requirements)

    def test_critical_and_full_suite_jobs_exist(self):
        self.assertIn('critical-tests', self.data['jobs'])
        self.assertIn('full-suite', self.data['jobs'])
        critical_run = '\n'.join(
            s.get('run', '')
            for s in self.data['jobs']['critical-tests']['steps']
            if 'run' in s
        )
        self.assertIn('run_critical_tests.sh', critical_run)
        full_run = '\n'.join(
            s.get('run', '')
            for s in self.data['jobs']['full-suite']['steps']
            if 'run' in s
        )
        self.assertIn('manage.py test --noinput', full_run)

    def test_full_suite_is_blocking(self):
        job = self.data['jobs']['full-suite']
        self.assertNotIn('continue-on-error', job)
        for step in job['steps']:
            self.assertNotIn('continue-on-error', step)
        self.assertEqual(job['needs'], 'critical-tests')

    def test_no_sqlite_in_integration_job_database_engine(self):
        for job_id in ('postgres-migrations', 'critical-tests', 'full-suite'):
            engine = self.data['jobs'][job_id]['env']['DATABASE_ENGINE']
            self.assertEqual(engine, 'postgresql')

    def test_shell_syntax_validation_present(self):
        script = (REPO_ROOT / 'deploy' / 'ci' / 'run_static_checks.sh').read_text(
            encoding='utf-8'
        )
        self.assertIn("git ls-files -z '*.sh'", script)
        self.assertIn('bash -n', script)

    def test_no_production_deploy_or_backup_execution(self):
        forbidden = (
            'backup_sigedon.sh',
            'restore_sigedon.sh',
            'run_scheduled_backup.sh',
            'run_restore_drill.sh',
            'seed_sigedon_demo',
            'pull_request_target',
        )
        for needle in forbidden:
            self.assertNotIn(needle, self.raw)
        # No deploy job name and no production deploy commands.
        job_ids = set(self.data['jobs'])
        self.assertNotIn('deploy', job_ids)
        self.assertNotIn('production-deploy', job_ids)
        self.assertNotIn('ghcr.io', self.raw)
        self.assertNotIn('docker push', self.raw)

    def test_no_real_secrets_referenced(self):
        # Fictional CI credentials only; no GitHub secrets context.
        self.assertNotIn('secrets.', self.raw)
        self.assertNotIn('${{ secrets', self.raw)
        self.assertIn('sigedon_ci_password', self.raw)
        self.assertNotIn('KOBO_API_TOKEN', self.raw)

    def test_no_failure_masking_on_critical_gates(self):
        self.assertNotIn('|| true', self.raw)
        self.assertNotIn('continue-on-error: true', self.raw)

    def test_job_timeouts_configured(self):
        for job_id, job in self.data['jobs'].items():
            self.assertIn('timeout-minutes', job, msg=job_id)

    def test_kobo_disabled_by_default_in_ci(self):
        for job_id in ('static', 'postgres-migrations', 'critical-tests', 'full-suite'):
            # static sets it in the step env; others at job env.
            job = self.data['jobs'][job_id]
            if 'KOBO_ENABLED' in job.get('env', {}):
                self.assertEqual(job['env']['KOBO_ENABLED'], 'False')
            else:
                joined = '\n'.join(
                    str(step.get('env', {})) for step in job['steps']
                )
                self.assertIn('KOBO_ENABLED', joined)

    def test_dependency_graph(self):
        self.assertEqual(
            self.data['jobs']['postgres-migrations']['needs'], 'static'
        )
        self.assertEqual(self.data['jobs']['critical-tests']['needs'], 'static')
        self.assertEqual(self.data['jobs']['full-suite']['needs'], 'critical-tests')
