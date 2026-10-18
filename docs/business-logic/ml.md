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
| ML-001 | Every label MUST carry `t1` (end of the last bar it uses) and `label_available_at`; no label may use data after `t1` (extends DF-014). Default target (D24): volatility-scaled residual return over `horizon` bars (intraday, capped at the close) or sessions (swing); swing ranking uses the per-date rank, bucketed into `rank_buckets` (default 5) integer relevance grades for LambdaRank (`tbcore.labels.rank_relevance`). | Implemented | `tbcore/labels.py`, `tbcore/ml/dataset.py` (`test_ml001_ml020_dataset_rows_end_before_the_boundary`, `test_ml003_uniqueness_weights_hand_example`) |
| ML-002 | Volatility used to scale labels or set barriers MUST come from bars up to and including the decision bar only (DF-015). | Implemented | `tbcore/labels.py` `_trailing_vol` (`test_df015_*`), `tbcore/ml/dataset.py` |
| ML-003 | Sample weights (`uniqueness`: mean of 1/concurrency over the label span; `return`: uniqueness-weighted absolute return attribution; `time_decay`: linear from `decay` for the oldest to 1 for the newest) MUST be computed from the training rows of each fold only. | Implemented | `tbcore/labels.py` `sample_weights`, `tbcore/ml/train.py` `_fit` (`test_ml003_uniqueness_weights_hand_example`) |
| ML-004 | The dataset builder MUST join features computed by `tbcore.features.compute` (one code path) as of each decision bar to labels on (`instrument_id`, decision bar), drop rows inside the feature warmup or without a label and record why, and write an immutable snapshot whose `snapshot_id` hashes the feature-set hash, label-spec hash, universe, timeframe, period, data source, and row content. | Implemented | `tbcore/ml/dataset.py` (`test_ml004_snapshot_id_is_deterministic_and_content_addressed`, `test_ml004_features_do_not_change_when_the_future_changes`) |
| ML-005 | Validation MUST use walk-forward, purged k-fold, or CPCV from `tbcore.validation` with eras = session dates (no session spans train and test) and an embargo of at least the label horizon in bars; random k-fold is refused (SM-002). | Implemented | `tbcore/ml/config.py` `resolve`, `tbcore/ml/train.py` `make_splits` (`test_ml005_folds_purge_and_embargo_at_least_the_horizon`) |
| ML-006 | Preprocessing (winsorizing at the training fold's 1st/99th percentiles, standardization, median imputation) MUST be fitted on the training fold only and persisted with the fold's model. | Implemented | `tbcore/ml/preprocess.py` (`test_ml006_pipeline_uses_training_rows_only`) |
| ML-007 | Early stopping MUST use a purged validation tail (last 20 % of the training fold's eras, embargoed) and MUST NOT see the test fold. | Implemented | `tbcore/ml/train.py` `_valid_tail` (`test_ml007_early_stopping_tail_is_inside_the_training_fold`) |
| ML-010 | A `TrainingConfig` MUST be fully serializable; its canonical-JSON hash is the run's input identity. Every run records code commit, config hash, environment lockfile hash, library versions, hardware, seed, threads, and runtime (SM-007). | Specified | — |
| ML-011 | Hyperparameters MUST validate against the family's JSON Schema at submit time; invalid configs, unknown families, and periods that reach the lineage holdout boundary are rejected before any job is queued. | Implemented | `tbcore/ml/schema.py`, `tbcore/ml/models.py` (`test_ml011_hyperparameter_schema_validation`); submit-time check in `services/ml` |
| ML-012 | Every run MUST get the standard evaluation: out-of-fold signals → top-quantile portfolio session returns after default costs (`tbcore.backtest.costs`) per fold and stitched; with `evaluation.event_backtest` the out-of-fold signals are rerun through the event-driven backtester and a tearsheet is linked from the run (BT-006). | Implemented | `tbcore/ml/train.py` `evaluate`, `tbcore/backtest/signals.py` (`test_ml012_signal_evaluator_enters_at_the_next_bar_open`); event-driven rerun in `services/ml` |
| ML-013 | Every run MUST log its config, dataset manifest, per-fold metrics, out-of-fold predictions, importance, calibration (classification), model artifacts with SHA-256 checksums, model card, and diagnostics to the run store; to MLflow when `TB_ML_MLFLOW_URL` is set; and a summary row to Postgres when `TB_ML_PG_DSN` is set. | Implemented | `tbcore/ml/runs.py`, `tbcore/ml/tracking.py`, `tbcore/docstore.py` (`test_ml022_ml013_run_is_stored_with_diagnostics_card_and_event`, `test_ml013_mlflow_mirror_uses_the_rest_api`) |
| ML-014 | Every run MUST report the `no_skill` baseline (prior mean / class prior) beside its own metrics; Tier 3–5 families are Planned and not served. | Implemented | `tbcore/ml/models.py`, `train.py` no-skill metrics (`test_ml014_every_family_trains_and_round_trips`) |
| ML-015 | With a fixed seed and thread count, rebuilding a run from its record MUST reproduce its fold metrics exactly for linear and tree families. | Implemented | `tbcore/ml/models.py` deterministic LightGBM (`test_ml015_rebuild_reproduces_metrics_exactly`) |
| ML-016 | Rule + ML filter families MUST take primary signals from the shared rule code (`tbcore.strategy.orb`, `tbcore.ml.rules.residual_reversal`), label each signal with its outcome in the trade's direction (ORB: net P&L under the rule's own stop, target, and session exit; residual reversal: `triple_barrier` with `pt`/`sl` and a `horizon`-session vertical barrier), and take a signal only when the filter probability is at least `primary.threshold`, or, when unset, the training rows' base rate. | Implemented | `tbcore/ml/rules.py`, `dataset.py` `_labels`, `train.py` `_threshold` (`test_ml016_meta_labels_and_filter_threshold`, `test_ml016_residual_z_is_shared_by_vectorized_signals`) |
| ML-020 | Diagnostics MUST be deterministic and computed only from the run's training and validation folds, whose period ends at or before the holdout boundary; every report carries `holdout_excluded = true`. | Implemented | `tbcore/ml/dataset.py` (boundary), `diagnostics.py` (`test_ml001_ml020_dataset_rows_end_before_the_boundary`, `test_ml023_overfit_fixture_is_flagged`) |
| ML-021 | Diagnostics APIs MUST return aggregated, size-limited payloads: at most `TB_ML_DIAG_TOP_FEATURES` (30) features per list, 200 shift rows, 50 slices, 20 worst samples, 50 curve points per fold; cut lists set `truncated`. Raw feature matrices are never returned. | Implemented | `tbcore/ml/diagnostics.py` `Limits` (`test_ml021_diagnostics_are_size_limited`) |
| ML-022 | Every finished run (succeeded or failed) MUST produce a `DiagnosticsReport` (when its folds exist) and publish `ml.run.completed` with the report ID. | Implemented | `services/ml/src/tb_ml/core.py` `_completed` (`test_ml022_ml013_run_is_stored_with_diagnostics_card_and_event`) |
| ML-023 | Findings MUST use configured thresholds (defaults in "Diagnostics thresholds"), each with its evidence values; thresholds are config, not code constants. | Implemented | `tbcore/ml/diagnostics.py` `Thresholds` (`test_ml023_overfit_fixture_is_flagged`, `test_ml023_leaky_feature_is_flagged`) |
| ML-024 | Jobs launched by actor `analyst` MUST be tagged with `launched_by=analyst` and `analyst_round_id`, count against the lineage's analyst budget (refused when spent), and be written to the lineage trial ledger. | Implemented | `services/ml/src/tb_ml/core.py` (`test_ml024_analyst_jobs_are_tagged_budgeted_and_ledgered`) |
| ML-025 | Ablations MUST reuse the base run's dataset snapshot, folds, seeds, and evaluation, change only the variant's factor, and link `base_run_id`. | Implemented | `services/ml/src/tb_ml/core.py` `ablate` (`test_ml025_ablation_reuses_snapshot_folds_and_seeds`) |
| ML-030 | Jobs MUST be queued by priority `live_retrain` (0) > `owner` (1) > `analyst` (2), FIFO within a priority, with progress (phase, fold, step, metrics, log) streamed and cancel honored between folds. | Implemented | `services/ml/src/tb_ml/core.py` (`test_ml030_queue_runs_owner_jobs_before_analyst_jobs`) |
| ML-031 | Every job MUST be estimated before launch (rows, fits, CPU seconds, memory, device); a job above `TB_ML_JOB_BUDGET_CPU_SECONDS` (3600) needs `confirm_over_budget` from the owner; analyst jobs over budget are refused. | Implemented | `services/ml/src/tb_ml/core.py` `estimate`, `submit` (`test_ml031_cost_guard_needs_owner_confirmation`) |
| ML-032 | Device selection: `auto` picks `cpu` for linear and tree families and `mps`/`mlx` only for families that declare them and only when available on the native worker (D25, D29); `cloud_gpu` runs only through the configured burst hook (`TB_ML_BURST_URL`), which is off by default — requesting it without a hook fails the job closed. Burst requests carry the config and dataset reference only, never credentials or account data. | Implemented | `services/ml/src/tb_ml/devices.py` (`test_ml032_cloud_gpu_fails_closed_without_a_hook`) |
| ML-033 | The ml service MUST NOT reach any broker or execution endpoint; it reads the lake read-only. | Implemented | Compose service `ml` (no broker or execution address, lake mounted read-only); `services/ml/AGENTS.md` |
| ML-040 | Inference MUST use the persisted pipeline and fail closed (no signal) when the model's feature-set hash, timeframe, or feature list differs from the online engine's. | Implemented | `tbcore/ml/bundle.py` `check_features`, `services/ml/src/tb_ml/core.py` `predict` (`test_ml014_every_family_trains_and_round_trips`, `test_ml040_ml042_registered_model_predicts_with_feature_vector_ids`) |
| ML-041 | Online predictions for a bar MUST equal offline predictions for the same bar within 1e-9 (features through `OnlineFeatureEngine`, same model and pipeline). | Implemented | `tbcore/strategy/model_signal.py` `OnlineModelScores` (`test_ml041_ml042_online_predictions_equal_offline_for_the_same_bars`) |
| ML-042 | Every model signal MUST carry `model_version` and `feature_snapshot_id` (hash of the exact feature vector used, `tbcore.ml.bundle.feature_vector_id`). Live and shadow model strategies MUST publish every online scoring as a `Signal` on `sig.<strategy_id>.<instrument_id>` and the scored features as a `FeatureVector` with the same snapshot ID on `feat.<feature_set>.<instrument_id>` (inputs for drift monitoring, MN-038); `Nats-Msg-Id` is deterministic per strategy, instrument, and bar, so replays deduplicate. A failed publish never blocks the intent. | Implemented | `services/ml/src/tb_ml/core.py` `predict`, `tbcore/strategy/model_signal.py` (`test_ml040_ml042_registered_model_predicts_with_feature_vector_ids`, `test_ml041_ml042_online_predictions_equal_offline_for_the_same_bars`), `tbcore.strategy.host.signal_messages`/`feature_vector_messages`, strategy-runtime `publish_scored`; `test_ml042_scored_batches_become_signals_and_feature_vectors` |
| ML-043 | Intraday tree inference MUST stay under 20 ms p99 per batch of the active universe on CPU (revise with U8). The runtime MUST record the online feature update and the model scoring per batch in `tb_inference_seconds{strategy, stage=features|score}`; `InferenceLatencyHigh` warns when the `score` p99 exceeds 20 ms for 30 minutes. | Implemented | `tbcore.strategy.model_signal.OnlineModelScores` (`ScoredBatch` timings), strategy-runtime `INFERENCE`, `infra/observability/prometheus/rules/monitoring.yml`; `test_ml043_tree_inference_p99_under_20ms_for_an_intraday_universe` (300-name batch, LightGBM, p99 measured in CI) |
| ML-044 | Every persisted run MUST store a training reference for live drift monitoring: `reference.parquet`, a deterministic sample of at most 5,000 dataset rows evenly spaced in decision time (training window only; the dataset never holds the sealed holdout) with the model's feature columns, and `importance.json`, each feature's rank by mean absolute gain importance across folds. Live predictions are compared with `oof.parquet` (out-of-fold), never with in-sample scores. | Implemented | `tbcore.ml.runs.write_monitoring_reference`; `test_ml044_training_reference_for_drift_monitoring` |

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
