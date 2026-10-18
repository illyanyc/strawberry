# ADR-0051: TradingView: opt-in `tv-<name>` webhook strategies behind Cloudflare Access, and chart embedding where licensing allows

- **Status:** Accepted
- **Date:** 2026-10-18
- **Decision register:** D69 (settles section 14's "TradingView webhooks as signal source" and "Advanced Charts" items; applies D12, D17)
- **Related:** SM-020, SE-064, SE-065, UI-048, UI-049, RM-001, VP-002, [ADR-0016](0016-remote-access.md), [ADR-0050](0050-signals-on-the-bus-and-chart-overlays.md), section 02 Stage B, section 14 prompt, research 07 §5, research 08 §1

## Context

Research 07 §5: TradingView webhooks can be a signal source in Phase 5, opt-in, through a dedicated Cloudflare Tunnel hostname with Access **Service Auth + an IP-range selector** (never Bypass), with source-IP, certificate where possible, a shared secret in the body, a timestamp and replay protection, and a 200 within 3 s; webhook signals are "just another strategy" (`tv-<name>`) with their own ledger and limits, never raw orders. TradingView webhooks cannot carry custom headers or an HMAC; the body is the only place for a secret. Advanced Charts / Trading Platform are licensed for public web projects only and forbid private use (D12). TradingView's free widgets are embeddable with attribution but load third-party code and reveal which symbols the owner views.

## Decision

- **A separate listener.** `TB_TV_WEBHOOK_ADDR` (unset by default) starts a second HTTP server in api-gateway that serves only `POST /webhooks/tradingview/{name}` and `GET /healthz`; config validation refuses the UI listener's address. In Compose it is reached only by `cloudflared` on the `edge` network for a dedicated hostname; the Access application uses a Service Auth policy with TradingView's four published IPs.
- **Verification, in order:** per-IP rate limit; client IP (Cf-Connecting-Ip only from `TB_TV_TRUSTED_PROXIES`) in `TB_TV_ALLOWED_IPS` (default TradingView's four); a valid Cloudflare Access token (`Cf-Access-Jwt-Assertion`, RS256 against the team's published keys, audience and issuer checked) when `TB_CF_ACCESS_TV_AUD` is set; strict JSON body ≤ 8 KB; constant-time comparison of `secret` with the strategy's secret. Unauthenticated requests are logged and counted (one security alert per burst of five in 10 minutes), not audited. From here every outcome is audited (actor `webhook:tradingview`, action `signals.create`): alert `time` within 60 s (≤ 30 s ahead), idempotency key over strategy, ticker, action, time, and alert ID (duplicates answer 200 `duplicate`), `policy.Authorize` (webhook actors may only create signals), the `tv-<name>` strategy registered in execution, the ticker resolved through the instrument master (unknown → 422, never guessed), then a `Signal` on `sig.tv-<name>.<instrument>` (JetStream, dedupe ID). Bus failure answers 503 so TradingView retries.
- **Per-strategy secrets** are HMAC-SHA256 of the strategy ID under the `tv_webhook_key` secret (32 chars, base64url), printed only by `make tv-secret NAME=<name>` on the host terminal; never logged, audited, or sent to the browser.
- **The strategy.** Runtime class `tradingview` in `tbcore.strategy.tradingview` (SM-020): instruments limited to the registry entry; signals older than 60 s or without a fresh platform bar are dropped; sizing and the protective stop use the platform's own bar; `buy` opens long when flat and closes a short, `sell` opens short (if allowed) when flat and closes a long, `exit` closes the strategy's own position, never a reversal; intraday entries flatten before the close; intent IDs derive from the signal ID. The runtime subscribes to `sig.<tv-strategy>.>` for new messages only (a signal that arrived while it was down is never acted on late). Intents pass the normal pre-trade gate with the strategy's own allocation and limits (RM-001); the strategy starts in paper like every other (N4).
- **Cloudflare Access on the UI hostname, too.** With `TB_CF_ACCESS_UI_AUD` and `TB_CF_ACCESS_UI_HOSTS`, requests for those hostnames need a valid Access token (healthz excepted), so a misconfigured tunnel or a Bypass policy cannot skip Access's independent passkey MFA. Tailscale and localhost hostnames are unaffected.
- **Charts.** Every chart is TradingView Lightweight Charts (Apache-2.0) with the attribution link (D12, ADR-0028). Each chart links to the symbol on tradingview.com (new tab, `noopener noreferrer`, nothing sent before a click). TradingView's free embeddable chart widget is optional behind the setting `ui.tradingview_widget_enabled` (off; enabling needs a passkey): while on, the SPA document's CSP adds exactly `frame-src 'self' https://s.tradingview.com`, and the widget runs in a cross-origin sandboxed frame. Advanced Charts is never used.

## Alternatives considered

| Option | Why not |
|---|---|
| Webhook path on the main UI listener | Section 14 pitfall: the UI's address would accept internet traffic; one misrouted rule exposes it |
| A separate relay service | Another image and credentials for ~300 lines that need the gateway's audit, policy, and bus access anyway |
| Tailscale Funnel instead of Cloudflare | Funnel has no IP-range or service-auth policy in front of the origin |
| HMAC signature header | TradingView cannot send custom headers or compute an HMAC |
| Webhook creates `OrderIntent`s directly in the gateway | Strategy logic (sizing, stops, own-position rules) would live outside `tbcore`; signals keep the gateway out of trading decisions |
| Advanced Charts / Trading Platform | Licence forbids private use (D12) |
| Widget always on | Third-party frame in the trading UI and symbol-interest leakage by default |

## Consequences

- Positive: TradingView alerts become a normal, risk-gated, attributable strategy with its own ledger; the owner sees every delivery on the TradingView page and every signal on the charts; the UI hostname cannot be reached around Access.
- Negative / risks: a leaked strategy secret plus a request from TradingView's IP range could create signals for that strategy; the strategy's limits, paper mode, and kill switch bound the damage, and the secret rotates by rotating `tv_webhook_key`. Client-certificate (mTLS) verification is not available through the tunnel and is not implemented.
- Follow-up: the owner configures the tunnel hostname and Access application (runbook `tradingview-webhook.md`).

## Validation

`TestSE064TradingViewWebhookRejectionPaths` (every rejection path, audit, replay, bus failure), `TestSE064WebhookOffByDefaultAndNeverOnTheUIListener`, `TestSE064AccessGateOnTheUIHostname`, `internal/cfaccess` tests (signature, audience, issuer, expiry, unknown keys), `TestUI047TearsheetEmbedAndSE065WidgetCSP`, `test_sm020_*` in `services/strategy-runtime/tests/test_runner.py` and `libs/py/tbcore/tests/test_tradingview.py`. Revisit if TradingView adds signed webhooks or mTLS through the tunnel becomes possible.
