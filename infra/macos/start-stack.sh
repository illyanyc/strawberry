#!/bin/bash
# Run by the com.strawberry.stack LaunchDaemon: wait for Colima's Docker socket, then `make up`.
set -euo pipefail
cd "$(dirname "$0")/../.."
for _ in $(seq 1 120); do
  if docker info >/dev/null 2>&1; then break; fi
  sleep 5
done
docker info >/dev/null 2>&1 || { echo "$(date -u +%FT%TZ) docker not ready after 10 minutes"; exit 1; }
echo "$(date -u +%FT%TZ) docker ready; starting stack"
exec make up NATIVE_ML="${NATIVE_ML:-}"
