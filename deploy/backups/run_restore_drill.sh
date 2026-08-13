#!/usr/bin/env bash
# PRE: SIGEDON_RESTORE_DRILL_ENABLED=YES; isolated SIGEDON_RESTORE_DB with safe
#      prefix; empty SIGEDON_RESTORE_MEDIA_ROOT; SIGEDON_RESTORE_CONFIRM=YES;
#      backup path argument or latest verified under SIGEDON_BACKUP_ROOT;
#      POSTGRES_DB names the active/production DB that must not be targeted.
# POST: restores only into disposable targets via restore_sigedon.sh; optional
#       Django verify chain when SIGEDON_RESTORE_DRILL_RUN_DJANGO=YES (repoints
#       POSTGRES_DB/SIGEDON_MEDIA_ROOT to the isolated restore targets for that
#       phase only; forces SIGEDON_PRIVATE_STORAGE=filesystem for portable
#       offline verify — never claims a real R2 drill); writes
#       .sigedon-restore-drill-status.json; exit non-zero on any failed stage.
# NOTE: must not be used to restore production. Timer examples must call this
#       script only with isolated prefixes.
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly SCRIPT_DIR
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"

RESTORE_SCRIPT="${SCRIPT_DIR}/restore_sigedon.sh"
VERIFY_SCRIPT="${SCRIPT_DIR}/verify_backup.sh"

PHASE="init"
BACKUP_ID=""
BACKUP_DIR=""
BACKUP_ROOT=""

cleanup_marker() {
  local code="$1"
  local phase="$2"
  local message="$3"
  if [[ -n "${BACKUP_ROOT}" && -d "${BACKUP_ROOT}" ]]; then
    local marker_status="failure"
    if [[ "${code}" -eq 0 ]]; then
      marker_status="success"
    fi
    write_status_marker \
      "${BACKUP_ROOT}" \
      ".sigedon-restore-drill-status.json" \
      "restore_drill" \
      "${marker_status}" \
      "${code}" \
      "${phase}" \
      "${BACKUP_ID}" \
      "${message}" || log "WARN: no se pudo escribir drill status marker"
  fi
  if [[ "${code}" -ne 0 ]]; then
    invoke_alert_hook "restore_drill" "failure" "${code}" "${phase}" "${BACKUP_ID}"
  elif [[ "${SIGEDON_BACKUP_ALERT_ON_SUCCESS:-}" == "YES" ]]; then
    invoke_alert_hook "restore_drill" "success" "0" "${phase}" "${BACKUP_ID}"
  fi
}

if [[ "${SIGEDON_RESTORE_DRILL_ENABLED:-}" != "YES" ]]; then
  die 4 "SIGEDON_RESTORE_DRILL_ENABLED=YES es obligatorio (drill aislado)"
fi

require_var SIGEDON_BACKUP_ROOT
require_var SIGEDON_RESTORE_DB
require_var SIGEDON_RESTORE_MEDIA_ROOT
require_var POSTGRES_DB

if [[ "${SIGEDON_RESTORE_CONFIRM:-}" != "YES" ]]; then
  die 4 "SIGEDON_RESTORE_CONFIRM=YES es obligatorio para el drill"
fi

if [[ "${SIGEDON_RESTORE_DB}" == "${POSTGRES_DB}" ]]; then
  die 4 "rechazado: drill no puede usar la base activa (POSTGRES_DB)"
fi

ALLOWED_PREFIXES="${SIGEDON_RESTORE_ALLOWED_PREFIXES:-test_restore_|staging_restore_}"
IFS='|' read -r -a PREFIX_ARRAY <<<"${ALLOWED_PREFIXES}"
PREFIX_OK=0
for prefix in "${PREFIX_ARRAY[@]}"; do
  [[ -n "${prefix}" ]] || continue
  if [[ "${SIGEDON_RESTORE_DB}" == "${prefix}"* ]]; then
    PREFIX_OK=1
    break
  fi
done
if [[ "${PREFIX_OK}" -ne 1 ]]; then
  die 4 "SIGEDON_RESTORE_DB debe comenzar con un prefijo seguro (${ALLOWED_PREFIXES})"
fi

[[ -f "${RESTORE_SCRIPT}" ]] || die 3 "no se encuentra restore_sigedon.sh"
[[ -f "${VERIFY_SCRIPT}" ]] || die 3 "no se encuentra verify_backup.sh"
require_cmd bash

BACKUP_ROOT="$(ensure_backup_root "${SIGEDON_BACKUP_ROOT}")"
ACTIVE_POSTGRES_DB="${POSTGRES_DB}"
RESTORE_MEDIA_ROOT="${SIGEDON_RESTORE_MEDIA_ROOT}"

PHASE="select"
if [[ "${#}" -ge 1 && -n "${1:-}" ]]; then
  BACKUP_DIR="$(cd -- "$1" && pwd)"
else
  mapfile -t CANDIDATES < <(
    find "${BACKUP_ROOT}" -mindepth 1 -maxdepth 1 -type d ! -name '.*' -printf '%f\n' \
      | LC_ALL=C sort -r
  )
  for name in "${CANDIDATES[@]}"; do
    if ! is_backup_id "${name}"; then
      continue
    fi
    candidate="${BACKUP_ROOT}/${name}"
    if [[ -L "${candidate}" ]]; then
      continue
    fi
    if bash "${VERIFY_SCRIPT}" "${candidate}" >/dev/null; then
      BACKUP_DIR="${candidate}"
      break
    fi
  done
fi

[[ -n "${BACKUP_DIR}" && -d "${BACKUP_DIR}" ]] || {
  cleanup_marker 3 "select" "no hay backup verificado para drill"
  exit 3
}
BACKUP_ID="$(basename -- "${BACKUP_DIR}")"

BACKUP_FORMAT_VERSION="$(
  python3 -c '
import json, sys
path = sys.argv[1] + "/manifest.json"
with open(path, encoding="utf-8") as handle:
    data = json.load(handle)
print(int(data.get("format_version", 0)))
' "${BACKUP_DIR}" 2>/dev/null || printf '0'
)"
BACKUP_PRIVATE_MODE="$(
  python3 -c '
import json, sys
path = sys.argv[1] + "/manifest.json"
with open(path, encoding="utf-8") as handle:
    data = json.load(handle)
ps = data.get("private_storage") or {}
print(ps.get("mode", ""))
' "${BACKUP_DIR}" 2>/dev/null || true
)"

PHASE="restore"
log "INFO: iniciando restore drill sobre backup_id=${BACKUP_ID} format_version=${BACKUP_FORMAT_VERSION}"
# Keep POSTGRES_DB as the active DB name so restore_sigedon.sh refuses it.
export POSTGRES_DB="${ACTIVE_POSTGRES_DB}"
if ! bash "${RESTORE_SCRIPT}" "${BACKUP_DIR}"; then
  cleanup_marker 3 "restore" "restore_sigedon.sh fallo"
  exit 3
fi

PHASE="django_verify"
if [[ "${SIGEDON_RESTORE_DRILL_RUN_DJANGO:-}" == "YES" ]]; then
  require_cmd python3
  REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
  MANAGE="${REPO_ROOT}/manage.py"
  [[ -f "${MANAGE}" ]] || {
    cleanup_marker 3 "django_verify" "manage.py ausente"
    exit 3
  }
  # Point Django at the isolated restore targets only for verification.
  # Always use filesystem-backed portable target for drills — including object
  # backups restored offline. This does NOT prove a real R2 restore drill.
  export POSTGRES_DB="${SIGEDON_RESTORE_DB}"
  export SIGEDON_PRIVATE_STORAGE=filesystem
  export SIGEDON_MEDIA_ROOT="${RESTORE_MEDIA_ROOT}"
  if [[ "${BACKUP_FORMAT_VERSION}" -eq 2 && "${BACKUP_PRIVATE_MODE}" == "object" ]]; then
    log "INFO: object-mode backup verificado contra filesystem aislado (no es drill R2 real)"
  fi
  (
    cd "${REPO_ROOT}"
    python3 manage.py migrate --check
    python3 manage.py check --deploy
    python3 manage.py verify_postgres_security
    python3 manage.py reconcile_operational_code_sequences
    python3 manage.py verify_restored_data
    python3 manage.py verify_private_storage --configuration-only
  ) || {
    cleanup_marker 3 "django_verify" "cadena Django de verificacion fallo"
    exit 3
  }
else
  log "INFO: verificacion Django omitida (SIGEDON_RESTORE_DRILL_RUN_DJANGO!=YES)"
  log "INFO: post-restore manual: migrate --check, check --deploy, verify_postgres_security,"
  log "INFO: reconcile_operational_code_sequences, verify_restored_data,"
  log "INFO: verify_private_storage --configuration-only"
  if [[ "${BACKUP_FORMAT_VERSION}" -eq 2 && "${BACKUP_PRIVATE_MODE}" == "object" ]]; then
    log "INFO: object-mode restore uso filesystem aislado; drill R2 real pendiente de infraestructura"
  fi
fi

PHASE="done"
cleanup_marker 0 "done" "restore drill completado"
log "OK: restore drill completado (${BACKUP_ID})"
if [[ "${BACKUP_FORMAT_VERSION}" -eq 2 && "${BACKUP_PRIVATE_MODE}" == "object" ]]; then
  log "NOTE: exito del drill no implica restore R2 real (infraestructura no provisionada)."
fi
printf '%s\n' "${BACKUP_ID}"
exit 0
