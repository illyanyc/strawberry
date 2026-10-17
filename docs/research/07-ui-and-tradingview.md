---
title: Research 07 – UI, mobile, real-time transport, and TradingView
covers: research prompt §8, §11; deliverable 7
updated: 2026-10-17
---

# 07 · UI, mobile, real-time transport, and TradingView

[← Report index](../research-report.md)

**Bottom line.** This UI is a private dashboard behind a login, so it needs no server-side rendering. Build it as a **static Vite + React 19 SPA** (TanStack Router/Query, Tailwind, shadcn/ui) served by the Go gateway. Chart with **TradingView Lightweight Charts v5**. TradingView's Advanced Charts licence forbids private use. Stream server-to-client updates over **SSE**. Ship mobile as an **installed PWA**, but send critical alerts through **Pushover emergency priority**, not Web Push alone. For TradingView, the MVP uses Lightweight Charts only; webhook alerts come later as an opt-in signal source behind strict ingress controls.

---

## 1. Frontend framework (changes D11)

| Option | Fit | Risk / notes |
|---|---|---|
| **Vite + React 19 SPA + TanStack Router/Query + Tailwind + shadcn/ui** | Matches the user's TS/React/shadcn preference. No SEO or SSR need. One auth surface (the Go gateway) | No server code in the UI tier, so there is no RSC attack surface. Practitioner consensus default for authenticated dashboards **[secondary]** |
| Next.js 16 (App Router, React 19.2, Turbopack default) | Works, and is the master default | **CVE-2025-29927** (2025-03): a header skipped middleware auth on self-hosted `next start` ([advisory](https://github.com/vercel/next.js/security/advisories/GHSA-f82v-jwr5-mffw)). **CVE-2025-55182 "React2Shell"** (CVSS 10, 2025-12): pre-auth RCE via the RSC Flight protocol, exploited in the wild ([React](https://react.dev/blog/2025/12/03/critical-security-vulnerability-in-react-server-components); [Next.js](https://nextjs.org/blog/CVE-2025-66478)). Self-hosting caveats: standalone output, per-instance cache ([docs](https://nextjs.org/docs/app/guides/self-hosting)). Acceptable only as a **static export** |
| SvelteKit / SolidStart / Astro islands | Technically fine | Smaller component ecosystem for shadcn-style trading UIs; Astro is now Cloudflare-owned (MIT) ([Cloudflare](https://blog.cloudflare.com/astro-joins-cloudflare/)) |
| Streamlit / Dash / Panel / Gradio / marimo (research views) | Fast notebooks for the owner | **Never expose them directly.** marimo edit mode had an unauthenticated terminal RCE before 0.23 ([marimo security](https://docs.marimo.io/security/)). Dash OSS has only Basic auth. If used, run behind the gateway's auth proxy, read-only data access. **Not needed for the MVP**; MLflow, Optuna dashboard, and Grafana cover research views |
| Tauri 2 desktop/mobile | Native shell | Remote push plugins are immature (0.1.x) ([crates](https://crates.io/search?q=tauri%20push)). No benefit over a PWA here |

## 2. Charting and grids (confirms D12)

| Use | Library | Facts |
|---|---|---|
| Price charts, signal and fill overlays | **Lightweight Charts 5.2.1** (2026-08-12) | About 35 KB; multi-pane; series-markers and pane-primitive plugins; data conflation since 5.1. **Apache-2.0 with attribution**: the NOTICE file plus a tradingview.com link (`attributionLogo`) ([GitHub](https://github.com/tradingview/lightweight-charts); [release notes](https://tradingview.github.io/lightweight-charts/docs/release-notes)) |
| Dense analytics series (equity curves, drift) | **uPlot** | 48 KB; 166k points rendered in 34 ms; low CPU when streaming ([bench](https://github.com/leeoniya/uPlot/blob/master/bench/table.md)) (older versions benchmarked) |
| Heatmaps, correlation, SHAP, distributions | **ECharts 6** | Progressive rendering, LTTB sampling ([handbook](https://echarts.apache.org/handbook/en/)) |
| Streaming blotters, pivots | **Perspective 4** (`@perspective-dev/*`, now OpenJS) | WASM engine; Python server handlers ([OpenJS](https://openjsf.org/blog/perspective-joins-openjs)) |
| Avoid | TradingView **Advanced Charts / Trading Platform** | "Not provided for personal use, hobbies, studies or testing… only to companies for public web projects"; the agreement forbids private and internal use and public repos ([TradingView](https://www.tradingview.com/free-charting-libraries/); [licence PDF](https://s3.amazonaws.com/tradingview/charting_library_license_agreement.pdf)). AG Grid Enterprise features are paid per developer |

## 3. Real-time transport (changes D14)

- **SSE first.**
  - It reconnects automatically with `Last-Event-ID` replay.
  - It works with cookie auth.
  - Over HTTP/2 the 6-connection limit disappears (100 streams by default) ([MDN](https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events/Using_server-sent_events)).
  - It suits every server-to-client feed here: bars, positions, P&L, orders, alerts, training progress.
- **Commands go over REST** (POST with CSRF protection and step-up where required).
- **WebSocket** is allowed for a specific high-rate bidirectional need. It requires an explicit `Origin` check and heartbeats.
- **WebTransport:** skip it. Safari shipped it only in 2026-03 and usage is about 0.01%.
- **Rule:** one multiplexed SSE stream per tab, and a REST snapshot after every reconnect.

## 4. Mobile: monitoring, alerts, kill switch (confirms D13 with an addition)

| Channel | Role | Facts |
|---|---|---|
| **Installed PWA** (responsive web app) | Dashboard, positions, P&L, alerts inbox, **kill switch** | iOS Web Push only for home-screen-installed web apps (16.4+). **Declarative Web Push** (iOS 18.4+) needs no service worker and avoids silent-push revocation ([WebKit](https://webkit.org/blog/16535/meet-declarative-web-push/)). The EU home-screen web-app removal was reversed in 2024 but shows platform risk ([Verge](https://www.theverge.com/2024/3/1/24087666/apple-iphone-web-apps-eu-reversal)) |
| **Pushover** | **Critical alerts** (risk breach, broker disconnect, reconciliation break, host down, IBKR 2FA needed) | Emergency priority repeats until acknowledged (retry ≥30 s), with receipts and an ack callback; $4.99 one-time per platform ([API](https://pushover.net/api#priority)); native Grafana contact point |
| ntfy / Telegram (optional) | Secondary channel; action buttons | A tailnet-only ntfy server won't deliver promptly to iOS without the ntfy.sh upstream ([ntfy](https://docs.ntfy.sh/known-issues/)). Telegram actions must be **safe-direction only** (halt/flatten), with sender-ID allowlist and secret-token check ([Bot API](https://core.telegram.org/bots/api#setwebhook)) |
| Expo / React Native | Not needed | Push relay has no SLA; critical-alert entitlement rarely granted ([Expo](https://docs.expo.dev/push-notifications/overview/)) |

**Kill-switch UX.**
- The phone can **freeze, cancel open orders, or flatten strategy-owned quantity**, after a fresh passkey assertion with user verification.
- **Re-enabling trading is desktop-only, after step-up.**
- Halting must work even when market-data or ML services are down. It is a direct API path to `execution`.

## 5. TradingView integration (deliverable 7)

| Capability | Options | Recommendation |
|---|---|---|
| **Charts in our UI** | Lightweight Charts (open source) vs Advanced Charts / Trading Platform (licensed, private use forbidden) | **MVP:** Lightweight Charts v5 with our own data from the lake/cache via the gateway, plus overlays for model signals, entries/exits, fills, and stops using series markers and primitives. Never Advanced Charts for a private app |
| **Custom datafeed** | Advanced Charts' JS Datafeed API / UDF | Not applicable (licence). Lightweight Charts takes data arrays and updates directly from our SSE stream |
| **Webhook alerts as a signal source** | TradingView POSTs to ports 80/443 only; 3 s timeout; 4 published source IPs; IPv4 only; 2FA required; Essential plan or higher ($14.95/mo); retries on 5xx; client certificate CN `webhook-server@tradingview.com` can be verified ([webhooks](https://www.tradingview.com/support/solutions/43000529348-how-to-configure-webhook-alerts/); [mTLS](https://www.tradingview.com/support/solutions/43000735201/)) | **Later (Phase 5), opt-in.** Intake on a dedicated Cloudflare Tunnel hostname/path (Access *Service Auth* + IP-range selector, never *Bypass*) or Tailscale Funnel. Then: verify source IP, client cert, and HMAC/shared secret in the body; enqueue and return 200 within 3 s; idempotency key. Webhook signals are **just another strategy**: they produce `OrderIntent`s under a `tv-<name>` strategy ID with their own virtual ledger and risk limits. Never raw orders |
| **Pine Script prototyping** | 9,000-order backtest cap, 5k–40k chart bars, 20–40 s execution limits ([Pine limits](https://www.tradingview.com/pine-script-docs/writing/limitations/)) | Idea screening only. Never use it as validation; port promising ideas to `tbcore` |
| **TradingView broker panel** (Alpaca OAuth; IBKR login) | Orders placed in TradingView arrive at the broker **without our `tb1-` tag** | Coexists safely: untagged orders are attributed to `manual` by design. Risks: (a) on Alpaca, an opposite-side resting manual order in a bot symbol triggers the 403 self-cross guard; (b) a TradingView connection may occupy Alpaca's single market-data WebSocket slot **[UNVERIFIED]**, so use a separate Alpaca key, or avoid the panel on the bot account; (c) manual trades in bot symbols trigger ledger conflicts. With symbol segregation (see [05](05-virtual-portfolios-and-tax.md)) these are contained |

**MVP:** Lightweight Charts with signal and fill overlays. **Later:** webhook intake as a strategy source; optional TradingView chart links (deep links to tradingview.com symbols) for manual analysis.
