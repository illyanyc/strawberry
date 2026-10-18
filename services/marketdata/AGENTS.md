# marketdata

Single writer of the Parquet lake (D4) and of the reference-data schema `refdata`; historical backfill, live bar pipeline, gap repair, cache writes, cross-source reconciliation, and the daily reference sync.

**Status:** Phase 1 complete (storage, ingestion, reconciliation)  ·  **Language/stack:** Go ([ADR-0004](../../docs/adr/0004-core-language-split.md), D1; [ADR-0020](../../docs/adr/0020-lake-layout-write-path-and-query-layer.md), D38; [ADR-0022](../../docs/adr/0022-market-data-ingestion.md), D40)

## Responsibilities
- Owns: the lake under `TB_LAKE_DIR` (bars, bar_revisions, context, ref mirrors), the manifest `marketdata.lake_files`, backfill jobs, gaps, reconciliation results and quarantine (schema `marketdata`), schema `refdata` (instrument master, symbols, broker IDs, attributes, calendar mirror, corporate actions, universes), Valkey keys `bar:*` and `md:watermark`.
- Does not own: feature snapshots (`snapshots/`, written by `tbcore.features`), broker connections and Alpaca keys (broker-gateway), trading decisions (execution consumes `IsFresh`).

## Interfaces
| Direction | Kind | Name | Contract |
|---|---|---|---|
| in | gRPC `:9090` | `tb.marketdata.v1.MarketDataService` (Backfill, GetBackfillStatus, SetUniverse: api-gateway; GetFeedStatus, IsFresh: execution, api-gateway) | `contracts/proto/tb/marketdata/v1/marketdata.proto`, MD-001, MD-007 |
| in | HTTP (internal) | ops endpoints `:8081` | OP-010 |
| out | gRPC | broker-gateway `BrokerMarketDataService` (historical bars, stream, assets, corporate actions) | ADR-0018, BR-015 |
| out | HTTPS | FMP `/stable` (assets, delistings, symbol changes, splits, dividends, EOD) | MD-008 |
| out | NATS | `md.bar.<tf>.<id>`, `md.bar.partial.*`, `md.bar.correction.*` (MARKETDATA); `ref.corporate_action.<id>` (REF); `sys.health.<source>`; `alert.warning` | MD-002, MD-012 |
| out | Valkey | `bar:last`, `bar:ring`, `md:watermark` | `contracts/cache/README.md` |
| out | filesystem | Parquet lake (host APFS bind mount) | ST-001..ST-008 |
| out | Postgres | schemas `refdata` (writer), `marketdata` | `libs/go/db/migrations/{refdata,marketdata}/` |

## Business rules enforced here
- `MD-001`, `MD-003`, `MD-009` → `internal/ingest`; `MD-002`, `MD-004` (building), `MD-006`, `MD-007` → `internal/live`; `MD-008` → `internal/fmp`; `MD-010` → `internal/recon`; `MD-011`, `MD-012` → `internal/refdata`, `internal/refsync`.
- `ST-001`..`ST-008`, `ST-010` → `internal/lake`, `internal/dq`; `DF-001`, `DF-005`, `DF-007` → `lake.RowFromBar`, `lake.Compact`; `DF-006`, `ST-007` → `internal/refdata`.

## Layout
| Path | Purpose |
|---|---|
| `cmd/marketdata/main.go` | Entrypoint: wiring, startup history/catch-up jobs, health publisher, scheduler jobs; `spike` (`spike.go`) and `lake verify` subcommands |
| `internal/api/` | `MarketDataService` server and caller allowlist |
| `internal/ingest/` | Backfill jobs (plan, chunks, checkpoints, gap pass), normalization (symbol → instrument, session alignment, `available_at`), broker source, Postgres job store |
| `internal/live/` | Live pipeline (finality window, corrections, resampling, repair, freshness), stream client, sinks (bus, cache, lake) |
| `internal/fmp/` | FMP `/stable` client (rate limit, bandwidth, retries, breaker, redaction) |
| `internal/recon/` | Cross-source daily reconciliation and quarantine |
| `internal/refsync/` | Daily reference sync and corporate-action cross-check |
| `internal/sched/` | Calendar-aware once-per-session job runner |
| `internal/universe/` | Embedded dev universe (`dev.txt`) and loader |
| `internal/lake/` | Lake writer: Parquet schemas, staging, compaction with revisions, atomic writes, manifest (memory + Postgres), verify, retention, context rows, ref exports |
| `internal/refdata/` | Reference store (memory + Postgres load/flush), symbol history, delisting, universes, corporate-action availability and owner resolutions, calendar mirror, lake export |
| `internal/dq/` | Data-quality checks (ST-010) |
| `internal/spikes/` | Verify-early harnesses (V2 delisted probe, V3 FMP probe, V4 atomic rename) |

## Configuration
| Variable | Default | Notes |
|---|---|---|
| `TB_NATS_USER` | `marketdata` | Password from secret `nats_marketdata_password` |
| `TB_LAKE_DIR` | `/data/lake` | Host APFS bind mount (D4); container runs as the host UID |
| `TB_BROKER_GATEWAY_ADDR` | `broker-gateway:9090` | Service token audience `broker-gateway` |
| `TB_MD_FEED` | `alpaca.sip` | `alpaca.iex` without Algo Trader Plus (O2) |
| `TB_MD_STREAM_ENABLED` | `true` | |
| `TB_UNIVERSE` / `TB_UNIVERSE_FILE` | `dev` / embedded | Ticker list file overrides the embedded dev universe |
| `TB_MD_HISTORY_START` | empty | `YYYY-MM-DD`: one-off history load on start |
| `TB_MD_CATCHUP_DAYS` | `10` | Every start backfills the last N days |
| `TB_PUBLICATION_DELAYS` | built-in | e.g. `alpaca.sip=60s,fmp/TIMEFRAME_1D=6h` (MD-003) |
| `TB_FMP_RATE_PER_MIN` / `TB_FMP_BANDWIDTH_GB_30D` | `240` / `16` | 80% of the plan (V3) |
| `TB_MD_MIN_GAP_RUN_1M` | `5` | Minutes; shorter runs are not gaps |
| `TB_LAKE_FLUSH_INTERVAL` / `TB_LAKE_TICK_RETENTION` | `30s` / `2160h` | |
| secret `fmp_api_key` | `unset` | FMP features disabled until the owner writes the key |

## Run and test
- Run: `make up`. Build: `go build ./...`. Lint: `golangci-lint run ./...`.
- Test: `go test -race ./...` (fakes for broker-gateway and FMP, embedded NATS for the U8 latency test); Postgres-backed tests with `TB_TEST_PG_SUPER_DSN` (`make test-integration`). Regenerate the Python-read fixture lake: `TB_UPDATE_FIXTURES=1 go test ./internal/lake -run Fixture`.
- Spikes: `make spike-v2`, `make spike-v3` (owner keys), `make spike-v4` (Mac). Lake integrity: `make lake-verify`.

## Operational notes
Readiness fails if NATS is unreachable. Alerts and fixes: [market-data runbook](../../docs/runbooks/market-data.md). No trading behavior; execution must treat `IsFresh = false` as "block new risk".

## Gotchas
- Never write the lake from another process (D4); feature snapshots are the only exception and live under `snapshots/`. `lake verify` and spikes are read-only or use their own directory.
- Reference data is held in memory and flushed to Postgres: edit `refdata` rows only while marketdata is stopped.
- Compaction rewrites a whole month partition; it runs in the end-of-day job.
- After NATS permissions change (`tools/tbctl/secrets.go`), restart NATS so it reloads `nats_users.conf`.
- Vendor 1-hour bars are refused by backfill (they start on the clock hour); 1h comes from minutes.
