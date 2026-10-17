---
title: "Section 08 – Backtesting Engine and Validation"
phase: "3"
depends_on: [00, 03, 05, 06, 07]
updated: 2026-10-17
---

# Section 08: Backtesting Engine and Validation

## Role

You are a quantitative researcher and engine builder who has seen hundreds of beautiful backtests die in live trading. Your job is to build a backtester that is pessimistic by default, matches live execution, and makes overfitting visible.

## Context

Research needs two things: **speed** (evaluate thousands of parameter sets and model variants) and **fidelity** (results that match paper and live trading). The default design is two tiers: a vectorized engine for fast screening and an event-driven engine that runs the exact same `tbcore.strategy` code, sizing, risk checks, and ledger math as live trading. On top sits a validation library (walk-forward, purged/embargoed CV, CPCV) and overfitting statistics (deflated Sharpe, PBO). Non-negotiable N2 (no look-ahead) and N5 (reproducibility) are central.

## Scope

In scope:
- Event-driven backtester using the shared strategy API, risk checks, and ledger library.
- Vectorized research engine for fast signal/parameter screening.
- Fill, cost, and slippage models; borrow, halts, corporate actions, delistings.
- Validation splitters: walk-forward (anchored and rolling), purged k-fold with embargo, combinatorial purged CV.
- Metrics and overfitting statistics; tearsheet reports.
- Live-vs-backtest parity harness (replay a paper/live period and compare trades).
- Leakage test suite.
- Batch execution of backtests as orchestrated jobs; result storage.

Out of scope:
- Model training (section 10), search loops (section 11). This section provides the evaluation primitives they call.
- Live order routing (section 07).

## Inputs and dependencies

- Section 03: point-in-time data API, universes, calendar, corporate actions, feature store.
- Section 07: `tbcore.strategy` API, sizing, pre-trade risk checks as importable pure functions.
- Section 06: ledger library (same P&L and lot math) usable in-process.
- Section 05: recorded live availability delays per source (for realistic `available_at`).
- Section 01: orchestrator, MLflow for logging runs.
- `INPUT PENDING (U1)`: PDT and account type apply in simulation too.
- `INPUT PENDING (U5)`: survivorship-free data depends on budget; reports must state the universe's survivorship status.

## Decisions pending

| Decision | Default | Marker |
|---|---|---|
| Event-driven engine | Custom engine in `tbcore.backtest` reusing live components; evaluate NautilusTrader as the alternative if research shows its live/backtest parity and adapters justify adopting it end to end | `DECISION PENDING (research §4)` (D7) |
| Vectorized engine | Polars/NumPy custom (or vectorbt if license and fit are acceptable; vectorbt PRO is paid) | `DECISION PENDING (research §4)` |
| Default fill model | Decision at bar close (`available_at`); fill at next bar open ± slippage; limit orders fill only if price trades through the limit (not touch) with a queue haircut | `DECISION PENDING (research §4)` |
| Default slippage | Half spread + k × volatility × sqrt(participation), with k calibrated from paper fills | `DECISION PENDING (research §4, §9)` |
| Ranking metric | Deflated Sharpe as the gate, with drawdown and turnover-adjusted return constraints | `DECISION PENDING (research §4)` |

## Interfaces and contracts to expose

```python
# tbcore.backtest
run_event_backtest(config: BacktestConfig) -> BacktestResult
run_vectorized(signal_fn, data, cost_model, rules) -> VectorizedResult
class BacktestConfig:  # fully serializable; its hash is the run identity
    strategy: StrategyRef            # code ref + params
    model_version: str | None
    universe: UniverseRef            # name + point-in-time flag
    timeframe: str
    start, end: datetime
    data_snapshot_id: str            # pinned lake watermarks
    cost_model: CostModelConfig
    fill_model: FillModelConfig
    risk_limits: RiskLimitsConfig    # same schema as live
    allocation: Decimal
    account: AccountSimConfig        # cash/margin, PDT, borrow
    seed: int

# tbcore.validation
WalkForward(train_len, test_len, step, anchored, embargo)
PurgedKFold(n_splits, embargo, label_end_times)
CombinatorialPurgedCV(n_groups, n_test_groups, embargo, label_end_times)
deflated_sharpe(sr, n_trials, sr_variance, skew, kurtosis, n_obs)
probability_of_backtest_overfitting(trial_returns_matrix, n_partitions)
min_track_record_length(sr, target_sr, skew, kurtosis, confidence)

# tbcore.parity
compare_live_vs_backtest(strategy_id, start, end) -> ParityReport
```

`BacktestResult` (stored in MinIO + MLflow, summary in Postgres, `BacktestRun` contract): config hash, trades, orders (including rejected intents with risk reasons), daily and per-bar equity, exposures, costs breakdown, metrics, warnings (survivorship, feed, low trade count).

## Functional requirements

### Event-driven engine
- [ ] Simulated clock driven by `available_at` of bars and context events; strategies receive data in the same order and with the same visibility rules as live.
- [ ] Uses the live sizing module, risk check functions, attribution, and ledger library; rejected intents are recorded with reasons.
- [ ] Simulated broker: order types and TIFs matching broker capabilities, partial fills by volume participation cap (default ≤ 5–10% of bar volume), latency between decision and order arrival, session rules (no fills outside allowed sessions), halts (no fills), shorting constraints (borrow availability snapshot, borrow fee), market-on-open/close handling.
- [ ] Corporate actions applied to simulated positions exactly as the live ledger does; delistings close positions at the last available price with a configurable haircut.
- [ ] Commissions and fees per broker schedule (Alpaca commission-free equities but regulatory fees; IBKR tiered/fixed), configurable; verify current schedules.
- [ ] Multi-strategy simulation within one simulated account with virtual ledgers (tests N3 logic offline), including a simulated manual bucket.
- [ ] Deterministic given config and seed.

### Vectorized engine
- [ ] Signal matrix (time × instrument) to positions with next-bar execution, costs, and simple rules (holding period, stops by bar high/low with conservative ordering assumptions).
- [ ] 100–1000× faster than event-driven for screening; documented known differences from event-driven; any candidate promoted from screening must be rerun event-driven.

### Validation
- [ ] Splitters with purging (remove training samples whose label windows overlap the test window) and embargo (gap after test); unit tests with synthetic overlapping labels.
- [ ] Walk-forward producing out-of-sample equity stitched across folds.
- [ ] CPCV producing a distribution of out-of-sample paths.
- [ ] Statistics: Sharpe (annualized correctly per bar frequency), Sortino, Calmar, max drawdown and duration, profit factor, hit rate, average win/loss, turnover, cost drag, exposure, capacity estimate, deflated Sharpe (needs the trial count from section 11), PBO, minimum track record length, bootstrap confidence intervals.
- [ ] Regime breakdown: metrics by VIX regime, trend/chop regime, time of day, day of week, and year.

### Leakage test suite (runs in CI on every strategy/feature change)
- [ ] Future-shift test: shifting all features forward by one bar must destroy performance; shifting them backward (into the future) should not be possible through the API.
- [ ] Shuffled-label test: performance on shuffled labels ≈ zero.
- [ ] Timestamp assertion: every feature value used at decision time t has `available_at <= t` (instrumented in the engine).
- [ ] Random-strategy baseline: distribution of results from random entries with the same turnover, to show the strategy beats noise.

### Parity
- [ ] Replay a paper or live period through the event-driven engine with the same strategy version, data, and model; compare intents, orders, fills, and P&L; produce a `ParityReport` with matched/missed/extra trades and slippage attribution. Feeds section 13 and the slippage calibration.

### Reports and storage
- [ ] Tearsheet: equity and drawdown curves, monthly returns table, trade list on chart (links to UI chart view), cost breakdown, regime table, overfitting statistics, warnings.
- [ ] Every run logged to MLflow with config, data snapshot ID, code commit, and metrics; artifacts in MinIO.

## Non-functional requirements

- **Performance** (reference machine, revise with U4): event-driven, 1 year × 100 symbols × 5m bars, single strategy, under 60 s; vectorized screening of 1,000 parameter sets on the same data under 5 min; parallel execution across cores via the orchestrator.
- **Correctness**: shares code with live; any duplicated logic is a defect.
- **Reproducibility**: identical config hash ⇒ identical results (test).
- **Tests**: golden backtests with hand-calculated expected P&L; splitter tests; statistics tests against published reference values; fill model edge cases (gap through stop, limit touched but not crossed, halt).

## Acceptance criteria

- [ ] Docs updated per `AGENTS.md` (master N6) in the same commits as the changes: `docs/file-map.md`, `docs/business-logic/backtesting.md` (fill, cost, and validation rules), `CHANGELOG.md`, `docs/glossary.md` for new terms, and, for every decision made, an ADR in `docs/adr/` plus its row updated (or added) in `docs/decisions.md`.
- [ ] Parity: event-driven replay of the baseline strategy's paper period (Gate 2) matches trade-for-trade within documented tolerance; P&L difference explained by the slippage model.
- [ ] Leakage suite passes for the baseline strategy and fails for a deliberately leaky fixture strategy.
- [ ] Purged K-fold and CPCV splitters pass synthetic overlap tests.
- [ ] Deflated Sharpe and PBO implementations match reference examples.
- [ ] A tearsheet is generated for the baseline and viewable in the UI (section 14 slice).
- [ ] Performance targets met and recorded.

## Pitfalls to avoid

- Filling at the close of the signal bar, or using the bar's high/low to decide and fill within the same bar.
- Assuming limit orders fill on touch; assuming unlimited liquidity at the open.
- Ignoring costs or using a fixed tiny slippage in intraday tests where costs dominate.
- Survivorship-biased universes; always label reports with the universe's survivorship status.
- Optimizing on the same data you report; out-of-sample means never used for any selection.
- Annualizing Sharpe incorrectly for intraday bars, or computing it on overlapping returns.
- Reporting only the best trial; always report the number of trials and deflated statistics.
- A separate "backtest version" of the strategy that drifts from the live one.
- Ignoring PDT, borrow, and halts, which make many intraday short strategies untradable in practice.
