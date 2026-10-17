# ledger

Will own real-account snapshots, virtual strategy ledgers, the manual bucket, lots, and reconciliation (section 06, Phase 2).

**Status:** Planned (Phase 0 placeholder: service template only)  ·  **Language/stack:** Go ([ADR-0004](../../docs/adr/0004-core-language-split.md), D1)

## Responsibilities
- Today: `/healthz`, `/readyz` (NATS connected), `/metrics` on `:8081`; JSON logs; NATS user `ledger` with least-privilege subjects (see `tools/tbctl/secrets.go`).
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
| `cmd/ledger/main.go` | Entrypoint on `libs/go/svc` |

## Configuration
| Variable | Default | Notes |
|---|---|---|
| `TB_NATS_USER` | `ledger` | Password from secret `nats_ledger_password` |

## Run and test
- Run: `make up`. Build: `go build ./...`. Lint: `golangci-lint run ./...`.

## Operational notes
Readiness fails if NATS is unreachable. No trading behavior exists yet.

## Gotchas
None yet.
