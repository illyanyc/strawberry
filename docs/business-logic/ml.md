# ML training, diagnostics, and inference

**Prefix:** `ML-`  ·  **Owners:** `libs/py/tbcore` (`tbcore.ml`, `tbcore.labels`), `services/ml`, `services/strategy-runtime` (shadow inference)  ·  **Related:** [data and features](data-and-features.md), [backtesting](backtesting.md), [optimization](optimization.md), [strategies and model lifecycle](strategies-and-model-lifecycle.md), ADR-0035, ADR-0036, ADR-0037

## Scope

Labels and sample weights beyond section 08's library, the dataset builder, the model zoo (Tier 1 and the rule + ML filter tier), training jobs and workers, run tracking, the `DiagnosticsReport` and the analyst read/launch APIs, and inference (batch, streaming, shadow). Search loops, trial accounting, the holdout, and promotion are in [optimization](optimization.md); backtest fills, costs, and statistics are in [backtesting](backtesting.md).

## Concepts and states

- **Model family:** one implementation of the `tbcore.ml.Model` protocol with a hyperparameter JSON Schema: `no_skill`, `ridge`, `logistic`, `lgbm_regression`, `lgbm_lambdarank`, `lgbm_binary`, and the rule + ML filter families `orb_meta` (intraday) and `residual_reversal_meta` (swing). Tiers 3–5 (deep sequence, foundation models, RL) are Planned.
- **Dataset snapshot:** features (as of decision time) joined to labels and weights, immutable, identified by `snapshot_id` (content hash of its inputs and rows).
- **Training run:** one job's output: per-fold metrics, out-of-fold predictions, a persisted pipeline and model per fold plus a final refit, a model card, a `DiagnosticsReport`, and an evaluation (vectorized always, event-driven when requested).
- **Job states:** `queued → running → succeeded | failed | canceled`.

## Rules

| ID | Rule | Status | Code / tests |
|---|---|---|---|
| ML-001 | Every label MUST carry `t1` (end of the last bar it uses) and `label_available_at`; no label may use data after `t1` (extends DF-014). Default target (D24): volatility-scaled residual return over `horizon` bars (intraday, capped at the close) or sessions (swing); swing ranking uses the per-date rank, bucketed into `rank_buckets` (default 5) integer relevance grades for LambdaRank. | Specified | — |
| ML-002 | Volatility used to scale labels or set barriers MUST come from bars up to and including the decision bar only (DF-015). | Specified | — |
| ML-003 | Sample weights (`uniqueness`: mean of 1/concurrency over the label span; `return`: uniqueness-weighted absolute return attribution; `time_decay`: linear from `decay` for the oldest to 1 for the newest) MUST be computed from the training rows of each fold only. | Specified | — |
| ML-004 | The dataset builder MUST join features computed by `tbcore.features.compute` (one code path) as of each decision bar to labels on (`instrument_id`, decision bar), drop rows inside the feature warmup or without a label and record why, and write an immutable snapshot whose `snapshot_id` hashes the feature-set hash, label-spec hash, universe, timeframe, period, data source, and row content. | Specified | — |
| ML-005 | Validation MUST use walk-forward, purged k-fold, or CPCV from `tbcore.validation` with eras = session dates (no session spans train and test) and an embargo of at least the label horizon in bars; random k-fold is refused (SM-002). | Specified | — |
| ML-006 | Preprocessing (winsorizing at the training fold's 1st/99th percentiles, standardization, median imputation) MUST be fitted on the training fold only and persisted with the fold's model. | Specified | — |
| ML-007 | Early stopping MUST use a purged validation tail (last 20 % of the training fold's eras, embargoed) and MUST NOT see the test fold. | Specified | — |
| ML-010 | A `TrainingConfig` MUST be fully serializable; its canonical-JSON hash is the run's input identity. Every run records code commit, config hash, environment lockfile hash, library versions, hardware, seed, threads, and runtime (SM-007). | Specified | — |
| ML-011 | Hyperparameters MUST validate against the family's JSON Schema at submit time; invalid configs, unknown families, and periods that reach the lineage holdout boundary are rejected before any job is queued. | Specified | — |
| ML-012 | Every run MUST get the standard evaluation: out-of-fold signals → top-quantile portfolio session returns after default costs (`tbcore.backtest.costs`) per fold and stitched; with `evaluation.event_backtest` the out-of-fold signals are rerun through the event-driven backtester and a tearsheet is linked from the run (BT-006). | Specified | — |
| ML-013 | Every run MUST log its config, dataset manifest, per-fold metrics, out-of-fold predictions, importance, calibration (classification), model artifacts with SHA-256 checksums, model card, and diagnostics to the run store; to MLflow when `TB_ML_MLFLOW_URL` is set; and a summary row to Postgres when `TB_ML_PG_DSN` is set. | Specified | — |
| ML-014 | Every run MUST report the `no_skill` baseline (prior mean / class prior) beside its own metrics; Tier 3–5 families are Planned and not served. | Specified | — |
| ML-015 | With a fixed seed and thread count, rebuilding a run from its record MUST reproduce its fold metrics exactly for linear and tree families. | Specified | — |
| ML-016 | Rule + ML filter families MUST take primary signals from the shared rule code (`tbcore.strategy.orb`, `tbcore.ml.rules.residual_reversal`), label each signal with the meta-label of its triple-barrier outcome in the trade's direction, and take a signal only when the filter probability is at least `primary.threshold`. | Specified | — |
| ML-020 | Diagnostics MUST be deterministic and computed only from the run's training and validation folds, whose period ends at or before the holdout boundary; every report carries `holdout_excluded = true`. | Specified | — |
| ML-021 | Diagnostics APIs MUST return aggregated, size-limited payloads: at most `TB_ML_DIAG_TOP_FEATURES` (30) features per list, 200 shift rows, 50 slices, 20 worst samples, 50 curve points per fold; cut lists set `truncated`. Raw feature matrices are never returned. | Specified | — |
| ML-022 | Every finished run (succeeded or failed) MUST produce a `DiagnosticsReport` (when its folds exist) and publish `ml.run.completed` with the report ID. | Specified | — |
| ML-023 | Findings MUST use configured thresholds (defaults in "Diagnostics thresholds"), each with its evidence values; thresholds are config, not code constants. | Specified | — |
| ML-024 | Jobs launched by actor `analyst` MUST be tagged with `launched_by=analyst` and `analyst_round_id`, count against the lineage's analyst budget (refused when spent), and be written to the lineage trial ledger. | Specified | — |
| ML-025 | Ablations MUST reuse the base run's dataset snapshot, folds, seeds, and evaluation, change only the variant's factor, and link `base_run_id`. | Specified | — |
| ML-030 | Jobs MUST be queued by priority `live_retrain` (0) > `owner` (1) > `analyst` (2), FIFO within a priority, with progress (phase, fold, step, metrics, log) streamed and cancel honored between folds. | Specified | — |
| ML-031 | Every job MUST be estimated before launch (rows, fits, CPU seconds, memory, device); a job above `TB_ML_JOB_BUDGET_CPU_SECONDS` (3600) needs `confirm_over_budget` from the owner; analyst jobs over budget are refused. | Specified | — |
| ML-032 | Device selection: `auto` picks `cpu` for linear and tree families and `mps`/`mlx` only for families that declare them and only when available on the native worker (D25, D29); `cloud_gpu` runs only through the configured burst hook (`TB_ML_BURST_URL`), which is off by default — requesting it without a hook fails the job closed. Burst requests carry the config and dataset reference only, never credentials or account data. | Specified | — |
| ML-033 | The ml service MUST NOT reach any broker or execution endpoint; it reads the lake read-only. | Specified | — |
| ML-040 | Inference MUST use the persisted pipeline and fail closed (no signal) when the model's feature-set hash, timeframe, or feature list differs from the online engine's. | Specified | — |
| ML-041 | Online predictions for a bar MUST equal offline predictions for the same bar within 1e-9 (features through `OnlineFeatureEngine`, same model and pipeline). | Specified | — |
| ML-042 | Every model signal MUST carry `model_version` and `feature_snapshot_id` (hash of the exact feature vector used). | Specified | — |
| ML-043 | Intraday tree inference MUST stay under 20 ms p99 per batch of the active universe on CPU (revise with U8). | Specified | — |

## Diagnostics thresholds

Defaults of `tbcore.ml.diagnostics.Thresholds` (config, ML-023); a run's report records the values used.

| Finding | Fires when (default) |
|---|---|
| `overfitting` | mean over folds of train metric − validation metric > 0.10 (IC for regression/ranking, AUC for classification) |
| `underfitting` | train and validation metric both below 0.01 IC (0.52 AUC) |
| `instability` | importance rank std > 5 for a top-10 feature, or sign flip rate > 0.3 (linear) |
| `regime_dependence` | more than 70 % of positive out-of-fold P&L comes from one regime slice |
| `data_starved` | fewer than 2,000 training rows in a fold, or the size curve still rises by > 0.01 between the last two sizes |
| `leakage_suspicion` | one feature holds > 50 % of total importance, or a single feature's out-of-fold \|IC\| > 0.30, or a feature's \|correlation\| with returns beyond the label horizon > 0.20 |
| `calibration` | expected calibration error > 0.10 (classification) |
| `distribution_shift` | PSI > 0.25 between train and validation for a top-10 feature |
| `no_skill` | out-of-fold IC (or AUC − 0.5) not above the no-skill baseline by 0.005 |
| `cost_sensitivity` | net return positive at 1× costs and negative at 2× |

## Invariants

| ID | Invariant | Checked by |
|---|---|---|
| ML-I1 | Every test-fold row's features, labels, and weights were computed without data after its `label_available_at` and without fitting on test rows. | Leakage tests in `test_ml_dataset.py`, `test_ml_training.py` |
| ML-I2 | A registered model's artifact checksum matches its run record. | Registry load check |

## Edge cases

| Scenario | Expected behavior | Rules |
|---|---|---|
| Training period reaches the holdout boundary | Refused at submit with the boundary in the error | ML-011, EX-010 |
| A fold has fewer eras than the walk-forward window | Fewer folds; a run with zero folds fails with `data_starved` | ML-005, ML-023 |
| Live bar for a model whose feature set changed version | No signal; error metric and log | ML-040 |
| `cloud_gpu` requested with no burst hook | Job fails closed before training | ML-032 |
| Analyst budget spent mid-round | Next analyst submit refused; owner jobs unaffected | ML-024 |

## Open questions

- Tier 3–5 families (GRU/Transformer, foundation-model volatility features, RL) are Planned behind the same `Model` protocol (D23 later models).
- Inference latency budget for intraday (U8) and the live-prediction store for section 13 drift analysis.
