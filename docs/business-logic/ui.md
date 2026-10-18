# UI

**Prefix:** `UI-`  ·  **Owners:** `apps/web`, `api-gateway` (BFF endpoints)  ·  **Related:** [security.md](security.md), [risk-management.md](risk-management.md), [ADR-0012](../adr/0012-web-ui-spa-sse-pwa.md)

## Scope

Presentation rules for the web UI on desktop and mobile: page inventory, PAPER/LIVE conventions, dangerous-action UX, real-time behavior. Authorization, step-up, and validation are server-side (`security.md`); the UI only reflects them.

## Concepts and states

- **Page inventory (Phase 0):** login, first-run setup, dashboard, security (passkeys, authenticator app, recovery codes, sessions), broker connections (write-only), platform settings, audit log.
- **Page inventory (Phase 2):** dashboard accounts summary and strategy cards, Portfolio, Strategies list and detail, Orders blotter with trace, Manual order ticket, Charts, Risk & kill switches. Later slices add backtests (3), training/experiments/assistant (4), monitoring and the alerts inbox (5).
- **Trading status:** streamed over SSE (`trading-status`): environment, live flag, session expiry, and execution's `TradingStatus` (global state; per account: session, reconciliation, margin flags, day-trade count and PDT mode; strategy health; active kill switches and halts).
- **Trading resources:** the gateway returns the canonical protojson form of the `tb.*` messages (camelCase, every field emitted; decimals `{value}`, int64 ns strings, full enum names; ADR-0028), typed in the UI by the generated `*Json` types. The UI displays what services compute and never derives P&L, ownership, or risk.

## Rules

| ID | Rule | Status | Code / tests |
|---|---|---|---|
| UI-001 | Every authenticated page MUST show a persistent environment banner: **PAPER** (calm) or **LIVE** (high-contrast, pulsing, labelled in text). Unknown state shows "connecting", never PAPER by default. | Implemented | `apps/web/src/components/EnvironmentBanner.tsx`; `EnvironmentBanner.test.tsx` |
| UI-002 | A `403 step_up_required` response MUST open the step-up dialog (passkey; TOTP only when the server says `any`) and retry the original request once on success; cancel surfaces an error, never a silent failure. | Implemented | `apps/web/src/lib/api.ts`, `StepUpDialog.tsx`; `api.test.ts` |
| UI-003 | Broker credential forms MUST be write-only: secret inputs are cleared after submit and stored values are never displayed; status shows only field names and timestamps. LIVE submissions use the danger style. | Implemented | `apps/web/src/routes/settings.tsx` |
| UI-004 | Live values MUST show staleness: if no SSE event arrived within 45 s the status indicator turns to "stale". The banner shows the most recent of the SSE stream and the REST snapshot (refetched after any settings change), so a mode change is visible immediately. | Implemented | `apps/web/src/lib/events.ts`, `AppShell.tsx`; `apps/web/e2e/shell.spec.ts` |
| UI-005 | The SPA MUST NOT use inline scripts, inline `<style>`, or raw HTML injection, so the gateway's strict CSP needs no exceptions. | Implemented | `apps/web/eslint.config.js`, `vite.config.ts`; build output check |
| UI-006 | The global kill switch MUST be reachable from every authenticated screen in at most two taps on mobile: the status-bar button opens a confirmation and the confirmation engages it (no step-up; `cancel_open` by default, `freeze` and `flatten` selectable). While a global switch is latched the button is replaced by a **FROZEN** link to the Risk page. Release is offered only on the Risk page and MUST need a fresh passkey from a desktop client (RM-007); a phone gets `desktop_required`. | Implemented | `components/KillSwitch.tsx`, `routes/risk.tsx`, gateway `handleKillEngage`/`handleKillRelease`; `TestRM007KillSwitchEngageAndDesktopPasskeyRelease`, `trading.test.tsx`, `e2e/shell.spec.ts` |
| UI-007 | Every data view MUST render loading, empty, and error states, and layouts MUST work at 360, 768, 1280, and 1920 px. | Implemented | `apps/web/src/routes/*` |
| UI-008 | Timestamps are shown in local time with the zone label; money is shown from server decimal strings (no float math). | Implemented | `apps/web/src/lib/utils.ts` (`formatTime`); `utils.test.ts` |
| UI-009 | Cached server data MUST be discarded on logout and login, so one session's data is never shown to the next. | Implemented | `apps/web/src/components/AppShell.tsx`, `src/routes/auth.tsx`; `e2e/shell.spec.ts` |
| UI-010 | Chart history MUST come from the platform's own data: marketdata `GetBars` serves final bars from the hot-cache ring (at most 500 per timeframe), oldest first, optionally windowed on `ts_open`. Lake-backed history is Planned. | Implemented | marketdata `api.GetBars`; `TestUI010GetBarsFromCacheRing` |
| UI-011 | Tickers entered in the UI MUST be resolved to an `instrument_id` (exact, case-insensitive, current symbol) before any chart, order, or filter uses them; unknown tickers are reported, never guessed. | Implemented | marketdata `ResolveSymbols`, gateway `/api/v1/instruments/resolve`, `lib/instruments.ts`; `TestUI011ResolveSymbols` |
| UI-012 | The chart stream (`/api/v1/stream/market`, SSE) MUST send final and corrected bars, coalesce partial bars to at most 4 Hz (latest wins), forward only executions in the charted instrument, end with `resync` when the client falls behind (the UI reloads history), and unsubscribe on disconnect. Instrument IDs and timeframes reaching bus subjects MUST match `[A-Za-z0-9_-]{1,64}` and `1m|5m|15m|30m|1h|1d`. Charts show fill markers and price lines for resting orders and protective stops. | Implemented | gateway `stream.go`, `components/PriceChart.tsx`, `routes/charts.tsx`; `TestUI012MarketStream`, `TestTradingInputValidationAndJSON`, `trading.test.tsx` |
| UI-013 | The status bar MUST show execution's state (running/frozen), each account's session, reconciliation, margin flags, and day-trade count (with PDT mode when `legacy`), and the number of active halts. When execution is unreachable the snapshot carries `trading: null` and the bar shows "Execution: unknown", never a healthy state. | Implemented | gateway `statusSnapshot`, `AppShell.tsx` |
| UI-014 | Strategy mode controls offer only `disabled`, `shadow`, and `paper`; `live` MUST be rejected by the gateway before reaching execution (go-live flow only, SM-004). Allocation increases and limit loosening need a passkey step-up (RM-007); decreases and tightening do not. | Implemented | gateway `handleStrategyMode`, `handleStrategyAllocation`, `handleRiskLimitsPut`; `TestUI014StrategyModeNeverLive`, `TestRM007AllocationIncreaseNeedsPasskey`, `TestRM007RiskLimitRaiseMapsToStepUp` |
| UI-015 | The manual order ticket MUST preview before submit (dry-run decision with every check, estimated price and notional, ownership note) and MUST NOT allow submitting a preview the gate would reject. Submitted tickets become `manual` intents with source `UI` and a client ULID reused on retry. An account whose environment is unknown is treated as live (passkey step-up, SE-003). From a phone, submit MUST be refused unless `mobile.order_entry_enabled` is on (U6). | Implemented | `routes/order-ticket.tsx`, gateway `handleOrderPreview`/`handleOrderSubmit`; `TestUI015ManualOrderTicket`, `trading.test.tsx` |
| UI-016 | The Portfolio page MUST show, per broker account and per instrument, the broker quantity, the ledger total, the `manual` bucket, and each strategy's quantity as the ledger reports them, flag broker/ledger differences, list open breaks with a resolution flow that needs a passkey (VP-019), and show the broker-lot shadow ledger. | Implemented | `routes/portfolio.tsx`, `components/trading.tsx` (`OwnershipTable`), gateway `handleResolveBreak`; `trading.test.tsx`, `TestVP019BreakResolutionNeedsPasskey` |
| UI-017 | The blotter MUST show rejected intents with every failed check (never hidden), orders with owner, role, and state (UNKNOWN highlighted), and fills; filters by strategy or `manual` and account; open orders can be canceled; every row opens the correlation trace (intent → decision → orders → fills). | Implemented | `routes/orders.tsx`, gateway `/api/v1/orders`, `/intents`, `/fills`, `/trace/{id}` |

## Invariants

| ID | Invariant | Checked by |
|---|---|---|
| UI-I1 | The UI never holds a session token in JS-readable storage (cookie is HttpOnly). | Code review; no `localStorage`/`sessionStorage` use |
| UI-I2 | The UI never computes P&L, ownership, or risk; money is displayed as the service's decimal string (chart plotting converts to numbers for drawing only). | Code review; `OwnershipTable` and `RiskChecks` render service values verbatim (`trading.test.tsx`) |

## Edge cases

| Scenario | Expected behavior | Rules |
|---|---|---|
| Owner signed in with TOTP tries to enable live trading | Step-up dialog offers passkey only; TOTP is not accepted | UI-002, SE-014 |
| Session revoked from another device while dashboard is open | SSE sends `session-ended`; UI navigates to login | UI-004, SE-005 |
| Gateway restarts | EventSource reconnects automatically; indicator shows stale until the next event | UI-004 |
| Execution is down while the dashboard is open | Status bar shows "Execution: unknown"; the kill switch button stays enabled and reports the 503 if pressed | UI-013, UI-006 |
| Owner tries to release a kill switch from a phone | Gateway answers `desktop_required`; execution would refuse it too | UI-006, RM-007 |
| Owner signed in with TOTP releases a kill switch | Step-up dialog offers passkey only; cancel leaves the switch latched | UI-006, SE-014 |
| Manual sell larger than the manual quantity in a ticker a strategy also holds | Preview shows the failed ownership check; submit stays disabled | UI-015, VP-003 |
| Chart client falls behind a fast stream | Gateway sends `resync` and closes; the UI reloads history and reconnects | UI-012 |
| Ticker unknown to the instrument master | "Unknown symbol" error; nothing is charted or ordered | UI-011 |

## Open questions

- Command palette and keyboard shortcuts (section 14): Planned.
- Virtualized blotter for 10k rows and split chart layouts: Planned (Phase 5 full UI).
- Signal overlays on charts: Planned with model signals (Phase 4).
