# api-gateway

The only service reachable from outside the host. Owns owner authentication, sessions, CSRF, step-up, the REST + SSE API for the UI, and serving the static UI. It never holds broker credentials in storage, never returns them, and never decides authorization outside `policy.Authorize`.

**Status:** In progress (Phase 0 auth slice; Phase 2 trading BFF; Phase 4 research and analyst BFF; Phase 5 Web Push, signals, TradingView intake)  ·  **Language/stack:** Go, go-webauthn, pquerna/otp, pgx, protojson ([ADR-0009](../../docs/adr/0009-owner-authentication.md), D15; [ADR-0004](../../docs/adr/0004-core-language-split.md), D1; [ADR-0028](../../docs/adr/0028-ui-trading-slice-charts-and-bff.md), D46)

## Responsibilities
- Owns: auth/session/CSRF/step-up flows, settings API, audit viewer API, credential write-through to broker-gateway, SSE status stream (with feed freshness), the trading BFF (accounts, portfolio, breaks, strategies, limits, orders, intents, fills, trace, kill switches, instruments, bars) and the SSE chart stream (bars, fills, signals), signals for chart overlays, Web Push (subscriptions, delivery, the Alertmanager webhook), the TradingView webhook intake, Cloudflare Access verification, static UI.
- Does not own: credential encryption or storage (broker-gateway), trading state, ownership, risk decisions (execution and ledger decide; the gateway authorizes the owner and shapes requests).

## Interfaces
| Direction | Kind | Name | Contract |
|---|---|---|---|
| in | HTTP (REST + SSE) | `/auth/*`, `/api/v1/*`, `/healthz`, static `/` | `contracts/openapi/gateway.yaml` |
| out | gRPC | `tb.broker.v1.CredentialService` (Put/Delete/ListStatus) | `contracts/proto/tb/broker/v1/credentials.proto` |
| out | gRPC | `tb.orders.v1.ExecutionService` (status, strategies, limits, orders, intents, fills, trace, kill switches, preview/submit/cancel, `RegisterStrategy` + `SetRiskLimits` for research candidates, SM-016) | `contracts/proto/tb/orders/v1/execution.proto` |
| out | gRPC | `tb.ledger.v1.LedgerService` (accounts, portfolio, lots, breaks + resolve, allocation, equity, recon reports) | `contracts/proto/tb/ledger/v1/ledger_service.proto` |
| out | gRPC | `tb.marketdata.v1.MarketDataService` (`GetBars`, `ResolveSymbols`) | `contracts/proto/tb/marketdata/v1/marketdata.proto` |
| out | gRPC | `tb.ml.v1.MLService` (families, feature sets, label presets, estimate, jobs + stream, runs, diagnostics, importance, model card, tearsheet, register, ablations) | `contracts/proto/tb/ml/v1/ml_service.proto` |
| out | gRPC | `tb.optimizer.v1.OptimizerService` (lineages, autonomy, cycles, studies + stream, trials, diagnostics, ledger, stats, candidates, gate report, evidence, graph, check/promote/demote, deployment limits (EX-062), retraining) and `HoldoutService` (status, boundary, evaluate; audience `holdout`) | `contracts/proto/tb/optimizer/v1/` |
| out | gRPC | `tb.analyst.v1.AnalystService` (journal, rounds, status, review + record launches, pause, analysis requests, trial ledger, code proposals, chat); approved level-0 actions go to ml/optimizer through the research clients as the owner (AN-060) | `contracts/proto/tb/analyst/v1/analyst.proto` |
| out | gRPC | `tb.monitor.v1.MonitorService` (health, reports, alert inbox with ack and silence, daily reports, containment policies and actions, graduation evidence; audience `monitor`) | `contracts/proto/tb/monitor/v1/monitor.proto` |
| out | NATS | `alert.security` | `tb.alerts.v1.Alert` |
| out | NATS (JetStream) | `sig.tv-<name>.<instrument_id>` (TradingView signals, dedupe ID = idempotency key) | `tb.signals.v1.Signal` |
| in | NATS (ordered consumers on stream `SIGNALS`) | `sig.*.<instrument_id>` for `/api/v1/signals` | `tb.signals.v1.Signal` |
| in | HTTP (internal listener `:8082`, backend only) | `POST /internal/alertmanager/webhook` (bearer token) | Alertmanager webhook v4 |
| in | HTTP (TradingView listener, optional) | `POST /webhooks/tradingview/{name}` | JSON alert body (`contracts/openapi/gateway.yaml` description) |
| out | HTTPS | Web Push to allowlisted push services; Cloudflare Access keys from `https://<team>/cdn-cgi/access/certs` | RFC 8291/8292; JWKS |
| in | NATS (core subscribe) | `md.bar.<tf>.<id>`, `md.bar.correction.*`, `md.bar.partial.*`, `exec.>`, `sig.*.<id>` for the chart stream; `exec.>` for fill pushes | `tb.marketdata.v1.Bar`, `tb.orders.v1.ExecutionReport`, `tb.signals.v1.Signal` |
| out | Postgres | schemas `auth` (incl. `push_subscriptions`), `config` (CRUD), `audit` (append + read) | `libs/go/db/migrations/` |

## Business rules enforced here
- `SE-004` public allowlist → `internal/gateway/server.go` (`registerRoutes`, `wrap`); test `TestSE004RouteInventory`
- `SE-005` sessions, `SE-006` CSRF, `SE-013` headers → `internal/gateway/server.go`; `TestSE006CSRFAndSE013Headers`
- `SE-003`, `SE-014` step-up via `policy.Authorize` → `server.go` (`authorize`), `auth.go` (step-up handlers)
- `SE-007` setup token, `SE-008` TOTP, `SE-009` recovery codes, `SE-011` lockout → `internal/gateway/auth.go`
- `SE-021`, `SE-023`, `SE-025` audit → `auth.go`, `settings.go` (settings write + audit in one transaction)
- `SE-031` credentials write-only → `internal/gateway/credentials.go`
- All of the above end to end: `TestSEAuthEndToEnd`, `TestSSEStreamRequiresSessionAndEndsOnRevoke`
- `EX-052`/`EX-011`/`EX-033`/`EX-025` research step-ups (promotion to paper or live_eligible, holdout evaluation and boundary, raising analyst autonomy, opening a cycle), `UI-025` sandboxed tearsheets → `internal/gateway/research.go`; `research_test.go` (`TestEX052*`, `TestUI025*`)
- `EX-062` deployment-limit raises and `SM-016` candidate registration → `internal/gateway/deployment.go`; `deployment_test.go`
- `MN-036` containment loosening and `MN-042` alert silences (passkey) → `internal/gateway/monitoring.go`; `monitoring_test.go`
- `RM-007` (kill release desktop + passkey, allocation increase, limit loosening), `VP-019` break resolution step-up, `SE-003` live/unknown-account manual orders, `UI-006`, `UI-012`..`UI-016` → `internal/gateway/trading.go`, `stream.go`; `trading_test.go` (`TestRM007*`, `TestVP019*`, `TestUI0*`)
- `SE-060`..`SE-063` Web Push (VAPID, RFC 8291, sealed subscriptions, push-host allowlist, redaction, internal Alertmanager webhook) → `internal/webpush`, `internal/gateway/push.go`; `webpush_test.go`, `push_test.go`
- `SE-064` TradingView intake and the Cloudflare Access gate, `SE-065` widget CSP, `UI-047` tearsheet embed, `UI-049` signals, `UI-051` freshness, `RM-020` phone halt step-up → `internal/gateway/tradingview.go`, `server.go`, `signals.go`, `research.go`, `trading.go`, `internal/cfaccess`; `tradingview_test.go`, `cfaccess_test.go`, `trading_test.go`
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
| `internal/gateway/deployment.go` | Lineage deployment limits (EX-062, passkey to raise) and registering a shadow/paper candidate with execution (SM-016, EX-064, `strategy.register`) |
| `internal/gateway/monitoring.go` | Monitoring BFF (section 13 Phase 5): `/api/v1/monitoring/*` over `MonitorService`; silencing and loosening containment need a passkey (MN-036, MN-042) |
| `internal/gateway/analyst.go` | Analyst BFF: AnalystService client; approvals with owner submission of prepared launches through the research clients, pause, analysis requests, proposals, chat (autonomy changes use the research autonomy route) |
| `internal/gateway/stream.go` | SSE chart stream (`/api/v1/stream/market`): bars, coalesced partials, executions |
| `internal/gateway/static.go` | SPA file server with index fallback |
| `internal/gateway/signals.go` | `/api/v1/signals` (recent signals of an instrument from the SIGNALS stream) and the `SignalBus` interface |
| `internal/gateway/push.go` | Web Push: subscription API, delivery fan-out, fill notifications, Alertmanager webhook on the internal listener, Postgres store |
| `internal/gateway/tradingview.go` | TradingView webhook listener (verification chain, audit, signal publication), per-strategy secrets, `/api/v1/tradingview` status |
| `internal/webpush/` | RFC 8291 encryption, RFC 8292 VAPID, sender, push-host allowlist (standard library only) |
| `internal/cfaccess/` | Cloudflare Access JWT verifier (RS256 via the team's published keys, audience, issuer, expiry) |
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
| `TB_MONITOR_ADDR` | `monitor:9090` | MonitorService for the Monitoring page (routes answer 503 `monitor_unavailable` while down) |
| `TB_WEB_DIR` | `/app/web` | Static SPA build |
| `TB_INTERNAL_HTTP_ADDR` | `:8082` | Internal listener: Alertmanager → Web Push webhook (backend network only) |
| `TB_PUSH_SUBJECT` / `TB_PUSH_REDACT` / `TB_PUSH_ALLOWED_HOSTS` | `mailto:owner@strawberry.invalid` / `true` / FCM, Mozilla, Apple, Windows | VAPID contact; lock-screen redaction; push-service hosts (SE-061) |
| `TB_TV_WEBHOOK_ADDR` | empty (off) | TradingView listener (e.g. `:8090`); must differ from `TB_HTTP_ADDR` |
| `TB_TV_PUBLIC_URL` / `TB_TV_ALLOWED_IPS` / `TB_TV_TRUSTED_PROXIES` / `TB_TV_MAX_AGE` | — / TradingView's four IPs / — / `60s` | Display URL; client allowlist; peers whose `Cf-Connecting-Ip` counts (cloudflared); alert age window |
| `TB_CF_ACCESS_TEAM` / `TB_CF_ACCESS_TV_AUD` / `TB_CF_ACCESS_UI_AUD` / `TB_CF_ACCESS_UI_HOSTS` | empty | Cloudflare Access verification for the webhook application and the public UI hostnames (SE-064) |
| Secrets | — | `gateway_auth_key`, `pg_svc_api_gateway_password`, `nats_api_gateway_password`, `svc_api_gateway_ed25519`, `webpush_vapid_key`, `alertmanager_webhook_token`, `tv_webhook_key` |

## Run and test
- Run: `make up` (Compose). First owner: `make setup-token`, then open http://localhost:8080. TradingView secrets: `make tv-secret NAME=<name>`.
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
- Web Push is disabled (Notifications page says so) until `webpush_vapid_key` exists; existing installs run `make secrets && make migrate` and restart api-gateway and Alertmanager.
- The TradingView listener never shares the UI address (config validation); it is off unless `TB_TV_WEBHOOK_ADDR` is set and `tv_webhook_key` exists.
- Kill-switch engagement from a phone needs a fresh passkey (RM-020); the UI's step-up dialog retries it.
- The chart stream needs the gateway NATS user's `md.bar.>`, `exec.>`, and `sig.>` subscribe permissions, and `/api/v1/signals` its SIGNALS-stream consumer permissions (`tools/tbctl/secrets.go`); on an existing install run `make secrets` (rewrites `nats_users.conf`) and restart `nats`.
