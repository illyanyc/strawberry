#!/usr/bin/env bash
# Restore a backup made by backup.sh into the running (typically fresh) stack (OP-051), then prove it:
# file checksums, per-table DB checksums, and audit-chain verification must all match the manifest.
# Runs in the ops container: make restore BACKUP=backups/strawberry-<ts>.tar.zst.age
set -euo pipefail

file=${1:?usage: restore.sh <backup file>}
identity=${BACKUP_IDENTITY:-/run/secrets/backup_age_identity}
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

age -d -i "$identity" "$file" | zstd -q -d | tar -C "$work" -xf -
(cd "$work" && sha256sum -c --quiet SHA256SUMS)
echo "restore: archive decrypted and file checksums verified"

export PGHOST=${TB_DB_HOST:-postgres} PGUSER=postgres
PGPASSWORD=$(cat /run/secrets/pg_superuser_password)
export PGPASSWORD

# Roles, databases, and schemas come from `tbctl db init` (passwords from current secrets).
tbctl db init >/dev/null
for db in platform mlflow prefect; do
  pg_restore --clean --if-exists --exit-on-error --single-transaction --dbname="$db" "$work/$db.dump"
done
echo "restore: databases restored"

for d in lake mlflow-artifacts; do
  if [ -f "$work/$d.tar" ]; then
    rm -rf "/data/${d:?}/"* 2>/dev/null || true
    tar -C /data -xf "$work/$d.tar"
  fi
done

tbctl db checksum >"$work/db-checksum.restored.json"
if ! diff <(jq -S . "$work/db-checksum.json") <(jq -S . "$work/db-checksum.restored.json") >/dev/null; then
  echo "restore: FAILED: table checksums differ" >&2
  diff <(jq -S . "$work/db-checksum.json") <(jq -S . "$work/db-checksum.restored.json") >&2 || true
  exit 1
fi
tbctl audit verify >"$work/audit.restored.json"
want=$(jq -r '.head_hash' "$work/audit-head.json")
got=$(jq -r '.head_hash' "$work/audit.restored.json")
if [ "$want" != "$got" ]; then
  echo "restore: FAILED: audit head $got != manifest $want" >&2
  exit 1
fi
echo "restore: OK (tables match, audit chain intact, head $(jq -r '.head_seq' "$work/audit.restored.json"))"
