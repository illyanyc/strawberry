# broker-gateway-ibkr

IBKR adapter: supervises the IB Gateway API session (daily restart, weekly 2FA login, outages), places and tracks orders with the attribution ID in `orderRef`, joins executions with commissions, publishes snapshots, and serves `tb.broker.v1.BrokerGatewayService` for its accounts. One process per environment (`broker-gateway-ibkr-paper`, `-live`).

**Status:** In progress (Phase 1, section 04)  ·  **Language/stack:** Python 3.12, `ib_async` 2.1, grpcio, asyncpg, nats-py on `tbcore` ([ADR-0023](../../docs/adr/0023-ibkr-adapter-gateway-and-sessions.md), D1/D18/D41; contract [ADR-0018](../../docs/adr/0018-broker-gateway-interface.md), D36)

## Responsibilities
- Owns: the IB Gateway API connections, IBKR order/execution state for mapped accounts, the execution cursor file, IBKR-specific mapping (statuses, error codes, rejects), pacing.
- Does not own: IBKR credentials (mounted only into `ib-gateway-<env>`, SE-034), risk and the OMS (execution), ownership and reconciliation (ledger), market-data ingestion (marketdata; IBKR data is minimal by D20).

## Interfaces
| Direction | Kind | Name | Contract |
|---|---|---|---|
| in | gRPC `:9090` | `tb.broker.v1.BrokerGatewayService` (callers per method: execution, ledger, marketdata, api-gateway; service tokens SE-040) | `contracts/proto/tb/broker/v1/gateway.proto` |
| out | TWS API socket | `ib-gateway-<env>:4004` (paper) / `:4003` (live), network `ibkr` only | IBKR TWS API via `ib_async` |
| out | NATS JetStream | `ord.update.<acct>`, `exec.<acct>` (`tb.orders.v1.ExecutionReport`), `acct.snapshot.<acct>`, `recon.request.<acct>`, `alert.<severity>` (`tb.alerts.v1.Alert`), `sys.health.broker-gateway-ibkr` | ADR-0018, ADR-0008 |
| out | Postgres (role `svc_broker_gateway_ibkr`) | `audit.append`; schema `broker_ibkr` (executions, order events, outbox, state); `broker.live_enablement` read-only | SE-020, BR-014, ADR-0023 |
| in | HTTP (internal) | `/healthz`, `/readyz` (`gateway_session`, `nats`, `live_enablement`), `/metrics` on `:8081` | OP-010 |

## Business rules enforced here
`IB-001`..`IB-073` in [`docs/business-logic/brokers-ibkr.md`](../../docs/business-logic/brokers-ibkr.md), and for IBKR: `BR-002`..`BR-014`, `BR-017`, `BR-019`..`BR-021`, `OE-002`..`OE-011`, `VP-001`, `SE-021`, `SE-040`. Tests carry the rule ID in their names.

## Layout
| Path | Purpose |
|---|---|
| `pyproject.toml` | uv workspace member `broker-gateway-ibkr` (script `broker-gateway-ibkr`) |
| `src/tb_broker_ibkr/__main__.py` | Entrypoint: settings, sinks (log, metrics, NATS, stream hub), audit, adapter, gRPC + ops servers, live gate |
| `src/tb_broker_ibkr/config.py` | `IbkrSettings` (`TB_*`), account map, schedule, live gate (IB-005/006/007) |
| `src/tb_broker_ibkr/models.py` | Domain types and typed errors (Decimal money, ns timestamps) |
| `src/tb_broker_ibkr/adapter.py` | `IbkrAdapter`: orders, cancel/replace, executions, snapshots, resync, holds, order translation and tick rounding |
| `src/tb_broker_ibkr/session.py` | `SessionSupervisor`: state machine, heartbeats, backoff, outage classification and alerts |
| `src/tb_broker_ibkr/schedule.py` | Maintenance calendar (ET), outage classifier, TIF cutoffs |
| `src/tb_broker_ibkr/port.py` | `IbPort` seam and port types |
| `src/tb_broker_ibkr/ib_async_port.py` | Production `IbPort` over `ib_async` (float → Decimal, optional recorder) |
| `src/tb_broker_ibkr/attribution.py` | BR-003 attribution IDs, owner resolution |
| `src/tb_broker_ibkr/status_map.py`, `error_codes.py` | IBKR status table, error-code categories |
| `src/tb_broker_ibkr/codec.py` | Domain ↔ protobuf, IBKR reject-code mapping |
| `src/tb_broker_ibkr/grpc_service.py`, `svcauth.py` | gRPC servicer, stream hub, service-token interceptor (Go-compatible tokens) |
| `src/tb_broker_ibkr/audit.py`, `bus.py`, `metrics.py`, `events.py` | Audit log, NATS publisher, Prometheus metrics, event sink fan-out |
| `src/tb_broker_ibkr/journal.py`, `relay.py` | `Journal` protocol, Postgres journal (`broker_ibkr` + outbox), DB live enablement; outbox relay to JetStream |
| `src/tb_broker_ibkr/cursor_store.py`, `pacing.py`, `backoff.py`, `clock.py`, `instruments.py` | File cursor (no-DB fallback journal), historical pacing + line budget, backoff, injectable clock, instrument map + IBKR contract resolver |
| `src/tb_broker_ibkr/fakes/` | `gateway.py` (port-level fake), `wire.py` (socket-level TWS fake for real `ib_async`), `replay.py` (fixture format, replay, recorder), `serve.py` (gRPC over the fake gateway for the Go contract suite; `--auto-fill` and `--instruments` for the cross-service swing test `make paper-ibkr`) |
| `ibkr_tests/` | Unit/behavior tests (package name distinct from `libs/py/tbcore/tests`) on virtual time, wire tests, gRPC and entrypoint tests; `fixtures/*.jsonl` recorded-format messages; `paper/` opt-in real-gateway suite and spike V1 |

## Configuration
| Variable | Default | Notes |
|---|---|---|
| `TB_IBKR_ENVIRONMENT` | `paper` | `paper` or `live`; one process per environment |
| `TB_IBKR_HOST` / `TB_IBKR_PORT` | `ib-gateway-paper` / `4004` | Live: `ib-gateway-live` / `4003` |
| `TB_IBKR_ACCOUNTS` | — (required) | `alias=CODE,…` → `ibkr-<env>-<alias>`; Compose takes `TB_IBKR_PAPER_ACCOUNTS` / `TB_IBKR_LIVE_ACCOUNTS` from `compose/.env` |
| `TB_IBKR_ORDER_REF_MAX_LEN` | `44` | Spike V1 may lower it |
| `TB_IBKR_DAILY_RESTART` / `TB_IBKR_COLD_RESTART` | `23:45` / `17:00` | ET; must match the gateway's IBC settings |
| `TB_IBKR_REAUTH_GRACE_SECONDS` | `180` | Grace before outage alerts |
| `TB_IBKR_SUBMIT_TIMEOUT` / `TB_IBKR_COMMISSION_WAIT` / `TB_IBKR_SNAPSHOT_INTERVAL` | `5` / `2` / `30` s | |
| `TB_IBKR_INSTRUMENTS_PATH` | — | JSON list of instruments (until the instrument master serves IBKR IDs) |
| `TB_IBKR_STATE_DIR` | `/var/lib/broker-gateway-ibkr` | Execution cursor (named volume) |
| `TB_IBKR_RECORD_PATH` | — | Opt-in: append gateway callbacks as fixture JSONL (account codes replaced) |
| `TB_TRADING_LIVE_ENABLED` | `false` | Live gate 1 (BR-013) |
| `TB_NATS_USER` / `TB_DB_USER` | — | `broker_gateway_ibkr` / `svc_broker_gateway_ibkr`. With a DB: audit, outbox journal, live gate. Without: every order request fails (SE-021) and a file cursor replaces the outbox (dev only) |
| `TB_GRPC_ADDR` / `TB_GRPC_CALLERS` | `0.0.0.0:9090` / `execution,ledger,marketdata,api-gateway` | Callers' public keys from `svc_<caller>_ed25519.pub` |
| Secrets | — | `nats_broker_gateway_ibkr_password`, `pg_svc_broker_gateway_ibkr_password`, `svc_*_ed25519.pub` |

## Run and test
- Run: `make up PROFILES=core,observability,ibkr` (paper; add `ibkr-live` for live). Setup and weekly login: [runbook](../../docs/runbooks/ibkr-weekly-2fa.md).
- Test: `uv run pytest services/broker-gateway-ibkr/ibkr_tests` (no IBKR needed; ~8 s; Postgres journal tests need `TB_TEST_PG_SUPER_DSN`). Shared Go contract suite over the fake gateway: `make test-ibkr-contract`. Opt-in real paper gateway and spike V1: `make test-ibkr-paper` with `TB_IBKR_PAPER_ACCOUNT`, `TB_IBKR_PAPER_HOST`, `TB_IBKR_PAPER_PORT`.
- Lint: `make lint-py`.

## Operational notes
- `/readyz` is 503 while the IBKR session is not ready (weekends, pending 2FA) and for a stopped live adapter; Docker health uses `/healthz`.
- Metrics: `tb_ibkr_session_state`, `tb_ibkr_session_ready`, `tb_ibkr_reauth_required`, `tb_ibkr_alert_active`, `tb_ibkr_connect_attempts_total`, `tb_ibkr_executions_total`, `tb_ibkr_margin_deficit`; alert rules in `infra/observability/prometheus/rules/ibkr.yml`.

## Gotchas
- Restarting `ib-gateway-<env>` mid-week forces a new 2FA; restart the adapter instead.
- `ib_async` delivers floats: always convert through `ib_async_port.dec`, never `Decimal(float)`.
- `orderStatus` callbacks carry ib_async's local `orderRef`; only `openOrder`/`completedOrder` (`ref_echoed=True`) count for IB-002.
- Tests run on virtual time (`FakeClock`): a call that waits on a clock timeout must be driven with `Harness.drive(...)`, or it hangs.
- The fixtures in `ibkr_tests/fixtures/` are hand-built in the recorded format, not captured from a real account; replace or extend them with `TB_IBKR_RECORD_PATH` recordings from the paper rehearsal.
