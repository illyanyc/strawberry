---
title: Trading Bot – Deep Research Prompt
status: draft v1 (open questions at the bottom)
updated: 2026-10-17
---

# Deep Research Prompt: Self-Hosted ML Day/Swing Trading Platform

## Role

You are a senior quant researcher, ML engineer, and systems architect with hands-on experience building retail and small-fund algorithmic trading systems. Be skeptical: favor evidence (papers, benchmarks, practitioner post-mortems, real broker docs) over hype, cite sources, and call out where retail traders typically fool themselves (overfitting, look-ahead bias, unrealistic fills).

## Context

I am a highly technical solo developer building a self-hosted platform to research, train, backtest, deploy, and monitor ML-driven trading strategies for **US equities** (other asset classes: see Inputs). Complexity is fine; correctness, speed, and real edge matter most.

The platform must:

1. **Connect to brokers**: Alpaca and Interactive Brokers (IBKR), for both market data and order execution, using whatever authentication each officially supports.
2. **Ingest and store market data** quickly and locally: historical and streaming bars (1m / 5m / 15m / daily), plus trades/quotes if useful, in a fast analytical store (e.g. DuckDB) with a hot cache (e.g. Redis).
3. **Ingest alternative and context data**: news and sentiment, the economic calendar and macro series, earnings, sector/index context, volatility regime, and anything else research shows adds signal.
4. **Train many model types** from a UI: classical ML (Random Forest, gradient boosting), deep learning (LSTM, TCN, Transformers), newer time-series foundation models, and RL, with parameter selection helped by an AI assistant.
5. **Backtest realistically** and **automatically optimize**: hyperparameters, features, labeling, timeframes, universes, and trade rules, in a loop that retrains until results are robust, not just good in-sample.
6. **Deploy models to paper and then live trading**, with risk controls, and **monitor them live** (P&L, drift, live vs backtest divergence, kill switches).
7. Offer a **data-science and trading UI** that works on desktop and mobile.
8. Track the **whole real portfolio** in each broker account while running each strategy in its own **virtual sub-portfolio**, trading only that strategy's positions and never touching manual holdings.
9. Integrate with **TradingView** where it adds value (charts, alerts, signal overlays).
10. Run **locally first**, later on a remote server (e.g. DigitalOcean), with **strong security and auth** from day one.

## Research questions

### 1. Strategy horizons and timeframes
- For intraday day trading, which bar sizes work best for ML (1m, 5m, 15m, 30m, multi-timeframe stacks)? What does evidence say about signal-to-noise and cost drag at each?
- For overnight swing trading (1–10 day holds), which inputs and horizons work best?
- Which strategy families have the most documented retail-accessible edge: momentum/breakout, mean reversion, opening-range, gap plays, event-driven (earnings/news), pairs/stat-arb, volatility regime?
- How should the system handle regimes (trend vs chop, high vs low VIX)?

### 2. Models: survey and ranking
Survey the models actually used (in papers, Kaggle/competition winners, and practitioner reports) for intraday and swing prediction, and rank them by real-world usefulness versus hype:
- Tree ensembles: Random Forest, XGBoost, LightGBM, CatBoost.
- Sequence models: LSTM, GRU, TCN, CNN-LSTM, attention variants.
- Transformers for time series: Temporal Fusion Transformer, PatchTST, iTransformer, Informer/Autoformer, and others.
- Time-series foundation models: Chronos, TimesFM, Moirai, Lag-Llama, and newer ones, including whether fine-tuning them on financial data helps.
- Reinforcement learning: PPO, SAC, DQN, FinRL-style setups; when RL is worth it versus supervised learning plus rules.
- LLM-based signals: news/sentiment scoring with FinBERT or LLMs, and LLM agents.
- Ensembles, stacking, and meta-labeling.
For each: typical inputs, training cost (CPU vs GPU), inference latency, overfitting risk, interpretability, and best-fit horizon.

### 3. Targets, labeling, and features
- Labeling: fixed-horizon returns vs triple-barrier vs trend-scanning; classification vs regression vs ranking; meta-labeling (López de Prado).
- Feature engineering: technical indicators, microstructure (spread, imbalance, VWAP distance), volume profile, cross-sectional ranks, market/sector context, time-of-day, fractional differentiation.
- Alternative data with real evidence of value: news sentiment, earnings surprises, options-derived signals (IV, skew, put/call), short interest, macro surprises. What is affordable for a solo trader?
- Feature store design: point-in-time correctness and avoiding look-ahead leakage.

### 4. Validation, backtesting, and overfitting control
- Walk-forward analysis, purged and embargoed k-fold, combinatorial purged cross-validation.
- Statistics against overfitting: deflated Sharpe ratio, probability of backtest overfitting, multiple-testing corrections, minimum track record length.
- Realistic simulation: slippage, spread, commissions, partial fills, latency, short borrow, halts, and corporate actions; survivorship-bias-free universes, including delisted symbols.
- Compare backtesting/trading engines: NautilusTrader, LEAN/QuantConnect, vectorbt (and PRO), backtrader, Zipline-reloaded, and custom. Which supports both fast vectorized research and event-driven backtests that match live execution, and which have native Alpaca/IBKR adapters?
- Which metrics should rank strategies: Sharpe, Sortino, Calmar, max drawdown, profit factor, hit rate × payoff, turnover-adjusted returns, capacity?

### 5. Automated optimization loop ("AutoML for alpha")
- Hyperparameter optimization: Optuna, Ray Tune, Bayesian optimization, Hyperband/ASHA, population-based training.
- Searching over features, labels, timeframes, universes, and trade rules without data-snooping (holdout discipline, nested walk-forward).
- Experiment tracking and model registry: MLflow, Weights & Biases, or alternatives that run self-hosted.
- Retraining cadence and triggers: scheduled vs drift-triggered; champion/challenger promotion.
- Design of an AI assistant (an LLM agent with tools) that proposes parameters, features, and experiments, reads results, and explains them. What works, and what are the guardrails?

### 6. Brokers and data sources
- **Alpaca**: auth (API keys vs OAuth), paper vs live, data plans (IEX vs SIP), historical depth, websocket limits, rate limits, news API, order types, fractional/short support, PDT handling.
- **IBKR**: TWS API vs IB Gateway vs Client Portal Web API vs the newer OAuth/Web API. What authentication is realistic for a self-hosted retail app (daily re-auth, 2FA, headless gateway options like IBC)? Market data subscriptions, pacing limits, historical bar limits.
- Other data vendors worth considering for clean historical intraday data and alt data: Polygon, Databento, Tiingo, Norgate, FRED, Benzinga, and others. Costs and quality.
- Broker abstraction layer design so strategies are broker-agnostic.

### 7. System architecture and tech stack
Recommend and justify (with benchmarks where possible):
- **Service split**: a Go (or Rust) core for ingestion, streaming, order routing, and risk vs a Python ML service for training and inference. Is Go the right choice versus Rust or all-Python (asyncio/uvloop)? How should the services talk: gRPC, REST, NATS, Redis Streams, Kafka/Redpanda?
- **Storage**: DuckDB vs QuestDB vs ClickHouse vs TimescaleDB vs ArcticDB vs a Parquet/Iceberg lake, for both tick/bar storage and analytical queries. Redis vs alternatives (Dragonfly, KeyDB) for the hot cache and pub/sub. Concurrency limits of DuckDB with a writer service plus readers.
- **Compute**: CPU vs GPU needs per model family; local GPU vs on-demand cloud GPU for training.
- **Containers and orchestration**: Docker Compose locally; path to a single DigitalOcean droplet, then to k3s/Nomad if needed. Job scheduling for training and backtests (Ray, Prefect, Dagster, Temporal, Celery).
- **Observability**: Prometheus/Grafana, OpenTelemetry, structured logs, trade audit trail.

### 8. UI
- Compare frontend options for a data-science + trading UI with real-time charts, order entry, model training dashboards, and experiment comparison: Next.js (React), Astro with islands, SvelteKit, SolidStart. Also consider Streamlit/Dash/Panel/Gradio for internal research views, and Tauri for a desktop app.
- Charting libraries: TradingView Lightweight Charts, TradingView Advanced Charts (licensing), ECharts, Plotly, uPlot, Perspective (FINOS) for streaming grids.
- Mobile: responsive web/PWA vs React Native/Expo; push notifications for fills, alerts, and kill switches.
- Real-time transport: WebSockets vs SSE.

### 9. Risk management and live operations
- Position sizing (fixed fractional, volatility targeting, fractional Kelly), per-trade and daily loss limits, max exposure, correlation limits.
- Pre-trade risk checks, kill switches, circuit breakers, broker disconnect handling, reconciliation of positions and orders.
- Live monitoring: live vs backtest P&L divergence, feature and prediction drift, model decay detection, automatic demotion.
- Paper-trading graduation criteria before risking real capital.

### 10. Virtual strategy portfolios inside real broker accounts
The connected Alpaca and IBKR accounts also hold long-term positions that are not part of any bot strategy. The system must:
- Track the **full real account** (all positions, cash, buying power) for each broker.
- Maintain **virtual sub-portfolios** per strategy/model, each with its own allocated capital, positions, P&L, and risk limits, carved out of the real account.
- **Only ever trade a strategy's own positions**: a strategy must never sell, close, or net against manual holdings, even when it trades the same ticker the user already owns.
- Research: order tagging and attribution (Alpaca `client_order_id`, IBKR `orderRef`/model codes, IBKR sub-accounts or Alpaca sub-accounts where available), internal ledgers and reconciliation against broker fills, handling of the same symbol held both manually and by a strategy (lot-level tracking, FIFO vs specific-lot tax effects, wash-sale interactions), buying-power sharing and over-allocation guards, and what happens after manual trades or broker-side corporate actions.
- Compare: one account with virtual ledgers vs dedicated sub-accounts per strategy (IBKR linked accounts, separate Alpaca accounts). Recommend one.

### 11. TradingView integration
- Options and trade-offs: embedding TradingView charts (Lightweight Charts open source vs Advanced Charts/Trading Platform library licensing, custom datafeed from our own DB), TradingView webhook alerts as a signal source into our execution engine, overlaying our model signals and fills on charts, and Pine Script prototyping vs our own engine.
- Can TradingView's broker integration coexist with our order routing without conflicts?
- Recommend what to integrate in the MVP vs later.

### 12. Security, auth, and compliance
- Auth for a single user now and multi-user later: passkeys/WebAuthn, TOTP 2FA, OIDC (self-hosted Authentik/Keycloak/Zitadel or a managed provider).
- Secrets management for broker credentials: encryption at rest, Vault/SOPS/age, least privilege, key rotation.
- Network security for remote deployment: Tailscale/WireGuard, zero-trust access, no public broker-facing ports, TLS everywhere, audit logs.
- US regulatory and tax considerations: Pattern Day Trader rule, wash sales, margin rules, record keeping.

## Deliverables

1. **Executive summary** with the recommended approach and the top risks.
2. **Model ranking table** for day trading (5m/15m) and swing trading, with evidence and citations.
3. **Recommended labeling, feature, and validation methodology** specific to these horizons.
4. **Tech stack recommendation** for every layer, with alternatives and the reasoning for each choice, including a clear answer on Go vs Rust vs Python and DuckDB vs alternatives.
5. **Architecture diagram** (Mermaid) showing services, data flows, and the train → backtest → optimize → deploy → monitor loop.
6. **Broker integration notes** for Alpaca and IBKR: auth flows, limits, gotchas, plus the recommended design for virtual strategy portfolios and order attribution.
7. **TradingView integration recommendation** (MVP vs later, licensing).
8. **Data source plan with costs** (free vs paid tiers, monthly estimate).
9. **Risk and security checklist.**
10. **Phased roadmap**: an MVP (one broker, one timeframe, two or three model types, walk-forward backtest, paper trading) through to the full platform, with milestones and rough effort.
11. **"Where retail ML trading fails"**: a list of the common traps and how this design avoids each.

## Constraints and preferences

- Self-hosted, local-first; must later run on a remote Linux server (e.g. DigitalOcean) with no redesign.
- Containerized (Docker Compose to start).
- Performance-critical paths (ingestion, streaming, order routing) should be low-latency; ML in Python.
- Security and auth are first-class, not an afterthought.
- Desktop and mobile UI.
- Prefer open-source and self-hostable components; flag any paid or licensed dependencies.

## Inputs (fill in before running)

- Starting capital and account type (cash vs margin; above or below the $25k PDT threshold):
- Asset classes (US stocks only, or also ETFs, options, futures, crypto):
- Universe (e.g. S&P 500, liquid top 1,000, a watchlist):
- Hardware available locally (CPU, RAM, GPU model):
- Monthly budget for market data and alt data:
- Mobile scope (monitor and alerts only, or full order entry):
- Single user forever, or multi-user/SaaS later:
- Target latency for intraday decisions (seconds is fine vs sub-second needed):
