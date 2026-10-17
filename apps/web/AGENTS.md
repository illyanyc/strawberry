# apps/web

The only human interface: a static Vite + React 19 SPA served by `api-gateway`. It talks only to the gateway (REST + SSE), never to internal services or brokers, never sees credentials, and never decides authorization or computes risk, P&L, or ownership.

**Status:** In progress (Phase 0 shell slice)  ·  **Stack:** Vite 8, React 19, TanStack Router + Query, Tailwind 4, shadcn/ui-style components, `@simplewebauthn/browser` ([ADR-0012](../../docs/adr/0012-web-ui-spa-sse-pwa.md), D11/D13/D14)

## Responsibilities
- Owns: screens, client-side routing, the step-up dialog, SSE subscription, PWA manifest.
- Does not own: any business rule; the server is authoritative (rules in `docs/business-logic/ui.md` and `security.md`).

## Interfaces
| Direction | Kind | Name | Contract |
|---|---|---|---|
| out | HTTP | `/auth/*`, `/api/v1/*` | `contracts/openapi/gateway.yaml` |
| in | SSE | `/api/v1/events` (`trading-status`, `session-ended`) | same |

## Business rules enforced here (presentation only)
- `UI-001` PAPER/LIVE banner on every authenticated page → `src/components/EnvironmentBanner.tsx`; test `EnvironmentBanner.test.tsx`
- `UI-002` step-up dialog on `step_up_required`, single retry → `src/lib/api.ts`, `src/components/StepUpDialog.tsx`; `api.test.ts`
- `UI-003` credentials write-only → `src/routes/settings.tsx` (`BrokersPage`)
- `UI-004` stale-status indicator → `src/lib/events.ts`, `src/components/AppShell.tsx`
- `UI-005` no inline scripts/styles or raw HTML → `eslint.config.js`, `vite.config.ts`
- `SE-006` CSRF header on state-changing requests → `src/lib/api.ts`; `api.test.ts`

## Layout
| Path | Purpose |
|---|---|
| `index.html`, `public/` | Entry HTML (no inline script), PWA manifest, icon |
| `src/main.tsx`, `src/router.tsx` | Bootstrap, code-based TanStack routes with auth guards |
| `src/lib/api.ts` | Fetch client: CSRF header, step-up retry, 401 handling, API types |
| `src/lib/webauthn.ts` | Passkey registration/assertion ceremonies |
| `src/lib/events.ts` | SSE status hook and staleness clock |
| `src/components/` | App shell, environment banner, step-up dialog, `ui/` primitives |
| `src/routes/` | Login/setup, dashboard, security, broker connections, platform settings, audit log |
| `e2e/`, `playwright.config.ts` | Playwright browser test against the running stack |

## Run and test
- Dev: `pnpm --filter @strawberry/web dev` (proxies `/api` and `/auth` to `TB_GATEWAY_URL`, default `http://localhost:8080`); WebAuthn needs the origin in `TB_AUTH_ORIGINS`.
- Build: `pnpm --filter @strawberry/web build` → `dist/` (copied into the api-gateway image).
- Test: `pnpm --filter @strawberry/web test`, `typecheck`, `lint`. Needs Node ≥ 22.15.
- Browser e2e against a fresh running stack: `make e2e-ui` (Playwright + Chrome virtual authenticator: setup, TOTP enrollment and login, passkey step-up for live trading, LIVE banner, write-only credentials, audit verification, mobile layout, passkey login, zero CSP violations). First time: `pnpm --filter @strawberry/web exec playwright install --with-deps chromium`.

## Gotchas
- The gateway's CSP is `script-src 'self'; style-src 'self'`: no inline `<script>`/`<style>`, no `dangerouslySetInnerHTML`. React `style` props are fine (CSSOM).
- Path alias `@/` → `src/`.
- Web Push and a service worker arrive with alerts (Phase 5); the manifest makes the app installable now.
