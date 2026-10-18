#!/usr/bin/env bash
# Smoke test for a running stack (OP-061; CI runs it after `make up`):
#   1. every core service answers /readyz from inside its container
#   2. the gateway answers /healthz, rejects anonymous API calls, and sends security headers
#   3. no container publishes a non-loopback port
#   4. on a fresh stack: the end-to-end gate flow (passkey setup/login, step-up, credential storage)
#      with a canary secret, then the canary must not appear in any container log or in VictoriaLogs
#   5. Prometheus scrapes every core target, and the audit chain verifies
set -euo pipefail
cd "$(dirname "$0")/.."
COMPOSE=(docker compose -f infra/compose/docker-compose.yml)
BASE=${TB_E2E_BASE_URL:-http://localhost:8080}
pass() { printf '  \033[32mPASS\033[0m %s\n' "$*"; }
fail() { printf '  \033[31mFAIL\033[0m %s\n' "$*"; exit 1; }

echo "1. readiness"
for s in api-gateway broker-gateway marketdata execution ledger; do
  "${COMPOSE[@]}" exec -T "$s" /app/service healthcheck http://127.0.0.1:8081/readyz && pass "$s /readyz" || fail "$s /readyz"
done
for s in context-data monitor strategy-runtime; do
  "${COMPOSE[@]}" exec -T "$s" /app/.venv/bin/python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8081/readyz', timeout=3)" \
    && pass "$s /readyz" || fail "$s /readyz"
done

echo "2. gateway edge"
[ "$(curl -s -o /dev/null -w '%{http_code}' "$BASE/healthz")" = 200 ] && pass "/healthz 200" || fail "/healthz"
for p in /api/v1/settings /auth/me /api/v1/audit /api/v1/events; do
  code=$(curl -s -o /dev/null -w '%{http_code}' "$BASE$p")
  [ "$code" = 401 ] && pass "anonymous $p -> 401" || fail "anonymous $p -> $code"
done
hdrs=$(curl -s -D - -o /dev/null "$BASE/")
for h in "content-security-policy: default-src 'self'" "x-content-type-options: nosniff" "referrer-policy: no-referrer"; do
  grep -qi "$h" <<<"$hdrs" && pass "header ${h%%:*}" || fail "missing header $h"
done
curl -s "$BASE/" | grep -q '<div id="root">' && pass "UI served" || fail "UI not served"

echo "3. ports"
scripts/port-check.sh >/tmp/port-check.txt && pass "only loopback publications ($(grep -c '^ok' /tmp/port-check.txt) published ports)" || { cat /tmp/port-check.txt; fail "port check"; }

echo "4. end-to-end gate flow"
if curl -s "$BASE/auth/state" | grep -q '"setup_required":true'; then
  canary="leak-canary-$(head -c 9 /dev/urandom | od -An -tx1 | tr -d ' \n')"
  token=$("${COMPOSE[@]}" exec -T api-gateway /app/service setup-token | awk '{print $NF}')
  (cd tests/e2e && TB_E2E_BASE_URL="$BASE" TB_E2E_SETUP_TOKEN="$token" TB_E2E_CANARY="$canary" go test -count=1 -run TestPhase0Gate -v ./... | tail -3) \
    && pass "e2e passkey setup/login/step-up/credential flow" || fail "e2e flow"
  sleep 3
  if "${COMPOSE[@]}" logs --no-color 2>/dev/null | grep -q "$canary"; then fail "canary found in container logs"; fi
  pass "canary absent from all container logs"
  hits=$("${COMPOSE[@]}" exec -T prometheus wget -q -O - "http://victorialogs:9428/select/logsql/query?query=$canary" 2>/dev/null | wc -l || true)
  [ "${hits:-0}" -eq 0 ] && pass "canary absent from VictoriaLogs" || fail "canary found in VictoriaLogs"
  if "${COMPOSE[@]}" exec -T postgres psql -U postgres -d platform -Atc "select count(*) from secrets.broker_credentials where encode(ciphertext,'escape') like '%$canary%'" | grep -qv '^0$'; then
    fail "canary stored in plaintext"
  fi
  pass "canary not stored in plaintext"
else
  echo "  SKIP stack already has an owner (run against a fresh stack for the full flow)"
fi

echo "5. observability and audit"
sleep 5
down=$("${COMPOSE[@]}" exec -T prometheus wget -q -O - 'http://localhost:9090/api/v1/query?query=up%7Btier%3D%22core%22%7D%3D%3D0' | grep -o '"service":"[^"]*"' || true)
total=$("${COMPOSE[@]}" exec -T prometheus wget -q -O - 'http://localhost:9090/api/v1/query?query=count(up%7Btier%3D%22core%22%7D)' | sed -E 's/.*"value":\[[^,]*,"([0-9]+)"\].*/\1/')
[ -z "$down" ] && pass "Prometheus: all ${total:-?} core targets up" || fail "core targets down: $down"
make -s audit-verify | tail -1 | grep -q '"ok":true' && pass "audit chain verifies" || fail "audit chain"
echo "smoke: OK"
