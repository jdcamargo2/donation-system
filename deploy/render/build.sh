#!/usr/bin/env bash
# SIGEDON Render native-Python build phase.
# PRE: repository root reachable; Python 3.12-compatible interpreter available;
#      Render (or local offline harness) has already installed requirements.txt
#      unless SIGEDON_RENDER_INSTALL_DEPS=YES requests an explicit pip install.
# POST: STATIC_ROOT populated and verify_deployment_assets succeeds; exit non-zero
#       on any failure. Never connects to PostgreSQL, R2, or starts a server.
#       Never runs migrations, Gunicorn, backup, restore, or role sync.
#
# Build vs runtime private storage:
#   Build forces filesystem mode with a temporary SIGEDON_MEDIA_ROOT so settings
#   import does not require R2 secrets during collectstatic. Runtime may use r2.
#   Release/post-deploy must still validate the final runtime storage mode.
set -Eeuo pipefail

PYTHON_BIN="${PYTHON_BIN:-python}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${REPO_ROOT}"

echo "sigedon-render-build: starting"

# --- Python compatibility (major.minor only; patch chosen at provision time) ---
"${PYTHON_BIN}" - <<'PY'
import sys

if sys.version_info[:2] != (3, 12):
    raise SystemExit(
        f"SIGEDON Render build requires Python 3.12.x "
        f"(found {sys.version_info.major}.{sys.version_info.minor})."
    )
print(f"python={sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")
PY

# Render's native Python runtime installs requirements.txt before the custom
# Build Command when that file is present. Re-run only when explicitly requested
# (local offline harness / environments that do not auto-install).
if [[ "${SIGEDON_RENDER_INSTALL_DEPS:-}" == "YES" ]]; then
  echo "sigedon-render-build: installing dependencies (SIGEDON_RENDER_INSTALL_DEPS=YES)"
  "${PYTHON_BIN}" -m pip install -r requirements.txt
fi

# Dependency integrity when packages are already present (no network required).
"${PYTHON_BIN}" -m pip check

# Safe build-time private-storage contract (no private files touched).
BUILD_MEDIA_ROOT="${SIGEDON_BUILD_MEDIA_ROOT:-}"
if [[ -z "${BUILD_MEDIA_ROOT}" ]]; then
  BUILD_MEDIA_ROOT="$(mktemp -d /tmp/sigedon-build-media.XXXXXX)"
fi
mkdir -p "${BUILD_MEDIA_ROOT}"

export DJANGO_DEBUG="${DJANGO_DEBUG:-False}"
export DATABASE_ENGINE="${DATABASE_ENGINE:-postgresql}"
export SIGEDON_PRIVATE_STORAGE=filesystem
export SIGEDON_MEDIA_ROOT="${BUILD_MEDIA_ROOT}"

# Settings import requires production shape when DJANGO_DEBUG=False. Collectstatic
# does not open the database; placeholder PostgreSQL values are accepted only for
# import. Prefer platform-provided values when already set.
export DJANGO_SECRET_KEY="${DJANGO_SECRET_KEY:-build-time-nonsecret-replace-in-runtime}"
export ALLOWED_HOSTS="${ALLOWED_HOSTS:-build.invalid}"
export POSTGRES_DB="${POSTGRES_DB:-sigedon_build_placeholder}"
export POSTGRES_USER="${POSTGRES_USER:-sigedon_build_placeholder}"
export POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-sigedon-build-placeholder-password}"
export POSTGRES_HOST="${POSTGRES_HOST:-127.0.0.1}"
export POSTGRES_PORT="${POSTGRES_PORT:-5432}"

echo "sigedon-render-build: collectstatic"
"${PYTHON_BIN}" manage.py collectstatic --noinput

echo "sigedon-render-build: verify_deployment_assets"
"${PYTHON_BIN}" manage.py verify_deployment_assets

echo "sigedon-render-build: ok"
