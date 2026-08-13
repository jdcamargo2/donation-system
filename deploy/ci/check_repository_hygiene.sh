#!/usr/bin/env bash
# PRE: executed from a Git working tree of SIGEDON; git is available.
# POST: exits 0 when no prohibited tracked artifacts or merge-conflict markers
#       are found; exits non-zero and prints only matching paths (never contents).
#       Does not delete, modify, or scan untracked/runtime paths.
set -Eeuo pipefail

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"

failures=0

report() {
  local kind="$1"
  local path="$2"
  printf 'hygiene: %s: %s\n' "$kind" "$path"
  failures=1
}

# --- Tracked artifact policy (filenames only) ---------------------------------

while IFS= read -r -d '' path; do
  base="$(basename -- "$path")"
  case "$base" in
    .env.example)
      continue
      ;;
    .env|.env.*)
      report 'tracked-env' "$path"
      continue
      ;;
    .sigedon-backup-status.json|.sigedon-restore-drill-status.json)
      report 'tracked-backup-status' "$path"
      continue
      ;;
  esac

  case "$path" in
    media/*|media)
      report 'tracked-runtime-media' "$path"
      continue
      ;;
    staticfiles/*|staticfiles)
      report 'tracked-collected-static' "$path"
      continue
      ;;
  esac

  case "$base" in
    *.sqlite3|*.dump|*.backup|*.pem|*.key)
      report 'tracked-prohibited-artifact' "$path"
      continue
      ;;
  esac
done < <(git ls-files -z)

# --- Merge-conflict markers in tracked source-like files ----------------------

# Allow documentation that mentions markers as examples by excluding pure docs
# only when the line is clearly instructional; we still reject real markers in
# code and scripts. Docs under docs/ and *.md are scanned for exact marker lines.
while IFS= read -r -d '' path; do
  case "$path" in
    *.py|*.sh|*.yml|*.yaml|*.toml|*.ini|*.cfg|*.js|*.css|*.html|*.txt|*.md|*.sql|*.env.example)
      ;;
    *)
      continue
      ;;
  esac
  # Unambiguous conflict open/close markers. A lone ======= line (e.g. RST
  # underline) is not enough; require <<<<<<< or >>>>>>> in the same file.
  if git grep -n -E '^(<<<<<<<|>>>>>>>)' -- "$path" >/dev/null 2>&1; then
    report 'merge-conflict-marker' "$path"
  fi
done < <(git ls-files -z)

if [[ "$failures" -ne 0 ]]; then
  echo 'hygiene: repository hygiene check failed' >&2
  exit 1
fi

echo 'hygiene: OK'
exit 0
