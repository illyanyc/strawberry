# apps/web

The only human interface: a static Vite + React 19 SPA served by `api-gateway`. It talks only to the gateway (REST + SSE), never to internal services or brokers, never sees credentials, and never decides authorization or computes risk, P&L, or ownership.

**Status:** In progress (Phase 0 shell; Phase 2 trading; Phase 4 research and analyst; Phase 5 full UI, PWA, Web Push, TradingView)  ·  **Stack:** Vite 8, React 19, TanStack Router + Query, Tailwind 4, shadcn/ui-style components, `lucide-react` icons, `@simplewebauthn/browser`, Lightweight Charts 5.2.1, generated types from `@strawberry/contracts` ([ADR-0012](../../docs/adr/0012-web-ui-spa-sse-pwa.md), D11/D13/D14; [ADR-0028](../../docs/adr/0028-ui-trading-slice-charts-and-bff.md), D46; [ADR-0050](../../docs/adr/0050-full-ui-shell-design-system-and-performance.md), D68)

## Responsibilities
- Owns: screens, client-side routing, the navigation registry, the step-up dialog, SSE subscriptions, the PWA manifest, icons, and the notification-only service worker.
- Does not own: any business rule; the server is authoritative (rules in `docs/business-logic/ui.md` and `security.md`).

## Interfaces
| Direction | Kind | Name | Contract |
|---|---|---|---|
| out | HTTP | `/auth/*`, `/api/v1/*` | `contracts/openapi/gateway.yaml` |
| in | SSE | `/api/v1/events` (`trading-status`, `session-ended`) | same |
| in | SSE | `/api/v1/stream/market` (`ready`, `bar`, `partial`, `fill`, `signal`, `resync`, `session-ended`) | same |
| in | Push | Web Push payloads (Declarative Web Push shape) handled by `public/sw.js` | ADR-0053 |
| in | SSE | `/api/v1/research/training/jobs/{id}/stream`, `/api/v1/research/studies/{id}/stream` (`progress`, `done`) | same |
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
- `UI-018`–`UI-022` AI analyst (journal, autonomy selector with step-up to raise, proposal approvals, trial ledger, code proposals, chat) → `src/routes/analyst.tsx`, `src/lib/analyst.ts`; `analyst.test.tsx`, `e2e/shell.spec.ts`
- `UI-I2` no P&L/ownership/risk math → `src/lib/trading.ts` (display helpers only)
- `UI-023` training (schema forms, estimate-before-launch, SSE progress, run diagnostics) → `src/routes/training.tsx`, `src/components/SchemaForm.tsx`, `src/lib/research.ts`; `research.test.tsx`
- `UI-024` experiments (ledger, lineage stats, cycles, pre-registered studies, trials, convergence) → `src/routes/experiments.tsx`; `research.test.tsx`
- `UI-025` tearsheets and evidence packs as their own sandboxed documents → `src/lib/research.ts` (`tearsheetUrl`, `evidenceUrl`); `research.test.tsx`
- `UI-026` registry stages (never `live`) and the holdout counter → `src/lib/research.ts` (`promotionTargets`), `src/routes/experiments.tsx`; `research.test.tsx`
- `UI-027` champion vs challenger → `src/routes/experiments.tsx` (`ChampionChallenger`)
- `UI-040` design tokens and primitives → `src/styles.css`, `src/components/ui/`, `src/lib/theme.ts`
- `UI-041` navigation registry → `src/lib/nav.ts`, `src/components/AppShell.tsx`; `phase5.test.tsx`
- `UI-042` command palette and shortcuts → `src/components/CommandPalette.tsx`, `src/lib/shortcuts.ts`; `phase5.test.tsx`
- `UI-043` route-level code splitting → `src/router.tsx`, `src/components/RouteStates.tsx`
- `UI-044` PWA install and Web Push → `public/manifest.webmanifest`, `public/sw.js`, `public/icons/`, `src/lib/push.ts`, `src/routes/notifications.tsx`; `phase5.test.tsx`
- `UI-045` dashboard → `src/routes/dashboard.tsx`, `src/components/Sparkline.tsx`
- `UI-046` portfolio overview and end of day → `src/routes/overview.tsx`; `phase5.test.tsx`
- `UI-047` backtests, in-app tearsheets, comparison → `src/routes/backtests.tsx`
- `UI-048` TradingView page → `src/routes/tradingview.tsx`; `phase5.test.tsx`
- `UI-049` chart layouts, indicators, signal overlays → `src/routes/charts.tsx`, `src/components/PriceChart.tsx`, `src/lib/indicators.ts`, `src/lib/market.ts`; `phase5.test.tsx`
- `UI-050` / `RM-020` phone halt with passkey → `src/components/KillSwitch.tsx`; `phase5.test.tsx`, `e2e/ui-phase5.spec.ts`
- `UI-013` status strip with feed freshness → `src/components/StatusStrip.tsx`; `phase5.test.tsx`
- `UI-051` data explorer → `src/routes/data.tsx`

## Layout
| Path | Purpose |
|---|---|
| `index.html`, `public/` | Entry HTML (no inline script), PWA manifest, icon |
| `src/main.tsx`, `src/router.tsx` | Bootstrap (theme, service worker), code-based TanStack routes with auth guards and lazily loaded pages |
| `src/lib/nav.ts` | Navigation registry (every page once: path, label, icon, section, phone tab, `g` key, palette keywords) |
| `src/lib/shortcuts.ts`, `src/lib/theme.ts` | Keyboard shortcuts; dark/light theme (localStorage display preference) |
| `src/lib/push.ts` | Service-worker registration, Web Push subscribe/unsubscribe, install prompt |
| `src/lib/indicators.ts` | Drawing-only chart indicators (SMA, EMA, session VWAP) |
| `src/lib/queries.ts` | Shared server-state hooks (accounts, strategies, status, equity curves) |
| `src/lib/api.ts` | Fetch client: CSRF header, step-up retry, 401 handling, API types |
| `src/lib/webauthn.ts` | Passkey registration/assertion ceremonies |
| `src/lib/events.ts` | SSE status hook and staleness clock |
| `src/lib/trading.ts` | Generated contract types (`Emitted<…Json>`) and display helpers (decimal strings, enum labels, ns timestamps) |
| `src/lib/market.ts` | SSE chart-stream hook |
| `src/lib/instruments.ts` | Ticker → instrument ID lookup |
| `src/lib/analyst.ts` | Analyst contract types, labels, display helpers, BFF calls |
| `src/lib/research.ts` | Research contract types, schema-form helpers, stage rules, SSE progress hook |
| `src/components/` | App shell (sidebar, header, phone tab bar, More sheet), `StatusStrip.tsx`, `CommandPalette.tsx` (+ shortcut help), `BrandMark.tsx`, `Sparkline.tsx`, `RouteStates.tsx`, environment banner, step-up dialog, `KillSwitch.tsx`, `PriceChart.tsx`, `LimitsEditor.tsx`, `trading.tsx` (signed values, mode badge, query states, risk checks, ownership table), `SchemaForm.tsx` (JSON-Schema hyperparameter forms), `ResearchCharts.tsx` (SVG line, bar, parallel-coordinates charts), `ui/` primitives |
| `src/routes/` | Login/setup, dashboard, portfolio overview (`overview.tsx`), data explorer (`data.tsx`), backtests & tearsheets (`backtests.tsx`), notifications, TradingView, portfolio, strategies (list + detail), orders (blotter + trace), order ticket, charts, risk, training (form, job, run), experiments (lineages, lineage, study, candidate), AI analyst, security, broker connections, platform settings, audit log |
| `e2e/`, `playwright.config.ts` | Playwright journeys against the running stack: `shell.spec.ts` (Phases 0–4) and `ui-phase5.spec.ts` (Phase 5 desktop and phone flows); screenshots in `test-results/screens` |
| `public/` | `manifest.webmanifest`, `icon.svg`, `icons/` (PNG icons generated by `scripts/gen-icons.mjs`), `sw.js` (notifications only, no caching) |
| `scripts/gen-icons.mjs` | Rasterizes the SVG mark into the PWA icons with Playwright's Chromium |

## Run and test
- Dev: `pnpm --filter @strawberry/web dev` (proxies `/api` and `/auth` to `TB_GATEWAY_URL`, default `http://localhost:8080`); WebAuthn needs the origin in `TB_AUTH_ORIGINS`.
- Build: `pnpm --filter @strawberry/web build` → `dist/` (copied into the api-gateway image).
- Test: `pnpm --filter @strawberry/web test`, `typecheck`, `lint`. Needs Node ≥ 22.15.
- Browser e2e against a fresh running stack: `make e2e-ui` (Playwright + Chrome virtual authenticator). `shell.spec.ts`: setup, TOTP enrollment and login, global kill switch engage, trading pages, release step-up (passkey only, cancel keeps it latched), passkey step-up for live trading, LIVE banner, write-only credentials, audit verification, mobile layout (FROZEN visible), passkey login and kill-switch release. `ui-phase5.spec.ts` (runs after it on the same stack): desktop shell, palette, shortcuts, overview, backtests, charts, notifications, TradingView, theme; phone tab bar, More sheet, passkey halt, desktop-only release, phone order entry refused. Both assert zero CSP violations. First time: `pnpm --filter @strawberry/web exec playwright install --with-deps chromium`.

## Gotchas
- The gateway's CSP is `script-src 'self'; style-src 'self'`: no inline `<script>`/`<style>`, no `dangerouslySetInnerHTML`. React `style` props are fine (CSSOM), but prefer Tailwind classes.
- Lightweight Charts: keep `layout.attributionLogo: false` (the logo injects an inline `<style>` the CSP blocks); the Charts page renders the attribution link instead. Chart prices are numbers for drawing only.
- Generated contract types are all-optional; use the `Emitted<T>` aliases from `src/lib/trading.ts` (the gateway emits every field; messages may be `null`, oneof members may be missing).
- `<Dialog>` titles use `useId`: the step-up dialog can open on top of another dialog.
- Pages are lazily loaded (`lazyRouteComponent(() => import(...))` in `router.tsx`): route-level code splitting is the one documented exception to "imports at the top" (UI-043). The entry chunk is about 102 kB gzip; keep it under 150 kB.
- Add a page: route in `router.tsx` plus one entry in `lib/nav.ts` (section, icon, optional phone tab and `g` key). Section 13's monitoring pages go in the `operations` section.
- Regenerate PWA icons after changing `public/icon.svg`: `pnpm --filter @strawberry/web exec node scripts/gen-icons.mjs`.
- The service worker must never cache or intercept requests (UI-044): a stale bundle must not run.
- Chart colors come from CSS variables read at draw time (canvas cannot use `color-mix`); `PriceChart` re-reads them on theme change.
- `Emitted<T>` types protojson maps as nullable objects; read them through `kv()` from `lib/research.ts` (the gateway always emits `{}`). Doubles may arrive as `"NaN"`/`"Infinity"`: format with `fmt()`/`pct()`, plot with `plotNum()`.
- Research charts are React SVG with Tailwind `stroke-*`/`fill-*` classes (no chart library, no inline `<style>`). Tests that render pages need a memory router (`research.test.tsx` `renderAt`) and stub `EventSource`.
- Path alias `@/` → `src/`.
- Web Push needs a secure origin (localhost or HTTPS) and, on iOS, the Home Screen app.
