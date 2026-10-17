#!/usr/bin/env bash
# SE-051 / OP-041: every published container port must bind to 127.0.0.1 (or ::1). Also lists host
# listeners for evidence. Exit 1 on any non-loopback publication.
set -euo pipefail
fail=0
while IFS=$'\t' read -r name ports; do
  [ -z "$ports" ] && continue
  IFS=',' read -ra items <<<"$ports"
  for p in "${items[@]}"; do
    p=$(echo "$p" | xargs)
    if [[ "$p" == *"->"* ]] && [[ "$p" != 127.0.0.1:* ]] && [[ "$p" != "[::1]:"* ]]; then
      echo "NON-LOOPBACK PUBLISH: $name $p"
      fail=1
    elif [[ "$p" == *"->"* ]]; then
      echo "ok  $name $p"
    fi
  done
done < <(docker ps --filter label=com.docker.compose.project=strawberry --format '{{.Names}}\t{{.Ports}}')
if command -v ss >/dev/null 2>&1; then
  echo "--- host TCP listeners on non-loopback addresses (should not include 8080/3000/5432/4222):"
  ss -ltnH | awk '{print $4}' | grep -vE '^(127\.|\[::1\]|::1)' | grep -E ':(8080|3000|5432|4222|6379|9090|9093)$' && fail=1 || echo "(none)"
fi
exit $fail
