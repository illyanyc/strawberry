# apps/web

The only human interface: a static Vite + React 19 SPA served by `api-gateway`. It talks only to the gateway (REST + SSE), never to internal services or brokers, never sees credentials, and never decides authorization or computes risk, P&L, or ownership.

**Status:** In progress (Phase 0 shell slice; Phase 2 trading slice)  ·  **Stack:** Vite 8, React 19, TanStack Router + Query, Tailwind 4, shadcn/ui-style components, `@simplewebauthn/browser`, Lightweight Charts 5.2.1, generated types from `@strawberry/contracts` ([ADR-0012](../../docs/adr/0012-web-ui-spa-sse-pwa.md), D11/D13/D14; [ADR-0028](../../docs/adr/0028-ui-trading-slice-charts-and-bff.md), D46)

## Responsibilities
- Owns: screens, client-side routing, the step-up dialog, SSE subscription, PWA manifest.
- Does not own: any business rule; the server is authoritative (rules in `docs/business-logic/ui.md` and `security.md`).

## Interfaces
| Direction | Kind | Name | Contract |
|---|---|---|---|
| out | HTTP | `/auth/*`, `/api/v1/*` | `contracts/openapi/gateway.yaml` |
| in | SSE | `/api/v1/events` (`trading-status`, `session-ended`) | same |
| in | SSE | `/api/v1/stream/market` (`ready`, `bar`, `partial`, `fill`, `resync`, `session-ended`) | same |
| types | TS | `@strawberry/contracts` `*Json` types (protoc-gen-es `json_types`) wrapped in `Emitted<T>` | `contracts/proto` |

## Business rules enforced here (presentation only)
- `UI-001` PAPER/LIVE banner on every authenticated page → `src/components/EnvironmentBanner.tsx`; test `EnvironmentBanner.test.tsx`
- `UI-002` step-up dialog on `step_up_required`, single retry → `src/lib/api.ts`, `src/components/StepUpDialog.tsx`; `api.test.ts`
- `UI-003` credentials write-only → `src/routes/settings.tsx` (`BrokersPage`)
- `UI-004` stale-status indicator → `src/lib/events.ts`, `src/components/AppShell.tsx`
- `UI-005` no inline scripts/styles or raw HTML → `eslint.config.js`, `vite.config.ts`
- `SE-006` CSRF header on state-changing requests → `src/lib/api.ts`; `api.test.ts`
- `UI-006` global kill switch (two taps, FROZEN link, release on the Risk page) → `src/components/KillSwitch.tsx`, `src/routes/risk.tsx`; `trading.test.tsx`, `e2e/shell.spec.ts`
- `UI-011` tickers → instrument IDs → `src/lib/instruments.ts`
- `UI-012` charts (history, live stream, fill markers, order lines) → `src/components/PriceChart.tsx`, `src/lib/market.ts`, `src/routes/charts.tsx`; `trading.test.tsx`
- `UI-013` status bar → `src/components/AppShell.tsx` (`TradingBadges`)
- `UI-014` strategies → `src/routes/strategies.tsx`, `src/components/LimitsEditor.tsx`
- `UI-015` order ticket → `src/routes/order-ticket.tsx`; `trading.test.tsx`
- `UI-016` portfolio → `src/routes/portfolio.tsx`, `src/components/trading.tsx` (`OwnershipTable`); `trading.test.tsx`
- `UI-017` blotter and trace → `src/routes/orders.tsx`
- `UI-I2` no P&L/ownership/risk math → `src/lib/trading.ts` (display helpers only)

## Layout
| Path | Purpose |
|---|---|
| `index.html`, `public/` | Entry HTML (no inline script), PWA manifest, icon |
| `src/main.tsx`, `src/router.tsx` | Bootstrap, code-based TanStack routes with auth guards |
| `src/lib/api.ts` | Fetch client: CSRF header, step-up retry, 401 handling, API types |
| `src/lib/webauthn.ts` | Passkey registration/assertion ceremonies |
| `src/lib/events.ts` | SSE status hook and staleness clock |
| `src/lib/trading.ts` | Generated contract types (`Emitted<…Json>`) and display helpers (decimal strings, enum labels, ns timestamps) |
| `src/lib/market.ts` | SSE chart-stream hook |
| `src/lib/instruments.ts` | Ticker → instrument ID lookup |
| `src/components/` | App shell (status badges, kill switch), environment banner, step-up dialog, `KillSwitch.tsx`, `PriceChart.tsx`, `LimitsEditor.tsx`, `trading.tsx` (signed values, mode badge, query states, risk checks, ownership table), `ui/` primitives |
| `src/routes/` | Login/setup, dashboard, portfolio, strategies (list + detail), orders (blotter + trace), order ticket, charts, risk, security, broker connections, platform settings, audit log |
| `e2e/`, `playwright.config.ts` | Playwright browser test against the running stack |

## Run and test
- Dev: `pnpm --filter @strawberry/web dev` (proxies `/api` and `/auth` to `TB_GATEWAY_URL`, default `http://localhost:8080`); WebAuthn needs the origin in `TB_AUTH_ORIGINS`.
- Build: `pnpm --filter @strawberry/web build` → `dist/` (copied into the api-gateway image).
- Test: `pnpm --filter @strawberry/web test`, `typecheck`, `lint`. Needs Node ≥ 22.15.
- Browser e2e against a fresh running stack: `make e2e-ui` (Playwright + Chrome virtual authenticator: setup, TOTP enrollment and login, global kill switch engage, trading pages, release step-up (passkey only, cancel keeps it latched), passkey step-up for live trading, LIVE banner, write-only credentials, audit verification, mobile layout (FROZEN visible), passkey login and kill-switch release, zero CSP violations). First time: `pnpm --filter @strawberry/web exec playwright install --with-deps chromium`.

## Gotchas
- The gateway's CSP is `script-src 'self'; style-src 'self'`: no inline `<script>`/`<style>`, no `dangerouslySetInnerHTML`. React `style` props are fine (CSSOM), but prefer Tailwind classes.
- Lightweight Charts: keep `layout.attributionLogo: false` (the logo injects an inline `<style>` the CSP blocks); the Charts page renders the attribution link instead. Chart prices are numbers for drawing only.
- Generated contract types are all-optional; use the `Emitted<T>` aliases from `src/lib/trading.ts` (the gateway emits every field; messages may be `null`, oneof members may be missing).
- `<Dialog>` titles use `useId`: the step-up dialog can open on top of another dialog.
- The bundle is ~205 kB gzipped with charts; route-level code splitting is deferred (it would need an in-function dynamic import).
- Path alias `@/` → `src/`.
- Web Push and a service worker arrive with alerts (Phase 5); the manifest makes the app installable now.
