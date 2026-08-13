#!/usr/bin/env bash
# PRE: repository root; Python with SIGEDON deps available as $PYTHON or
#      ./venv/bin/python or python3; no PostgreSQL required.
# POST: runs deterministic static/repository gates; preserves exit codes;
#       does not mutate production data or write secrets.
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
export DJANGO_DEBUG="${DJANGO_DEBUG:-True}"
export DJANGO_SECRET_KEY="${DJANGO_SECRET_KEY:-ci-only-secret}"
export ALLOWED_HOSTS="${ALLOWED_HOSTS:-localhost,127.0.0.1,testserver}"
export KOBO_ENABLED="${KOBO_ENABLED:-False}"
# Static gates intentionally use SQLite when PostgreSQL is unavailable.
# Integration jobs must set DATABASE_ENGINE=postgresql explicitly.
export DATABASE_ENGINE="${DATABASE_ENGINE:-sqlite}"

echo "== git diff --check =="
git diff --check

echo "== repository hygiene =="
./deploy/ci/check_repository_hygiene.sh

echo "== pip check =="
"$PY" -m pip check

echo "== bash -n (tracked *.sh) =="
git ls-files -z '*.sh' | xargs -0 -r -n1 bash -n

echo "== manage.py check =="
"$PY" manage.py check

echo "== makemigrations --check --dry-run =="
"$PY" manage.py makemigrations --check --dry-run

echo "static checks: OK"
