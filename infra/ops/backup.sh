#!/usr/bin/env bash
# Encrypted local backup (OP-050): logical Postgres dumps, the Parquet lake, MLflow artifacts, a
# per-table checksum, and the audit head (SE-024), all age-encrypted to the backup recipient.
# Runs in the ops container: make backup.
set -euo pipefail

out_dir=${BACKUP_DIR:-/backups}
ts=$(date -u +%Y%m%dT%H%M%SZ)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

export PGHOST=${TB_DB_HOST:-postgres} PGUSER=postgres
PGPASSWORD=$(cat /run/secrets/pg_superuser_password)
export PGPASSWORD

pg_dumpall --globals-only --no-role-passwords >"$work/globals.sql"
for db in platform mlflow prefect; do
  pg_dump --format=custom --dbname="$db" --file="$work/$db.dump"
done
for d in lake mlflow-artifacts; do
  if [ -d "/data/$d" ]; then tar -C /data -cf "$work/$d.tar" "$d"; fi
done
tbctl audit head >"$work/audit-head.json"
tbctl db checksum >"$work/db-checksum.json"
(cd "$work" && sha256sum -- *.sql *.dump *.tar *.json 2>/dev/null >SHA256SUMS)
(cd "$work" && ls) | jq -R . | jq -s --arg ts "$ts" --slurpfile head "$work/audit-head.json" \
  '{created_at: $ts, format: "tb-backup-v1", files: ., audit_head: $head[0]}' >"$work/manifest.json"

mkdir -p "$out_dir"
target="$out_dir/strawberry-$ts.tar.zst.age"
tar -C "$work" -cf - . | zstd -q -19 -T0 | age -r "$(tr -d '[:space:]' </run/secrets/backup_age_recipient)" >"$target.partial"
mv "$target.partial" "$target"

mkdir -p "$out_dir/metrics"
cat >"$out_dir/metrics/backup.prom.tmp" <<EOF
# HELP tb_backup_last_success_timestamp_seconds Unix time of the last successful backup.
# TYPE tb_backup_last_success_timestamp_seconds gauge
tb_backup_last_success_timestamp_seconds $(date -u +%s)
EOF
mv "$out_dir/metrics/backup.prom.tmp" "$out_dir/metrics/backup.prom"
echo "backup written: $(basename "$target") ($(du -h "$target" | cut -f1))"
echo "audit head: $(cat "$work/audit-head.json")"
