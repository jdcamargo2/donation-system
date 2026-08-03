#!/usr/bin/env bash
# SIGEDON Render pre-deploy / release phase (owner/migrator credentials).
# PRE: environment configured for schema mutation; collectstatic already done in
#      build; operator understands credential separation (see docs/runbooks).
# POST: migrations applied; roles synced; sequences reconciled; release preflight
#       passed. Never starts Gunicorn. Never runs verify_postgres_security
#       (runtime-role gate). Never runs R2 --probe. Never runs backup/restore.
#       Never seeds demo data.
#
# First-deployment strategy (recommended):
#   Do NOT leave owner/migrator credentials on the Render Web Service.
#   Run this script from an authorized one-off shell/job with owner credentials,
#   then configure the web service with runtime credentials only.
#
# Optional migrator remapping (future / controlled one-off only):
#   If POSTGRES_MIGRATOR_USER and POSTGRES_MIGRATOR_PASSWORD are set, they are
#   exported as POSTGRES_USER / POSTGRES_PASSWORD for this process only.
#   Leaving migrator secrets on a long-lived web service is unsafe on platforms
#   that cannot scope environment variables by process.
set -Eeuo pipefail

PYTHON_BIN="${PYTHON_BIN:-python}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${REPO_ROOT}"

echo "sigedon-render-pre-deploy: starting"

if [[ -n "${POSTGRES_MIGRATOR_USER:-}" ]]; then
  if [[ -z "${POSTGRES_MIGRATOR_PASSWORD:-}" ]]; then
    echo "POSTGRES_MIGRATOR_USER set but POSTGRES_MIGRATOR_PASSWORD is empty." >&2
    exit 2
  fi
  export POSTGRES_USER="${POSTGRES_MIGRATOR_USER}"
  export POSTGRES_PASSWORD="${POSTGRES_MIGRATOR_PASSWORD}"
  echo "sigedon-render-pre-deploy: using migrator credentials for this process"
fi

echo "sigedon-render-pre-deploy: check --deploy"
"${PYTHON_BIN}" manage.py check --deploy

echo "sigedon-render-pre-deploy: makemigrations --check --dry-run"
"${PYTHON_BIN}" manage.py makemigrations --check --dry-run

echo "sigedon-render-pre-deploy: migrate --plan"
"${PYTHON_BIN}" manage.py migrate --plan

echo "sigedon-render-pre-deploy: migrate --noinput"
"${PYTHON_BIN}" manage.py migrate --noinput

echo "sigedon-render-pre-deploy: migrate --check"
"${PYTHON_BIN}" manage.py migrate --check

echo "sigedon-render-pre-deploy: sync_sigedon_roles"
"${PYTHON_BIN}" manage.py sync_sigedon_roles

echo "sigedon-render-pre-deploy: reconcile_operational_code_sequences"
"${PYTHON_BIN}" manage.py reconcile_operational_code_sequences

echo "sigedon-render-pre-deploy: release preflight"
# Read-only gates after mutations. May require filesystem media mount when
# SIGEDON_PRIVATE_STORAGE=filesystem; R2 mode uses configuration-only deploy
# checks (no network). Does not apply migrations or start a server.
./deploy/preflight.sh

echo "sigedon-render-pre-deploy: ok"
