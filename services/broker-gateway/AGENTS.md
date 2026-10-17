# broker-gateway

Custodian of broker credentials and (from Phase 1) the Alpaca adapter, broker sessions, order submission/cancel, and execution/account streams. It is the only process that can decrypt a broker credential, and it never returns plaintext to anyone. The IBKR adapter is a separate Python service (`broker-gateway-ibkr`, D1/D18).

**Status:** In progress (Phase 0: credential custody only)  ·  **Language/stack:** Go ([ADR-0004](../../docs/adr/0004-core-language-split.md), D1; [ADR-0011](../../docs/adr/0011-secrets-management.md), D16)

## Responsibilities
- Owns: `secrets.broker_credentials` (envelope-encrypted), the KEK ring, `CredentialService`.
- Later: Alpaca adapter, session state machine, order routing to brokers (section 04).
- Does not own: authorization of the owner (api-gateway), risk (execution).

## Interfaces
| Direction | Kind | Name | Contract |
|---|---|---|---|
| in | gRPC `:9090` | `tb.broker.v1.CredentialService` (Put, Delete, ListStatus); callers: `api-gateway` only | `contracts/proto/tb/broker/v1/credentials.proto` |
| out | Postgres | schema `secrets` (only this role), `audit.append` | `libs/go/db/migrations/secrets/` |

## Business rules enforced here
- `SE-030` envelope encryption + AAD → `internal/credstore/credstore.go`; `TestSE030EnvelopeAADBinding`, `TestSE030StoreIntegration`
- `SE-031` no plaintext RPC; `SE-033` KEK ring and `rewrap-keys` → `internal/credstore`, `cmd/broker-gateway`
- `SE-032` schema isolation → migrations; `libs/go/db` `TestSE032SchemaIsolation`
- `SE-040` caller allowlist, `SE-012` error-path redaction → `internal/credsvc/credsvc.go`; `TestSE040CallerAllowlistAndSE012ErrorPath`
- `SE-021` credential change and its audit record commit in one transaction → `credstore.Put/Delete`

## Layout
| Path | Purpose |
|---|---|
| `cmd/broker-gateway/` | Entrypoint; subcommands `rewrap-keys`, `healthcheck` |
| `internal/credstore/` | KEK ring parsing, seal/open, Postgres store, KEK rotation |
| `internal/credsvc/` | gRPC CredentialService implementation and caller allowlist |

## Configuration
| Variable | Default | Notes |
|---|---|---|
| `TB_GRPC_ADDR` | `:9090` | Internal network only |
| `TB_NTP_SERVER` / `TB_CLOCK_MAX_SKEW` | `time.cloudflare.com` / `250ms` | Clock-skew metric (OP-020) |
| Secrets | — | `broker_kek` (lines `<id>:<base64 32B>`, first = active), `pg_svc_broker_gateway_password`, `svc_api_gateway_ed25519.pub` |

## Run and test
- Run: `make up`. KEK rotation: prepend a new key line to `broker_kek`, restart, then `docker compose exec broker-gateway /app/service rewrap-keys`.
- Test: `go test -race ./...`; Postgres-backed tests via `make test-integration`.

## Operational notes
- `/healthz`, `/readyz` (postgres), `/metrics` on `:8081`.
- Losing every KEK makes stored credentials unrecoverable: keep an offline copy of `broker_kek` (password manager or hardware key) before storing live credentials (runbook: `docs/runbooks/secrets-and-keys.md`).

## Gotchas
- Credential values are registered with the log redactor while handled; never log request structs.
- Moving a row between `paper` and `live` makes it undecryptable by design (AAD).
