# api-gateway

The only service reachable from outside the host. Owns owner authentication, sessions, CSRF, step-up, the REST + SSE API for the UI, and serving the static UI. It never holds broker credentials in storage, never returns them, and never decides authorization outside `policy.Authorize`.

**Status:** In progress (Phase 0 auth slice)  ·  **Language/stack:** Go, go-webauthn, pquerna/otp, pgx ([ADR-0009](../../docs/adr/0009-owner-authentication.md), D15; [ADR-0004](../../docs/adr/0004-core-language-split.md), D1)

## Responsibilities
- Owns: auth/session/CSRF/step-up flows, settings API, audit viewer API, credential write-through to broker-gateway, SSE status stream, static UI.
- Does not own: credential encryption or storage (broker-gateway), trading state, risk.

## Interfaces
| Direction | Kind | Name | Contract |
|---|---|---|---|
| in | HTTP (REST + SSE) | `/auth/*`, `/api/v1/*`, `/healthz`, static `/` | `contracts/openapi/gateway.yaml` |
| out | gRPC | `tb.broker.v1.CredentialService` (Put/Delete/ListStatus) | `contracts/proto/tb/broker/v1/credentials.proto` |
| out | NATS | `alert.security` | `tb.alerts.v1.Alert` |
| out | Postgres | schemas `auth`, `config` (CRUD), `audit` (append + read) | `libs/go/db/migrations/` |

## Business rules enforced here
- `SE-004` public allowlist → `internal/gateway/server.go` (`registerRoutes`, `wrap`); test `TestSE004RouteInventory`
- `SE-005` sessions, `SE-006` CSRF, `SE-013` headers → `internal/gateway/server.go`; `TestSE006CSRFAndSE013Headers`
- `SE-003`, `SE-014` step-up via `policy.Authorize` → `server.go` (`authorize`), `auth.go` (step-up handlers)
- `SE-007` setup token, `SE-008` TOTP, `SE-009` recovery codes, `SE-011` lockout → `internal/gateway/auth.go`
- `SE-021`, `SE-023`, `SE-025` audit → `auth.go`, `settings.go` (settings write + audit in one transaction)
- `SE-031` credentials write-only → `internal/gateway/credentials.go`
- All of the above end to end: `TestSEAuthEndToEnd`, `TestSSEStreamRequiresSessionAndEndsOnRevoke`

## Layout
| Path | Purpose |
|---|---|
| `cmd/api-gateway/` | Entrypoint; subcommands `setup-token`, `healthcheck` |
| `internal/gateway/config.go` | Typed config and validation |
| `internal/gateway/server.go` | Routes, middleware (headers, correlation, rate limit, CSRF, session), authorization helper |
| `internal/gateway/auth.go` | Setup, passkey login/registration, TOTP, recovery codes, step-up, sessions |
| `internal/gateway/store.go` | Postgres access (auth, config) |
| `internal/gateway/crypto.go` | HKDF subkeys, TOTP secret sealing, code HMAC, recovery code format |
| `internal/gateway/settings.go` | Closed settings list; dangerous enables map to step-up actions |
| `internal/gateway/audit_api.go` | Audit list and verify endpoints |
| `internal/gateway/credentials.go` | gRPC client to broker-gateway and credential endpoints |
| `internal/gateway/events.go` | SSE `trading-status` stream and status snapshot |
| `internal/gateway/static.go` | SPA file server with index fallback |
| `internal/gateway/*_test.go` | Route inventory, CSRF/headers, software WebAuthn authenticator, end-to-end integration tests |

## Configuration
| Variable | Default | Notes |
|---|---|---|
| `TB_HTTP_ADDR` | `:8080` | Published on the host only as `127.0.0.1:8080` |
| `TB_AUTH_RP_ID` | `localhost` | WebAuthn RP ID; set to the Tailscale/Cloudflare hostname for remote use |
| `TB_AUTH_ORIGINS` | `http://localhost:8080` | Comma list; WebAuthn + CSRF origin allowlist; plain http only for localhost |
| `TB_AUTH_COOKIE_SECURE` | `true` | `__Host-` cookies; browsers treat `http://localhost` as secure |
| `TB_AUTH_SESSION_IDLE` / `_ABSOLUTE` | `30m` / `12h` | SE-005 |
| `TB_AUTH_STEP_UP_WINDOW` | `5m` | SE-003 |
| `TB_AUTH_MAX_FAILURES` / `TB_AUTH_LOCKOUT_WINDOW` | `5` / `15m` | SE-011 |
| `TB_TRUSTED_PROXIES` | — | CIDRs whose `X-Forwarded-For` is honored |
| `TB_BROKER_GATEWAY_ADDR` | `broker-gateway:9090` | gRPC |
| `TB_WEB_DIR` | `/app/web` | Static SPA build |
| Secrets | — | `gateway_auth_key`, `pg_svc_api_gateway_password`, `nats_api_gateway_password`, `svc_api_gateway_ed25519` |

## Run and test
- Run: `make up` (Compose). First owner: `make setup-token`, then open http://localhost:8080.
- Test: `go test -race ./...` (unit); integration needs Postgres: `make test-integration`.

## Operational notes
- `/healthz` on `:8080` (public) and the ops listener `:8081` (`/healthz`, `/readyz` with postgres + bus, `/metrics`), the latter on the internal network only.
- Fails closed: if the audit append fails, logins and changes fail (SE-021).

## Gotchas
- WebAuthn RP ID must be a domain (not an IP); use `localhost` locally.
- The SSE stream re-checks the session each tick without sliding the idle timeout.
- `setup-token` prints to the terminal, never to logs. Run it via `make setup-token`.
