#!/usr/bin/env bash
# PRE: PostgreSQL service reachable; DATABASE_ENGINE=postgresql; fictional CI
#      credentials only; Python with SIGEDON deps.
# POST: migrates from zero, proves idempotency, runs production-like
#       check --deploy with temp media, collectstatic + verify_deployment_assets
#       into an isolated STATIC_ROOT, then removes temps. Does not run
#       verify_postgres_security as a gate (owner-role rejection is staging).
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
  echo "migration gates require DATABASE_ENGINE=postgresql." >&2
  exit 2
fi

tmp_media="$(mktemp -d "${TMPDIR:-/tmp}/sigedon-ci-media.XXXXXX")"
tmp_static="$(mktemp -d "${TMPDIR:-/tmp}/sigedon-ci-static.XXXXXX")"
cleanup() {
  rm -rf -- "$tmp_media" "$tmp_static"
}
trap cleanup EXIT

export SIGEDON_MEDIA_ROOT="${SIGEDON_MEDIA_ROOT:-$tmp_media}"
export SIGEDON_CI_STATIC_ROOT="$tmp_static"

echo "== makemigrations --check --dry-run =="
"$PY" manage.py makemigrations --check --dry-run

echo "== migrate --plan =="
"$PY" manage.py migrate --plan

echo "== migrate --noinput (from zero) =="
"$PY" manage.py migrate --noinput

echo "== migrate --check =="
"$PY" manage.py migrate --check

echo "== migrate --noinput (idempotent second pass) =="
"$PY" manage.py migrate --noinput

echo "== migrate --check (after second pass) =="
"$PY" manage.py migrate --check

echo "== manage.py check =="
"$PY" manage.py check

echo "== reconcile_operational_code_sequences =="
"$PY" manage.py reconcile_operational_code_sequences

echo "== check --deploy (fictional production-like values) =="
(
  export DJANGO_DEBUG=False
  export DJANGO_SECRET_KEY='ci-only-long-random-looking-secret-not-for-production'
  export ALLOWED_HOSTS='localhost,testserver'
  export CSRF_TRUSTED_ORIGINS='https://localhost'
  export DATABASE_ENGINE=postgresql
  export SIGEDON_MEDIA_ROOT="$tmp_media"
  # Explicit secure flags expected by check --deploy; fictional CI only.
  export SECURE_SSL_REDIRECT=True
  export SECURE_HSTS_SECONDS=3600
  "$PY" manage.py check --deploy
)

echo "== collectstatic + verify_deployment_assets (isolated STATIC_ROOT) =="
SIGEDON_CI_STATIC_ROOT="$tmp_static" \
  "$PY" manage.py collectstatic --noinput --settings=core.ci_settings
SIGEDON_CI_STATIC_ROOT="$tmp_static" \
  "$PY" manage.py verify_deployment_assets --settings=core.ci_settings

echo "== append-only trigger modules (owner CI role; catalog evidence) =="
"$PY" manage.py test \
  apps.operations.tests.test_audit_log_postgresql_trigger \
  apps.operations.tests.test_expense_request_events \
  --noinput

echo "migration gates: OK"
# NOTE: verify_postgres_security intentionally rejects owner/superuser roles.
# Runtime-role success remains a staging/deployment gate, not this CI job.
