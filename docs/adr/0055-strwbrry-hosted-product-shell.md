# ADR-0055: Strwbrry public site, hosted shell, and Clerk with a local mock

- **Status:** Accepted
- **Date:** 2026-10-18
- **Decision register:** D73
- **Related:** ADR-0009, ADR-0012, ADR-0054, UI-054..UI-056, SE-065..SE-067

## Context

The trading UI is a Vite SPA (D11) with no server render, so it cannot be the public site. The hosted product needs marketing pages, monthly plans, and a place to connect the user's own Alpaca and FMP keys. Clerk is the hosted identity and billing vendor. The Mac Mini install keeps passkeys (ADR-0009) and must keep booting when Clerk is not configured. Live trading stays off (ADR-0054).

## Decision

- The public name is **Strwbrry**. The marketing origin is `https://strwbrry.ai`. The app origin is `https://app.strwbrry.ai`. The operator shown on the site is Adwise Partners LLC, 228 Park Ave S PMB 849662, New York, New York 10003. Mail is `support@strwbrry.ai`.
- Marketing pages are static HTML generated from `apps/www` (no new framework). The trading SPA stays the Vite app. The app origin sends `X-Robots-Tag: noindex, nofollow` and `robots.txt` disallows `/`.
- The logo source is `brand/logo.svg`. The SPA, the PWA icon, and the marketing site use that drawing.
- Hosted signup and the pricing table use `@clerk/react` only when `VITE_CLERK_PUBLISHABLE_KEY` is set. Otherwise the SPA shows a local mock stored in `sessionStorage`. The mock does not collect a card, does not replace the gateway session, and does not enable live trading.
- The gateway CSP stays `script-src 'self'` unless `TB_CLERK_FRONTEND_API` is one https origin (SE-066). Missing Clerk config does not open a half-configured hosted mode.
- FMP keys are sealed in `config.api_keys` (paper only, write-only, passkey step-up). Alpaca paper keys keep using broker-gateway. The hosted connections screen does not offer IBKR and does not submit a live Alpaca secret.

## Alternatives considered

| Option | Why not |
|---|---|
| Put marketing routes in the Vite SPA | Empty shells for crawlers; D11 stays a static SPA |
| Astro or Next.js for the marketing site | A new framework for a page list a generator already emits |
| Require Clerk keys to build | The Mac Mini path and CI have no Clerk project |
| Store FMP secrets in the browser | The browser must not keep the secret after submit |

## Consequences

- Positive: the public pages, plans, and connection slots exist without turning live trading on.
- Negative / risks: the mock is not a subscription. A real charge needs Clerk Billing, a Stripe account, and counsel on tax. Legal pages are drafts.
- Follow-up work: Go JWT verification for Clerk sessions, per-tenant RLS, and billing webhooks. Not this change. Organizations stay off.

## Validation

`apps/www` tests check the page list, index rules, and banned phrases. Gateway tests check the robots header, the closed CSP, and that an FMP secret is not returned. The web tests check the live slot stays disabled and the mock does not write the account to `localStorage`.
