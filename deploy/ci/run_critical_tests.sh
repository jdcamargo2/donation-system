#!/usr/bin/env bash
# PRE: PostgreSQL reachable with Django DATABASE_* / POSTGRES_* env set;
#      DATABASE_ENGINE=postgresql (no SQLite fallback); Python with deps.
# POST: runs the focused critical suite; exit code is the test runner's.
set -Eeuo pipefail

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"

if [[ -n "${PYTHON:-}" ]]; then
  PY="$PYTHON"
elif [[ -x ./venv/bin/python ]]; then
  PY=./venv/bin/python
else
  PY=python3
fi

export TZ="${TZ:-UTC}"
export KOBO_ENABLED="${KOBO_ENABLED:-False}"

if [[ "${DATABASE_ENGINE:-}" != "postgresql" ]]; then
  echo "critical tests require DATABASE_ENGINE=postgresql (got '${DATABASE_ENGINE:-}')." >&2
  exit 2
fi

exec "$PY" manage.py test \
  core.tests.test_ci_workflow \
  core.tests.test_repository_hygiene \
  core.tests.test_ci_settings \
  core.tests.test_runtime_startup \
  core.tests.test_static_resilience \
  core.tests.test_settings \
  core.tests.test_deploy_checks \
  core.tests.test_media_settings \
  core.tests.test_health_endpoints \
  core.tests.test_request_ids \
  core.tests.test_logging_config \
  apps.operations.tests.test_backup_scripts \
  apps.operations.tests.test_backup_automation \
  apps.operations.tests.test_management_command_safety \
  apps.operations.tests.test_verify_postgres_security \
  apps.operations.tests.test_verify_postgres_security_command \
  apps.operations.tests.test_audit_log_postgresql_trigger \
  apps.operations.tests.test_expense_request_events \
  apps.operations.tests.test_database_constraints \
  apps.operations.tests.test_expense_lifecycle \
  apps.operations.tests.test_expense_reassignment_integrity \
  apps.operations.tests.test_roles \
  apps.operations.tests.test_permissions \
  apps.operations.tests.test_protected_file_preview \
  apps.integrations.kobo.tests.test_process_kobo_submissions_command \
  apps.integrations.kobo.tests.test_reconcile_kobo_submissions_command \
  --noinput \
  "$@"
