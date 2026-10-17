# marketdata

Single writer of the Parquet lake (D4) and of the reference-data schema `refdata`; from section 05 also historical backfill, live streams, bar building, gap repair, cache writes, and cross-source reconciliation.

**Status:** In progress (Phase 1: storage layer; ingestion lands with section 05)  ·  **Language/stack:** Go ([ADR-0004](../../docs/adr/0004-core-language-split.md), D1; [ADR-0020](../../docs/adr/0020-lake-layout-write-path-and-query-layer.md), D38)

## Responsibilities
- Owns: the lake under `TB_LAKE_DIR` (bars, bar_revisions, context, ref mirrors), the manifest `marketdata.lake_files`, schema `refdata` (instrument master, symbols, broker IDs, attributes, calendar mirror, corporate actions, universes).
- Does not own: feature snapshots (`snapshots/`, written by `tbcore.features`), broker connections (broker-gateway).

## Interfaces
| Direction | Kind | Name | Contract |
|---|---|---|---|
| in | HTTP (internal) | ops endpoints `:8081` | OP-010 |
| out | filesystem | Parquet lake (host APFS bind mount) | ST-001..ST-008 |
| out | Postgres | schemas `refdata` (writer), `marketdata` | `libs/go/db/migrations/{refdata,marketdata}/` |
| out | NATS | connection (subjects arrive with section 05) | `contracts/proto` |

## Business rules enforced here
- `ST-001`..`ST-008`, `ST-010` → `internal/lake`, `internal/dq`; `DF-001`, `DF-005`, `DF-007` → `lake.RowFromBar`, `lake.Compact`; `DF-006`, `MD-011`, `ST-007` → `internal/refdata`.

## Layout
| Path | Purpose |
|---|---|
| `cmd/marketdata/main.go` | Entrypoint on `libs/go/svc`; `spike` subcommands (`spike.go`) |
| `internal/lake/` | Lake writer: Parquet schemas, staging, compaction with revisions, atomic writes, manifest (memory + Postgres), verify, retention, context rows, ref exports |
| `internal/refdata/` | Reference store (memory + Postgres load/flush), symbol history, delisting, universes, corporate-action availability, calendar mirror, lake export |
| `internal/dq/` | Data-quality checks (ST-010) |
| `internal/spikes/` | Verify-early harnesses (V4 atomic rename) |

## Configuration
| Variable | Default | Notes |
|---|---|---|
| `TB_NATS_USER` | `marketdata` | Password from secret `nats_marketdata_password` |
| `TB_LAKE_DIR` | `/data/lake` | Host APFS bind mount (D4); container runs as the host UID |

## Run and test
- Run: `make up`. Build: `go build ./...`. Lint: `golangci-lint run ./...`.
- Test: `go test -race ./...`; Postgres-backed refdata test with `TB_TEST_PG_SUPER_DSN` (`make test-integration`). Regenerate the Python-read fixture lake: `TB_UPDATE_FIXTURES=1 go test ./internal/lake -run Fixture`.
- Spike V4 on the Mac: `make spike-v4`.

## Operational notes
Readiness fails if NATS is unreachable. No trading behavior.

## Gotchas
- Never write the lake from another process (D4); feature snapshots are the only exception and live under `snapshots/`.
- Compaction rewrites a whole month partition; run it at session end for large universes.
