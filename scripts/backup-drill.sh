#!/usr/bin/env bash
# Backup/restore drill (OP-051): back up the running stack, destroy every Docker volume and move the
# host lake aside (a fresh host), bring up a fresh stack, restore, and verify:
#   - restore.sh checks archive checksums, per-table DB checksums, and the audit chain/head
#   - this script additionally diffs the restored lake against the original
set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="$PWD/.tools/bin:$PATH"

marker="data/lake/_drill/marker-$(date -u +%s).bin"
mkdir -p "$(dirname "$marker")"
head -c 65536 /dev/urandom >"$marker"
echo "drill: wrote lake marker $marker ($(sha256sum "$marker" | cut -c1-16)…)"

make -s backup
file=$(ls -t backups/strawberry-*.tar.zst.age | head -1)
echo "drill: backup $file"

echo "drill: destroying stack volumes and moving the lake aside"
make -s clean
rm -rf data/lake.drill-orig && mv data/lake data/lake.drill-orig && mkdir -p data/lake

make -s up >/dev/null
echo "drill: fresh stack is up; restoring"
make -s restore BACKUP="$file"

if diff -r data/lake.drill-orig data/lake >/dev/null; then
  echo "drill: lake restored byte-for-byte"
else
  diff -rq data/lake.drill-orig data/lake >&2 || true
  echo "drill: FAILED: lake differs" >&2; exit 1
fi
rm -rf data/lake.drill-orig data/lake/_drill
echo "drill: OK"
