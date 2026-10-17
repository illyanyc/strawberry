#!/usr/bin/env bash
# Every pinned third-party image (Compose files and Dockerfiles) must publish linux/arm64 so the stack
# runs natively on the Apple Silicon Mac Mini (D21).
set -euo pipefail
cd "$(dirname "$0")/.."
images=$( { grep -hoE 'image: [^ ]+@sha256:[a-f0-9]+' infra/compose/*.yml | awk '{print $2}';
            grep -hoE '_IMAGE=[^ ]+@sha256:[a-f0-9]+' infra/docker/*.Dockerfile | cut -d= -f2; } | sort -u)
fail=0
for img in $images; do
  if docker buildx imagetools inspect "$img" 2>/dev/null | grep -q 'linux/arm64'; then
    echo "arm64 ok  $img"
  else
    echo "NO ARM64  $img"; fail=1
  fi
done
exit $fail
