---
title: Trading Bot – Project context
updated: 2026-10-17
---

# Project context

Stable goals, constraints, and user inputs. These override the `INPUT PENDING (U1–U8)` placeholders in the [master prompt](prompts/00-master-prompt.md) and the Inputs section of the [research prompt](research-prompt.md).

## Code location

- All code lives in [illyanyc/strawberry](https://github.com/illyanyc/strawberry). One branch and PR per build phase, Conventional Commits per section, documentation protocol on every commit.

## Accounts and capital

- **IBKR**: about $100,000. Above the $25k Pattern Day Trader threshold, so day trading is allowed there (if it's a margin account).
- **Alpaca**: about $20,000. **Below the $25k PDT threshold**: in a margin account, day trades are limited to 3 per rolling 5 business days. Options: route intraday strategies to IBKR and use Alpaca for swing/overnight, use an Alpaca cash account (no PDT limit, but settled-funds rules apply), or enforce a PDT counter in the risk engine. The risk engine must track day-trade counts per account either way.
- **Update (research, 2026-10-17)**: FINRA retired the PDT rule on 2026-06-04, and Alpaca removed it the same day, so the $20k Alpaca account isn't day-trade limited.
- **Decided (D30)**: intraday strategies route to Alpaca with no margin; swing strategies route to IBKR, ideally a dedicated linked bot account. The risk engine keeps a margin guard and a configurable PDT guard (off by default). This supersedes the earlier IBKR-intraday routing.
- Both accounts also hold positions outside the bot; strategies trade only their own virtual sub-portfolio positions.

## Data sources

- **Alpaca Market Data API**: bars, trades/quotes, streaming, news.
- **Financial Modeling Prep (FMP)**: https://site.financialmodelingprep.com/developer/docs (fundamentals, earnings, economic calendar, intraday/historical prices, etc.). The user has an existing integration and API key.
- **IBKR**: market data where subscribed; primarily execution.
- **Reliability requirement**: data writes and fetches must be reliable: idempotent upserts, gap detection and backfill, retries with backoff, rate-limit awareness, per-source health status, deduplication, and reconciliation across sources.

## Hosting

- **Primary host: a Mac Mini** (assume Apple Silicon, so no NVIDIA GPU: PyTorch on MPS, optionally MLX; heavy deep-learning training can burst to a cloud GPU later).
- Runs 24/7 under Docker (Docker Desktop, OrbStack, or Colima on macOS); must survive reboots (auto-start, launchd) and power or network blips.
- **Remote access**: the user logs in to the web UI from anywhere (desktop and mobile). Use a secure tunnel, never an open port: Tailscale (private) and/or Cloudflare Tunnel with Cloudflare Access (public URL), plus app-level passkey/2FA auth.
- A future move to a remote server (e.g. DigitalOcean) must not require a redesign.

## AI-driven model improvement

- An AI Research Analyst analyzes training data and results after every run, diagnoses weaknesses, and proposes and launches the next retrain and optimization round until gains plateau.
- Autonomy is configurable: suggest-only, auto-run on paper, or auto-promote within limits. It never sees the sealed holdout, all its trials are counted against overfitting, and live promotion always goes through the go-live gate.

## Remaining assumptions (not yet confirmed by the user)

- Asset classes: US stocks and ETFs first.
- Data budget: Alpaca plus the existing FMP plan, up to about $200/month extra.
- Mobile: monitoring and alerts plus a kill switch; full order entry later.
- Single user now; multi-user possible later.
- Intraday latency: seconds-level is fine.
