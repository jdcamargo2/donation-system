#!/usr/bin/env bash
# PRE: backup verificado; SIGEDON_RESTORE_DB obligatorio con prefijo seguro;
#      distinto de POSTGRES_DB; SIGEDON_RESTORE_CONFIRM=YES; destino de media
#      nuevo y vacio; herramientas psql/pg_restore/tar disponibles.
# POST: restaura dump y media solo en entorno aislado; no toca .env ni el
#       MEDIA_ROOT activo; documenta validaciones posteriores.
set -Eeuo pipefail

umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly SCRIPT_DIR
VERIFY_SCRIPT="${SCRIPT_DIR}/verify_backup.sh"

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

assert_safe_ident() {
  local name="$1"
  local value="$2"
  if [[ ! "${value}" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then
    die 2 "${name} solo puede contener identificadores SQL seguros [A-Za-z_][A-Za-z0-9_]*"
  fi
}

resolve_abs_parent_create() {
  local path="$1"
  local parent base
  parent="$(dirname -- "${path}")"
  base="$(basename -- "${path}")"
  [[ -d "${parent}" ]] || die 3 "directorio padre inexistente para media restore: ${parent}"
  parent="$(cd -- "${parent}" && pwd)"
  printf '%s/%s\n' "${parent}" "${base}"
}

cleanup_partial_media() {
  if [[ -n "${CREATED_MEDIA_ROOT:-}" && -d "${CREATED_MEDIA_ROOT}" ]]; then
    rm -rf -- "${CREATED_MEDIA_ROOT}"
  fi
}

trap cleanup_partial_media EXIT

if [[ "${#}" -ne 1 ]]; then
  die 2 "uso: $0 <ruta-del-backup>"
fi

BACKUP_PATH_RAW="$1"
[[ -n "${BACKUP_PATH_RAW}" ]] || die 2 "ruta de backup vacia"

require_var SIGEDON_RESTORE_DB
require_var SIGEDON_RESTORE_MEDIA_ROOT
require_var POSTGRES_DB
require_var POSTGRES_USER
require_var POSTGRES_HOST
require_var POSTGRES_PORT

if [[ -z "${SIGEDON_RESTORE_DB}" ]]; then
  die 2 "SIGEDON_RESTORE_DB no puede estar vacio"
fi

assert_safe_ident SIGEDON_RESTORE_DB "${SIGEDON_RESTORE_DB}"
assert_safe_ident POSTGRES_DB "${POSTGRES_DB}"
assert_safe_ident POSTGRES_USER "${POSTGRES_USER}"

if [[ "${SIGEDON_RESTORE_DB}" == "${POSTGRES_DB}" ]]; then
  die 4 "rechazado: SIGEDON_RESTORE_DB coincide con POSTGRES_DB (base activa)"
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

if [[ "${SIGEDON_RESTORE_CONFIRM:-}" != "YES" ]]; then
  die 4 "SIGEDON_RESTORE_CONFIRM=YES es obligatorio para recrear la base destino"
fi

require_cmd bash
require_cmd psql
require_cmd pg_restore
require_cmd tar
require_cmd python3

[[ -f "${VERIFY_SCRIPT}" ]] || die 3 "no se encuentra verify_backup.sh"
bash "${VERIFY_SCRIPT}" "${BACKUP_PATH_RAW}" || die 3 "verify_backup.sh rechazo el backup"

BACKUP_DIR="$(cd -- "${BACKUP_PATH_RAW}" && pwd)"
DUMP_FILE="${BACKUP_DIR}/database.dump"
MEDIA_ARCHIVE="${BACKUP_DIR}/media.tar.gz"

RESTORE_MEDIA_ROOT="$(resolve_abs_parent_create "${SIGEDON_RESTORE_MEDIA_ROOT}")"

if [[ -e "${RESTORE_MEDIA_ROOT}" ]]; then
  if [[ ! -d "${RESTORE_MEDIA_ROOT}" ]]; then
    die 4 "SIGEDON_RESTORE_MEDIA_ROOT existe y no es directorio"
  fi
  if [[ -n "$(find "${RESTORE_MEDIA_ROOT}" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null || true)" ]]; then
    die 4 "SIGEDON_RESTORE_MEDIA_ROOT no esta vacio; nunca se sobrescribe media activa"
  fi
else
  mkdir -m 700 -- "${RESTORE_MEDIA_ROOT}"
  CREATED_MEDIA_ROOT="${RESTORE_MEDIA_ROOT}"
fi

if [[ -z "${PGPASSWORD:-}" ]]; then
  log "INFO: PGPASSWORD no definida; se usara .pgpass / autenticacion del cliente si aplica."
fi

PSQL=(
  psql
  --host="${POSTGRES_HOST}"
  --port="${POSTGRES_PORT}"
  --username="${POSTGRES_USER}"
  --dbname=postgres
  --set=ON_ERROR_STOP=1
)

log "INFO: comprobando existencia de base destino"
DB_EXISTS="$(
  PGPASSWORD="${PGPASSWORD:-}" "${PSQL[@]}" -Atc \
    "SELECT EXISTS(SELECT 1 FROM pg_database WHERE datname = '${SIGEDON_RESTORE_DB}');"
)"

if [[ "${DB_EXISTS}" == "t" ]]; then
  log "INFO: la base destino existe; se recreara tras confirmacion explicita"
  PGPASSWORD="${PGPASSWORD:-}" "${PSQL[@]}" -c \
    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '${SIGEDON_RESTORE_DB}' AND pid <> pg_backend_pid();" \
    >/dev/null || true
  PGPASSWORD="${PGPASSWORD:-}" "${PSQL[@]}" -c \
    "DROP DATABASE ${SIGEDON_RESTORE_DB};"
fi

PGPASSWORD="${PGPASSWORD:-}" "${PSQL[@]}" -c \
  "CREATE DATABASE ${SIGEDON_RESTORE_DB};"

log "INFO: restaurando dump con pg_restore"
PGPASSWORD="${PGPASSWORD:-}" pg_restore \
  --host="${POSTGRES_HOST}" \
  --port="${POSTGRES_PORT}" \
  --username="${POSTGRES_USER}" \
  --dbname="${SIGEDON_RESTORE_DB}" \
  --no-owner \
  --no-acl \
  --exit-on-error \
  -- "${DUMP_FILE}"

log "INFO: restaurando media en directorio aislado"
tar -C "${RESTORE_MEDIA_ROOT}" -xzf "${MEDIA_ARCHIVE}"
CREATED_MEDIA_ROOT=""

log "OK: restauracion aislada completada"
log "POST-RESTORE (usar variables del entorno restaurado; no modificar .env de produccion):"
log "  export POSTGRES_DB=<SIGEDON_RESTORE_DB>"
log "  apuntar MEDIA_ROOT al directorio de restauracion aislado"
log "  python manage.py migrate --check"
log "  python manage.py check"
log "  python manage.py verify_postgres_security"
log "  python manage.py verify_restored_data"
printf '%s\n' "${SIGEDON_RESTORE_DB}"
exit 0
