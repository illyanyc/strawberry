# api-gateway

The only service reachable from outside the host. Owns owner authentication, sessions, CSRF, step-up, the REST + SSE API for the UI, and serving the static UI. It never holds broker credentials in storage, never returns them, and never decides authorization outside `policy.Authorize`.

**Status:** In progress (Phase 0 auth slice; Phase 2 trading BFF)  ·  **Language/stack:** Go, go-webauthn, pquerna/otp, pgx, protojson ([ADR-0009](../../docs/adr/0009-owner-authentication.md), D15; [ADR-0004](../../docs/adr/0004-core-language-split.md), D1; [ADR-0028](../../docs/adr/0028-ui-trading-slice-charts-and-bff.md), D46)

## Responsibilities
- Owns: auth/session/CSRF/step-up flows, settings API, audit viewer API, credential write-through to broker-gateway, SSE status stream, the trading BFF (accounts, portfolio, breaks, strategies, limits, orders, intents, fills, trace, kill switches, instruments, bars) and the SSE chart stream, static UI.
- Does not own: credential encryption or storage (broker-gateway), trading state, ownership, risk decisions (execution and ledger decide; the gateway authorizes the owner and shapes requests).

## Interfaces
| Direction | Kind | Name | Contract |
|---|---|---|---|
| in | HTTP (REST + SSE) | `/auth/*`, `/api/v1/*`, `/healthz`, static `/` | `contracts/openapi/gateway.yaml` |
| out | gRPC | `tb.broker.v1.CredentialService` (Put/Delete/ListStatus) | `contracts/proto/tb/broker/v1/credentials.proto` |
| out | gRPC | `tb.orders.v1.ExecutionService` (status, strategies, limits, orders, intents, fills, trace, kill switches, preview/submit/cancel) | `contracts/proto/tb/orders/v1/execution.proto` |
| out | gRPC | `tb.ledger.v1.LedgerService` (accounts, portfolio, lots, breaks + resolve, allocation, equity, recon reports) | `contracts/proto/tb/ledger/v1/ledger_service.proto` |
| out | gRPC | `tb.marketdata.v1.MarketDataService` (`GetBars`, `ResolveSymbols`) | `contracts/proto/tb/marketdata/v1/marketdata.proto` |
| out | gRPC | `tb.ml.v1.MLService` (families, feature sets, label presets, estimate, jobs + stream, runs, diagnostics, importance, model card, tearsheet, register, ablations) | `contracts/proto/tb/ml/v1/ml_service.proto` |
| out | gRPC | `tb.optimizer.v1.OptimizerService` (lineages, autonomy, cycles, studies + stream, trials, diagnostics, ledger, stats, candidates, gate report, evidence, graph, check/promote/demote, retraining) and `HoldoutService` (status, boundary, evaluate; audience `holdout`) | `contracts/proto/tb/optimizer/v1/` |
| out | gRPC | `tb.analyst.v1.AnalystService` (journal, rounds, status, review + record launches, pause, analysis requests, trial ledger, code proposals, chat); approved level-0 actions go to ml/optimizer through the research clients as the owner (AN-060) | `contracts/proto/tb/analyst/v1/analyst.proto` |
| out | NATS | `alert.security` | `tb.alerts.v1.Alert` |
| in | NATS (core subscribe) | `md.bar.<tf>.<id>`, `md.bar.correction.*`, `md.bar.partial.*`, `exec.>` for the chart stream | `tb.marketdata.v1.Bar`, `tb.orders.v1.ExecutionReport` |
| out | Postgres | schemas `auth`, `config` (CRUD), `audit` (append + read) | `libs/go/db/migrations/` |

## Business rules enforced here
- `SE-004` public allowlist → `internal/gateway/server.go` (`registerRoutes`, `wrap`); test `TestSE004RouteInventory`
- `SE-005` sessions, `SE-006` CSRF, `SE-013` headers → `internal/gateway/server.go`; `TestSE006CSRFAndSE013Headers`
- `SE-003`, `SE-014` step-up via `policy.Authorize` → `server.go` (`authorize`), `auth.go` (step-up handlers)
- `SE-007` setup token, `SE-008` TOTP, `SE-009` recovery codes, `SE-011` lockout → `internal/gateway/auth.go`
- `SE-021`, `SE-023`, `SE-025` audit → `auth.go`, `settings.go` (settings write + audit in one transaction)
- `SE-031` credentials write-only → `internal/gateway/credentials.go`
- All of the above end to end: `TestSEAuthEndToEnd`, `TestSSEStreamRequiresSessionAndEndsOnRevoke`
- `EX-052`/`EX-011`/`EX-033`/`EX-025` research step-ups (promotion to paper or live_eligible, holdout evaluation and boundary, raising analyst autonomy, opening a cycle), `UI-025` sandboxed tearsheets → `internal/gateway/research.go`; `research_test.go` (`TestEX052*`, `TestUI025*`)
- `RM-007` (kill release desktop + passkey, allocation increase, limit loosening), `VP-019` break resolution step-up, `SE-003` live/unknown-account manual orders, `UI-006`, `UI-012`..`UI-016` → `internal/gateway/trading.go`, `stream.go`; `trading_test.go` (`TestRM007*`, `TestVP019*`, `TestUI0*`)
- `AN-060` (owner submits approved analyst actions at level 0, caller always replaced by the owner), `AN-061` (raising analyst autonomy or budgets needs a passkey step-up; audited before the change), `AN-073` (proposal review), `AN-080` (chat) → `internal/gateway/analyst.go`; `analyst_test.go` (`TestAN060*`, `TestAN061*`)

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
| `internal/gateway/events.go` | SSE `trading-status` stream and status snapshot (includes execution's `TradingStatus`) |
| `internal/gateway/trading.go` | Trading BFF: signed gRPC clients to execution/ledger/marketdata, protojson responses, owner actions with step-up |
| `internal/gateway/research.go` | Research BFF (section 14 Phase 4): signed gRPC clients to ml, optimizer, holdout; owner caller with step-up attestation; SSE job and study progress; sandboxed tearsheet documents |
| `internal/gateway/analyst.go` | Analyst BFF: AnalystService client; approvals with owner submission of prepared launches through the research clients, pause, analysis requests, proposals, chat (autonomy changes use the research autonomy route) |
| `internal/gateway/stream.go` | SSE chart stream (`/api/v1/stream/market`): bars, coalesced partials, executions |
| `internal/gateway/static.go` | SPA file server with index fallback |
| `internal/gateway/*_test.go` | Route inventory, CSRF/headers, static UI serving, end-to-end integration tests (passkeys via `libs/go/webauthntest`), trading handlers against fake gRPC clients and a fake bus |

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
| `TB_EXECUTION_ADDR` / `TB_LEDGER_ADDR` / `TB_MARKETDATA_ADDR` | `execution:9090` / `ledger:9090` / `marketdata:9090` | gRPC (lazy connections; routes answer 503 `<service>_unavailable` while a service is down) |
| `TB_ML_ADDR` / `TB_OPTIMIZER_ADDR` / `TB_HOLDOUT_ADDR` | `ml:9090` / `optimizer:9091` / `optimizer:9092` | Research services (profile `ml-workers`; native macOS workers need an address the Colima VM can reach) |
| `TB_ASSISTANT_ADDR` | `assistant:9090` | AnalystService for the analyst page (routes answer 503 `assistant_unavailable` while down) |
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
- The SPA handler is registered on `/` without a method (Go's ServeMux rejects `GET /` next to the `/api/` catch-all); `webUI` rejects non-GET/HEAD itself.
- `setup-token` prints to the terminal, never to logs. Run it via `make setup-token`.
- Trading responses are canonical protojson (camelCase, `EmitUnpopulated`); the UI types them with the generated `*Json` types. Request bodies the gateway defines (ticket, kill, mode, allocation, break resolution) stay snake_case.
- Identifiers that reach gRPC or bus subjects are regex-validated (`[A-Za-z0-9_-]{1,64}`, no dots or wildcards) before use.
- "Mobile" is detected from `Sec-CH-UA-Mobile` or the user agent; it only tightens (release refused, phone orders need `mobile.order_entry_enabled`), so spoofing it gains nothing.
- The chart stream needs the gateway NATS user's `md.bar.>` and `exec.>` subscribe permissions (`tools/tbctl/secrets.go`); on an existing install run `make secrets` (rewrites `nats_users.conf`) and restart `nats`.
