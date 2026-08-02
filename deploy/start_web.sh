#!/usr/bin/env bash
# Canonical production web process for SIGEDON.
# PRE: release/preflight completed; Gunicorn installed; cwd may be anywhere.
# POST: replaces this shell with Gunicorn serving core.wsgi:application.
# Never runs migrations, collectstatic, or role synchronization.
set -Eeuo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_ROOT}"

exec gunicorn core.wsgi:application \
  --config deploy/gunicorn.conf.py \
  "$@"
