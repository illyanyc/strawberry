---
title: "Section 14 – UI: Desktop, Mobile, and TradingView"
phase: "0–5 (built in slices alongside each phase)"
depends_on: [00, 02, plus the section that owns each slice's data]
updated: 2026-10-17
---

# Section 14: UI (Desktop, Mobile, and TradingView)

## Role

You are a senior product engineer who builds trading and data-science interfaces. You design for a technical power user on a large monitor and for a quick, safe check from a phone. Dangerous actions are deliberate; information is dense but legible; real-time data never freezes the page.

## Context

The `web` app is the only human interface. It talks exclusively to `api-gateway` (REST + WebSocket); it never calls internal services or brokers directly and never sees credentials. It is built in slices so each phase is visible: shell and auth (Phase 0), charts and data (Phase 1), portfolio, strategies, orders, and kill switches (Phase 2), backtests and research (Phases 3–4), monitoring and full mobile (Phase 5). Non-negotiables N1 (auth, step-up, no secrets in the browser), N3 (clear separation of manual vs strategy holdings), and N4 (unmistakable paper vs live) apply.

## Scope

In scope:
- App shell, navigation, auth screens (passkey, TOTP, recovery, step-up dialog), session management.
- Real-time data layer (WebSocket subscriptions, reconnection, backpressure).
- Pages listed below, with loading, empty, and error states, on desktop and mobile.
- Charting with TradingView Lightweight Charts fed from the platform's own data; signal, order, and fill overlays.
- TradingView integration options: embedded charts now; Advanced Charts license and webhook alert intake as configurable later items.
- PWA: installable, Web Push notifications, mobile layouts.
- Gateway BFF endpoints needed by the UI (in `api-gateway`), following contracts.

Out of scope:
- Business logic (lives in services; the UI never computes risk, P&L, or ownership itself).
- Native mobile apps (unless research §8 and U6 change the default).

## Inputs and dependencies

- Section 02: auth API, step-up, CSP, session rules.
- Each slice depends on the section owning the data: 05/03 (charts), 06 (portfolio), 07 (orders, strategies, kill switches), 08 (backtests), 10/11 (training, experiments), 12 (AI Research Analyst and assistant), 13 (monitoring, alerts).
- `INPUT PENDING (U6)`: mobile scope. Default monitor + alerts + kill switch; mobile order entry behind a feature flag.
- `INPUT PENDING (U7)`: multi-user would add user management screens; out of scope by default.

## Decisions pending

| Decision | Default | Marker |
|---|---|---|
| Framework | Next.js (App Router, TypeScript, React Server Components where useful) + Tailwind + shadcn/ui; client-heavy pages for real-time views | `DECISION PENDING (research §8)` (D11) |
| Price charts | TradingView Lightweight Charts (Apache-2.0) with a custom datafeed from the gateway | `DECISION PENDING (research §8, §11)` (D12) |
| Advanced Charts / Trading Platform library | Not in MVP; requires a license application with TradingView; evaluate later | `DECISION PENDING (research §11)` |
| Analytics charts | ECharts or uPlot (high-volume series); Perspective for streaming blotters/grids | `DECISION PENDING (research §8)` |
| Data fetching | TanStack Query for REST, a typed WebSocket client generated from contracts | Medium confidence |
| Mobile | Responsive PWA with Web Push | `DECISION PENDING (research §8)` (D13) |
| Transport | WebSocket via gateway; SSE fallback for read-only streams | D14 |
| TradingView webhooks as signal source | Optional, off by default; requires public relay (section 01 Stage B) | `DECISION PENDING (research §11)` |

## Interfaces and contracts to expose

- Gateway REST resources (OpenAPI in `contracts/openapi`): accounts, portfolio views, strategies, orders, intents, risk limits, kill switches, instruments, bars (chart datafeed), backtests, training jobs, studies, models, assistant, alerts, audit log, settings.
- WebSocket channels (authenticated at upgrade; per-channel authorization): `bars:<tf>:<instrument>`, `quotes:<instrument>`, `orders`, `fills`, `portfolio:<account>`, `strategy:<id>`, `jobs:<id>`, `studies:<id>`, `alerts`, `trading-status`.
- Chart datafeed adapter implementing Lightweight Charts' data needs (historical bars by range + live updates), plus marker/overlay APIs for signals, orders, fills, stops/targets.
- TradingView webhook intake (if enabled): `POST /webhooks/tradingview/<alert-id>` handled by the gateway per section 02 Stage B rules; converts payloads into `Signal`s for a designated strategy, never into orders.

## Pages and functional requirements

Global:
- [ ] Persistent environment banner: **PAPER** (calm color) or **LIVE** (high-contrast, impossible to miss) per account and per strategy; live elements require explicit visual distinction everywhere orders or modes appear.
- [ ] Global trading status bar: broker sessions, feed freshness, reconciliation status, active kill switches, PDT count; a one-tap global kill switch (engage needs confirmation, not step-up; release needs step-up).
- [ ] Step-up dialog triggered by `RequireStepUp` responses from the gateway (passkey re-auth), then automatic retry of the action.
- [ ] Real-time layer: reconnect with backoff, resubscribe, stale-data indicators (gray out values older than threshold), throttled rendering for high-frequency streams.
- [ ] Keyboard shortcuts and command palette on desktop; dark mode default with light option.

Pages (slice in parentheses):
- [ ] **Login / setup / security settings** (0): passkeys, TOTP, recovery codes, sessions, audit log viewer.
- [ ] **Dashboard** (2): accounts summary (equity, cash, buying power, day P&L), strategy cards (mode, allocation, P&L, health), alerts, upcoming events.
- [ ] **Charts** (1): multi-timeframe chart for any instrument from platform data; indicators; overlays of model signals, strategy orders, fills, stops/targets (2+); split layouts; feed label (IEX/SIP).
- [ ] **Portfolio** (2): per broker account, the real positions table split by owner (`manual` vs each strategy) with quantity breakdown per symbol; unallocated cash and reserve; reconciliation status and break list with resolution flow (step-up).
- [ ] **Strategies** (2): registry list, detail page (params, model version, allocation, limits, mode, equity curve, trades), mode controls (`disabled/shadow/paper`; `live` only via the go-live flow in section 15), per-strategy kill switch, allocation and limit editors (increases require step-up).
- [ ] **Orders & blotter** (2): live orders, fills, rejected intents with risk reasons, filters by strategy/owner/account; cancel actions; correlation trace view (signal → intent → decision → order → fills).
- [ ] **Manual order entry** (2): ticket with account, instrument, side, qty, type, limit, TIF; pre-trade preview (risk decision dry-run, estimated cost, ownership note: "this order affects manual holdings"); confirmation; step-up above threshold for live.
- [ ] **Data explorer** (1): instrument search, coverage and gaps, data quality reports, context events timeline (news, earnings, macro) aligned to the chart.
- [ ] **Backtests** (3): launch form, run list, tearsheet (equity/drawdown, monthly table, regime table, costs, overfitting stats, warnings), trades plotted on the chart, side-by-side comparison.
- [ ] **Training** (4): model family picker with forms generated from hyperparameter JSON Schemas, feature set and label spec pickers, universe/timeframe/period, resource estimate; live progress (fold/epoch metrics, loss curves, logs); model card view.
- [ ] **Experiments & studies** (4): study creation, live progress, trial table and parallel-coordinates/importance plots, Pareto fronts, gate reports, holdout access counter and evaluation action (step-up), registry stages and champion/challenger comparison from live paper results.
- [ ] **AI Research Analyst & assistant** (4): per-lineage research journal (each round's diagnosis, ranked plan, actions, outcomes, reasoning, with links to runs and reports); proposals with config diffs, compute estimate, and trial-count impact, with approve/reject; autonomy level selector (suggest-only, auto-run on paper, auto-promote within limits; raising requires step-up) with budget and promotion-limit settings; loop status and pause/resume; lineage trial ledger with deflated Sharpe/PBO; chat panel (dockable on desktop).
- [ ] **Monitoring** (5): strategy health, drift and divergence reports, execution quality, alerts inbox with acknowledge/silence, daily reports.
- [ ] **Settings** (0→5): broker connections (write-only credential forms, connection tests, paper/live labels), universes, data sources, alert channels, containment policies, feature flags.

Mobile (PWA):
- [ ] Installable PWA; Web Push for alerts, fills (configurable), and daily reports.
- [ ] Mobile layouts for Dashboard, Strategy detail, Portfolio, Alerts, Charts (simplified), and the kill switch; passkey login on the phone.
- [ ] Mobile order entry only if the U6 feature flag is on; then with step-up for live orders.

## Non-functional requirements

- **Performance**: initial load (dashboard) LCP under 2.5 s on desktop over Tailscale; chart renders 50k bars smoothly; live updates at up to 10 Hz per visible chart without dropped frames; blotter handles 10k rows virtualized.
- **Accessibility**: keyboard navigable, sufficient contrast (including PAPER/LIVE banners), screen-reader labels on controls, no information conveyed by color alone (e.g., P&L signs and icons).
- **Security**: strict CSP, no secrets in the bundle or local storage, CSRF protection, all authorization server-side; WebSocket origin checks.
- **Responsiveness**: layouts tested at 360 px, 768 px, 1280 px, and 1920+ px widths.
- **Tests**: component tests for critical widgets (order ticket, kill switch, step-up dialog, ownership table); Playwright end-to-end tests per slice against the `dev` profile (broker simulator), including mobile viewport runs; visual check that LIVE banners appear wherever live actions exist.

## Acceptance criteria

- [ ] Docs updated per `AGENTS.md` (master N6) in the same commits as the changes: `docs/file-map.md`, `docs/business-logic/ui.md` (page inventory, dangerous-action rules, PAPER/LIVE conventions), `CHANGELOG.md`, `docs/glossary.md` for new terms, and, for every decision made, an ADR in `docs/adr/` plus its row updated (or added) in `docs/decisions.md`.
- [ ] Each slice's pages exist with loading, empty, and error states on desktop and mobile, and Playwright tests pass for that slice.
- [ ] Portfolio page shows manual vs strategy quantities per symbol consistent with the ledger in the same-ticker scenario.
- [ ] Global kill switch engages from desktop and from a phone; release prompts for step-up.
- [ ] Changing a risk limit upward, raising an allocation, or viewing broker credential status after edit triggers step-up; credentials are never displayed.
- [ ] Charts show platform data with signal, order, and fill overlays for a paper strategy.
- [ ] Training a LightGBM model and launching a study are possible end to end from the UI (Gate 4).
- [ ] PWA installs on iOS and Android; a critical alert arrives as a push notification (Gate 5).

## Pitfalls to avoid

- Computing P&L, ownership, or risk in the browser; display what services compute.
- Paper and live views that look the same.
- Unthrottled WebSocket rendering that freezes the page during fast markets.
- Using TradingView's Advanced Charts or broker-integration features without a license, or letting TradingView's own broker integration place orders on the same accounts outside the platform's attribution (those orders would be treated as manual and could collide with strategy positions).
- Exposing the TradingView webhook on the main gateway's public address; use the dedicated relay.
- Forms generated from schemas without validation messages; training configs fail late and confuse.
- Hiding rejected intents; the risk reasons are essential feedback.
