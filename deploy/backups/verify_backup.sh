#!/usr/bin/env bash
# PRE: recibe la ruta de un directorio de backup SIGEDON con database.dump,
#      media.tar.gz y manifest.json; herramientas pg_restore/tar/sha256sum/
#      python3 disponibles.
# POST: sale 0 solo si estructura, manifiesto, tamaños, listados y backup_id
#       son consistentes; no modifica ningun archivo.
set -Eeuo pipefail

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

require_cmd() {
  local cmd="$1"
  command -v "${cmd}" >/dev/null 2>&1 || die 3 "herramienta requerida no encontrada: ${cmd}"
}

sha256_file() {
  local file="$1"
  sha256sum -- "${file}" | awk '{print $1}'
}

file_size() {
  local file="$1"
  wc -c <"${file}" | tr -d '[:space:]'
}

if [[ "${#}" -ne 1 ]]; then
  die 2 "uso: $0 <ruta-del-backup>"
fi

BACKUP_PATH_RAW="$1"
[[ -n "${BACKUP_PATH_RAW}" ]] || die 2 "ruta de backup vacia"
[[ -d "${BACKUP_PATH_RAW}" ]] || die 3 "backup inexistente o no es directorio"

BACKUP_DIR="$(cd -- "${BACKUP_PATH_RAW}" && pwd)"
BACKUP_DIRNAME="$(basename -- "${BACKUP_DIR}")"

require_cmd pg_restore
require_cmd tar
require_cmd sha256sum
require_cmd python3

DUMP_FILE="${BACKUP_DIR}/database.dump"
MEDIA_ARCHIVE="${BACKUP_DIR}/media.tar.gz"
MANIFEST_FILE="${BACKUP_DIR}/manifest.json"

[[ -f "${DUMP_FILE}" ]] || die 3 "falta database.dump"
[[ -f "${MEDIA_ARCHIVE}" ]] || die 3 "falta media.tar.gz"
[[ -f "${MANIFEST_FILE}" ]] || die 3 "falta manifest.json"

[[ -s "${DUMP_FILE}" ]] || die 3 "database.dump vacio o truncado"
[[ -s "${MEDIA_ARCHIVE}" ]] || die 3 "media.tar.gz vacio o truncado"
[[ -s "${MANIFEST_FILE}" ]] || die 3 "manifest.json vacio"

DUMP_SIZE="$(file_size "${DUMP_FILE}")"
MEDIA_SIZE="$(file_size "${MEDIA_ARCHIVE}")"
DUMP_SHA="$(sha256_file "${DUMP_FILE}")"
MEDIA_SHA="$(sha256_file "${MEDIA_ARCHIVE}")"

pg_restore --list -- "${DUMP_FILE}" >/dev/null || die 3 "database.dump corrupto (pg_restore --list fallo)"
tar -tzf "${MEDIA_ARCHIVE}" >/dev/null || die 3 "media.tar.gz corrupto (tar -tzf fallo)"

export SIGEDON_VERIFY_MANIFEST="${MANIFEST_FILE}"
export SIGEDON_VERIFY_DIRNAME="${BACKUP_DIRNAME}"
export SIGEDON_VERIFY_DUMP_SHA="${DUMP_SHA}"
export SIGEDON_VERIFY_DUMP_SIZE="${DUMP_SIZE}"
export SIGEDON_VERIFY_MEDIA_SHA="${MEDIA_SHA}"
export SIGEDON_VERIFY_MEDIA_SIZE="${MEDIA_SIZE}"

python3 <<'PY'
import json
import os
import sys

path = os.environ["SIGEDON_VERIFY_MANIFEST"]
dirname = os.environ["SIGEDON_VERIFY_DIRNAME"]
dump_sha = os.environ["SIGEDON_VERIFY_DUMP_SHA"]
dump_size = int(os.environ["SIGEDON_VERIFY_DUMP_SIZE"])
media_sha = os.environ["SIGEDON_VERIFY_MEDIA_SHA"]
media_size = int(os.environ["SIGEDON_VERIFY_MEDIA_SIZE"])

try:
    with open(path, "r", encoding="utf-8") as handle:
        manifest = json.load(handle)
except (OSError, json.JSONDecodeError) as exc:
    print(f"manifest.json invalido: {exc}", file=sys.stderr)
    sys.exit(3)

required_top = ("format_version", "backup_id", "created_at_utc", "database", "media", "application", "consistency")
for key in required_top:
    if key not in manifest:
        print(f"manifiesto incompleto: falta {key}", file=sys.stderr)
        sys.exit(3)

if manifest.get("format_version") != 1:
    print("format_version no soportado", file=sys.stderr)
    sys.exit(3)

if manifest.get("backup_id") != dirname:
    print("backup_id del manifiesto no coincide con el directorio", file=sys.stderr)
    sys.exit(3)

database = manifest["database"]
media = manifest["media"]
application = manifest["application"]
consistency = manifest["consistency"]

for key in ("filename", "sha256", "size_bytes", "postgres_client_version"):
    if key not in database:
        print(f"manifiesto incompleto: database.{key}", file=sys.stderr)
        sys.exit(3)

for key in ("filename", "sha256", "size_bytes", "file_count"):
    if key not in media:
        print(f"manifiesto incompleto: media.{key}", file=sys.stderr)
        sys.exit(3)

for key in ("git_commit", "git_branch", "django_version", "python_version"):
    if key not in application:
        print(f"manifiesto incompleto: application.{key}", file=sys.stderr)
        sys.exit(3)

for key in ("maintenance_confirmed", "strategy"):
    if key not in consistency:
        print(f"manifiesto incompleto: consistency.{key}", file=sys.stderr)
        sys.exit(3)

if database["filename"] != "database.dump":
    print("database.filename inesperado", file=sys.stderr)
    sys.exit(3)
if media["filename"] != "media.tar.gz":
    print("media.filename inesperado", file=sys.stderr)
    sys.exit(3)

if database["sha256"] != dump_sha:
    print("checksum database.dump incorrecto", file=sys.stderr)
    sys.exit(3)
if int(database["size_bytes"]) != dump_size:
    print("size_bytes de database.dump no coincide", file=sys.stderr)
    sys.exit(3)
if media["sha256"] != media_sha:
    print("checksum media.tar.gz incorrecto", file=sys.stderr)
    sys.exit(3)
if int(media["size_bytes"]) != media_size:
    print("size_bytes de media.tar.gz no coincide", file=sys.stderr)
    sys.exit(3)

forbidden = ("password", "token", "connection_url", "dsn", "pgpassword")
blob = json.dumps(manifest).lower()
for word in forbidden:
    if word in blob:
        print(f"manifiesto contiene campo sensible prohibido: {word}", file=sys.stderr)
        sys.exit(3)

print("OK: backup verificado", file=sys.stderr)
sys.exit(0)
PY

exit 0
