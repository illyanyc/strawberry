#!/usr/bin/env bash
# D30 swing routing through the real IBKR adapter: starts the adapter's gRPC server over its fake
# gateway in auto-fill mode (`python -m tb_broker_ibkr.fakes.serve --auto-fill`), then runs
# tests/paper TestGate2SwingRoutesThroughIBKRAdapter with execution pointed at it.
set -euo pipefail
cd "$(dirname "$0")/.."
port="${TB_IBKR_PAPER_PORT:-19091}"
tmp=$(mktemp -d)
trap 'kill "${pid:-}" 2>/dev/null || true; rm -rf "$tmp"' EXIT
# tests/paper derives instrument IDs as 01J<symbol> zero-padded to 26 characters.
cat >"$tmp/instruments.json" <<'JSON'
[{"instrument_id": "01JSPY00000000000000000000", "con_id": 756733, "symbol": "SPY", "primary_exchange": "ARCA"}]
JSON
uv run python -m tb_broker_ibkr.fakes.serve --addr "127.0.0.1:${port}" --seed-out "$tmp/execution.seed" --auto-fill \
  --instruments "$tmp/instruments.json" \
  >"$tmp/server.log" 2>&1 &
pid=$!
for _ in $(seq 1 100); do
  [ -s "$tmp/execution.seed" ] && (exec 3<>"/dev/tcp/127.0.0.1/${port}") 2>/dev/null && break
  sleep 0.1
done
status=0
(cd tests/paper && TB_IBKR_ADAPTER_ADDR="127.0.0.1:${port}" TB_IBKR_SEED_FILE="$tmp/execution.seed" \
  go test -count=1 -run TestGate2SwingRoutesThroughIBKRAdapter -v ./...) || status=$?
if [ "$status" -ne 0 ]; then
  echo "--- IBKR adapter log"; tail -80 "$tmp/server.log"
fi
exit "$status"
