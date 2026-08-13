# Shared helpers for SIGEDON backup automation.
# PRE: sourced by backup automation scripts (not executed directly).
# POST: provides log/die/require_*, path guards, exclusive lock, status markers,
#       and bounded alert-hook invocation without embedding secrets.
# shellcheck shell=bash

if [[ -n "${SIGEDON_BACKUP_COMMON_LOADED:-}" ]]; then
  return 0 2>/dev/null || exit 0
fi
SIGEDON_BACKUP_COMMON_LOADED=1

umask 077

log() {
  printf '%s\n' "$*" >&2
}

die() {
  local code="${1:-1}"
  shift || true
  log "ERROR: $*"
  exit "${code}"
}

require_var() {
  local name="$1"
  if [[ -z "${!name:-}" ]]; then
    die 2 "variable requerida ausente: ${name}"
  fi
}

require_cmd() {
  local cmd="$1"
  command -v "${cmd}" >/dev/null 2>&1 || die 3 "herramienta requerida no encontrada: ${cmd}"
}

resolve_private_storage_mode() {
  # PRE: SIGEDON_PRIVATE_STORAGE may be unset (defaults to filesystem).
  # POST: prints filesystem|r2; dies on unknown values. Never prints secrets.
  local raw="${SIGEDON_PRIVATE_STORAGE:-}"
  # Trim and lowercase without echoing credentials from other vars.
  raw="${raw#"${raw%%[![:space:]]*}"}"
  raw="${raw%"${raw##*[![:space:]]}"}"
  raw="$(printf '%s' "${raw}" | tr '[:upper:]' '[:lower:]')"
  if [[ -z "${raw}" ]]; then
    printf 'filesystem\n'
    return 0
  fi
  case "${raw}" in
    filesystem|r2)
      printf '%s\n' "${raw}"
      ;;
    *)
      die 2 "SIGEDON_PRIVATE_STORAGE debe ser filesystem o r2"
      ;;
  esac
}

repo_manage_py() {
  # PRE: SCRIPT_DIR is set to deploy/backups (caller responsibility).
  # POST: prints absolute path to manage.py or dies.
  local repo_root manage
  repo_root="$(cd "${SCRIPT_DIR}/../.." && pwd)"
  manage="${repo_root}/manage.py"
  [[ -f "${manage}" ]] || die 3 "manage.py ausente en ${repo_root}"
  printf '%s\n' "${manage}"
}

resolve_abs() {
  local path="$1"
  if [[ -d "${path}" ]]; then
    (cd "${path}" && pwd)
    return
  fi
  if [[ -e "${path}" ]]; then
    local parent base
    parent="$(cd "$(dirname "${path}")" && pwd)"
    base="$(basename "${path}")"
    printf '%s/%s\n' "${parent}" "${base}"
    return
  fi
  die 3 "ruta inexistente: ${path}"
}

ensure_backup_root() {
  # PRE: path is the requested SIGEDON_BACKUP_ROOT (may not exist).
  # POST: directory exists with mode 0700, is writable; prints absolute path.
  local path="$1"
  if [[ -e "${path}" && ! -d "${path}" ]]; then
    die 3 "SIGEDON_BACKUP_ROOT existe y no es un directorio: ${path}"
  fi
  if [[ ! -d "${path}" ]]; then
    mkdir -m 700 -p -- "${path}" || die 3 "no se pudo crear SIGEDON_BACKUP_ROOT: ${path}"
    chmod 700 -- "${path}" || die 3 "no se pudieron fijar permisos 0700 en SIGEDON_BACKUP_ROOT"
  fi
  local resolved
  resolved="$(resolve_abs "${path}")"
  [[ -d "${resolved}" ]] || die 3 "SIGEDON_BACKUP_ROOT no es un directorio: ${resolved}"
  [[ -w "${resolved}" ]] || die 3 "SIGEDON_BACKUP_ROOT no es escribible: ${resolved}"
  printf '%s\n' "${resolved}"
}

is_backup_id() {
  # PRE: candidate string.
  # POST: exit 0 iff candidate matches published backup_id pattern.
  local candidate="$1"
  [[ "${candidate}" =~ ^[0-9]{8}T[0-9]{6}Z$ ]]
}

path_is_under() {
  # PRE: child and parent are absolute existing directories.
  # POST: exit 0 when child is parent or a descendant (string prefix after resolve).
  local child="$1"
  local parent="$2"
  case "${child}" in
    "${parent}"|"${parent}"/*) return 0 ;;
    *) return 1 ;;
  esac
}

_backup_lock_fd_targets_file() {
  # PRE: candidate lock file path exists or will be the flock target.
  # POST: exit 0 iff FD 9 is already open and resolves to the same inode/path
  #       as lock_file (inherited open file description from parent).
  local lock_file="$1"
  local fd_path="/proc/self/fd/9"
  [[ -e "${fd_path}" ]] || return 1

  local fd_target lock_target
  fd_target="$(readlink -f -- "${fd_path}" 2>/dev/null || true)"
  [[ -n "${fd_target}" ]] || return 1
  # Lock file may not exist yet on first open; if it exists, compare resolved paths.
  if [[ -e "${lock_file}" ]]; then
    lock_target="$(readlink -f -- "${lock_file}" 2>/dev/null || true)"
    [[ -n "${lock_target}" && "${fd_target}" == "${lock_target}" ]] || return 1
  else
    return 1
  fi
  return 0
}

acquire_backup_lock() {
  # PRE: backup_root is absolute writable directory; flock available.
  # POST: exclusive non-blocking lock held on reserved FD 9 for this process
  #       tree (return 0), or return 8 if another job holds it.
  #
  # Architecture: one global deployment lock (.sigedon-ops.lock) acquired by
  # whichever entry point starts the operation (runner, manual backup, or
  # manual retention). Child scripts must NOT reopen the lock file: that
  # creates a new open-file description and flock -n fails with exit 8 even
  # though the parent already holds the lock. Safe internal bypass = prove FD 9
  # already targets the lock file (inherited OFD), then reaffirm flock on that
  # same FD. An ordinary operator env var cannot skip acquisition.
  local backup_root="$1"
  require_cmd flock
  local lock_file="${backup_root}/.sigedon-ops.lock"

  if _backup_lock_fd_targets_file "${lock_file}"; then
    # Inherited FD 9: reaffirm on the same OFD (never exec 9> reopen).
    if ! flock -n 9; then
      log "ERROR: otro proceso de backup/retencion tiene el lock exclusivo"
      return 8
    fi
    return 0
  fi

  # Fresh acquisition on reserved FD 9.
  exec 9>"${lock_file}"
  chmod 600 -- "${lock_file}" 2>/dev/null || true
  if ! flock -n 9; then
    log "ERROR: otro proceso de backup/retencion tiene el lock exclusivo"
    return 8
  fi
  return 0
}

write_status_marker() {
  # PRE: backup_root absolute; job/status/exit_code/phase provided; no secrets.
  # POST: writes .sigedon-backup-status.json (or drill marker) with mode 0600.
  local backup_root="$1"
  local marker_name="$2"
  local job="$3"
  local status="$4"
  local exit_code="$5"
  local phase="$6"
  local backup_id="${7:-}"
  local message="${8:-}"
  local finished_at
  finished_at="$(date -u +'%Y-%m-%dT%H:%M:%SZ')"
  local out="${backup_root}/${marker_name}"

  export SIGEDON_STATUS_OUT="${out}"
  export SIGEDON_STATUS_JOB="${job}"
  export SIGEDON_STATUS_STATUS="${status}"
  export SIGEDON_STATUS_EXIT="${exit_code}"
  export SIGEDON_STATUS_PHASE="${phase}"
  export SIGEDON_STATUS_BACKUP_ID="${backup_id}"
  export SIGEDON_STATUS_MESSAGE="${message}"
  export SIGEDON_STATUS_FINISHED_AT="${finished_at}"

  require_cmd python3
  python3 <<'PY'
import json
import os
import sys

out = os.environ["SIGEDON_STATUS_OUT"]
payload = {
    "format_version": 1,
    "job": os.environ["SIGEDON_STATUS_JOB"],
    "status": os.environ["SIGEDON_STATUS_STATUS"],
    "exit_code": int(os.environ["SIGEDON_STATUS_EXIT"]),
    "phase": os.environ["SIGEDON_STATUS_PHASE"],
    "backup_id": os.environ.get("SIGEDON_STATUS_BACKUP_ID", ""),
    "message": os.environ.get("SIGEDON_STATUS_MESSAGE", ""),
    "finished_at_utc": os.environ["SIGEDON_STATUS_FINISHED_AT"],
}
message = os.environ.get("SIGEDON_STATUS_MESSAGE", "").lower()
for word in ("password", "token", "pgpassword", "secret", "connection_url", "dsn"):
    if word in message:
        print("status message contains forbidden token", file=sys.stderr)
        sys.exit(3)

with open(out, "w", encoding="utf-8") as handle:
    json.dump(payload, handle, indent=2, sort_keys=True)
    handle.write("\n")
os.chmod(out, 0o600)
PY
}

mark_backup_verified() {
  # PRE: backup_dir is a published backup directory that passed verify_backup.sh.
  # POST: writes .sigedon-verified marker (backward-compatible extra file).
  local backup_dir="$1"
  local marker="${backup_dir}/.sigedon-verified"
  local stamp
  stamp="$(date -u +'%Y-%m-%dT%H:%M:%SZ')"
  printf 'verified_at_utc=%s\n' "${stamp}" >"${marker}"
  chmod 600 -- "${marker}"
}

invoke_alert_hook() {
  # PRE: optional SIGEDON_BACKUP_ALERT_HOOK is empty or an executable file.
  # POST: runs hook with bounded env; never prints secrets; hook failure is
  #       logged but does not alter the caller exit code decision.
  local job="$1"
  local status="$2"
  local exit_code="$3"
  local phase="$4"
  local backup_id="${5:-}"
  local hook="${SIGEDON_BACKUP_ALERT_HOOK:-}"

  if [[ -z "${hook}" ]]; then
    return 0
  fi
  if [[ ! -f "${hook}" || ! -x "${hook}" ]]; then
    log "WARN: SIGEDON_BACKUP_ALERT_HOOK no es un ejecutable; se omite"
    return 0
  fi

  local timeout_secs="${SIGEDON_BACKUP_ALERT_HOOK_TIMEOUT_SECONDS:-30}"
  if [[ ! "${timeout_secs}" =~ ^[0-9]+$ ]] || [[ "${timeout_secs}" -lt 1 ]]; then
    timeout_secs=30
  fi
  if [[ "${timeout_secs}" -gt 120 ]]; then
    timeout_secs=120
  fi

  local finished_at
  finished_at="$(date -u +'%Y-%m-%dT%H:%M:%SZ')"

  export SIGEDON_ALERT_JOB="${job}"
  export SIGEDON_ALERT_STATUS="${status}"
  export SIGEDON_ALERT_EXIT_CODE="${exit_code}"
  export SIGEDON_ALERT_PHASE="${phase}"
  export SIGEDON_ALERT_BACKUP_ID="${backup_id}"
  export SIGEDON_ALERT_FINISHED_AT="${finished_at}"

  # Avoid `timeout -- cmd`: some coreutils builds treat `--` as the command.
  if command -v timeout >/dev/null 2>&1; then
    if ! timeout "${timeout_secs}" "${hook}" \
      "${job}" "${status}" "${exit_code}" "${phase}" "${backup_id}"; then
      log "WARN: alert hook salio distinto de cero o expiro (timeout=${timeout_secs}s)"
    fi
  else
    if ! "${hook}" "${job}" "${status}" "${exit_code}" "${phase}" "${backup_id}"; then
      log "WARN: alert hook salio distinto de cero"
    fi
  fi
  return 0
}
