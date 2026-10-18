# Backtesting and validation

**Prefix:** `BT-`  ·  **Owners:** `libs/py/tbcore` (`tbcore.backtest`, `tbcore.validation`, `tbcore.parity`, `tbcore.features.lint`), `services/backtest` (scorer, CLI, batch runs), `services/execution/cmd/tb-simcore` (live gate and ledger core in simulation)  ·  **Related:** [ADR-0034](../adr/0034-evaluator-isolation-scorer-and-leakage-suite.md), [ADR-0031](../adr/0031-two-tier-backtester-and-simcore.md), [strategies-and-model-lifecycle.md](strategies-and-model-lifecycle.md) (SM-), [data-and-features.md](data-and-features.md) (DF-), [risk-management.md](risk-management.md) (RM-), [virtual-portfolios-and-attribution.md](virtual-portfolios-and-attribution.md) (VP-), [ADR-0029](../adr/0029-backtest-cost-and-fill-models.md), [ADR-0030](../adr/0030-validation-splitters-and-overfitting-statistics.md), research [02 §3–§4](../research/02-labeling-features-validation.md)

## Scope

How the platform simulates trading (fills, costs, the event-driven and vectorized tiers), how it validates strategies (splitters, purging, embargo), which statistics it reports and gates on (DSR, PBO, SPA), how it proves that backtests match paper trading (parity), and how it detects look-ahead (leakage suite, point-in-time lint). Model training is [section 10](../prompts/10-ml-training.md); search loops, the trial ledger, and the holdout service are section 11; live order routing is [orders-and-execution.md](orders-and-execution.md).

## Concepts

- **Decision time:** a bar's `available_at` (close + feed delay, MD-002). Strategies see only data with `available_at <=` decision time.
- **Fill bar:** the bar series used to simulate executions (`fill_model.fill_timeframe`, default the decision timeframe; 1-minute bars when loaded).
- **Arrival:** decision time + `fill_model.latency_ms`; the first instant an order can work at the simulated broker.
- **Cost stress:** the same run with `cost_model.cost_multiplier = 2` (every cost component doubled).
- **Units / eras:** splitters work on samples or on eras (`groups`, e.g. session dates for swing books); an era is never split.

## Rules

| ID | Rule | Status | Code / tests |
|---|---|---|---|
| BT-001 | A backtest MUST be fully described by a serializable `BacktestConfig`; the SHA-256 of its canonical JSON is the run identity. The same config hash on the same data snapshot and code MUST produce identical results (N5). | Implemented | `tbcore.backtest.config.BacktestConfig.config_hash`, deterministic simcore wall clock (`internal/simcore` `wall`); `test_bt001_deterministic_and_config_hash_is_identity` |
| BT-002 | Backtests MUST run the live strategy code (`tbcore.strategy`) through the live runtime core, dispatching final bars in `available_at` order with the strategy clock at the bar's `available_at` (SM-011, SM-014). | Implemented | `tbcore.strategy.host.Runner.dispatch` used by `tbcore.backtest.event`; `test_bt002_bt003_golden_trade_hand_calculated_without_costs` |
| BT-003 | The event-driven tier MUST evaluate every intent with the live pre-trade gate and book every fill with the ledger's accounting core (no Python re-implementation); rejected intents are kept with every failing check. | Implemented | `services/execution/internal/simcore` (real `oms.Engine` seeded by `app.Seed`, `oms.Ledger` over `services/ledger/book`); `TestBT003*`, `TestBT007*`, `TestBT004StaleBarFailsClosed` |
| BT-004 | The event-driven engine MUST raise if any datum handed to a strategy has `available_at` after the decision time (timestamp assertion, DF-002). | Implemented | `normalize_bars` and the dispatch assertion in `tbcore.backtest.event`; `test_bt004_bars_available_before_close_are_rejected` |
| BT-005 | Every result MUST carry warnings for: universe survivorship status not `complete` (U5/O3), coarse fills (BT-020), fewer than `min_trades` (default 30) trades, and impact computed with the fallback (BT-012). | Implemented | `EventEngine` warnings, `BacktestRun.warnings`/`survivorship_status`; `test_bt005_warnings_label_survivorship_and_coarse_fills` |
| BT-006 | Vectorized results are screening only: a candidate from the vectorized tier MUST be rerun event-driven before any gate or promotion. | Implemented | `VectorizedResult.known_differences`, `BacktestRun.engine`; `test_bt006_vectorized_orb_matches_the_event_driven_orb` |
| BT-007 | Several strategies MAY share one simulated account with virtual ledgers and a simulated `manual` bucket; strategy sells never take the account below other owners' quantity (VP-003 through the gate and ledger core). | Implemented | simcore (live gate + ledger core), `stats.manual_positions`; `test_bt007_orb_sessions_flat_protected_and_manual_untouched`, `test_bt007_two_strategies_share_one_account`, `TestBT007*` |
| BT-008 | Corporate actions MUST be applied to simulated positions by the ledger core on the ex-date (VP-010, VP-023); a delisting closes positions at the last available price × (1 − `fill_model.delisting_haircut`, default 0.0). | Implemented for splits and dividends (ledger core on the ex-date open); delistings Specified | `SimCorporateAction`; `test_bt008_split_applied_by_the_ledger_core` |
| BT-009 | A strategy with an event blackout (`StrategySpec.blackout`, registry `runtime.blackout`) MUST be wrapped in the same `BlackoutStrategy` as paper/live (CX-016) and receive the window's scheduled events point-in-time (supplied `scheduled_events` and/or a `ContextStore`; each version applies from its `available_at`). A blackout with no events MUST warn `blackout_without_events`; `stats.blackout_blocked` counts blocked intents. | Implemented | `run_event_backtest(scheduled_events=..., context_store=...)`; `test_cx016_event_blackout_blocks_entries_in_backtests` |
| BT-010 | Every simulated fill MUST pay a modeled price concession (half-spread + impact) and fees; zero-cost runs exist only as an explicit `broker="none"` diagnostic and are labeled. | Implemented | `tbcore.backtest.costs`; `test_costs.py` |
| BT-011 | The half-spread MUST use a separate, wider regime for the first and last `open_close_minutes` (default 30) of the regular session: defaults 1.5 bps regular, 5.0 bps open/close per side; with `spread_estimator="edge"` the regular value comes from the EDGE OHLC estimator on trailing bars (floored at `min_half_spread_bps`, default 0.5), and the open/close regime keeps the larger value. | Implemented | `half_spread_bps`, `edge_half_spread_bps`; `test_bt011_*` |
| BT-012 | Impact MUST be `impact_k × σ_daily × sqrt(qty / ADV)` (default `impact_k` 0.1) with σ and ADV from data available at the decision; if either is unknown the impact is `fallback_impact_bps` (default 10 bps) and the result is warned. | Implemented | `impact_bps`; `test_bt012_*` |
| BT-013 | Fees MUST follow the broker schedule and the **dated** regulatory schedule: Alpaca $0 commission + SEC Section 31 on sales + FINRA TAF on shares sold (capped per trade) + CAT on all shares; IBKR fixed $0.005/share (min $1.00, max 1 % of value) + regulatory; IBKR tiered $0.0035/share (min $0.35, max 1 %) + exchange $0.0030/share + clearing $0.0002/share (max 0.5 %) + pass-through 0.0735 % of commission + regulatory. Each fee component is rounded up to the cent per fill. | Implemented | `fees`, `DEFAULT_REGULATORY_FEES`; `test_bt013_*` |
| BT-014 | Short positions MUST accrue borrow at the instrument's snapshot rate (default `borrow_fee_bps_annual` 30) per calendar day held on an ACT/360 basis. | Implemented (cost function); engine accrual Specified | `borrow_cost`; `test_bt015_*` |
| BT-015 | Every report MUST show results at 1× and 2× costs (`cost_multiplier` scales spread, impact, fees, and borrow). | Implemented | `CostModelConfig.stressed`, `report.cost_stress`, tearsheet section; `test_bt015_*` |
| BT-016 | Simulated prices and fees are computed in float64 by the one shared cost model and MUST enter the ledger as decimals: prices rounded half-up to $0.0001, fees up to the cent (OE-007). | Implemented | `to_price`, `to_fee`; `test_bt016_*` |
| BT-020 | An order MUST fill no earlier than the fill bar that contains or follows its arrival, at that bar's open plus slippage; never in the signal bar. When fill bars are coarser than the finality delay plus latency this is the "next bar open" convention and the result carries the `coarse_fills` warning. | Implemented | `tbcore.backtest.fills.eligible`, `EventEngine`; `test_bt020_*` |
| BT-021 | A marketable limit fills at open ± slippage when that price is within its limit; otherwise it rests and fills only when the bar trades **through** the limit by at least `limit_through_ticks` (default 1 tick) at the limit, for at most `limit_queue_haircut` (default 0.5) of the participation-capped volume. | Implemented | `fills._match_one`; `test_bt021_*` |
| BT-022 | A stop triggers when the bar trades at or through it; a gap through the stop fills at the open (the worse price) minus slippage. When a stop and a target both trigger in one bar, the stop fills first unless the open is already beyond the target. | Implemented | `fills._priority`, `_match_one`; `test_bt022_*` |
| BT-023 | Fills per instrument per fill bar MUST NOT exceed `participation_cap` (default 0.05) × bar volume; the remainder keeps working until filled or expired (DAY orders expire at the session close). | Implemented | `fills.match_bar`; `test_bt023_participation_cap_partial_fill` |
| BT-024 | There MUST be no fills in halted intervals, in bars with zero volume, or outside the regular session unless the order allows extended hours. | Implemented | `fills.match_bar`, `AccountSimConfig.halts`; `test_bt024_*` |
| BT-025 | The vectorized tier executes a signal from bar t at the open of the next fill bar with the same cost model; stops use bar high/low with the stop-first ordering of BT-022. Its documented differences from the event-driven tier (no gate, no ledger, no participation cap, no partial fills) are listed in the result. | Implemented | `tbcore.backtest.vectorized.run_vectorized`, `tbcore.strategy.orb_vector`; `test_bt025_*` |
| BT-026 | For event-driven runs of daily-bar (swing) model and rule strategies, each daily bar MUST become available at the next session's open + 60 s (`next_open_decisions`): decisions made on a session's bar are submitted at the next open, when the gate accepts orders (RM-003 market session). Later than the bar's own finality, so never look-ahead; the last session is dropped. The live runtime equivalent (hold daily decisions to the next open) is Planned. | Implemented | `tbcore/backtest/model_eval.py` (`test_bt026_daily_decisions_execute_at_the_next_open`) |
| BT-027 | The vectorized signal evaluator (`tbcore.backtest.signals`) MUST enter a decision at the open of the bar after the decision bar, exit at the open of bar `t+1+horizon` (intraday: the session's last close if the horizon crosses it; swing: across sessions), hold one trade per instrument, split the allocation equally among trades decided at the same bar (at most `1/min_names` each), and apply the shared cost model; it does not simulate protective stops (screening only, BT-006). | Implemented | `tbcore/backtest/signals.py` (`test_ml012_signal_evaluator_enters_at_the_next_bar_open`) |
| BT-028 | Model decisions (out-of-fold or holdout tables) MUST be rerun event-driven through the live `model_signal` / `meta_filter` strategies and `tb-simcore` at 1× and 2× costs before any gate (`tbcore.backtest.model_eval`, EX-051). | Implemented | `tbcore/backtest/model_eval.py` (`test_sm011_model_signal_runs_event_driven_with_protective_stops`, `test_ml016_meta_filter_drops_entries_the_filter_does_not_take`) |
| BT-030 | Every sample MUST carry a decision time and a label end time (`t1 >= t0`); splitters reject unsorted input and inverted windows; eras (`groups`) are contiguous and never split. | Implemented | `tbcore.validation.splitters`; `test_bt030_*` |
| BT-031 | Training sets MUST exclude every sample whose label window overlaps a test block's span `[min t0, max t1]` (purging). | Implemented | `purge`; `test_bt031_bt033_*`, `test_bt033_*` |
| BT-032 | Training sets MUST exclude samples whose decision time falls within `embargo` after a test block (embargo ≥ label horizon by convention); walk-forward uses it as a gap before the test block. | Implemented | `purge`, `WalkForward`; `test_bt034_*` |
| BT-033 | Purged k-fold and CPCV are the selection splitters (inner loop): CPCV with `n_groups`/`n_test_groups` yields `C(N, k)` splits and `C(N−1, k−1)` full out-of-sample paths. Random k-fold is not provided (SM-002). | Implemented | `PurgedKFold`, `CombinatorialPurgedCV`; `test_bt033_*` |
| BT-034 | Walk-forward (rolling or anchored) is the realism splitter (outer loop); non-overlapping test windows stitch into one out-of-sample series, overlapping ones are refused. | Implemented | `WalkForward`, `stitch_walk_forward`; `test_bt034_*` |
| BT-040 | PSR and Sharpe use per-period returns with skewness and kurtosis (normal = 3); annualization multiplies by sqrt(periods per year) only for reporting. | Implemented | `probabilistic_sharpe`, `sharpe`; `test_bt040_*` |
| BT-041 | DSR MUST deflate against the expected maximum Sharpe of the **effective** number of trials using the cross-trial Sharpe variance from the full trial ledger (D31 rule 2). | Implemented | `deflated_sharpe`, `expected_max_sharpe`; `test_bt041_*` (published example: E[max SR] 0.1132, DSR 0.9004) |
| BT-042 | MinTRL is the number of observations at which PSR(target) reaches the confidence (default 0.95). | Implemented | `min_track_record_length`; `test_bt042_*` |
| BT-043 | MinBTL (years) bounds the trials a research cycle may spend for a target Sharpe (D31 rule 3). | Implemented | `min_backtest_length`; `test_bt042_bt043_*` (45 trials ⇒ 5 years at SR 1) |
| BT-044 | The effective number of trials is the number of ONC clusters of the trial-return correlation matrix; each cluster contributes one inverse-variance-weighted series to the cross-trial Sharpe variance. | Implemented | `effective_trials`, `onc_clusters`; `test_bt044_*` |
| BT-045 | PBO MUST be computed by CSCV with S = 16 partitions by default (12,870 combinations) and reported per study. | Implemented | `pbo_cscv`; `test_bt045_*` |
| BT-046 | Challenger-vs-champion superiority uses Hansen's SPA (consistent p-value, stationary bootstrap, default mean block 10, 2,000 resamples). | Implemented | `spa_test`; `test_bt046_*` |
| BT-047 | Multiple challengers use Romano–Wolf stepdown adjusted p-values; confidence intervals use the stationary bootstrap. | Implemented | `romano_wolf`, `bootstrap_ci`; `test_bt047_*` |
| BT-050 | Sharpe, Sortino, and Calmar in reports MUST be computed on non-overlapping session (daily) returns and annualized with sqrt(252) / 252, never on overlapping or raw intraday returns. | Implemented | `tbcore.backtest.report.session_returns`, `metrics`; `test_bt050_ratios_use_session_returns` |
| BT-051 | Reports MUST include: DSR (effective N, trials counted), PBO, SPA or Romano–Wolf when a champion exists, Sharpe, Sortino, Calmar, max drawdown and duration, turnover, cost drag, exposure, capacity, hit rate, average win/loss, profit factor, bootstrap CIs, regime breakdown (time of day, weekday, year, supplied regime labels), per-date IC for swing models, and results with and without microcaps. | Implemented (statistics, regimes, microcap split, per-date IC, tearsheet; SPA/RW and trial-ledger DSR are passed in by the caller, section 11) | `tbcore.backtest.report`, `tbcore.backtest.tearsheet`; `test_bt051_*`, `test_tearsheet_renders_without_scripts` |
| BT-060 | Future-shift test: decisions up to time T MUST be identical when the data after T is replaced; any change fails the strategy (look-ahead). | Implemented | `tbcore.backtest.leakage.future_shift_test` (with the strategy's `prepare` step over each run's bars); `test_bt065_*` |
| BT-061 | Random-walk test: on martingale prices (default 20 instruments × 60 sessions of 15m bars, 0.8 % per-bar volatility) a strategy MUST NOT earn a significant gross profit (t-statistic of mean trade P&L > 3 fails). Fewer than 30 trades is inconclusive and MUST fail, never pass. The diagnostic lifts only the consecutive-loss halt (`max_consecutive_losses`), which would otherwise end the sample on a martingale's loss streaks; every other gate check stays live. | Implemented | `random_walk_test` on `tbcore.backtest.synthetic.random_walk_bars`; `test_bt065_*`, `test_bt061_random_walk_with_too_few_trades_is_inconclusive_and_fails` |
| BT-062 | Shuffled-label test: a pipeline evaluated on shuffled labels MUST score ≈ 0 out of sample (|IC| ≤ 0.05 by default). | Implemented | `shuffled_label_test`; `test_bt062_shuffled_labels_score_zero_unless_the_pipeline_leaks` |
| BT-063 | Every report MUST show the strategy's percentile in a random-entry baseline with the same number of trades and holding periods. | Implemented (function; wiring into the report is the caller's) | `random_entry_baseline`; `test_bt063_random_entry_baseline_percentile` |
| BT-064 | Feature and generated feature code MUST pass the point-in-time lint (no negative shifts, centered windows, whole-history aggregates, backward fills, forward as-of joins, storage or clock access, `event_time` joins). | Implemented | `tbcore.features.lint` (PIT001..PIT008, `# pit: ok <reason>` exemptions), `make lint-py`; `test_bt064_*` |
| BT-065 | The leakage suite (BT-060..BT-064) MUST run in CI for every strategy and feature change and MUST fail a deliberately leaky fixture. | Implemented | `run_leakage_suite` in CI via `test_leakage.py` and `make lint-py`; the leaky fixture `tests/leaky.py` fails future shift and random walk, the ORB baseline passes |
| BT-070 | Replaying a recorded paper or live bus stream with the recorded fills MUST reproduce the same intents (IDs, quantities, prices), the same decisions, and realized P&L within $0.01 in total. | Implemented | `tbcore.parity.compare_recording` (recorded mode), `EventEngine` recorded fills and recorded bars through `Runner.on_bar`; `test_bt070_*`, `make parity-gate` |
| BT-071 | Replaying with modeled fills MUST match trade-for-trade (same intent IDs and kinds); the P&L difference is attributed to price slippage, fees, and size, with an unexplained residual ≤ $0.01 per trade. | Implemented | `tbcore.parity.compare_recording` (model mode); `test_bt071_*`, `make parity-gate` |
| BT-080 | The scorer verdict (D31 rule 7) passes only if DSR ≥ 0.95, PBO ≤ 0.2, SPA (or Romano–Wolf) p ≤ 0.05 against the champion when one exists, net P&L > 0 at 2× costs, and at least `min_trades` trades; the analyst sees only pass/fail and DSR. | Implemented (scorer; trial-ledger feed is section 11) | `services/backtest/src/tb_backtest/scorer/gate.py`; `test_bt080_*` |
| BT-081 | Evaluator code (backtester, cost and fill models, splitters, statistics, scorer) MUST be listed in the evaluator manifest; assistant and analyst code MUST NOT import it, and the scorer lives outside `tbcore` (D31 rule 9). | Implemented (import ban, manifest; sandbox mounts Planned in section 12) | `services/backtest/evaluator_manifest.json`; `test_bt081_*` |
| BT-082 | Analyst candidate features MUST be checked on the evaluator side before review: synthetic inputs only (no lake, no real prices), the point-in-time lint on the source, bit-identical outputs across two runs, and every prefix run equal to the full run on that prefix; the verdict carries only check names, pass/fail, and rule IDs or short messages (AN-071, AN-072). | Implemented | `services/backtest/src/tb_backtest/candidates.py`; `tests/python/test_analyst_sandbox.py` |

## Gate 3 parity evidence

The Phase 2 paper gate (five ORB sessions through the real runtime, OMS, simulator, and ledger) recorded 2,717 bus messages. Replaying them (`make parity-gate`, `test_parity.py`):

| Mode | Intents | Realized P&L (paper / backtest) | Attribution |
|---|---|---|---|
| Recorded fills (BT-070) | 25 / 25 matched (19 entries, 6 strategy exits), decisions identical, quantities identical | 1,300.20 / 1,300.20 | difference 0 |
| Modeled fills (BT-071) | 25 / 25 matched, every leg matched by intent and role | 1,300.20 / 1,041.49 | 258.71 = price 250.63 + size 5.04 + fees 3.04; unexplained 0.00 |

The modeled run is more pessimistic than the paper simulator, which fills at the signal bar's close (the price when the order arrives in its world) and fills take-profits at the crossing price; the backtest fills at the next bar's open plus slippage and take-profits at the limit (BT-020, BT-021). A changed strategy version (target 3R instead of 2R) fails parity.

## Performance targets (measured)

Reference: the 4-vCPU Linux VM that runs CI-class work (the Mac Mini, U4, is expected to be faster single-threaded). `make perf-backtest` reruns both.

| Target (section 08) | Measured | Test |
|---|---|---|
| Event-driven, 1 year × 100 symbols × 5-minute bars, one strategy, < 60 s | 1,954,800 bars, ORB, ~40,000 simcore requests, 7,134 trades in 32.9 s | `test_bt003_perf_event_driven_one_year_100_symbols_5m_under_60s` |
| Vectorized, 1,000 parameter sets on the same data, < 5 min | 151.7–157.8 s on one core (≈ 0.15 s per set, ≈ 200× the event tier) | `test_bt006_perf_vectorized_1000_parameter_sets_under_5_minutes` |

## Invariants

| ID | Invariant | Checked by |
|---|---|---|
| BT-I1 | No training sample of any split overlaps its test blocks' label spans or embargo windows. | `_assert_purged` in `test_validation.py` (k-fold, CPCV, era-wise) |
| BT-I2 | Each CPCV `(group, split)` test result belongs to exactly one path, and every path covers every group once. | `test_bt033_cpcv_splits_and_paths` |
| BT-I3 | Fees and slippage are non-negative and scale linearly with `cost_multiplier`. | `test_bt015_*` |
| BT-I4 | At every session close the backtester's mark-to-market equity of each strategy equals the ledger core's owner equity within $0.01; a difference is a `valuation_mismatch` warning. | `EventEngine._record_daily`; `test_bt007_orb_sessions_flat_protected_and_manual_untouched` |
| BT-I5 | The simcore never consults the host clock: the OMS's wall clock is the virtual clock plus one microsecond per read, and finished OMS history older than ten days is compacted at session closes (the gate looks back at most five sessions). | `internal/simcore` (`wall`, `Compact`); `TestCompactKeepsWorkingAndRecentState`, three identical fingerprints in the determinism check |

## Edge cases

| Scenario | Expected behavior | Rules |
|---|---|---|
| Trade at 09:45 ET with a 30-minute open regime | Open/close half-spread applies (minute 15 < 30) | BT-011 |
| ADV unknown (new listing) | Impact = fallback 10 bps, result warned | BT-012, BT-005 |
| Sale during the TAF holiday (2026-10-01..2026-12-31) | SEC + CAT only | BT-013 |
| Single noise dataset shows PBO 0.2 | Expected: CSCV PBO is dispersed per dataset; it averages 0.5 over noise datasets | BT-045 |
| Exit intent at 15:46 on 15m bars | The exit fills in the 15:45–16:00 bar, which is matched before the 16:00 session close expires DAY orders | BT-020, BT-023 |
| A partial override of the global limits (e.g. only `max_positions`) | Other fields keep the OMS defaults (merged, not replaced) | BT-003 |
| Strategy flips long to short in one fill | Not modeled as two trades: the trade list records flat-to-flat episodes only (ORB is long only) | BT-051 |
| A recorded bar carries `406`, the lake float `406.0` | A replay dispatches the recorded protobuf bar through `Runner.on_bar`, unchanged; parity compares numbers as decimals | BT-070 |
| A regime label decided after a session's open | Never applied to that session's trades: labels join on the entry session as known at its open | BT-051, DF-002 |
| A strategy peeks through a closure over data it was not given | Input perturbation (future shift) cannot see it; the random-walk check catches it (significant gross profit on martingale prices) | BT-060, BT-061 |
| A frame feature aggregates per session (`group_by(...).agg(first())`) | Not flagged by PIT003 (frame features group explicitly); reviewed with the feature's parity and future-shift tests (DF-012) | BT-064 |
| Strategy never trades (flat equity) | Sharpe and its bootstrap interval are undefined (NaN), not an error | BT-047, BT-050 |
| Walk-forward with `step < test_len` | Splits are produced; stitching refuses overlapping windows | BT-034 |
| Era-wise splits with eras of unequal size | Folds are built over eras, never splitting one | BT-030 |

## Open questions

- Regulatory fee rows before 2024-05-22 are approximations (SEC FY2022–FY2023 from memory, TAF fixed at the 2024 value); refine if pre-2024 intraday history is purchased (O3).
- Calibrating `impact_k` and the spread regimes from paper fills waits on real Alpaca paper trading (Gate 2 owner action); the parity report's slippage attribution is the input.
