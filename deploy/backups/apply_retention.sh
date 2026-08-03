#!/usr/bin/env bash
# PRE: SIGEDON_BACKUP_ROOT configured; SIGEDON_BACKUP_KEEP_COUNT and/or
#      SIGEDON_BACKUP_KEEP_DAYS set to positive integers; verify_backup.sh
#      available; exclusive backup lock free or already held by caller.
# POST: deletes only verified backup sets that are direct children of the
#       configured backup root and fall outside retention; never follows
#       unsafe paths; never deletes lock/status/temp entries; exit 0 if
#       nothing eligible or deletions succeed; non-zero on policy/path error.
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly SCRIPT_DIR
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"

VERIFY_SCRIPT="${SCRIPT_DIR}/verify_backup.sh"

require_var SIGEDON_BACKUP_ROOT
[[ -f "${VERIFY_SCRIPT}" ]] || die 3 "no se encuentra verify_backup.sh"
require_cmd bash

KEEP_COUNT="${SIGEDON_BACKUP_KEEP_COUNT:-}"
KEEP_DAYS="${SIGEDON_BACKUP_KEEP_DAYS:-}"

if [[ -z "${KEEP_COUNT}" && -z "${KEEP_DAYS}" ]]; then
  die 2 "defina SIGEDON_BACKUP_KEEP_COUNT y/o SIGEDON_BACKUP_KEEP_DAYS"
fi

if [[ -n "${KEEP_COUNT}" ]]; then
  [[ "${KEEP_COUNT}" =~ ^[0-9]+$ ]] || die 2 "SIGEDON_BACKUP_KEEP_COUNT debe ser entero >= 0"
fi
if [[ -n "${KEEP_DAYS}" ]]; then
  [[ "${KEEP_DAYS}" =~ ^[0-9]+$ ]] || die 2 "SIGEDON_BACKUP_KEEP_DAYS debe ser entero >= 0"
fi

BACKUP_ROOT="$(ensure_backup_root "${SIGEDON_BACKUP_ROOT}")"
acquire_backup_lock "${BACKUP_ROOT}" || exit $?

# Collect verified backup directories (newest first by backup_id / UTC stamp).
mapfile -t CANDIDATES < <(
  find "${BACKUP_ROOT}" -mindepth 1 -maxdepth 1 -type d ! -name '.*' -printf '%f\n' \
    | LC_ALL=C sort -r
)

VERIFIED=()
for name in "${CANDIDATES[@]}"; do
  [[ -n "${name}" ]] || continue
  if ! is_backup_id "${name}"; then
    log "INFO: se omite entrada no reconocida como backup_id: ${name}"
    continue
  fi
  candidate="${BACKUP_ROOT}/${name}"
  # Refuse symlinked backup sets (could point outside the root).
  if [[ -L "${candidate}" ]]; then
    log "WARN: se omite symlink de backup (no elegible para retencion): ${name}"
    continue
  fi
  resolved="$(cd -- "${candidate}" && pwd)"
  if ! path_is_under "${resolved}" "${BACKUP_ROOT}"; then
    die 9 "ruta de backup fuera de SIGEDON_BACKUP_ROOT; retencion abortada"
  fi
  if ! bash "${VERIFY_SCRIPT}" "${resolved}" >/dev/null; then
    log "INFO: backup no verificado; no elegible para borrado: ${name}"
    continue
  fi
  VERIFIED+=("${resolved}")
done

NOW_EPOCH="$(date -u +%s)"
KEEP_SECONDS=0
if [[ -n "${KEEP_DAYS}" ]]; then
  KEEP_SECONDS=$((KEEP_DAYS * 86400))
fi

to_delete=()
index=0
for path in "${VERIFIED[@]}"; do
  index=$((index + 1))
  name="$(basename -- "${path}")"
  keep_by_count=0
  keep_by_age=0

  if [[ -n "${KEEP_COUNT}" && "${index}" -le "${KEEP_COUNT}" ]]; then
    keep_by_count=1
  fi

  if [[ -n "${KEEP_DAYS}" ]]; then
    # backup_id is UTC: YYYYMMDDTHHMMSSZ
    stamp="${name}"
    year="${stamp:0:4}"
    month="${stamp:4:2}"
    day="${stamp:6:2}"
    hour="${stamp:9:2}"
    minute="${stamp:11:2}"
    second="${stamp:13:2}"
    backup_epoch="$(date -u -d "${year}-${month}-${day} ${hour}:${minute}:${second}" +%s 2>/dev/null || true)"
    if [[ -z "${backup_epoch}" ]]; then
      log "WARN: no se pudo parsear edad de ${name}; se conserva"
      keep_by_age=1
    elif [[ $((NOW_EPOCH - backup_epoch)) -le "${KEEP_SECONDS}" ]]; then
      keep_by_age=1
    fi
  fi

  # Keep if within count OR within days when that dimension is configured.
  # When both are set: keep if either policy still protects the set.
  # When only one is set: that dimension alone decides.
  if [[ -n "${KEEP_COUNT}" && -n "${KEEP_DAYS}" ]]; then
    if [[ "${keep_by_count}" -eq 1 || "${keep_by_age}" -eq 1 ]]; then
      continue
    fi
  elif [[ -n "${KEEP_COUNT}" ]]; then
    if [[ "${keep_by_count}" -eq 1 ]]; then
      continue
    fi
  else
    if [[ "${keep_by_age}" -eq 1 ]]; then
      continue
    fi
  fi

  to_delete+=("${path}")
done

deleted=0
for path in "${to_delete[@]}"; do
  name="$(basename -- "${path}")"
  resolved="$(cd -- "${path}" && pwd)"
  if ! path_is_under "${resolved}" "${BACKUP_ROOT}"; then
    die 9 "rechazo de seguridad: borrado fuera de backup root"
  fi
  if [[ "${resolved}" == "${BACKUP_ROOT}" ]]; then
    die 9 "rechazo de seguridad: intento de borrar backup root"
  fi
  # Final guard: only delete directories whose basename is a backup_id.
  is_backup_id "${name}" || die 9 "rechazo de seguridad: nombre no es backup_id"
  log "INFO: retencion elimina backup verificado ${name}"
  rm -rf -- "${resolved}"
  deleted=$((deleted + 1))
done

log "OK: retencion aplicada (verificados=${#VERIFIED[@]} eliminados=${deleted})"
exit 0
