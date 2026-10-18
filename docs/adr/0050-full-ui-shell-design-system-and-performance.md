# ADR-0050: Full UI: one shell and navigation registry, a shared design system, keyboard-first desktop, route-level code splitting, and in-app tearsheets

- **Status:** Accepted
- **Date:** 2026-10-18
- **Decision register:** D68 (applies D11, D12, D14 to the full UI)
- **Related:** UI-040..UI-047, UI-049, UI-051, UI-005, UI-007, UI-013, UI-025, [ADR-0012](0012-web-ui-spa-sse-pwa.md), [ADR-0028](0028-ui-trading-slice-charts-and-bff.md), section 14 prompt (Phase 5), research 07 §1–§2

## Context

Phases 0–4 built the UI in slices: each page had its own header, table styling, and container width, the navigation was a flat list of 13 links (a horizontal scroller on phones), and every page shipped in one 222 kB (gzip) bundle. Phase 5 must finish section 14: a cohesive desktop and mobile UI, keyboard shortcuts and a command palette, an end-of-day and manual-vs-strategy overview, tearsheet viewing, charts with indicators and split layouts, and performance under the strict CSP (LCP under 2.5 s over Tailscale). Section 13 is building its monitoring pages in parallel and needs a place to plug them in.

Constraints: the CSP is `script-src 'self'; style-src 'self'` (UI-005); the UI computes no P&L, ownership, or risk (UI-I2); every view has loading, empty, and error states (UI-007).

## Decision

- **One shell, one registry.** `apps/web/src/lib/nav.ts` lists every page once (path, label, icon, section, phone placement, `g`-shortcut key, palette keywords). The sidebar (collapsible, grouped into Overview, Trading, Research, Operations, Settings), the phone tab bar (four primary pages plus a "More" sheet), the command palette, and the shortcuts are all built from it. Section 13's pages join the `operations` section with one entry each.
- **Design tokens and primitives.** Colors, surfaces, and semantic tones are CSS variables in `styles.css` (dark default, light via `html.light`); `components/ui/` holds the primitives (`Button`, `Card`, `Badge`, `Dialog` with sheet and palette placements, `PageHeader`, `Stat`, `Segmented`, `Skeleton`, `EmptyState`, `Kbd`) and the shared `.data-table` style. Icons come from `lucide-react` (the shadcn/ui icon set; tree-shaken SVG components, no runtime CSS).
- **Status strip.** The header shows execution state, each account's session, reconciliation, margin flags, and day-trade count, halts, clock, strategy-runtime heartbeat age, open orders, and market-data feed freshness (the gateway adds marketdata's `GetFeedStatus` to the status snapshot). Unreachable services read "unknown".
- **Keyboard first on desktop.** ⌘K/Ctrl+K opens the command palette (pages, ticker → chart, theme, log out, and "engage global kill switch…", which only opens the usual confirmation); `g <key>` navigates; `?` lists shortcuts; Shift+T toggles the theme. Plain keys are ignored while typing. No shortcut performs a trading action by itself.
- **Route-level code splitting.** The entry chunk holds the login screens, the shell, and the dashboard; every other page is a lazily loaded chunk (`lazyRouteComponent`), prefetched on link hover or focus. The entry chunk drops from 222 kB to about 102 kB gzip; Lightweight Charts (64 kB) loads only with the Charts page. Chunks are same-origin files, so the CSP is unchanged. A failed chunk load (after a deploy) shows "Reload the app" instead of a blank page.
- **New pages.** Portfolio overview with an end-of-day tab (manual next to each strategy, positions by owner, equity by owner, today's fills and rejected intents, all as services report them); Backtests & tearsheets (runs with stitched out-of-sample metrics, an in-app tearsheet viewer, side-by-side comparison of two runs); a Data explorer (per-timeframe freshness, cached bars, feeds); Notifications; TradingView. The dashboard is rebuilt around per-account tiles, strategy cards with equity sparklines, and the trading state.
- **In-app tearsheets.** The tearsheet stays its own sandboxed document (UI-025). For the viewer, `?embed=1` returns it with `frame-ancestors 'self'` and `X-Frame-Options: SAMEORIGIN`; the CSP `sandbox` directive stays, and the viewer's `<iframe sandbox="">` grants nothing (no scripts, opaque origin, no navigation). Without `embed=1` the document is still unframeable.

## Alternatives considered

| Option | Why not |
|---|---|
| Keep per-page navigation and headers | Every new page (including section 13's) would re-invent layout; phones would keep a 13-item horizontal scroller |
| A component library with runtime CSS-in-JS (MUI, Chakra) | Injects `<style>` elements, which the CSP forbids; heavier than Tailwind + small primitives |
| Hand-written SVG icons | Equivalent output with more code to maintain; `lucide-react` is the shadcn/ui default and tree-shakes |
| Manual chunks only (`build.rollupOptions.output.manualChunks`) | Splits files but still loads them all at start; route-level lazy loading defers them |
| Inline the tearsheet HTML in the SPA | Needs `style-src 'unsafe-inline'` on the SPA and trusts generated HTML in the app's origin; a sandboxed frame keeps both out |
| Service-worker caching of the app shell for speed | A trading UI must never run a stale bundle; the SW caches nothing (ADR-0053) |

## Consequences

- Positive: one place to add a page; consistent layouts at 360/768/1280/1920 px; a smaller first load; tearsheets viewable without leaving the app; keyboard-driven desktop use.
- Negative / risks: lazily loaded pages show a skeleton for a moment on first visit; the `lucide-react` dependency must be kept current (pinned, Dependabot). Dynamic `import()` calls live only in `router.tsx` (route-level code splitting), the one documented exception to the "imports at the top" convention.
- Follow-up: section 13's monitoring and alerts pages add entries to `NAV` in the `operations` section; a virtualized blotter for 10k rows remains open (UI open question).

## Validation

`apps/web/src/components/phase5.test.tsx` (registry uniqueness and phone tabs, shortcut handling, palette entries, status strip "unknown" states), `TestUI047TearsheetEmbedAndSE065WidgetCSP`, the Playwright desktop and mobile journeys in `apps/web/e2e/`, and the build size (`pnpm --filter @strawberry/web build`). Revisit if the entry chunk grows past 150 kB gzip or the dashboard LCP over Tailscale exceeds 2.5 s.
