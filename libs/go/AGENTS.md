# libs/go

Shared Go module `github.com/illyanyc/strawberry/libs/go` used by every Go service and `tools/tbctl`. It holds cross-cutting plumbing only; business logic stays in services.

**Status:** In progress (Phase 0)  ·  **Language/stack:** Go 1.27 ([ADR-0004](../../docs/adr/0004-core-language-split.md), D1 provisional)

## Responsibilities
- Owns: config loading, logging with redaction, secrets access, ops endpoints, tracing setup, service tokens, the authorization policy, the audit writer/verifier, DB bootstrap + migrations, NATS streams, clock-skew checks, the service template.
- Does not own: broker adapters, OMS, ledger logic, HTTP APIs (services own those).

## Layout
| Path | Purpose | Rules |
|---|---|---|
| `config/` | Typed config: optional JSON file (`TB_CONFIG_FILE`) + env overrides, validated at startup | OP-010 |
| `logging/` | slog JSON logger with `service`, `correlation_id`, `trace_id`; redacting handler; `Redact()` | SE-012 |
| `secrets/` | `Store.Get(name)` from mounted files; registers values with the redactor; `Zero()` | SE-010 |
| `ops/` | `/healthz`, `/readyz`, `/metrics` listener, HTTP metrics middleware, `healthcheck` subcommand | OP-010 |
| `telemetry/` | OpenTelemetry tracer provider (OTLP/HTTP) | D22 |
| `svc/` | Service template wiring the above (`svc.Start`, `ServeOps`, `Wait`) | OP-010 |
| `svcauth/` | Ed25519 service tokens; gRPC per-RPC credentials and unary + stream server interceptors with per-method allowlists | SE-040 |
| `policy/` | `Authorize(actor, action, resource, now)`; closed action list; step-up requirements | SE-001..SE-003, SE-014 |
| `audit/` | `Writer.Append` (via `audit.append`), canonical encoding, `Verify` | SE-020..SE-022 |
| `db/` | Pool connect as `svc_<service>`; `Bootstrap` (roles, databases, schemas) and embedded migrations per schema | SE-032, SE-050 |
| `db/migrations/<schema>/` | SQL migrations, applied as `<schema>_owner` by `tbctl db init` (schemas: audit, auth, config, secrets, broker, broker_ibkr) | — |
| `bus/` | NATS connect, JetStream stream definitions, proto publish with dedupe + correlation headers | OP-030 |
| `resilience/` | Full-jitter backoff, `Retry` with `Retry-After`, priority token-bucket `Limiter`, circuit `Breaker` | BR-012, BR-016, MD-008 |
| `outbox/` | Transactional outbox: `Enqueue` in the caller's transaction, `Relay` publishing to JetStream with `Nats-Msg-Id`, `Purge` | ADR-0018, D2 |
| `clock/` | NTP skew checker, `tb_clock_skew_seconds`, fail-closed `Healthy()` | OP-020 |
| `webauthntest/` | Software WebAuthn authenticator (ES256, none attestation, UV) for gateway and end-to-end tests; test use only | SE-003..SE-009 tests |

## Run and test
- Unit: `cd libs/go && go test -race ./...`
- Integration (real Postgres): `TB_TEST_PG_SUPER_DSN=postgres://postgres:<pw>@127.0.0.1:55432/postgres?sslmode=disable go test ./db/` (`make test-integration` starts a throwaway container).
- Lint: `golangci-lint run ./...` (config in repo root `.golangci.yml`).

## Gotchas
- The audit canonical encoding exists twice: SQL (`audit.append`) and Go (`audit.ComputeHash`). Change both together; `TestSE020AuditChainEndToEnd` proves they agree.
- `db.Bootstrap` runs migrations as each schema's owner via `SET ROLE` from a superuser connection; grants to `svc_*` roles live in the migrations.
- New schema: add a `db.Schemas` entry, a `migrations/<schema>/` directory, and a rule for who may access it.
