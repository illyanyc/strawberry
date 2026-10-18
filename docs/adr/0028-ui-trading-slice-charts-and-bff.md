# ADR-0028: UI trading slice: Lightweight Charts under the strict CSP, protojson BFF resources, and an SSE chart stream

- **Status:** Accepted
- **Date:** 2026-10-18
- **Decision register:** D46 (settles D12 for price charts; applies D11 and D14 to the trading pages)
- **Related:** UI-005, UI-006, UI-010..UI-017, RM-007, SE-003, VP-019, [ADR-0012](0012-web-ui-spa-sse-pwa.md), [ADR-0025](0025-oms-risk-gate-and-kill-switches.md), section 14 prompt (Phase 2 slice and the Phase 1 chart slice)

## Context

Phase 2 needs the portfolio, strategy, order, kill-switch, and chart pages. The open items were:

- D12 left the price-chart library to the chart slice.
- The gateway had no resources for execution, ledger, or marketdata.
- Section 14 sketches WebSocket channels, but D14 makes SSE the primary transport.

Three constraints apply:

- The gateway's CSP has no `unsafe-inline` (UI-005).
- The UI must never compute P&L, ownership, or risk.
- Every dangerous action must go through `policy.Authorize` with step-up.

## Decision

- **Price charts use TradingView Lightweight Charts 5.2.1** (Apache-2.0), pinned exactly.
  - Its built-in attribution logo injects an inline `<style>`, which the CSP blocks. Its link also sends the page's hostname and path to tradingview.com.
  - So `layout.attributionLogo` is `false`. The Charts page renders its own "Charting by TradingView" link with `rel="noopener noreferrer"`, and `Referrer-Policy: no-referrer` applies.
  - Chart values are converted to numbers for plotting only. Money shown as text stays a server decimal string (UI-008).
  - Analytics charts (uPlot or ECharts) remain open until the research slices.
- **Trading resources are the canonical protojson form of the `tb.*` contract messages.** Names are lowerCamelCase, and the gateway sets `EmitUnpopulated`, so it defines no private UI formats.
  - Decimals arrive as `{ "value": "…" }`, int64 timestamps as UTC-nanosecond strings, and enums by full name. Singular message fields are an object or `null`. Unset oneof members are omitted.
  - The UI's types are the generated protoc-gen-es `*Json` types (`json_types=true`) from `@strawberry/contracts`. They are wrapped in `Emitted<T>` (`apps/web/src/lib/trading.ts`) to express that emission contract. None are hand-written (frontend rule).
  - Request bodies the gateway defines itself (ticket, kill switch, mode, allocation, break resolution) are small validated snake_case JSON shapes, like the Phase 0 API. Exception: risk limits accept the `RiskLimits` message.
- **Authorization stays in the gateway's policy layer; services re-check what they own.**
  - New owner actions: `trading.read`, `orders.cancel`, and `strategy.mode.set` need no step-up. `ledger.break.resolve` needs a passkey.
  - Allocation increases are detected by the gateway, comparing against the ledger's current value without floats.
  - Limit loosening is detected by execution. Its `PermissionDenied` is answered with the policy's `risk.limits.raise` step-up.
  - Kill-switch release needs a passkey from a desktop client. Both the gateway and execution check this from the forwarded `Actor`.
  - A manual order for an account whose environment is unknown is authorized as live, which fails closed to a passkey.
- **One SSE stream per chart** (`/api/v1/stream/market`):
  - It subscribes to `md.bar.<tf>.<id>`, `md.bar.correction.<tf>.<id>`, `md.bar.partial.<tf>.<id>`, and `exec.>`.
  - Partial bars are coalesced to at most 4 Hz.
  - A slow client gets `resync` instead of silently dropped bars.
  - The gateway's NATS user can subscribe to `md.bar.>` and `exec.>`, and publishes nothing new.
  - History comes from marketdata `GetBars`, which reads the hot-cache ring (500 bars). Lake-backed history is Planned.
- **Tickers resolve through marketdata `ResolveSymbols`** (exact, current). Every UI request after that carries the `instrument_id`.

## Alternatives considered

| Option | Why not |
|---|---|
| Keep the library's attribution logo and relax `style-src` with a hash or nonce | The style is created at runtime, so a hash is brittle across upgrades; a nonce needs server-side templating of `index.html`. The logo link would also leak the tailnet hostname. |
| Hand-written JSON views per page in the gateway | A second, private schema to keep in sync with the protos; contracts-first forbids it. |
| protojson with proto field names (snake_case) | The generated TypeScript JSON types describe the canonical JSON names; snake_case would force hand-written UI types. |
| WebSocket channels per section 14 | D14: SSE covers the server-to-client need; commands are REST. |
| Gateway compares risk limits itself | Duplicates execution's `Raised` rule (17 fields with different directions); one implementation is safer. |
| Code-splitting the chart page | Needs an in-function dynamic import (repo rule: no inline imports). The bundle is about 205 kB gzipped, inside the LCP budget. Revisit with a route-level split if the budget tightens. |

## Consequences

- Positive:
  - The UI shows exactly what services compute, and every money or safety action has one decision point plus the owning service's check.
  - Charts run under the unchanged strict CSP.
- Negative / risks:
  - Protojson JSON names are the UI's API, so a proto field rename is a UI-visible breaking change. `buf breaking` guards field numbers; a rename also fails the web typecheck through the generated types.
  - The 500-bar cache limits chart history until lake-backed reads exist.
- Follow-up:
  - Signal overlays (Phase 4 model signals).
  - Lake-backed `GetBars`.
  - Mobile order entry behind U6 (setting `mobile.order_entry_enabled`, already enforced).
  - Analytics charts (D12 remainder).

## Validation

- `services/api-gateway/internal/gateway/trading_test.go`: kill switch, allocation and limit step-up, order ticket, mode, breaks, input validation, and the SSE stream.
- `services/marketdata/internal/api/api_test.go` (UI-010, UI-011).
- `apps/web/src/components/trading.test.tsx`.
- The Playwright shell spec's trading section: engage, pages, release step-up, and no CSP violations.
