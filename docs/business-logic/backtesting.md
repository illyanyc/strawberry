# Backtesting and validation

**Prefix:** `BT-`  ·  **Owners:** `libs/py/tbcore` (`tbcore.backtest`, `tbcore.validation`, `tbcore.parity`, `tbcore.features.lint`), `services/backtest` (scorer, CLI, batch runs), `services/execution/cmd/tb-simcore` (live gate and ledger core in simulation)  ·  **Related:** [strategies-and-model-lifecycle.md](strategies-and-model-lifecycle.md) (SM-), [data-and-features.md](data-and-features.md) (DF-), [risk-management.md](risk-management.md) (RM-), [virtual-portfolios-and-attribution.md](virtual-portfolios-and-attribution.md) (VP-), [ADR-0029](../adr/0029-backtest-cost-and-fill-models.md), [ADR-0030](../adr/0030-validation-splitters-and-overfitting-statistics.md), research [02 §3–§4](../research/02-labeling-features-validation.md)

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
| BT-001 | A backtest MUST be fully described by a serializable `BacktestConfig`; the SHA-256 of its canonical JSON is the run identity. The same config hash on the same data snapshot and code MUST produce identical results (N5). | Specified | — |
| BT-002 | Backtests MUST run the live strategy code (`tbcore.strategy`) through the live runtime core, dispatching final bars in `available_at` order with the strategy clock at the bar's `available_at` (SM-011, SM-014). | Specified | — |
| BT-003 | The event-driven tier MUST evaluate every intent with the live pre-trade gate and book every fill with the ledger's accounting core (no Python re-implementation); rejected intents are kept with every check. | Specified | — |
| BT-004 | The event-driven engine MUST raise if any datum handed to a strategy has `available_at` after the decision time (timestamp assertion, DF-002). | Specified | — |
| BT-005 | Every result MUST carry warnings for: universe survivorship status not `complete` (U5/O3), coarse fills (BT-020), fewer than `min_trades` (default 30) trades, and impact computed with the fallback (BT-012). | Specified | — |
| BT-006 | Vectorized results are screening only: a candidate from the vectorized tier MUST be rerun event-driven before any gate or promotion. | Specified | — |
| BT-007 | Several strategies MAY share one simulated account with virtual ledgers and a simulated `manual` bucket; strategy sells never take the account below other owners' quantity (VP-003 through the gate and ledger core). | Specified | — |
| BT-008 | Corporate actions MUST be applied to simulated positions by the ledger core on the ex-date (VP-010, VP-023); a delisting closes positions at the last available price × (1 − `fill_model.delisting_haircut`, default 0.0). | Specified | — |
| BT-010 | Every simulated fill MUST pay a modeled price concession (half-spread + impact) and fees; zero-cost runs exist only as an explicit `broker="none"` diagnostic and are labeled. | Implemented | `tbcore.backtest.costs`; `test_costs.py` |
| BT-011 | The half-spread MUST use a separate, wider regime for the first and last `open_close_minutes` (default 30) of the regular session: defaults 1.5 bps regular, 5.0 bps open/close per side; with `spread_estimator="edge"` the regular value comes from the EDGE OHLC estimator on trailing bars (floored at `min_half_spread_bps`, default 0.5), and the open/close regime keeps the larger value. | Implemented | `half_spread_bps`, `edge_half_spread_bps`; `test_bt011_*` |
| BT-012 | Impact MUST be `impact_k × σ_daily × sqrt(qty / ADV)` (default `impact_k` 0.1) with σ and ADV from data available at the decision; if either is unknown the impact is `fallback_impact_bps` (default 10 bps) and the result is warned. | Implemented | `impact_bps`; `test_bt012_*` |
| BT-013 | Fees MUST follow the broker schedule and the **dated** regulatory schedule: Alpaca $0 commission + SEC Section 31 on sales + FINRA TAF on shares sold (capped per trade) + CAT on all shares; IBKR fixed $0.005/share (min $1.00, max 1 % of value) + regulatory; IBKR tiered $0.0035/share (min $0.35, max 1 %) + exchange $0.0030/share + clearing $0.0002/share (max 0.5 %) + pass-through 0.0735 % of commission + regulatory. Each fee component is rounded up to the cent per fill. | Implemented | `fees`, `DEFAULT_REGULATORY_FEES`; `test_bt013_*` |
| BT-014 | Short positions MUST accrue borrow at the instrument's snapshot rate (default `borrow_fee_bps_annual` 30) per calendar day held on an ACT/360 basis. | Implemented (cost function); engine accrual Specified | `borrow_cost`; `test_bt015_*` |
| BT-015 | Every report MUST show results at 1× and 2× costs (`cost_multiplier` scales spread, impact, fees, and borrow). | Implemented (model); reports Specified | `CostModelConfig.stressed`; `test_bt015_*` |
| BT-016 | Simulated prices and fees are computed in float64 by the one shared cost model and MUST enter the ledger as decimals: prices rounded half-up to $0.0001, fees up to the cent (OE-007). | Implemented | `to_price`, `to_fee`; `test_bt016_*` |
| BT-020 | An order MUST fill no earlier than the fill bar that contains or follows its arrival, at that bar's open plus slippage; never in the signal bar. When fill bars are coarser than the finality delay plus latency this is the "next bar open" convention and the result carries the `coarse_fills` warning. | Specified | — |
| BT-021 | A marketable limit fills at open ± slippage when that price is within its limit; otherwise it rests and fills only when the bar trades **through** the limit by at least `limit_through_ticks` (default 1 tick) at the limit, for at most `limit_queue_haircut` (default 0.5) of the participation-capped volume. | Specified | — |
| BT-022 | A stop triggers when the bar trades at or through it; a gap through the stop fills at the open (the worse price) minus slippage. When a stop and a target both trigger in one bar, the stop fills first unless the open is already beyond the target. | Specified | — |
| BT-023 | Fills per instrument per fill bar MUST NOT exceed `participation_cap` (default 0.05) × bar volume; the remainder keeps working until filled or expired (DAY orders expire at the session close). | Specified | — |
| BT-024 | There MUST be no fills in halted intervals, in bars with zero volume, or outside the regular session unless the order allows extended hours. | Specified | — |
| BT-025 | The vectorized tier executes a signal from bar t at the open of the next fill bar with the same cost model; stops use bar high/low with the stop-first ordering of BT-022. Its documented differences from the event-driven tier (no gate, no ledger, no participation cap, no partial fills) are listed in the result. | Specified | — |
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
| BT-050 | Sharpe, Sortino, and Calmar in reports MUST be computed on non-overlapping session (daily) returns and annualized with sqrt(252) / 252, never on overlapping or raw intraday returns. | Specified | — |
| BT-051 | Reports MUST include: DSR (effective N, trials counted), PBO, SPA or Romano–Wolf when a champion exists, Sharpe, Sortino, Calmar, max drawdown and duration, turnover, cost drag, exposure, capacity, hit rate, average win/loss, profit factor, bootstrap CIs, regime breakdown (time of day, weekday, year, supplied regime labels), per-date IC for swing models, and results with and without microcaps. | Specified | — |
| BT-060 | Future-shift test: decisions up to time T MUST be identical when the data after T is replaced; any change fails the strategy (look-ahead). | Specified | — |
| BT-061 | Random-walk test: on martingale prices a strategy MUST NOT earn a significant gross profit (t-statistic of mean trade return > 3 fails). | Specified | — |
| BT-062 | Shuffled-label test: a pipeline evaluated on shuffled labels MUST score ≈ 0 out of sample (|IC| ≤ 0.05 by default). | Specified | — |
| BT-063 | Every report MUST show the strategy's percentile in a random-entry baseline with the same number of trades and holding periods. | Specified | — |
| BT-064 | Feature and generated feature code MUST pass the point-in-time lint (no negative shifts, centered windows, whole-history aggregates, backward fills, forward as-of joins, storage or clock access, `event_time` joins). | Specified | — |
| BT-065 | The leakage suite (BT-060..BT-064) MUST run in CI for every strategy and feature change and MUST fail a deliberately leaky fixture. | Specified | — |
| BT-070 | Replaying a recorded paper or live bus stream with the recorded fills MUST reproduce the same intents (IDs, quantities, prices), the same decisions, and realized P&L within $0.01 in total. | Specified | — |
| BT-071 | Replaying with modeled fills MUST match trade-for-trade (same intent IDs and kinds); the P&L difference is attributed to price slippage, fees, and size, with an unexplained residual ≤ $0.01 per trade. | Specified | — |
| BT-080 | The scorer verdict (D31 rule 7) passes only if DSR ≥ 0.95, PBO ≤ 0.2, SPA (or Romano–Wolf) p ≤ 0.05 against the champion when one exists, net P&L > 0 at 2× costs, and at least `min_trades` trades; the analyst sees only pass/fail and DSR. | Specified | — |
| BT-081 | Evaluator code (backtester, cost and fill models, splitters, statistics, scorer) MUST be listed in the evaluator manifest; assistant and analyst code MUST NOT import it, and the scorer lives outside `tbcore` (D31 rule 9). | Specified | — |

## Invariants

| ID | Invariant | Checked by |
|---|---|---|
| BT-I1 | No training sample of any split overlaps its test blocks' label spans or embargo windows. | `_assert_purged` in `test_validation.py` (k-fold, CPCV, era-wise) |
| BT-I2 | Each CPCV `(group, split)` test result belongs to exactly one path, and every path covers every group once. | `test_bt033_cpcv_splits_and_paths` |
| BT-I3 | Fees and slippage are non-negative and scale linearly with `cost_multiplier`. | `test_bt015_*` |

## Edge cases

| Scenario | Expected behavior | Rules |
|---|---|---|
| Trade at 09:45 ET with a 30-minute open regime | Open/close half-spread applies (minute 15 < 30) | BT-011 |
| ADV unknown (new listing) | Impact = fallback 10 bps, result warned | BT-012, BT-005 |
| Sale during the TAF holiday (2026-10-01..2026-12-31) | SEC + CAT only | BT-013 |
| Single noise dataset shows PBO 0.2 | Expected: CSCV PBO is dispersed per dataset; it averages 0.5 over noise datasets | BT-045 |
| Walk-forward with `step < test_len` | Splits are produced; stitching refuses overlapping windows | BT-034 |
| Era-wise splits with eras of unequal size | Folds are built over eras, never splitting one | BT-030 |

## Open questions

- Regulatory fee rows before 2024-05-22 are approximations (SEC FY2022–FY2023 from memory, TAF fixed at the 2024 value); refine if pre-2024 intraday history is purchased (O3).
- Calibrating `impact_k` and the spread regimes from paper fills waits on real Alpaca paper trading (Gate 2 owner action); the parity report's slippage attribution is the input.
