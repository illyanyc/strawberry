# broker-gateway

Custodian of broker credentials and home of the Alpaca adapter, the broker simulator, broker sessions, order submit/cancel/replace, execution and account streams, and the single Alpaca market-data socket. It is the only process that can decrypt a broker credential, and it never returns plaintext to anyone. The IBKR adapter is a separate Python service (`broker-gateway-ibkr`, D1/D18) serving the same gRPC contract.

**Status:** In progress (Phase 1: Alpaca adapter, simulator, market-data hub)  ·  **Language/stack:** Go ([ADR-0004](../../docs/adr/0004-core-language-split.md), D1; [ADR-0011](../../docs/adr/0011-secrets-management.md), D16; [ADR-0018](../../docs/adr/0018-broker-gateway-interface.md), D36; [ADR-0019](../../docs/adr/0019-alpaca-adapter-and-simulator.md), D37)

## Responsibilities
- Owns: `secrets.broker_credentials` (envelope-encrypted), the KEK ring, `CredentialService`; schema `broker` (executions, order events, live enablement (read-only), outbox); `BrokerGatewayService` and `BrokerMarketDataService` for Alpaca and simulator accounts; bus subjects `ord.update.<acct>` (`BrokerOrderUpdate`), `exec.<acct>`, `acct.snapshot.<acct>`, `recon.request.<acct>`, `alert.*`.
- Does not own: authorization of the owner (api-gateway), risk and the OMS (execution), ledgers and reconciliation (ledger), market-data storage (marketdata), IBKR (broker-gateway-ibkr).

## Interfaces
| Direction | Kind | Name | Contract |
|---|---|---|---|
| in | gRPC `:9090` | `tb.broker.v1.CredentialService` (api-gateway only) | `contracts/proto/tb/broker/v1/credentials.proto` |
| in | gRPC `:9090` | `tb.broker.v1.BrokerGatewayService` (orders: execution only; reads: execution, ledger, marketdata, api-gateway; see `grpcsvc.AllowedCallers`) | `contracts/proto/tb/broker/v1/gateway.proto` |
| in | gRPC `:9090` | `tb.broker.v1.BrokerMarketDataService` (marketdata; feed status also api-gateway) | `contracts/proto/tb/broker/v1/market_data.proto` |
| out | HTTPS/WSS | Alpaca trading REST, `trade_updates` stream, data REST, market-data stream | brokers.md "Alpaca specifics" |
| out | Postgres | schemas `secrets`, `broker`; `audit.append` | `libs/go/db/migrations/{secrets,broker}/` |
| out | NATS | outbox relay → `ord.update.*`, `exec.*`, `acct.snapshot.*`, `recon.request.*`, `alert.*` (user `broker_gateway`) | ADR-0018 |

## Business rules enforced here
- Attribution IDs are validated with the shared `libs/go/attribution` package (BR-003, VP-002).
- `BR-001`..`BR-021` (brokers.md): contract suite, capabilities, attribution, error model, reject and status mapping, idempotent submit, canonical exec IDs, ordering, session state machine, resync, backoff, live gate, audit, single data socket, budgets, rounding, bracket legs, manual orders, no account-wide endpoints, margin events.
- `OE-002`..`OE-011` (adapter side), `VP-001` (manual orders pass through untouched).
- `SE-030`..`SE-033` credential custody; `SE-040` per-method caller allowlists (`internal/grpcsvc`, `internal/credsvc`); `SE-012` redaction of credential material in errors and logs; `SE-021` order requests audited before sending.

## Layout
| Path | Purpose |
|---|---|
| `cmd/broker-gateway/` | Entrypoint: config, credential cache, accounts, outbox relay, gRPC services; subcommands `rewrap-keys`, `healthcheck` |
| `internal/credstore/`, `internal/credsvc/` | Credential store (envelope encryption, KEK ring) and the write-only gRPC service |
| `internal/session/` | Session state machine (BR-010) |
| `internal/adapter/` | `Broker` interface, `Account` engine (session loop, resync, idempotent submit, dedupe, audit, margin events), validation, tick rounding, fan-out |
| `internal/execstore/` | Durable executions/order events + outbox (Postgres) and an in-memory store for tests |
| `internal/livegate/` | Live gate: global flag + audited per-account enablement (BR-013) |
| `internal/alpaca/` | Alpaca REST client (route table, budgets, ambiguity), `trade_updates` stream, data REST, market-data `DataStream`, status/reject mapping |
| `internal/alpaca/alpacatest/` | `httptest` fake of Alpaca REST + both WebSockets backed by the simulator (tests only) |
| `internal/sim/` | Deterministic broker simulator with fault hooks |
| `internal/mdhub/` | Market-data fan-out over one upstream per feed (BR-015) |
| `internal/grpcsvc/` | gRPC servers and allowlists |
| `internal/contract/` | Shared contract suite (BR-001) + in-process harness; opt-in remote and paper targets |
| `internal/testkit/` | Test wiring (fast options, sim hooks, audit recorder) |
| `internal/deadman/` | Execution-heartbeat dead-man: cancels working `tb1-` orders (never manual orders or protective stops/legs) 30 s after the last `sys.health.execution` during the session (RM-017) |
| `simstack/` | Public package: simulator accounts behind the adapter engine and `BrokerGatewayService`, publishing through a callback (paper harness `tests/paper`) |

## Configuration
| Variable | Default | Notes |
|---|---|---|
| `TB_DEADMAN_TIMEOUT` | `30s` | Execution-heartbeat dead-man (RM-017); `0` disables |
| `TB_GRPC_ADDR` | `:9090` | Internal network only |
| `TB_ALPACA_ACCOUNTS` | `alpaca-paper:paper` | `<id>:<paper\|live>` list; credentials come from the UI (encrypted store) |
| `TB_ALPACA_DATA_ACCOUNT` | `alpaca-paper` | Credential used for the single market-data socket per feed |
| `TB_ALPACA_DATA_FEEDS` | `alpaca.sip,alpaca.iex` | Declared in capabilities; SIP needs Algo Trader Plus (O2) |
| `TB_ALPACA_TRADING_RATE_PER_MIN` / `TB_ALPACA_DATA_RATE_PER_MIN` | `160` / `8000` | 80% of Alpaca's limits (use `160` data on Basic) |
| `TB_ALPACA_SNAPSHOT_INTERVAL` | `15s` | Account snapshot poll |
| `TB_SIM_ACCOUNTS` | empty (Compose: `sim-paper`) | Simulator accounts for development |
| `TB_TRADING_LIVE_ENABLED` | `false` | Global live flag (BR-013) |
| `TB_NATS_USER` | `broker_gateway` | Password from `nats_broker_gateway_password` |
| `TB_NTP_SERVER` / `TB_CLOCK_MAX_SKEW` | `time.cloudflare.com` / `250ms` | Clock-skew metric (OP-020) |
| Secrets | — | `broker_kek`, `pg_svc_broker_gateway_password`, `nats_broker_gateway_password`, `svc_{api_gateway,execution,ledger,marketdata}_ed25519.pub` |

## Run and test
- Run: `make up`. KEK rotation: prepend a new key line to `broker_kek`, restart, then `docker compose exec broker-gateway /app/service rewrap-keys`.
- Test: `go test -race ./...` (contract suite against the simulator and the Alpaca fake); Postgres-backed tests (`execstore`, `livegate`) via `make test-integration` or `TB_TEST_PG_SUPER_DSN`.
- Opt-in real APIs: `TB_ALPACA_PAPER_KEY_ID=... TB_ALPACA_PAPER_SECRET=... go test ./internal/alpaca/ -run Paper`; remote adapter (IBKR): `TB_BROKER_CONTRACT_ADDR=host:port TB_BROKER_CONTRACT_ACCOUNT=ibkr-paper TB_BROKER_CONTRACT_SEED_FILE=.secrets/svc_execution_ed25519 go test ./internal/contract/ -run Remote`.

## Operational notes
- `/healthz`, `/readyz` (postgres, bus), `/metrics` on `:8081`: `tb_broker_session_state`, `tb_broker_order_ops_total`, `tb_broker_submit_seconds`, `tb_broker_update_propagation_seconds`, `tb_broker_executions_total`, `tb_broker_duplicate_executions_total`, `tb_broker_reconnects_total`, `tb_outbox_*`, `tb_md_hub_*`.
- Runbook: [`docs/runbooks/broker-outage.md`](../../docs/runbooks/broker-outage.md). Losing every KEK makes stored credentials unrecoverable: keep an offline copy of `broker_kek` ([`secrets-and-keys.md`](../../docs/runbooks/secrets-and-keys.md)).

## Gotchas
- The simulator can impersonate an IBKR account (`sim.Config.Broker`/`Capabilities`, no bracket support) for D30 swing tests; real IBKR goes through `broker-gateway-ibkr`.
- Credential values are registered with the log redactor while handled; never log request structs or HTTP requests.
- Moving a row between `paper` and `live` makes it undecryptable by design (AAD).
- Order RPC errors use `FAILED_PRECONDITION`, never `UNAVAILABLE` (auto-retry risk, BR-004).
- Never add `DELETE /v2/orders` or `DELETE /v2/positions` to `alpaca.Routes` (BR-020); the client refuses routes not in the table.
- Historical bars are always requested raw with `asof` = the window's last New York date; dropping `asof` makes recycled tickers return the wrong company's history (MD-001, spike V2).
- `alpacatest` JSON is built from public docs; re-record fixtures from a real paper account when keys exist.
