# marketdata

Will own historical backfill, live streams, bar building, gap repair, and lake/cache writes (section 05, Phase 1). It is the single writer of the Parquet lake (D4).

**Status:** Planned (Phase 0 placeholder: service template only)  ·  **Language/stack:** Go ([ADR-0004](../../docs/adr/0004-core-language-split.md), D1)

## Responsibilities
- Today: `/healthz`, `/readyz` (NATS connected), `/metrics` on `:8081`; JSON logs; NATS user `marketdata` with least-privilege subjects (see `tools/tbctl/secrets.go`).
- Does not own: anything outside its section prompt (`docs/prompts/`).

## Interfaces
| Direction | Kind | Name | Contract |
|---|---|---|---|
| in | HTTP (internal) | ops endpoints `:8081` | OP-010 |
| out | NATS | connection only (subjects arrive with the phase) | `contracts/proto` |

## Business rules enforced here
None yet.

## Layout
| Path | Purpose |
|---|---|
| `cmd/marketdata/main.go` | Entrypoint on `libs/go/svc` |

## Configuration
| Variable | Default | Notes |
|---|---|---|
| `TB_NATS_USER` | `marketdata` | Password from secret `nats_marketdata_password` |
| `TB_LAKE_DIR` | `/data/lake` | Host APFS bind mount (D4); container runs as the host UID |

## Run and test
- Run: `make up`. Build: `go build ./...`. Lint: `golangci-lint run ./...`.

## Operational notes
Readiness fails if NATS is unreachable. No trading behavior exists yet.

## Gotchas
None yet.
