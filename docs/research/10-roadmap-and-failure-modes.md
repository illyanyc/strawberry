---
title: Research 10 – Phased roadmap and where retail ML trading fails
covers: deliverables 10 and 11
updated: 2026-10-17
---

# 10 · Phased roadmap and "where retail ML trading fails"

[← Report index](../research-report.md)

## 1. Phased roadmap (deliverable 10)

The roadmap follows the master's phases and gates. Effort is described as **technical scope and risk**, not calendar time. "Size" is a relative measure of implementation and verification work: S, M, L, XL.

| Phase | Scope (research-adjusted) | Milestone / gate evidence | Size | Main risks |
|---|---|---|---|---|
| **0 · Foundation** | Monorepo, contracts (buf), Compose on **Colima with LaunchDaemon autostart** (D28), native launchd worker skeleton (D29), Go gateway with **go-webauthn passkeys + TOTP**, server-side sessions, SOPS + age, envelope-encrypted credential store, hash-chained audit, Grafana + metrics + VictoriaLogs + Alloy, **tailscaled + Serve**, Pushover contact point, SPA shell (Vite/React/shadcn) | Gate 0, plus: Mac reboot drill (power cut → auto-restart → SSH FileVault unlock over Tailscale → stack healthy) | L | macOS headless quirks; VirtioFS rename semantics (spike) |
| **1 · Data and brokers** | Alpaca adapter (Go) + **IBKR adapter (Python ib_async)** + gnzsnz IB Gateway arm64 (pinned); marketdata with token buckets, breakers, idempotent upserts, `updatedBars`, exchange-calendar table, gap detector, Prefect backfills; FMP `/stable` client with snapshotting; lake + manifest; nightly cross-source reconciliation; **Phase 1 spikes:** IBKR `orderRef` length and persistence; Alpaca delisted-symbol coverage; FMP legacy vs stable for the existing key | Gate 1, plus: reconciliation report Alpaca vs FMP within tolerance for the dev universe; weekly IBKR 2FA runbook rehearsed | L | IBC fragility; FMP data quality; Alpaca single-WS-connection constraint |
| **2 · Trading core on paper** | Ledger with virtual sub-portfolios, `manual` bucket, broker-lot shadow; **IBKR bot linked account** (D19); OMS state machine; pre-trade checks (15c3-5 model), **intraday-margin guard** (D30); latched kill switch; dead-man heartbeat; external uptime monitor; rule-based baseline (e.g. ORB on stocks in play) on Alpaca paper | Gate 2 (same-ticker tests, kill-switch tests, disconnect test), plus Alpaca self-cross 403 test and symbol-segregation test | XL | Order-state edge cases; reconciliation after corporate actions |
| **3 · Research core** | `tbcore` features/labels (vol-scaled residual targets, D24), splitters (purged walk-forward, CPCV), cost model (spread incl. open/close regime, sqrt impact, borrow), **custom vectorized + event-driven replay** (D7), DSR/PBO/SPA, leakage test suite, context data (FMP calendars, ALFRED vintages, FINRA SI with publication dates) | Gate 3 (backtest ↔ paper parity on the baseline) | L | Silent look-ahead; cost-model calibration |
| **4 · ML and optimization** | LightGBM (regression/LambdaRank) + ridge + meta-label; MLflow (filesystem/RustFS); Optuna 5 + **trial ledger + holdout service** (D31); champion/challenger shadow; **assistant** (Pydantic AI, MCP tools, sandbox, local + API LLM, masked inputs) | Gate 4, plus: the assistant cannot reach the holdout, scorer, or order path (red-team tests); DSR/PBO reported for every study | XL | Agent-driven overfitting; LLM look-ahead; sandbox escapes |
| **5 · Operations and full UI** | Drift/divergence dashboards, auto-demotion, PWA + declarative Web Push, Pushover emergency, mobile kill switch with passkey step-up; Lightweight Charts overlays; optional Cloudflare Tunnel + Access UI; optional TradingView webhook strategy source | Gate 5 | M–L | Alert fatigue; iOS push reliability |
| **6 · Remote and live** | Security hardening (checklist in [08 §5](08-security-remote-access-risk.md#5-risk-and-security-checklist-deliverable-9)), backups and restore drill, paper graduation, **first live strategy on Alpaca intraday or IBKR swing with a small cap**; optional DigitalOcean move (same Compose) | Gate 6 | M | First-live-day operational errors |

**MVP definition** ("one broker, one timeframe, 2–3 model types, walk-forward, paper"):
- **Alpaca paper only**, on **15-minute decision bars** over a 100–300 symbol tight-spread universe.
- Three models: **ridge baseline, LightGBM, and ORB + meta-label**.
- Purged walk-forward with costs, plus DSR.
- Paper trading through the full OMS, risk, and ledger path.
- The UI shows charts, positions, P&L, the kill switch, and experiment results.

This is Phases 0–3 plus the LightGBM slice of Phase 4. IBKR, the AI assistant, deep models, and TradingView come after the MVP is stable.

**Parallelizable:** the IBKR adapter, once the broker-gateway contract is fixed; context-data ingestion alongside Phase 3; and the UI slices.

## 2. Where retail ML trading fails (deliverable 11)

| # | Trap | Evidence | How this design avoids it |
|---|---|---|---|
| 1 | **Edges smaller than costs** | Intraday ML gross edge is 1–5 bps per trade versus 3–10 bps round-trip costs ([Fischer & Krauss 2018](https://doi.org/10.1016/j.ejor.2017.11.054); [Virtu spreads](https://virtu-www.s3.amazonaws.com/uploads/documents/Virtu_US_MarketStructure_March2023.pdf)) | Tight-spread universe; explicit spread model, including the open/close regime; 2× cost-stress gate; implementation shortfall monitored live |
| 2 | **Look-ahead bias** | Same-bar fills, normalizing with future data, revised fundamentals | `available_at` on every datum; as-of joins; next-bar fills; FMP snapshots keyed on `acceptedDate`; FINRA publication dates; leakage test suite in CI |
| 3 | **Survivorship bias** | Missing delisting returns average about −30% and inflate small-stock returns by up to 5.2 pp/yr ([Shumway 1997](https://doi.org/10.1111/j.1540-6261.1997.tb03818.x)); survivors-only universes inflated Kronos's results 20× | Delisted symbols bought once; point-in-time universes; FMP delisted list; IBKR history never used for backtests |
| 4 | **Data snooping and multiple testing** | Backtest Sharpe explained <2.5% of OOS Sharpe across 888 algorithms ([Wiecki et al. 2016](https://doi.org/10.3905/joi.2016.25.3.069)); about 45 trials make Sharpe 1 from noise on 5 years of data | Trial ledger, DSR over effective N, PBO, MinBTL budgets, pre-registration, sealed holdout (D31) |
| 5 | **Autonomous agents overfitting or cheating** | AIRA's 9–13 point validation-overfit gap; METR reward hacking was 43× more frequent with a visible scorer | Scorer and holdout invisible to the agent; Optuna as the optimizer; human promotion gates; replay logs |
| 6 | **LLM look-ahead contamination** | Agent Sharpe collapses after the model's training cutoff ([Profit Mirage](https://arxiv.org/html/2510.07920)); DatedGPT measured a look-ahead premium | Masked tickers and relative dates in prompts; quarantined local model for news; pre-cutoff LLM features flagged as contaminated |
| 7 | **Leakage through overlapping labels** | Non-IID samples and leaky CV ([López de Prado 2018](https://doi.org/10.3905/jpm.2018.44.6.120)) | Purge and embargo of at least the label horizon; era-wise CV for swing |
| 8 | **Chasing hyped models** | Zero-shot TSFMs have negative R² on returns; Kronos fails independent checks; deep RL loses to 1/N | Ranking table; TSFMs used only as volatility features; RL excluded |
| 9 | **Building on dead anomalies** | PEAD dead in large caps; publication decay of 58% ([McLean & Pontiff 2016](https://doi.org/10.1111/jofi.12365)) | Residualized families with live evidence; continuous OOS monitoring; auto-demotion |
| 10 | **Static models in a drifting market** | Online updates drove most of the Optiver and Jane Street gains | Scheduled and drift-triggered refits; champion/challenger shadowing |
| 11 | **Train/serve skew** | IEX is about 3–4% of volume versus SIP history; separate feature code paths | One `tbcore` feature library; same feed (SIP) for training and serving; nightly online/offline parity check |
| 12 | **Unrealistic fills and shorting** | Ignoring halts, partial fills, borrow | Conservative fill model; ETB/HTB and borrow fees; halt handling; paper-vs-backtest parity gate |
| 13 | **Microcap illusions** | DL alpha concentrates in microcaps ([Avramov et al. 2023](https://doi.org/10.1287/mnsc.2022.4449)) | Report results with and without microcaps; ADV caps; liquid universes |
| 14 | **Operational failures** (runaway orders, stale state) | Knight Capital: a loss of more than $460M in 45 minutes | 15c3-5-style pre-trade checks; latched kill switch; reconcile on reconnect; immutable deploys; paging, not email |
| 15 | **Broker and host fragility** | IBKR weekly 2FA; IBC archived; home-host outages | IBKR on swing with GTC stops; dead-man and external monitors; UPS/auto-restart/FileVault-SSH runbook |
| 16 | **Trading against your own manual book** | Alpaca self-cross 403; FIFO lot relief; cross-account wash sales | Virtual ledgers; symbol segregation; IBKR bot account; wash-sale report |
| 17 | **Overconfidence in base rates** | Fewer than 1% of day traders are persistently profitable ([Barber et al. 2014](https://doi.org/10.1016/j.finmar.2013.05.006)); 97% of persistent Brazilian day traders lost money ([Chague et al. 2019](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3423101)) | Paper graduation, small live caps, MinTRL-based scaling, demotion on drawdown |
