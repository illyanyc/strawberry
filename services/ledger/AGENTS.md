# ledger

Owns the full real account per broker, split into per-strategy virtual portfolios and the `manual` bucket, plus the broker-lot shadow ledger, reservations, allocations, and reconciliation (section 06, [ADR-0024](../../docs/adr/0024-virtual-ledgers-and-reconciliation.md), D42). Rules: VP-001..VP-025 in [`virtual-portfolios-and-attribution.md`](../../docs/business-logic/virtual-portfolios-and-attribution.md).

**Status:** Phase 2 (paper)  ·  **Language/stack:** Go, pgx, shopspring/decimal, NATS JetStream ([ADR-0004](../../docs/adr/0004-core-language-split.md), D1)

## Responsibilities
- Owns: schema `ledger` (event log and materialized state), `LedgerService`, `ledger.update.*`, `recon.result.*`.
- Does not own: orders or risk limits (execution), broker sessions (broker adapters), instrument master (marketdata `refdata`, read-only here), open-order reconciliation (execution, OE-019).

## Interfaces
| Direction | Kind | Name | Contract |
|---|---|---|---|
| in | gRPC `:9090` | `tb.ledger.v1.LedgerService`; callers execution (order path) and api-gateway (views, owner actions) | `contracts/proto/tb/ledger/v1/ledger_service.proto` |
| in | NATS (durable) | `exec.>` (ORDERS), `acct.snapshot.>` and `recon.request.>` (LEDGER), `ref.corporate_action.>` (REF) | `tb.orders.v1.ExecutionReport`, `tb.ledger.v1.AccountSnapshot`, `ReconciliationRequest`, `tb.marketdata.v1.CorporateAction` |
| out | NATS (outbox) | `ledger.update.<acct>`, `recon.result.<acct>`, `alert.critical` | `LedgerUpdate`, `ReconciliationResult`, `tb.alerts.v1.Alert` |
| out | Postgres | schema `ledger` (owner `ledger_owner`), `refdata` (read), `audit.append` | `libs/go/db/migrations/ledger/` |

## Business rules enforced here
- VP-001, VP-015..VP-018 attribution, opening balance, breaks → `internal/engine/engine.go`, `internal/book/events.go`
- VP-003..VP-005, VP-020 ownership and capital → `internal/book/capital.go`
- VP-007, VP-008, VP-013, VP-014 reconciliation → `internal/book/recon.go`
- VP-009 allocations, VP-019 resolutions (audited) → `internal/engine/engine.go`
- VP-010, VP-023 corporate actions; VP-021 shadow ledger; VP-022 wash sales; VP-024 equity; VP-025 reports
- Tests cite the IDs: `internal/book/book_test.go`, `internal/engine/engine_test.go`

## Layout
| Path | Purpose |
|---|---|
| `cmd/ledger/` | Entrypoint (config, Postgres, NATS, relay, gRPC with the svcauth allowlist) |
| `app/` | Public wiring: engine + consumers + tick loop + gRPC registration; memory and Postgres stores (used by `tests/paper`) |
| `internal/book/` | Pure accounting core: owners, lots, shadow ledger, events, breaks, reservations, reconciliation, capital |
| `internal/engine/` | Serialized engine, persistence batches, Postgres and memory stores, refdata resolver, views |
| `internal/server/` | `LedgerService` implementation and caller allowlist |

## Configuration
| Variable | Default | Notes |
|---|---|---|
| `TB_GRPC_ADDR` | `:9090` | Internal network only |
| `TB_LEDGER_RECON_GRACE` / `TB_LEDGER_RECON_STALE_AFTER` | `60s` / `90s` | VP-013 |
| `TB_LEDGER_CASH_TOLERANCE` | `5` | USD, VP-018 |
| `TB_LEDGER_RESERVE_PCT` / `TB_LEDGER_RESERVE_ABS` | `0.10` / `0` | VP-009 |
| `TB_LEDGER_STRATEGY_BP_SHARE` | `1` | Strategies' combined share of broker buying power, VP-020 |
| Secrets | — | `pg_svc_ledger_password`, `nats_ledger_password`, `svc_api_gateway_ed25519.pub`, `svc_execution_ed25519.pub` |

## Run and test
- Run: `make up`. Build: `go build ./...`. Lint: `golangci-lint run ./...`.
- Test: `go test -race ./...`; Postgres-backed store test via `make test-integration` (`TestVPPostgresStoreRebuild`). The Phase 2 gate runs the ledger in-process: `make paper-gate`.

## Operational notes
- `/readyz` checks Postgres and NATS. A failed commit reloads state from Postgres (fail closed).
- Runbook: [`docs/runbooks/reconciliation-break.md`](../../docs/runbooks/reconciliation-break.md).

## Gotchas
- Never add UPDATE/DELETE grants on `ledger.events`; corrections are new events.
- Breaks and reservations are operational tables, overlaid after the event replay at startup; break IDs from events are deterministic (`brk-<acct>-<exec>`), diff breaks are `diff-<acct>-<instrument>[-<ts>]`.
- Snapshot positions without `instrument_id` are resolved by symbol through `refdata`; unresolved ones become `sym:<SYMBOL>` (manual only).
