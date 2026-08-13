#!/usr/bin/env bash
# SIGEDON Render post-deploy runtime verification (operator-triggered).
# PRE: web service configured with final runtime credentials; build artifacts
#      present; schema already migrated under owner role.
# POST: exits 0 only when runtime configuration and security gates pass.
#       Never mutates schema, syncs roles, migrates, seeds, or backs up.
#       Never prints environment values or secrets.
#
# Usage:
#   ./deploy/render/post_deploy_verify.sh
#   ./deploy/render/post_deploy_verify.sh --probe-private-storage
set -Eeuo pipefail

PYTHON_BIN="${PYTHON_BIN:-python}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${REPO_ROOT}"

PROBE_PRIVATE_STORAGE=0
for arg in "$@"; do
  case "${arg}" in
    --probe-private-storage)
      PROBE_PRIVATE_STORAGE=1
      ;;
    -h|--help)
      echo "Usage: $0 [--probe-private-storage]"
      exit 0
      ;;
    *)
      echo "Unknown argument: ${arg}" >&2
      echo "Usage: $0 [--probe-private-storage]" >&2
      exit 2
      ;;
  esac
done

echo "sigedon-render-post-deploy: starting"

echo "sigedon-render-post-deploy: check --deploy"
"${PYTHON_BIN}" manage.py check --deploy

echo "sigedon-render-post-deploy: verify_postgres_security"
"${PYTHON_BIN}" manage.py verify_postgres_security

echo "sigedon-render-post-deploy: verify_private_storage --configuration-only"
"${PYTHON_BIN}" manage.py verify_private_storage --configuration-only

echo "sigedon-render-post-deploy: verify_deployment_assets"
"${PYTHON_BIN}" manage.py verify_deployment_assets

echo "sigedon-render-post-deploy: verify_render_configuration"
"${PYTHON_BIN}" manage.py verify_render_configuration

if [[ "${PROBE_PRIVATE_STORAGE}" -eq 1 ]]; then
  echo "sigedon-render-post-deploy: verify_private_storage --probe (explicit)"
  "${PYTHON_BIN}" manage.py verify_private_storage --probe
else
  echo "sigedon-render-post-deploy: skipping R2/storage probe (pass --probe-private-storage to enable)"
fi

echo "sigedon-render-post-deploy: ok"
