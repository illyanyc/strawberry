#!/usr/bin/env bash
# Shared broker contract suite (BR-001) against the IBKR adapter's gRPC server backed by the fake
# gateway: starts `python -m tb_broker_ibkr.fakes.serve`, then runs the Go suite's remote mode
# (services/broker-gateway/internal/contract TestBR001ContractRemote) against it.
set -euo pipefail
cd "$(dirname "$0")/.."
port="${TB_IBKR_CONTRACT_PORT:-19090}"
tmp=$(mktemp -d)
trap 'kill "${pid:-}" 2>/dev/null || true; rm -rf "$tmp"' EXIT
uv run python -m tb_broker_ibkr.fakes.serve --addr "127.0.0.1:${port}" --seed-out "$tmp/execution.seed" \
  >"$tmp/server.log" 2>&1 &
pid=$!
for _ in $(seq 1 100); do
  [ -s "$tmp/execution.seed" ] && (exec 3<>"/dev/tcp/127.0.0.1/${port}") 2>/dev/null && break
  sleep 0.1
done
status=0
(cd services/broker-gateway && TB_BROKER_CONTRACT_ADDR="127.0.0.1:${port}" TB_BROKER_CONTRACT_ACCOUNT=ibkr-paper-fake \
  TB_BROKER_CONTRACT_SEED_FILE="$tmp/execution.seed" TB_BROKER_CONTRACT_SYMBOL=SPY \
  go test -count=1 -run TestBR001ContractRemote -v ./internal/contract/) || status=$?
if [ "$status" -ne 0 ]; then
  echo "--- IBKR adapter log"; tail -50 "$tmp/server.log"
fi
exit "$status"
