#!/usr/bin/env bash
# Deployment readiness gate for SIGEDON.
# PRE: environment configured; persistent media mounted; collectstatic already run
#      in the release phase when static assets are served from STATIC_ROOT.
# POST: exits 0 only when deploy checks, migration consistency, unapplied-migration
#       gate, and collected static sentinels all pass. Does not start Gunicorn.
# Does not mutate data: no migrate (apply), no collectstatic, no role sync.
set -Eeuo pipefail

PYTHON_BIN="${PYTHON_BIN:-python}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_ROOT}"

"${PYTHON_BIN}" manage.py check --deploy
"${PYTHON_BIN}" manage.py makemigrations --check --dry-run
"${PYTHON_BIN}" manage.py migrate --check

if [[ "${SIGEDON_PREFLIGHT_SHOW_MIGRATE_PLAN:-}" == "YES" ]]; then
  "${PYTHON_BIN}" manage.py migrate --plan
fi

"${PYTHON_BIN}" manage.py verify_deployment_assets
