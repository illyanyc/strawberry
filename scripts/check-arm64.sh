#!/usr/bin/env bash
# Every pinned third-party image (Compose files and Dockerfiles) must publish linux/arm64 so the stack
# runs natively on the Apple Silicon Mac Mini (OP-043). Registry hiccups (rate limits) are retried and
# reported; a missing arm64 variant fails.
set -euo pipefail
cd "$(dirname "$0")/.."
images=$( { grep -hoE 'image: [^ ]+@sha256:[a-f0-9]+' infra/compose/*.yml | awk '{print $2}';
            grep -hoE '_IMAGE=[^ ]+@sha256:[a-f0-9]+' infra/docker/*.Dockerfile | cut -d= -f2; } | sort -u)
fail=0
for img in $images; do
  out=""
  for attempt in 1 2 3 4; do
    if out=$(docker buildx imagetools inspect "$img" 2>&1); then break; fi
    sleep $((attempt * 5))
  done
  if grep -q 'linux/arm64' <<<"$out"; then
    echo "arm64 ok  $img"
  elif grep -qiE 'toomanyrequests|rate limit|unauthorized|timeout|connection' <<<"$out"; then
    echo "REGISTRY ERROR  $img: $(head -1 <<<"$out")"; fail=1
  else
    echo "NO ARM64  $img"; fail=1
  fi
done
exit $fail
