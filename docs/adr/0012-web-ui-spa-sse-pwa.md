# ADR-0012: Static Vite + React SPA, SSE for real-time, installed PWA with Pushover for critical alerts

- **Status:** Accepted (research)
- **Date:** 2026-10-17
- **Decision register:** D11, D13, D14 (and D12 for charts, implemented in Phase 1)
- **Related:** research [09 §D11](../research/09-decisions.md#d11), [§D13](../research/09-decisions.md#d13), [§D14](../research/09-decisions.md#d14), [07](../research/07-ui-and-tradingview.md); rules UI-001..UI-008, SE-006, SE-013

## Context

The UI is a private dashboard for one owner on desktop and phone, behind Tailscale or Cloudflare Access plus app-level passkeys. The master default was Next.js with WebSockets. Research changed both: self-hosted Next.js had a middleware auth bypass (CVE-2025-29927) and React Server Components had a pre-auth RCE exploited in the wild (CVE-2025-55182); there is no SSR or SEO need; SSE reconnects with `Last-Event-ID`, works with cookie auth, and is sufficient for seconds-level latency.

## Decision

- **D11**: a static **Vite + React 19 SPA** (TanStack Router + Query, Tailwind 4, shadcn/ui-style components) built to `dist/` and served by the Go `api-gateway`. One auth surface: the gateway. No Node server in production. The build has no inline scripts, so the CSP stays `script-src 'self'; style-src 'self'`.
- **D14**: **SSE** (`/api/v1/events`) for all server→client streams; REST for commands; WebSocket only for a proven bidirectional high-rate need (none yet). The stream re-checks the session on every tick and ends with `session-ended`.
- **D13**: an installable **PWA** (manifest now; service worker and Web Push in Phase 5). Critical alerts also go through **Pushover emergency priority** (repeats until acknowledged), configured in the observability stack.
- Routing is code-based (no generated route tree) to keep builds simple; guards call `/auth/state`, but the server enforces every rule.

## Alternatives considered

| Option | Why not |
|---|---|
| Next.js (master default) | Second auth surface and RSC attack class with no benefit for a private SPA. Use static export only if public pages ever appear. |
| SvelteKit / SolidStart | Smaller ecosystem for trading widgets (Lightweight Charts wrappers, Perspective, TanStack). |
| WebSocket primary | Needs custom heartbeats, reconnect, and auth at upgrade; SSE covers all current flows. |
| Native mobile app | Not needed for monitor + alerts + kill switch (U6). |

## Consequences

- Positive: tiny attack surface, strict CSP without hashes, simple deploy (static files in the gateway image).
- Negative / risks: iOS Web Push only works for installed PWAs; Pushover covers critical alerts. SSE over HTTP/1.1 is limited to 6 connections per origin; served over HTTP/2 behind Tailscale serve / Cloudflare this disappears.

## Validation

`api.test.ts` (CSRF header, step-up retry), `EnvironmentBanner.test.tsx`, gateway SSE test `TestSSEStreamRequiresSessionAndEndsOnRevoke`, and the manual/automated browser walkthrough against the Compose stack.
