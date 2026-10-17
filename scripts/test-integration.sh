#!/usr/bin/env bash
# Runs Go integration tests (those gated on TB_TEST_PG_SUPER_DSN) against a throwaway Postgres.
# CI provides its own Postgres service and sets TB_TEST_PG_SUPER_DSN, in which case no container starts.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [ -z "${TB_TEST_PG_SUPER_DSN:-}" ]; then
  name="tb-itest-pg-$$"
  pw="$(head -c 18 /dev/urandom | base64 | tr -dc 'A-Za-z0-9')"
  docker run -d --rm --name "$name" -e POSTGRES_PASSWORD="$pw" -p 127.0.0.1::5432 postgres:17-alpine >/dev/null
  trap 'docker rm -f "$name" >/dev/null 2>&1 || true' EXIT
  port="$(docker port "$name" 5432/tcp | head -1 | sed 's/.*://')"
  for _ in $(seq 1 60); do
    docker exec "$name" pg_isready -U postgres >/dev/null 2>&1 && break
    sleep 1
  done
  sleep 1
  export TB_TEST_PG_SUPER_DSN="postgres://postgres:${pw}@127.0.0.1:${port}/postgres?sslmode=disable"
fi

for m in $(sed -n 's#^\s*\./##p' go.work); do
  echo "integration: $m"
  (cd "$m" && go test -race -count=1 ./...)
done
