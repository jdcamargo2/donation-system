#!/usr/bin/env bash
# PRE: recibe la ruta de un directorio de backup SIGEDON; herramientas
#      pg_restore/sha256sum/python3 disponibles (tar solo para format_version 1).
# POST: sale 0 solo si estructura, checksums, tamaños y backup_id son
#       consistentes segun format_version; no modifica ningun archivo.
#       format_version 1 → database.dump + media.tar.gz (nunca reinterpretado
#       como object mode).
#       format_version 2 + private_storage.mode=object → database.dump +
#       object-manifest.json + objects/ verificados por checksums del manifiesto.
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
require_cmd sha256sum
require_cmd python3

DUMP_FILE="${BACKUP_DIR}/database.dump"
MEDIA_ARCHIVE="${BACKUP_DIR}/media.tar.gz"
OBJECT_MANIFEST="${BACKUP_DIR}/object-manifest.json"
MANIFEST_FILE="${BACKUP_DIR}/manifest.json"

[[ -f "${DUMP_FILE}" ]] || die 3 "falta database.dump"
[[ -f "${MANIFEST_FILE}" ]] || die 3 "falta manifest.json"
[[ -s "${DUMP_FILE}" ]] || die 3 "database.dump vacio o truncado"
[[ -s "${MANIFEST_FILE}" ]] || die 3 "manifest.json vacio"

DUMP_SIZE="$(file_size "${DUMP_FILE}")"
DUMP_SHA="$(sha256_file "${DUMP_FILE}")"
pg_restore --list -- "${DUMP_FILE}" >/dev/null || die 3 "database.dump corrupto (pg_restore --list fallo)"

# Peek format_version before choosing media vs object verification path.
FORMAT_VERSION="$(
  python3 -c '
import json, sys
with open(sys.argv[1], encoding="utf-8") as handle:
    data = json.load(handle)
print(data.get("format_version", ""))
' "${MANIFEST_FILE}"
)" || die 3 "manifest.json invalido"

case "${FORMAT_VERSION}" in
  1)
    require_cmd tar
    [[ -f "${MEDIA_ARCHIVE}" ]] || die 3 "falta media.tar.gz"
    [[ -s "${MEDIA_ARCHIVE}" ]] || die 3 "media.tar.gz vacio o truncado"
    MEDIA_SIZE="$(file_size "${MEDIA_ARCHIVE}")"
    MEDIA_SHA="$(sha256_file "${MEDIA_ARCHIVE}")"
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

required_top = (
    "format_version",
    "backup_id",
    "created_at_utc",
    "database",
    "media",
    "application",
    "consistency",
)
for key in required_top:
    if key not in manifest:
        print(f"manifiesto incompleto: falta {key}", file=sys.stderr)
        sys.exit(3)

if manifest.get("format_version") != 1:
    print("format_version no soportado para ruta v1", file=sys.stderr)
    sys.exit(3)

# Never silently reinterpret a v1 backup as object mode.
private_storage = manifest.get("private_storage")
if isinstance(private_storage, dict) and private_storage.get("mode") == "object":
    print(
        "format_version 1 no puede declarar private_storage.mode=object",
        file=sys.stderr,
    )
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

print("OK: backup verificado (format_version=1 filesystem)", file=sys.stderr)
sys.exit(0)
PY
    ;;
  2)
    [[ -f "${OBJECT_MANIFEST}" ]] || die 3 "falta object-manifest.json"
    [[ -d "${BACKUP_DIR}/objects" ]] || die 3 "falta objects/"
    [[ -s "${OBJECT_MANIFEST}" ]] || die 3 "object-manifest.json vacio"
    OBJECT_MANIFEST_SIZE="$(file_size "${OBJECT_MANIFEST}")"
    OBJECT_MANIFEST_SHA="$(sha256_file "${OBJECT_MANIFEST}")"

    export SIGEDON_VERIFY_MANIFEST="${MANIFEST_FILE}"
    export SIGEDON_VERIFY_OBJECT_MANIFEST="${OBJECT_MANIFEST}"
    export SIGEDON_VERIFY_BACKUP_DIR="${BACKUP_DIR}"
    export SIGEDON_VERIFY_DIRNAME="${BACKUP_DIRNAME}"
    export SIGEDON_VERIFY_DUMP_SHA="${DUMP_SHA}"
    export SIGEDON_VERIFY_DUMP_SIZE="${DUMP_SIZE}"
    export SIGEDON_VERIFY_OBJECT_SHA="${OBJECT_MANIFEST_SHA}"
    export SIGEDON_VERIFY_OBJECT_SIZE="${OBJECT_MANIFEST_SIZE}"

    python3 <<'PY'
import hashlib
import json
import os
import sys

path = os.environ["SIGEDON_VERIFY_MANIFEST"]
object_manifest_path = os.environ["SIGEDON_VERIFY_OBJECT_MANIFEST"]
backup_dir = os.environ["SIGEDON_VERIFY_BACKUP_DIR"]
dirname = os.environ["SIGEDON_VERIFY_DIRNAME"]
dump_sha = os.environ["SIGEDON_VERIFY_DUMP_SHA"]
dump_size = int(os.environ["SIGEDON_VERIFY_DUMP_SIZE"])
object_sha = os.environ["SIGEDON_VERIFY_OBJECT_SHA"]
object_size = int(os.environ["SIGEDON_VERIFY_OBJECT_SIZE"])


def die(msg: str) -> None:
    print(msg, file=sys.stderr)
    sys.exit(3)


try:
    with open(path, "r", encoding="utf-8") as handle:
        manifest = json.load(handle)
except (OSError, json.JSONDecodeError) as exc:
    die(f"manifest.json invalido: {exc}")

required_top = (
    "format_version",
    "backup_id",
    "created_at_utc",
    "database",
    "private_storage",
    "application",
    "consistency",
)
for key in required_top:
    if key not in manifest:
        die(f"manifiesto incompleto: falta {key}")

if manifest.get("format_version") != 2:
    die("format_version no soportado para ruta v2")

private_storage = manifest["private_storage"]
if not isinstance(private_storage, dict):
    die("private_storage invalido")
if private_storage.get("mode") != "object":
    die("format_version 2 requiere private_storage.mode=object")

# Do not treat presence of media.tar.gz as authoritative for v2.
if "media" in manifest:
    die("format_version 2 object mode no debe incluir bloque media")

if manifest.get("backup_id") != dirname:
    die("backup_id del manifiesto no coincide con el directorio")

database = manifest["database"]
application = manifest["application"]
consistency = manifest["consistency"]
ometa = private_storage.get("object_manifest")
if not isinstance(ometa, dict):
    die("private_storage.object_manifest ausente")

for key in ("filename", "sha256", "size_bytes", "postgres_client_version"):
    if key not in database:
        die(f"manifiesto incompleto: database.{key}")
for key in ("filename", "sha256", "size_bytes", "object_count"):
    if key not in ometa:
        die(f"manifiesto incompleto: private_storage.object_manifest.{key}")
for key in ("git_commit", "git_branch", "django_version", "python_version"):
    if key not in application:
        die(f"manifiesto incompleto: application.{key}")
for key in ("maintenance_confirmed", "strategy"):
    if key not in consistency:
        die(f"manifiesto incompleto: consistency.{key}")

if database["filename"] != "database.dump":
    die("database.filename inesperado")
if ometa["filename"] != "object-manifest.json":
    die("object_manifest.filename inesperado")

if database["sha256"] != dump_sha:
    die("checksum database.dump incorrecto")
if int(database["size_bytes"]) != dump_size:
    die("size_bytes de database.dump no coincide")
if ometa["sha256"] != object_sha:
    die("checksum object-manifest.json incorrecto")
if int(ometa["size_bytes"]) != object_size:
    die("size_bytes de object-manifest.json no coincide")

try:
    with open(object_manifest_path, "r", encoding="utf-8") as handle:
        object_manifest = json.load(handle)
except (OSError, json.JSONDecodeError) as exc:
    die(f"object-manifest.json invalido: {exc}")

if int(object_manifest.get("format_version", 0)) != 1:
    die("object-manifest format_version no soportado")
if (object_manifest.get("private_storage") or {}).get("mode") != "object":
    die("object-manifest private_storage.mode invalido")

entries = object_manifest.get("objects") or []
declared_count = int(object_manifest.get("object_count", len(entries)))
if declared_count != len(entries):
    die("object_count no coincide con objects[]")
if int(ometa["object_count"]) != declared_count:
    die("object_count del manifiesto superior no coincide")

objects_root = os.path.join(backup_dir, "objects")
if not os.path.isdir(objects_root):
    die("objects/ ausente")

for entry in entries:
    key = entry.get("key") or ""
    rel = entry.get("relative_path") or ""
    expected_sha = entry.get("sha256") or ""
    expected_size = int(entry.get("size_bytes", -1))
    if not key or not rel or not expected_sha or expected_size < 0:
        die("entrada de object-manifest incompleta")
    if ".." in rel.split("/") or rel.startswith("/"):
        die("relative_path inseguro en object-manifest")
    source = os.path.join(backup_dir, rel)
    if not os.path.isfile(source):
        # Fallback objects/<key>
        source = os.path.join(objects_root, key)
    if not os.path.isfile(source):
        die(f"objeto ausente: {key}")
    digest = hashlib.sha256()
    size = 0
    with open(source, "rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
    if size != expected_size:
        die(f"size mismatch para objeto: {key}")
    if digest.hexdigest() != expected_sha:
        die(f"checksum incorrecto para objeto: {key}")

forbidden = (
    "password",
    "token",
    "connection_url",
    "dsn",
    "pgpassword",
    "access_key",
    "secret_access",
    "endpoint_url",
    "bucket_name",
)
blob = json.dumps(manifest).lower()
for word in forbidden:
    if word in blob:
        die(f"manifiesto contiene campo sensible prohibido: {word}")

print("OK: backup verificado (format_version=2 object)", file=sys.stderr)
sys.exit(0)
PY
    ;;
  *)
    die "format_version no soportado: ${FORMAT_VERSION:-<ausente>}"
    ;;
esac

exit 0
