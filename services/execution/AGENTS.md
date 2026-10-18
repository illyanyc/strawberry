# execution

Owns the OMS, the pre-trade risk gate (D30 routing, intraday-margin guard, PDT guard off by default), broker-side protection, latched kill switches, halts, the strategy-runtime dead-man, and the trading-day lifecycle (section 07, [ADR-0025](../../docs/adr/0025-oms-risk-gate-and-kill-switches.md), D43). Rules: OE-* ([orders](../../docs/business-logic/orders-and-execution.md)), RM-* ([risk](../../docs/business-logic/risk-management.md)), SM-001/012/013.

**Status:** Phase 2 (paper)  ·  **Language/stack:** Go, pgx, shopspring/decimal, NATS JetStream, gRPC ([ADR-0004](../../docs/adr/0004-core-language-split.md), D1)

## Responsibilities
- Owns: schema `execution`, `ExecutionService`, `ord.decision`, `ord.order.<acct>`, `risk.kill` (engagements), `sys.health.execution` heartbeat.
- Does not own: ledger math and reservations (ledger), broker sessions and attribution validation (adapters), sizing (strategy layer, `tbcore.strategy`), live promotion (section 15).

## Interfaces
| Direction | Kind | Name | Contract |
|---|---|---|---|
| in | gRPC `:9090` | `tb.orders.v1.ExecutionService` (caller api-gateway; monitor may read and engage) | `contracts/proto/tb/orders/v1/execution.proto` |
| in | NATS (durable) | `ord.intent`, `ord.update.>`, `exec.>`, `risk.kill` (ORDERS); `acct.snapshot.>`, `recon.result.>` (LEDGER); `md.bar.*.*` (ordered, last per subject) | `OrderIntent`, `BrokerOrderUpdate`, `ExecutionReport`, `KillSwitchEvent`, `AccountSnapshot`, `ReconciliationResult`, `Bar` |
| in | NATS (core) | `sys.health.strategy-runtime` | runtime heartbeat (dead-man) |
| out | gRPC | `BrokerGatewayService` per adapter (`TB_BROKER_ENDPOINTS`), `LedgerService` (`TB_LEDGER_ADDR`) | service tokens (SE-040) |
| out | NATS (outbox) | `ord.decision`, `ord.order.<acct>`, `risk.kill`, `alert.*`; core `sys.health.execution` | `RiskDecision`, `Order`, `KillSwitchEvent`, `Alert`, `TradingStatus` |
| out | Postgres | schema `execution`, `refdata` (read), `audit.append` | `libs/go/db/migrations/execution/` |

## Business rules enforced here
- RM-001..RM-016 gate → `internal/risk/risk.go` (`TestRM003RiskMatrix`, `TestRM008…`, `TestRM011…`)
- OE-001, OE-012..OE-020 OMS → `internal/oms/engine.go`, `events.go` (`TestOE006StateMachineConvergesUnderReordering`)
- RM-006, RM-007, RM-013, RM-016 kill switches → `internal/oms/kill.go`; RM-009, RM-017, OE-016 → `internal/oms/lifecycle.go`
- Cross-service acceptance: `tests/paper` (`TestGate2*`)

## Layout
| Path | Purpose |
|---|---|
| `cmd/execution/` | Entrypoint (config, Postgres, NATS, relay, clients, registry seed, gRPC allowlist) |
| `app/` | Public wiring: engine + consumers + tick loop, gRPC clients for adapters and the ledger, refdata instrument lookup, registry seed, metrics |
| `internal/risk/` | Pure pre-trade checks and limits |
| `internal/oms/` | OMS engine, stores (memory, Postgres), kill switches, lifecycle, views |
| `internal/server/` | `ExecutionService` and caller allowlist |

## Configuration
| Variable | Default | Notes |
|---|---|---|
| `TB_BROKER_ENDPOINTS` | `broker-gateway=broker-gateway:9090` | `audience=host:port` list; add `broker-gateway-ibkr=broker-gateway-ibkr-paper:9090` with the ibkr profile |
| `TB_LEDGER_ADDR` | `ledger:9090` | |
| `TB_ROUTING` | `intraday=alpaca-paper\|sim-paper;swing=ibkr-paper-bot` | RM-011 |
| `TB_ACCOUNT_POLICIES` | Alpaca/sim `mult=1,cap=1,pdt=off,seg=true`; IBKR bot `mult=2,cap=0.5,pdt=off,seg=false` | RM-008, RM-012, RM-014 |
| `TB_STRATEGY_REGISTRY` | `` (compose: `/etc/tb/strategies.json`) | SM-001 seed |
| `TB_INTRADAY_FLATTEN_BEFORE_CLOSE` / `TB_DEADMAN_TIMEOUT` | `10m` / `30s` | OE-016 / RM-017 |
| `TB_NTP_SERVER` / `TB_CLOCK_MAX_SKEW` | `time.cloudflare.com` / `250ms` | Clock check gates intents (fail closed) |
| Secrets | — | `pg_svc_execution_password`, `nats_execution_password`, `svc_execution_ed25519`, `svc_api_gateway_ed25519.pub` |

## Run and test
- Run: `make up`. Build: `go build ./...`. Lint: `golangci-lint run ./...`.
- Unit: `go test -race ./...`. Cross-service gate scenarios: `cd tests/paper && go test ./...`; the five-session ORB gate: `make paper-gate`.

## Operational notes
- `/readyz` checks Postgres and NATS. Metrics: `tb_trading_enabled`, `tb_execution_account_accepting{account}`, `tb_execution_open_orders`, `tb_execution_stuck_orders`, `tb_execution_unknown_orders`, `tb_execution_kill_switches_active`, `tb_execution_halts_active`, `tb_execution_runtime_heartbeat_age_seconds`, `tb_execution_day_trades{account}`.
- Runbook: [`docs/runbooks/kill-switch.md`](../../docs/runbooks/kill-switch.md).

## Gotchas
- Intent IDs must be ULIDs (they are part of the attribution ID). Child orders use attempts 50–99.
- After a restart nothing is approved until open orders are reconciled; after a reconnect until a reconciliation newer than the READY transition arrives.
- Released kill switches stay in memory and in the table (30 days) so a late `risk.kill` copy cannot re-latch them.
