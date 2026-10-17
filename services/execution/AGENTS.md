# execution

Will own the OMS state machine, pre-trade risk (including routing, margin, and PDT guards, D30), and kill switches (section 07, Phase 2). Until then it accepts no intents (fail closed).

**Status:** Planned (Phase 0 placeholder: service template only)  ·  **Language/stack:** Go ([ADR-0004](../../docs/adr/0004-core-language-split.md), D1)

## Responsibilities
- Today: `/healthz`, `/readyz` (NATS connected), `/metrics` on `:8081`; JSON logs; NATS user `execution` with least-privilege subjects (see `tools/tbctl/secrets.go`).
- Clock-skew checker (OP-020) and `tb_trading_enabled` gauge fixed at 0.
- Does not own: anything outside its section prompt (`docs/prompts/`).

## Interfaces
| Direction | Kind | Name | Contract |
|---|---|---|---|
| in | HTTP (internal) | ops endpoints `:8081` | OP-010 |
| out | NATS | connection only (subjects arrive with the phase) | `contracts/proto` |

## Business rules enforced here
None yet (OP-020 metric only; RM- rules arrive in Phase 2).

## Layout
| Path | Purpose |
|---|---|
| `cmd/execution/main.go` | Entrypoint on `libs/go/svc` |

## Configuration
| Variable | Default | Notes |
|---|---|---|
| `TB_NATS_USER` | `execution` | Password from secret `nats_execution_password` |
| `TB_NTP_SERVER` / `TB_CLOCK_MAX_SKEW` | `time.cloudflare.com` / `250ms` | OP-020 |

## Run and test
- Run: `make up`. Build: `go build ./...`. Lint: `golangci-lint run ./...`.

## Operational notes
Readiness fails if NATS is unreachable. No trading behavior exists yet.

## Gotchas
None yet.
