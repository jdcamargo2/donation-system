#!/usr/bin/env bash
# PRE: entorno en ventana de mantenimiento confirmada; variables de backup
#      definidas; herramientas pg_dump/tar/sha256sum/pg_restore disponibles;
#      SIGEDON_BACKUP_ROOT se crea con permisos 0700 si no existe.
#      Modo filesystem (default): SIGEDON_MEDIA_ROOT existe y es accesible.
#      Modo r2 (SIGEDON_PRIVATE_STORAGE=r2): Django configurado para exportar
#      objetos privados; no se empaqueta media.tar.gz ni se exige MEDIA_ROOT.
# POST: publica <backup_id>/ solo si todas las validaciones pasan.
#       filesystem → database.dump + media.tar.gz + manifest.json (format_version 1)
#       r2         → database.dump + objects/ + object-manifest.json +
#                    manifest.json (format_version 2, private_storage.mode=object)
#       En fallo elimina el temporal y no deja un artefacto final parcial.
set -Eeuo pipefail

umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly SCRIPT_DIR
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"

sha256_file() {
  local file="$1"
  sha256sum -- "${file}" | awk '{print $1}'
}

file_size() {
  local file="$1"
  wc -c <"${file}" | tr -d '[:space:]'
}

cleanup_temp() {
  if [[ -n "${TEMP_ROOT:-}" && -d "${TEMP_ROOT}" ]]; then
    rm -rf -- "${TEMP_ROOT}"
  fi
}

trap cleanup_temp EXIT

# --- Controles de seguridad / variables -------------------------------------

if [[ "${SIGEDON_MAINTENANCE_CONFIRMED:-}" != "YES" ]]; then
  die 4 "SIGEDON_MAINTENANCE_CONFIRMED=YES es obligatorio. Detenga web, workers, comandos Kobo y uploads antes del backup."
fi

require_var SIGEDON_BACKUP_ROOT
require_var POSTGRES_DB
require_var POSTGRES_USER
require_var POSTGRES_HOST
require_var POSTGRES_PORT

if [[ -z "${POSTGRES_DB}" || -z "${POSTGRES_USER}" || -z "${POSTGRES_HOST}" || -z "${POSTGRES_PORT}" ]]; then
  die 2 "POSTGRES_DB, POSTGRES_USER, POSTGRES_HOST y POSTGRES_PORT no pueden estar vacios"
fi

STORAGE_MODE="$(resolve_private_storage_mode)"

# Preferir ~/.pgpass; PGPASSWORD es opcional y no se imprime.
if [[ -z "${PGPASSWORD:-}" ]]; then
  log "INFO: PGPASSWORD no definida; se usara .pgpass / autenticacion del cliente si aplica."
fi

require_cmd pg_dump
require_cmd pg_restore
require_cmd sha256sum
require_cmd python3
require_cmd mktemp

if [[ "${STORAGE_MODE}" == "filesystem" ]]; then
  require_cmd tar
  require_var SIGEDON_MEDIA_ROOT
fi

BACKUP_ROOT="$(ensure_backup_root "${SIGEDON_BACKUP_ROOT}")"

# One global deployment lock (.sigedon-ops.lock on FD 9). When invoked under
# run_scheduled_backup.sh, FD 9 is inherited and reaffirmed; forging an env
# var without that FD does not skip acquisition.
acquire_backup_lock "${BACKUP_ROOT}" || exit $?

REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
MEDIA_ROOT=""

if [[ "${STORAGE_MODE}" == "filesystem" ]]; then
  # Reject relative / blank / filesystem-root media paths before resolve_abs.
  case "${SIGEDON_MEDIA_ROOT}" in
    ''|'/'|'//'|'///')
      die 3 "SIGEDON_MEDIA_ROOT no puede estar vacio ni ser la raiz del sistema de archivos"
      ;;
  esac
  if [[ "${SIGEDON_MEDIA_ROOT}" != /* ]]; then
    die 3 "SIGEDON_MEDIA_ROOT debe ser una ruta absoluta al volumen persistente de media"
  fi

  MEDIA_ROOT="$(resolve_abs "${SIGEDON_MEDIA_ROOT}")"

  [[ -d "${MEDIA_ROOT}" ]] || die 3 "SIGEDON_MEDIA_ROOT no es un directorio: ${MEDIA_ROOT}"
  if [[ "${MEDIA_ROOT}" == "/" ]]; then
    die 3 "SIGEDON_MEDIA_ROOT no puede ser la raiz del sistema de archivos"
  fi

  # Evitar archivar silenciosamente el media/ efimero del repositorio.
  REPO_MEDIA="$(resolve_abs "${REPO_ROOT}/media" 2>/dev/null || true)"
  if [[ -n "${REPO_MEDIA}" && "${MEDIA_ROOT}" == "${REPO_MEDIA}" && "${SIGEDON_ALLOW_REPO_MEDIA:-}" != "YES" ]]; then
    die 3 "SIGEDON_MEDIA_ROOT apunta al media/ del repositorio; use un volumen persistente (o SIGEDON_ALLOW_REPO_MEDIA=YES solo para pruebas locales intencionales)"
  fi
else
  # Object mode: Django export uses configured private storage (R2). No media.tar.gz.
  MANAGE_PY="$(repo_manage_py)"
  log "INFO: modo object storage (SIGEDON_PRIVATE_STORAGE=r2); no se empaqueta media.tar.gz"
fi

BACKUP_ID="$(date -u +'%Y%m%dT%H%M%SZ')"
FINAL_DIR="${BACKUP_ROOT}/${BACKUP_ID}"

if [[ -e "${FINAL_DIR}" ]]; then
  die 5 "ya existe un backup con id ${BACKUP_ID}; no se sobrescribe"
fi

TEMP_ROOT="$(mktemp -d "${BACKUP_ROOT}/.sigedon-backup.${BACKUP_ID}.XXXXXX")"
chmod 700 "${TEMP_ROOT}"
STAGE_DIR="${TEMP_ROOT}/stage"
mkdir -m 700 -- "${STAGE_DIR}"

DUMP_FILE="${STAGE_DIR}/database.dump"
MEDIA_ARCHIVE="${STAGE_DIR}/media.tar.gz"
OBJECT_MANIFEST="${STAGE_DIR}/object-manifest.json"
MANIFEST_FILE="${STAGE_DIR}/manifest.json"

log "INFO: iniciando backup ${BACKUP_ID} storage_mode=${STORAGE_MODE} (sin listar rutas ni secretos)"

# --- Dump PostgreSQL (formato custom; nunca SQL texto) ----------------------

PGPASSWORD="${PGPASSWORD:-}" pg_dump \
  --host="${POSTGRES_HOST}" \
  --port="${POSTGRES_PORT}" \
  --username="${POSTGRES_USER}" \
  --dbname="${POSTGRES_DB}" \
  --format=custom \
  --no-owner \
  --no-acl \
  --file="${DUMP_FILE}"

[[ -f "${DUMP_FILE}" ]] || die 6 "dump no creado"
DUMP_SIZE="$(file_size "${DUMP_FILE}")"
[[ "${DUMP_SIZE}" -gt 0 ]] || die 6 "dump vacio"
pg_restore --list -- "${DUMP_FILE}" >/dev/null || die 6 "dump no listable con pg_restore --list"
DUMP_SHA="$(sha256_file "${DUMP_FILE}")"
PG_CLIENT_VERSION="$(pg_dump --version | head -n1)"

# --- Metadatos de aplicacion (sin secretos) ---------------------------------

GIT_COMMIT="unknown"
GIT_BRANCH="unknown"
if command -v git >/dev/null 2>&1; then
  if git -C "${REPO_ROOT}" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    GIT_COMMIT="$(git -C "${REPO_ROOT}" rev-parse HEAD 2>/dev/null || printf 'unknown')"
    GIT_BRANCH="$(git -C "${REPO_ROOT}" rev-parse --abbrev-ref HEAD 2>/dev/null || printf 'unknown')"
  fi
fi

DJANGO_VERSION="unknown"
PYTHON_VERSION="$(python3 -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])')"
if command -v python3 >/dev/null 2>&1; then
  DJANGO_VERSION="$(
    python3 -c 'import django; print(django.get_version())' 2>/dev/null || printf 'unknown'
  )"
fi

CREATED_AT_UTC="$(date -u +'%Y-%m-%dT%H:%M:%SZ')"

export SIGEDON_MANIFEST_OUT="${MANIFEST_FILE}"
export SIGEDON_MANIFEST_BACKUP_ID="${BACKUP_ID}"
export SIGEDON_MANIFEST_CREATED_AT="${CREATED_AT_UTC}"
export SIGEDON_MANIFEST_DUMP_NAME="database.dump"
export SIGEDON_MANIFEST_DUMP_SHA="${DUMP_SHA}"
export SIGEDON_MANIFEST_DUMP_SIZE="${DUMP_SIZE}"
export SIGEDON_MANIFEST_PG_VERSION="${PG_CLIENT_VERSION}"
export SIGEDON_MANIFEST_GIT_COMMIT="${GIT_COMMIT}"
export SIGEDON_MANIFEST_GIT_BRANCH="${GIT_BRANCH}"
export SIGEDON_MANIFEST_DJANGO="${DJANGO_VERSION}"
export SIGEDON_MANIFEST_PYTHON="${PYTHON_VERSION}"

if [[ "${STORAGE_MODE}" == "filesystem" ]]; then
  # --- Copia de MEDIA_ROOT (ventana de mantenimiento) -----------------------
  # Solo archivos regulares bajo MEDIA_ROOT (rutas relativas). Se omiten
  # staticfiles y enlaces simbolicos (incluye externos).
  MEDIA_LIST="${TEMP_ROOT}/media_paths.txt"
  (
    cd -- "${MEDIA_ROOT}"
    find . -path './staticfiles' -prune -o -path './staticfiles/*' -prune -o \
      -type f -print
  ) | awk 'NF && !seen[$0]++' >"${MEDIA_LIST}"

  tar -C "${MEDIA_ROOT}" -czf "${MEDIA_ARCHIVE}" -T "${MEDIA_LIST}"
  [[ -f "${MEDIA_ARCHIVE}" ]] || die 7 "media.tar.gz no creado"
  MEDIA_SIZE="$(file_size "${MEDIA_ARCHIVE}")"
  [[ "${MEDIA_SIZE}" -gt 0 ]] || die 7 "media.tar.gz vacio"
  tar -tzf "${MEDIA_ARCHIVE}" >/dev/null || die 7 "media.tar.gz no listable con tar -tzf"
  MEDIA_SHA="$(sha256_file "${MEDIA_ARCHIVE}")"
  MEDIA_FILE_COUNT="$(
    tar -tzf "${MEDIA_ARCHIVE}" | grep -cv '/$' || true
  )"

  export SIGEDON_MANIFEST_MEDIA_NAME="media.tar.gz"
  export SIGEDON_MANIFEST_MEDIA_SHA="${MEDIA_SHA}"
  export SIGEDON_MANIFEST_MEDIA_SIZE="${MEDIA_SIZE}"
  export SIGEDON_MANIFEST_MEDIA_COUNT="${MEDIA_FILE_COUNT}"

  python3 <<'PY'
import json
import os
import sys

required = [
    "SIGEDON_MANIFEST_OUT",
    "SIGEDON_MANIFEST_BACKUP_ID",
    "SIGEDON_MANIFEST_CREATED_AT",
    "SIGEDON_MANIFEST_DUMP_NAME",
    "SIGEDON_MANIFEST_DUMP_SHA",
    "SIGEDON_MANIFEST_DUMP_SIZE",
    "SIGEDON_MANIFEST_PG_VERSION",
    "SIGEDON_MANIFEST_MEDIA_NAME",
    "SIGEDON_MANIFEST_MEDIA_SHA",
    "SIGEDON_MANIFEST_MEDIA_SIZE",
    "SIGEDON_MANIFEST_MEDIA_COUNT",
    "SIGEDON_MANIFEST_GIT_COMMIT",
    "SIGEDON_MANIFEST_GIT_BRANCH",
    "SIGEDON_MANIFEST_DJANGO",
    "SIGEDON_MANIFEST_PYTHON",
]
missing = [name for name in required if not os.environ.get(name)]
if missing:
    print("manifest env missing: " + ", ".join(missing), file=sys.stderr)
    sys.exit(1)

manifest = {
    "format_version": 1,
    "backup_id": os.environ["SIGEDON_MANIFEST_BACKUP_ID"],
    "created_at_utc": os.environ["SIGEDON_MANIFEST_CREATED_AT"],
    "database": {
        "filename": os.environ["SIGEDON_MANIFEST_DUMP_NAME"],
        "sha256": os.environ["SIGEDON_MANIFEST_DUMP_SHA"],
        "size_bytes": int(os.environ["SIGEDON_MANIFEST_DUMP_SIZE"]),
        "postgres_client_version": os.environ["SIGEDON_MANIFEST_PG_VERSION"],
    },
    "media": {
        "filename": os.environ["SIGEDON_MANIFEST_MEDIA_NAME"],
        "sha256": os.environ["SIGEDON_MANIFEST_MEDIA_SHA"],
        "size_bytes": int(os.environ["SIGEDON_MANIFEST_MEDIA_SIZE"]),
        "file_count": int(os.environ["SIGEDON_MANIFEST_MEDIA_COUNT"]),
    },
    "application": {
        "git_commit": os.environ["SIGEDON_MANIFEST_GIT_COMMIT"],
        "git_branch": os.environ["SIGEDON_MANIFEST_GIT_BRANCH"],
        "django_version": os.environ["SIGEDON_MANIFEST_DJANGO"],
        "python_version": os.environ["SIGEDON_MANIFEST_PYTHON"],
    },
    "consistency": {
        "maintenance_confirmed": True,
        "strategy": "maintenance_window",
    },
}

out = os.environ["SIGEDON_MANIFEST_OUT"]
with open(out, "w", encoding="utf-8") as handle:
    json.dump(manifest, handle, indent=2, sort_keys=True)
    handle.write("\n")
os.chmod(out, 0o600)
PY

  chmod 600 -- "${DUMP_FILE}" "${MEDIA_ARCHIVE}" "${MANIFEST_FILE}"
else
  # --- Export de objetos privados referenciados (Django) --------------------
  # No usa aws/rclone/s3cmd/curl hacia R2: solo manage.py export_private_objects.
  log "INFO: exportando objetos privados via export_private_objects"
  (
    cd "${REPO_ROOT}"
    python3 "${MANAGE_PY}" export_private_objects --output-directory "${STAGE_DIR}"
  ) || die 7 "export_private_objects fallo"

  [[ -f "${OBJECT_MANIFEST}" ]] || die 7 "object-manifest.json no creado"
  [[ -d "${STAGE_DIR}/objects" ]] || die 7 "objects/ no creado"
  [[ -s "${OBJECT_MANIFEST}" ]] || die 7 "object-manifest.json vacio"

  OBJECT_MANIFEST_SIZE="$(file_size "${OBJECT_MANIFEST}")"
  OBJECT_MANIFEST_SHA="$(sha256_file "${OBJECT_MANIFEST}")"
  OBJECT_COUNT="$(
    python3 -c '
import json, sys
with open(sys.argv[1], encoding="utf-8") as handle:
    data = json.load(handle)
print(int(data.get("object_count", len(data.get("objects") or []))))
' "${OBJECT_MANIFEST}"
  )"

  export SIGEDON_MANIFEST_OBJECT_NAME="object-manifest.json"
  export SIGEDON_MANIFEST_OBJECT_SHA="${OBJECT_MANIFEST_SHA}"
  export SIGEDON_MANIFEST_OBJECT_SIZE="${OBJECT_MANIFEST_SIZE}"
  export SIGEDON_MANIFEST_OBJECT_COUNT="${OBJECT_COUNT}"

  python3 <<'PY'
import json
import os
import sys

required = [
    "SIGEDON_MANIFEST_OUT",
    "SIGEDON_MANIFEST_BACKUP_ID",
    "SIGEDON_MANIFEST_CREATED_AT",
    "SIGEDON_MANIFEST_DUMP_NAME",
    "SIGEDON_MANIFEST_DUMP_SHA",
    "SIGEDON_MANIFEST_DUMP_SIZE",
    "SIGEDON_MANIFEST_PG_VERSION",
    "SIGEDON_MANIFEST_OBJECT_NAME",
    "SIGEDON_MANIFEST_OBJECT_SHA",
    "SIGEDON_MANIFEST_OBJECT_SIZE",
    "SIGEDON_MANIFEST_OBJECT_COUNT",
    "SIGEDON_MANIFEST_GIT_COMMIT",
    "SIGEDON_MANIFEST_GIT_BRANCH",
    "SIGEDON_MANIFEST_DJANGO",
    "SIGEDON_MANIFEST_PYTHON",
]
missing = [name for name in required if not os.environ.get(name)]
if missing:
    print("manifest env missing: " + ", ".join(missing), file=sys.stderr)
    sys.exit(1)

manifest = {
    "format_version": 2,
    "backup_id": os.environ["SIGEDON_MANIFEST_BACKUP_ID"],
    "created_at_utc": os.environ["SIGEDON_MANIFEST_CREATED_AT"],
    "database": {
        "filename": os.environ["SIGEDON_MANIFEST_DUMP_NAME"],
        "sha256": os.environ["SIGEDON_MANIFEST_DUMP_SHA"],
        "size_bytes": int(os.environ["SIGEDON_MANIFEST_DUMP_SIZE"]),
        "postgres_client_version": os.environ["SIGEDON_MANIFEST_PG_VERSION"],
    },
    "private_storage": {
        "mode": "object",
        "object_manifest": {
            "filename": os.environ["SIGEDON_MANIFEST_OBJECT_NAME"],
            "sha256": os.environ["SIGEDON_MANIFEST_OBJECT_SHA"],
            "size_bytes": int(os.environ["SIGEDON_MANIFEST_OBJECT_SIZE"]),
            "object_count": int(os.environ["SIGEDON_MANIFEST_OBJECT_COUNT"]),
        },
    },
    "application": {
        "git_commit": os.environ["SIGEDON_MANIFEST_GIT_COMMIT"],
        "git_branch": os.environ["SIGEDON_MANIFEST_GIT_BRANCH"],
        "django_version": os.environ["SIGEDON_MANIFEST_DJANGO"],
        "python_version": os.environ["SIGEDON_MANIFEST_PYTHON"],
    },
    "consistency": {
        "maintenance_confirmed": True,
        "strategy": "maintenance_window",
        "note": (
            "Maintenance window reduces drift between PostgreSQL and private "
            "object storage; this is not a perfect cross-provider atomic snapshot."
        ),
    },
}

blob = json.dumps(manifest).lower()
for word in (
    "password",
    "token",
    "secret_access",
    "access_key",
    "endpoint_url",
    "bucket_name",
    "connection_url",
    "dsn",
    "pgpassword",
):
    if word in blob:
        print(f"manifest would embed forbidden token: {word}", file=sys.stderr)
        sys.exit(1)

out = os.environ["SIGEDON_MANIFEST_OUT"]
with open(out, "w", encoding="utf-8") as handle:
    json.dump(manifest, handle, indent=2, sort_keys=True)
    handle.write("\n")
os.chmod(out, 0o600)
PY

  chmod 600 -- "${DUMP_FILE}" "${OBJECT_MANIFEST}" "${MANIFEST_FILE}"
  # Restrict objects tree permissions without printing paths.
  find "${STAGE_DIR}/objects" -type d -exec chmod 700 {} +
  find "${STAGE_DIR}/objects" -type f -exec chmod 600 {} +
fi

# --- Publicacion atomica ----------------------------------------------------

if [[ -e "${FINAL_DIR}" ]]; then
  die 5 "conflicto al publicar: ${BACKUP_ID} ya existe"
fi

mv -- "${STAGE_DIR}" "${FINAL_DIR}"
chmod 700 -- "${FINAL_DIR}"

log "OK: backup publicado: ${FINAL_DIR}"
printf '%s\n' "${FINAL_DIR}"
exit 0
