#!/usr/bin/env bash
# PRE: same variables as backup_sigedon.sh (including
#      SIGEDON_MAINTENANCE_CONFIRMED=YES); SIGEDON_BACKUP_ROOT writable;
#      optional retention and alert-hook variables; flock available.
# POST: runs at most one backup pipeline at a time; publishes only after
#       backup+verify succeed; applies retention when configured; writes
#       .sigedon-backup-status.json; never advertises failure as success;
#       exit non-zero on any failed stage; optional alert hook on failure.
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly SCRIPT_DIR
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"

BACKUP_SCRIPT="${SCRIPT_DIR}/backup_sigedon.sh"
VERIFY_SCRIPT="${SCRIPT_DIR}/verify_backup.sh"
RETENTION_SCRIPT="${SCRIPT_DIR}/apply_retention.sh"

PHASE="init"
BACKUP_ID=""
BACKUP_ROOT=""

cleanup_and_signal() {
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
      ".sigedon-backup-status.json" \
      "scheduled_backup" \
      "${marker_status}" \
      "${code}" \
      "${phase}" \
      "${BACKUP_ID}" \
      "${message}" || log "WARN: no se pudo escribir status marker"
  fi
  if [[ "${code}" -ne 0 ]]; then
    invoke_alert_hook "scheduled_backup" "failure" "${code}" "${phase}" "${BACKUP_ID}"
  elif [[ "${SIGEDON_BACKUP_ALERT_ON_SUCCESS:-}" == "YES" ]]; then
    invoke_alert_hook "scheduled_backup" "success" "0" "${phase}" "${BACKUP_ID}"
  fi
}

require_var SIGEDON_BACKUP_ROOT
[[ -f "${BACKUP_SCRIPT}" ]] || die 2 "no se encuentra backup_sigedon.sh"
[[ -f "${VERIFY_SCRIPT}" ]] || die 2 "no se encuentra verify_backup.sh"
require_cmd bash
require_cmd flock

BACKUP_ROOT="$(ensure_backup_root "${SIGEDON_BACKUP_ROOT}")"

PHASE="lock"
# One global lock for the full pipeline. Children inherit FD 9 and reaffirm
# via acquire_backup_lock (never reopen the lock file under the parent hold).
if ! acquire_backup_lock "${BACKUP_ROOT}"; then
  cleanup_and_signal 8 "lock" "lock exclusivo no disponible"
  exit 8
fi

PHASE="backup"
log "INFO: iniciando pipeline de backup programado"
BACKUP_STDOUT="$(mktemp "${BACKUP_ROOT}/.sigedon-backup-runner.XXXXXX")"
cleanup_stdout() {
  rm -f -- "${BACKUP_STDOUT}"
}
trap cleanup_stdout EXIT

set +e
bash "${BACKUP_SCRIPT}" >"${BACKUP_STDOUT}"
backup_rc=$?
set -e
if [[ "${backup_rc}" -ne 0 ]]; then
  cleanup_and_signal "${backup_rc}" "backup" "backup_sigedon.sh fallo"
  exit "${backup_rc}"
fi

BACKUP_DIR="$(tail -n1 -- "${BACKUP_STDOUT}")"
[[ -n "${BACKUP_DIR}" && -d "${BACKUP_DIR}" ]] || {
  cleanup_and_signal 3 "backup" "salida de backup sin directorio publicado"
  exit 3
}
BACKUP_ID="$(basename -- "${BACKUP_DIR}")"
is_backup_id "${BACKUP_ID}" || {
  cleanup_and_signal 3 "backup" "backup_id invalido en salida"
  exit 3
}

PHASE="verify"
if ! bash "${VERIFY_SCRIPT}" "${BACKUP_DIR}"; then
  cleanup_and_signal 3 "verify" "verify_backup.sh rechazo el artefacto"
  exit 3
fi
mark_backup_verified "${BACKUP_DIR}"

PHASE="retention"
if [[ -n "${SIGEDON_BACKUP_KEEP_COUNT:-}" || -n "${SIGEDON_BACKUP_KEEP_DAYS:-}" ]]; then
  [[ -f "${RETENTION_SCRIPT}" ]] || {
    cleanup_and_signal 3 "retention" "apply_retention.sh ausente"
    exit 3
  }
  if ! bash "${RETENTION_SCRIPT}"; then
    cleanup_and_signal 9 "retention" "apply_retention.sh fallo"
    exit 9
  fi
else
  log "INFO: retencion omitida (SIGEDON_BACKUP_KEEP_COUNT/DAYS no definidos)"
fi

PHASE="done"
cleanup_and_signal 0 "done" "backup verificado"
log "OK: pipeline de backup programado completado (${BACKUP_ID})"
printf '%s\n' "${BACKUP_DIR}"
exit 0
