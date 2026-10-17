# UI

**Prefix:** `UI-`  ·  **Owners:** `apps/web`, `api-gateway` (BFF endpoints)  ·  **Related:** [security.md](security.md), [risk-management.md](risk-management.md), [ADR-0012](../adr/0012-web-ui-spa-sse-pwa.md)

## Scope

Presentation rules for the web UI on desktop and mobile: page inventory, PAPER/LIVE conventions, dangerous-action UX, real-time behavior. Authorization, step-up, and validation are server-side (`security.md`); the UI only reflects them.

## Concepts and states

- **Page inventory (Phase 0):** login, first-run setup, dashboard, security (passkeys, authenticator app, recovery codes, sessions), broker connections (write-only), platform settings, audit log. Later slices add charts (1), portfolio/strategies/orders/kill switch (2), backtests (3), training/experiments/assistant (4), monitoring (5).
- **Trading status:** streamed over SSE (`trading-status`); fields grow per phase (broker sessions, feed freshness, reconciliation, kill switches, margin/day-trade state).

## Rules

| ID | Rule | Status | Code / tests |
|---|---|---|---|
| UI-001 | Every authenticated page MUST show a persistent environment banner: **PAPER** (calm) or **LIVE** (high-contrast, pulsing, labelled in text). Unknown state shows "connecting", never PAPER by default. | Implemented | `apps/web/src/components/EnvironmentBanner.tsx`; `EnvironmentBanner.test.tsx` |
| UI-002 | A `403 step_up_required` response MUST open the step-up dialog (passkey; TOTP only when the server says `any`) and retry the original request once on success; cancel surfaces an error, never a silent failure. | Implemented | `apps/web/src/lib/api.ts`, `StepUpDialog.tsx`; `api.test.ts` |
| UI-003 | Broker credential forms MUST be write-only: secret inputs are cleared after submit and stored values are never displayed; status shows only field names and timestamps. LIVE submissions use the danger style. | Implemented | `apps/web/src/routes/settings.tsx` |
| UI-004 | Live values MUST show staleness: if no SSE event arrived within 45 s the status indicator turns to "stale". | Implemented | `apps/web/src/lib/events.ts`, `AppShell.tsx` |
| UI-005 | The SPA MUST NOT use inline scripts, inline `<style>`, or raw HTML injection, so the gateway's strict CSP needs no exceptions. | Implemented | `apps/web/eslint.config.js`, `vite.config.ts`; build output check |
| UI-006 | The global kill switch MUST be reachable from every authenticated screen in at most two taps on mobile (engage: confirmation; release: step-up). Phase 0 shows it disabled until execution exists. | Specified | `apps/web/src/components/AppShell.tsx` (placeholder) |
| UI-007 | Every data view MUST render loading, empty, and error states, and layouts MUST work at 360, 768, 1280, and 1920 px. | Implemented | `apps/web/src/routes/*` |
| UI-008 | Timestamps are shown in local time with the zone label; money is shown from server decimal strings (no float math). | Implemented | `apps/web/src/lib/utils.ts` (`formatTime`) |

## Invariants

| ID | Invariant | Checked by |
|---|---|---|
| UI-I1 | The UI never holds a session token in JS-readable storage (cookie is HttpOnly). | Code review; no `localStorage`/`sessionStorage` use |

## Edge cases

| Scenario | Expected behavior | Rules |
|---|---|---|
| Owner signed in with TOTP tries to enable live trading | Step-up dialog offers passkey only; TOTP is not accepted | UI-002, SE-014 |
| Session revoked from another device while dashboard is open | SSE sends `session-ended`; UI navigates to login | UI-004, SE-005 |
| Gateway restarts | EventSource reconnects automatically; indicator shows stale until the next event | UI-004 |

## Open questions

- Command palette and keyboard shortcuts (section 14) arrive with the trading pages.
