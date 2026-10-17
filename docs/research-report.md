---
title: Trading Bot – Deep Research Report
status: v1 (answers docs/research-prompt.md; resolves master decisions D1–D27, proposes D28–D31)
updated: 2026-10-17
depends_on:
  - research-prompt.md
  - project-context.md
  - prompts/00-master-prompt.md
---

# Deep Research Report: Self-Hosted ML Day/Swing Trading Platform

This report answers every question and deliverable in the [research prompt](research-prompt.md). It also resolves the 27 pending stack and design decisions in the [master build prompt](prompts/00-master-prompt.md). Research was done on 2026-10-17 with live web sources (papers, broker and vendor docs, benchmarks, practitioner reports; mostly 2024–2026). Every factual claim in the section files carries an inline citation. Items that could not be confirmed from a primary source are marked **[UNVERIFIED]**.

The report is split into ten section files under [`research/`](research/). This page is the index and the executive summary.

---

## Assumptions

### Confirmed by the owner (from [project-context.md](project-context.md))

| Input | Value | Effect on recommendations |
|---|---|---|
| Accounts | **IBKR ≈ $100k**, **Alpaca ≈ $20k**, both margin and both holding manual positions | Routing and virtual-ledger design ([04](research/04-brokers-data-ingestion.md), [05](research/05-virtual-portfolios-and-tax.md)) |
| Data | **Alpaca Market Data API** + **Financial Modeling Prep (FMP)** with an existing key; reliable ingestion required | FMP's role and the ingestion design ([04 §4–6](research/04-brokers-data-ingestion.md)) |
| Host | **Mac Mini (Apple Silicon, no NVIDIA GPU)**, 24/7 under Docker, must survive reboots and blips | Runtime, native ML workers, reliability ([06](research/06-architecture-and-stack.md)) |
| Remote access | Secure web login from anywhere; no open ports; Tailscale and/or Cloudflare Tunnel + Access; app-level passkeys | [08 §1](research/08-security-remote-access-risk.md) |
| Agentic optimization | An AI agent analyzes data and results and drives retraining and optimization autonomously | [03](research/03-optimization-and-ai-agent.md), new decision D31 |

### Still assumed (not yet confirmed)

| Assumption | Where a different answer changes a recommendation |
|---|---|
| US stocks and ETFs first | Options or futures add OPRA/CME data, margin models, and new adapters. Crypto would add 24/7 operations |
| Up to about $200/month extra for data | **$0** means Alpaca Basic, so swing-only on SIP history or IEX on both sides with weak volume features. **About $400** means Massive Advanced or Databento as one clean survivorship-free source ([04 §5](research/04-brokers-data-ingestion.md#5-data-source-plan-with-costs-deliverable-8)) |
| Mobile: monitor, alerts, kill switch | Full mobile order entry would need stronger step-up flows and possibly a native app for reliable push |
| Single user now, multi-user possible later | Multi-user triggers data-licence changes (FMP display agreement, Massive non-pro limits, IBKR pro fees), the Tailscale commercial plan, and an OIDC provider |
| Seconds-level intraday latency | A sub-100 ms requirement would favor Rust/Nautilus and co-located data, and would revisit D1/D7 |
| Mac Mini RAM ≥ 32 GB (unknown) | Below 32 GB, local LLMs become impractical: use API-only plus a tiny local model, and shift more training to cloud GPU |
| The original task assumption "margin account above $25k (PDT not binding)" | **Superseded.** Alpaca is $20k, but **the PDT rule itself was retired on 2026-06-04**, so it no longer binds either account ([04 §1](research/04-brokers-data-ingestion.md#1-pdt-is-gone-account-routing-for-alpaca-20k-and-ibkr-100k)) |

---

## Executive summary (deliverable 1)

**Recommended approach.**
- **Research.** Treat this as a **disciplined research factory, not a model zoo**.
  - Start with **LightGBM** on residualized, volatility-scaled targets, alongside a ridge baseline and a rule plus meta-label challenger. Use **15-minute decision bars** for intraday and **daily bars** for swing.
  - Validate with purged walk-forward and CPCV, realistic costs, and a **deflated Sharpe ratio over a full trial ledger**.
  - Graduate through paper trading.
  - Add sequence models, foundation-model volatility features, and LLM sentiment only after the core pipeline is proven.
- **Operations.** Route **intraday strategies to Alpaca and swing strategies to IBKR**, with IBKR bot capital in a dedicated linked account. The PDT rule was retired on 2026-06-04, so Alpaca's $20k is not a constraint.
- **Infrastructure.** Run everything on the **Mac Mini under Colima/Compose**, with **ML workers native on macOS** (CPU, MPS, MLX) and cloud GPU bursts for heavy training. Use **Tailscale** for private access and optionally **Cloudflare Access** for a public URL and webhooks.
- **AI agent.** It works **alongside Optuna, not instead of it**. It proposes, codes, and explains. It never judges, promotes, trades, or sees the holdout.

**What the evidence says, in one paragraph.**
- **Models.** Tree ensembles with frequent retraining are the most reliable model family on financial tabular data. Zero-shot time-series foundation models have negative R² on US returns, and deep RL fails to beat 1/N.
- **LLMs.** LLM trading agents lose most of their Sharpe after their training cutoff. LLM AutoML agents now reach 61–64% Kaggle medal rates, but they measurably overfit validation and reward-hack visible scorers. Classical TPE and CMA-ES still beat them on fixed hyperparameter spaces.
- **Costs and decay.** Most intraday ML edges are about the size of the spread. Published anomalies lose more than half their returns after publication.

**Top risks.**
1. **Cost-dominated intraday edge.** Mitigated by a tight-spread universe, an explicit open/close spread model, a 2× cost-stress gate, and live implementation-shortfall tracking.
2. **Overfitting by an autonomous search loop.** Mitigated by the D31 policy: trial ledger, DSR, PBO, MinBTL budgets, a sealed holdout service, and human gates.
3. **IBKR fragility.** IBC was archived on 2026-09-01, 2FA is required every week, and OAuth is unavailable to individuals. Mitigated by putting swing strategies on IBKR with GTC stops, pinning versions, a weekly runbook, and VNC fallback.
4. **Home-host outages.** Mitigated by UPS and auto-restart, SSH FileVault unlock via the `tailscaled` daemon, broker-side protective orders, and an external dead-man monitor.
5. **Data quality and look-ahead.** FMP intraday has no delisted symbols, FMP fundamentals are restated in place, and IEX live data paired with SIP history creates train/serve skew. Mitigated by Alpaca SIP for both training and serving, snapshotted fundamentals, nightly cross-source reconciliation, and one-off purchased survivorship-free history.
6. **Trading against the manual book.** Mitigated by virtual ledgers, the IBKR bot account, Alpaca symbol segregation, and a cross-account wash-sale report.

---

## Decisions that differ from the master's defaults

Full table and evidence: [09 · Decisions](research/09-decisions.md).

| ID | Master default | Research recommendation |
|---|---|---|
| D7 | Vectorized + event-driven (vectorbt / NautilusTrader or custom) | **Custom two-tier on `tbcore`**. NautilusTrader has no Alpaca adapter and would duplicate the Go OMS |
| D11 | Next.js | **Static Vite + React 19 SPA** (TanStack, Tailwind, shadcn). Avoids the React Server Components attack surface (CVE-2025-29927, React2Shell) |
| D14 | WebSocket primary, SSE fallback | **SSE primary**, REST commands, WebSocket only when needed |
| D19 | One account per broker + virtual ledgers | Virtual ledgers **plus a dedicated IBKR linked "bot" account** and Alpaca symbol segregation |
| D20 | Alpaca IEX free / SIP optional + IBKR | **Alpaca SIP ($99) required** for intraday; **FMP** for fundamentals, calendars, and universe; **one-off survivorship-free intraday history** |
| D21 | Single DigitalOcean droplet | **Mac Mini + Compose (Colima)**; same Compose file on a droplet later |
| D24 | Triple barrier + meta-labeling | **Volatility-scaled residual forward returns** (regression or rank). Triple barrier and meta-labeling become alternatives |
| D25 | CPU first; optional local GPU Compose profile | **Native macOS ML workers** (MPS/MLX) + **cloud GPU bursts**; no GPU profile |
| Refined | D1 (IBKR adapter in Python), D2 (`sync_interval: always` + outbox), D4 (DuckLake path), D8 (no MinIO), D13 (Pushover), D17 (`tailscaled` + optional Cloudflare Access), D18 (IBC archived; VNC fallback, not CPAPI), D22 (VictoriaLogs + Alloy), D26 (hybrid LLM, masked inputs) | Same choice with mandatory configuration or scope changes |
| New | D28 Colima runtime; D29 native ML workers; D30 PDT off + routing + intraday-margin guard (replaces U1 placeholder); D31 honest-optimization policy | — |

Confirmed unchanged: D3, D5, D6, D9, D10, D12, D15, D16, D23, D27.

---

## Index of sections (deliverable map)

| # | Section | Research prompt § | Deliverables |
|---|---|---|---|
| 01 | [Strategy horizons and model ranking](research/01-strategies-and-models.md) | §1, §2 | 2 (model ranking tables) |
| 02 | [Labeling, features, validation, backtest realism](research/02-labeling-features-validation.md) | §3, §4 | 3 (methodology); engine comparison |
| 03 | [Automated optimization and the autonomous AI research agent](research/03-optimization-and-ai-agent.md) | §5 + agentic requirement | Agent architecture diagram; 12-rule policy; LLM choice |
| 04 | [Brokers, account routing, data sources, reliable ingestion](research/04-brokers-data-ingestion.md) | §6 + PDT, FMP, ingestion | 6 (broker notes), 8 (data plan with costs); ingestion diagram |
| 05 | [Virtual strategy portfolios, attribution, tax](research/05-virtual-portfolios-and-tax.md) | §10, §12 (tax) | 6 (virtual portfolio design); attribution sequence diagram |
| 06 | [Architecture, tech stack, Mac Mini host](research/06-architecture-and-stack.md) | §7 | 4 (stack), 5 (architecture diagrams) |
| 07 | [UI, mobile, real-time transport, TradingView](research/07-ui-and-tradingview.md) | §8, §11 | 7 (TradingView recommendation) |
| 08 | [Security, remote access, risk, live operations](research/08-security-remote-access-risk.md) | §9, §12 | 9 (risk and security checklist); remote-access comparison |
| 09 | [Decisions D1–D27 (+ D28–D31)](research/09-decisions.md) | Master register | Decision resolutions |
| 10 | [Roadmap and where retail ML trading fails](research/10-roadmap-and-failure-modes.md) | — | 10 (roadmap), 11 (failure modes) |

**Mermaid diagrams:**
- Service and data-flow architecture on the Mac host ([06 §8.1](research/06-architecture-and-stack.md#81-services-and-data-flows-on-the-mac-mini-host))
- Train → backtest → optimize → deploy → monitor loop ([06 §8.2](research/06-architecture-and-stack.md#82-research-and-deployment-loop))
- Deployment topology ([06 §8.3](research/06-architecture-and-stack.md#83-deployment-topology-local-first-remote-later))
- Agent loop ([03 §4](research/03-optimization-and-ai-agent.md#4-agent-architecture-and-guardrails))
- Ingestion pipeline ([04 §6.2](research/04-brokers-data-ingestion.md#62-ingestion-flow))
- Order attribution sequence ([05 §3.4](research/05-virtual-portfolios-and-tax.md#34-attribution-flow))

## Follow-ups for the build

1. **Phase 1 verification spikes:**
   - IBKR `orderRef` maximum length and persistence.
   - Whether Alpaca history includes delisted symbols.
   - Whether the owner's FMP key predates 2025-08-31 (legacy vs `/stable` paths) and what the plan tier is.
   - Atomic rename across VirtioFS for the lake.
   - Mac mini RAM.
2. **Owner actions:**
   - Decide whether to open the IBKR linked bot account (D19).
   - Review §475(f) with a tax adviser before the election deadline.
   - Confirm the Alpaca SIP subscription.
3. **Repo doc protocol (N6):** each resolved decision needs its row in `docs/decisions.md` and an ADR in `docs/adr/`. Section prompt 14 (Next.js → SPA, WebSocket → SSE) and the master's U1 placeholder (PDT guard) need updates.
