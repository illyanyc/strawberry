---
title: "Section 10 – ML Training Service"
phase: "4"
depends_on: [00, 03, 08, 09]
updated: 2026-10-17
---

# Section 10: ML Training Service

## Role

You are a senior ML engineer for financial time series. You start with strong, cheap baselines, add complexity only when honest validation rewards it, and make every model reproducible and explainable enough to trust with money.

## Context

The `ml` service trains and serves models that produce `Signal`s for strategies. It covers labeling, feature selection from the feature store, a model zoo (tree ensembles through deep sequence models, time-series foundation models, and RL), training jobs launched from the UI, model artifacts and cards, and an inference path that uses the same feature code as training. Validation uses section 08's splitters; search loops belong to section 11. Non-negotiables N2 and N5 apply.

## Scope

In scope:
- Labeling library: fixed-horizon returns, triple-barrier, trend-scanning, meta-labels; sample weights (uniqueness, return attribution, time decay).
- Dataset builder: features from the feature store + labels + sample weights → immutable training datasets tied to a `snapshot_id`.
- Additional feature groups beyond section 03's reference set (microstructure, volume profile, fractional differentiation, cross-sectional, context features from section 09).
- Model zoo behind one interface, in tiers.
- Training jobs: config schema, orchestration, progress streaming, GPU/CPU selection, checkpoints.
- Model artifacts, model cards, MLflow registration.
- Inference: batch (swing) and streaming (intraday), latency budgets, `Signal` publication.
- Explainability: feature importance (permutation, SHAP for trees), calibration.
- Training diagnostics (`DiagnosticsReport`) and the read/launch APIs the AI Research Analyst (section 12) uses to diagnose runs and drive retraining.

Out of scope:
- Hyperparameter/feature search loops and promotion policy (section 11).
- Backtest engine (section 08), but every trained model is evaluated through it.
- UI screens (section 14), but this section exposes the APIs they use.

## Inputs and dependencies

- Section 03: feature store (`@feature`, `FeatureSet`, `materialize`, online engine), point-in-time API.
- Section 08: splitters, metrics, `run_event_backtest`, leakage suite.
- Section 09: context datasets and helpers.
- Section 01: MLflow, MinIO, orchestrator, optional `gpu` profile.
- Section 12: the AI Research Analyst consumes this section's diagnostics and launches training and ablation jobs through these APIs (actor `analyst`, subject to its autonomy level and budget).
- `INPUT PENDING (U4)`: GPU. Default CPU; deep models train on small configs on CPU and on GPU when available (local profile or on-demand cloud worker, D25).
- `INPUT PENDING (U3)`: universe size drives dataset sizes.
- `INPUT PENDING (U8)`: inference latency budget for intraday.

## Decisions pending

| Decision | Default | Marker |
|---|---|---|
| MVP model families | Logistic regression baseline + LightGBM | `DECISION PENDING (research §2)` (D23, high confidence) |
| Labeling default | Triple-barrier with volatility-scaled barriers + meta-labeling on top of a primary rule/model; fixed-horizon as baseline | `DECISION PENDING (research §3)` (D24) |
| Deep learning framework | PyTorch (+ Lightning optional) | High confidence |
| Foundation models | Chronos / TimesFM / Moirai via their official packages, zero-shot first, fine-tune only if validation justifies | `DECISION PENDING (research §2)` |
| RL framework | Stable-Baselines3 with a custom Gym environment built on the event-driven backtester | `DECISION PENDING (research §2)` |
| Tabular data stack | Polars + Arrow; NumPy/PyTorch tensors at the model boundary | High confidence |
| Inference serving | In-process in the strategy runtime for trees (lowest latency); separate `ml-infer` gRPC service for heavy models | `DECISION PENDING (research §7)` |

## Interfaces and contracts to expose

```python
# tbcore.labels
fixed_horizon(prices, horizon, threshold=None) -> Labels
triple_barrier(prices, events, pt_sl=(k_up, k_down), vol=..., max_holding=...) -> Labels  # includes label end time
trend_scanning(prices, windows) -> Labels
meta_labels(primary_signals, triple_barrier_labels) -> Labels
sample_weights(labels, method="uniqueness"|"return"|"time_decay") -> Series

# tbcore.ml
class Model(Protocol):
    family: str
    def fit(self, X, y, sample_weight=None, eval_set=None, groups=None) -> "Model": ...
    def predict(self, X) -> Array            # regression score or class
    def predict_proba(self, X) -> Array      # where applicable; calibrated
    def save(self, path) -> None
    @classmethod
    def load(cls, path) -> "Model": ...
    def explain(self, X) -> Explanation | None

TrainingConfig  # serializable: feature_set@version, label spec, universe, timeframe, period,
                # splitter spec, model family + hyperparams, sample weighting, seed, resources
train(config) -> TrainingRun   # logs to MLflow, returns run_id, metrics per fold, artifact URIs
```

gRPC `MLService` (used by UI gateway, optimizer, AI Research Analyst):

```
SubmitTrainingJob(TrainingConfig) -> job_id
GetJob(job_id) / StreamJobProgress(job_id) -> stream {epoch|fold, metrics, logs}
CancelJob(job_id)
ListModelFamilies() -> [{family, tier, hyperparam_schema (JSON Schema), resource_hint, supports_gpu}]
RegisterModel(run_id, name) -> ModelVersion
Predict(model_version, instrument_ids, as_of) -> [Signal]   # batch path

# Diagnostics and analyst tools (all exclude the sealed holdout period by construction)
GetDiagnostics(run_id) -> DiagnosticsReport
GetLearningCurves(run_id) -> {per_fold: [{step, train_metric, val_metric}], size_curve?: [{train_rows, train_metric, val_metric}]}
GetFeatureImportance(run_id, method = gain|permutation|shap) -> {per_fold: {feature: value}, stability: {feature: {mean, std, sign_flip_rate, rank_std}}}
GetErrorSlices(run_id, slice_by = regime|time_of_day|symbol|sector|vol_bucket|label_class) -> [{slice, n, metric, calibration_error, vs_overall}]
GetDatasetProfile(snapshot_id) -> DatasetProfile
SubmitAblation(run_id, variants: [{drop|add feature_groups, label_spec?, hyperparams?}]) -> job_id   # same folds and seeds as the base run
```

`DiagnosticsReport` (contract, stored as an MLflow artifact and in Postgres, emitted with `ml.run.completed`):
- **Data quality**: rows per fold, missingness per feature, outlier rates, label distribution and class balance per fold, average label uniqueness/overlap, dropped rows and why.
- **Feature diagnostics**: train-vs-validation shift per feature per fold (PSI/KS), correlated clusters and redundancy, stationarity test results, leakage-suspicion flags (feature dominates importance, unrealistically high single-feature skill, correlation with future returns beyond the label horizon).
- **Learning curves**: per-fold train/validation curves; early-stopping point; overfitting gap; optional training-size curve.
- **Fold metrics**: per-fold ML metrics (AUC, log loss, Brier, IC/rank IC) and the linked backtest's per-fold after-cost metrics.
- **Importance and SHAP**: per-fold importances, cross-fold stability, top SHAP interactions for tree models.
- **Error analysis**: error and calibration by slice (as in `GetErrorSlices`), worst-error samples with timestamps and context.
- **Automated findings**: rule-based flags with thresholds (e.g., `overfitting: train AUC − val AUC > 0.1`, `instability: importance rank std > k`, `regime_dependence: > 70% of PnL from one regime`, `underfitting`, `data_starved`), each with evidence values. The analyst interprets these; it does not recompute them.

Streaming inference publishes `sig.<strategy_id>` with `model_version` and `feature_snapshot_id`; every signal references the exact feature vector used (stored for drift analysis and audits).

Model card (artifact, rendered in UI): purpose, data period and universe, features, labels, validation scheme, out-of-sample metrics with confidence intervals, deflated Sharpe of the associated strategy, known limitations, training cutoff, compute cost.

## Functional requirements

### Labels and datasets
- [ ] Label functions return label end times so section 08's purging works; no label uses data beyond its end time.
- [ ] Volatility used for barriers computed from past data only.
- [ ] Dataset builder joins features (as of decision time) and labels, applies sample weights, drops rows with insufficient lookback, and stores an immutable dataset with manifest (feature set version, label spec, universe, period, `snapshot_id`, row counts, class balance).
- [ ] Normalization/scaling fitted inside each training fold only (pipeline objects persisted with the model).

### Feature groups (extend section 03's reference set)
- [ ] Price/volume: multi-horizon returns, realized volatility (several estimators), ATR, RSI, MACD-like spreads, Bollinger position, volume z-scores, VWAP distance, opening-range features, gap features.
- [ ] Microstructure (when quotes/trades enabled): spread, quote imbalance, trade intensity.
- [ ] Fractionally differentiated price series with the minimum d that passes stationarity (fitted on training data only).
- [ ] Cross-sectional: ranks/z-scores within universe and sector at each timestamp.
- [ ] Context: market and sector returns, VIX regime, time to next macro event, earnings window flags, news sentiment aggregates (from section 09).
- [ ] Calendar: time of day, minutes since open/to close, day of week, month-end, option-expiry week.
- [ ] Every new feature has a leakage unit test and an offline/online parity test.

### Model zoo (tiered; each family implements `Model` and declares a hyperparameter JSON Schema)
- [ ] **Tier 1 (MVP)**: logistic/linear baseline, LightGBM (classification, regression, ranking objectives). Also a "no-skill" baseline (always predicts prior) for comparison.
- [ ] **Tier 2**: XGBoost, CatBoost, Random Forest / ExtraTrees; stacking and simple ensembles; meta-labeling model (secondary classifier that sizes/filters primary signals).
- [ ] **Tier 3 (deep sequence)**: LSTM/GRU, TCN, CNN-LSTM, Transformer variants (PatchTST, iTransformer, Temporal Fusion Transformer); sliding-window datasets built from the same features; early stopping on validation fold only.
- [ ] **Tier 4 (foundation models)**: zero-shot forecasts from Chronos/TimesFM/Moirai used as features or direct forecasts; optional fine-tuning on training folds; record the foundation model's pretraining data cutoff and treat backtest periods before it as potentially contaminated.
- [ ] **Tier 5 (RL)**: Gym environment wrapping the event-driven backtester (same costs, same risk checks, own virtual portfolio only); PPO/SAC baselines; RL policies emit `OrderIntent`-compatible actions and pass through normal risk.
- [ ] Tiers 3–5 are implemented only after Tier 1–2 are working end to end through backtest and paper.

### Training jobs
- [ ] Jobs run via the orchestrator with resource requests (CPU threads, memory, GPU); progress (fold, epoch, loss, validation metrics) streamed to the UI.
- [ ] Deterministic seeds; record library versions, hardware, and runtime.
- [ ] Each run logs config, dataset manifest, per-fold metrics, out-of-sample predictions (for stacking and analysis), feature importance, calibration curves, and the model artifact to MLflow.
- [ ] Every trained model triggers a standard evaluation: walk-forward backtest via section 08 with default costs, producing a tearsheet linked from the MLflow run.
- [ ] Cost guard: estimated runtime and resource use shown before launch; jobs over a configured budget require confirmation.

### Diagnostics for the AI Research Analyst
- [ ] Every training run produces a `DiagnosticsReport` automatically and emits `ml.run.completed` with the report ID; the analyst (section 12) is triggered from this event.
- [ ] Diagnostics are deterministic, computed only on training/validation folds, and never touch the sealed holdout period (enforced by the data API boundary from section 11).
- [ ] Rule-based findings use documented thresholds in `docs/business-logic/ml.md`; thresholds are config, not code constants.
- [ ] Ablation jobs reuse the base run's folds, seeds, and dataset snapshot so differences are attributable to the change; results link back to the base run.
- [ ] Training and ablation jobs launched by actor `analyst` are tagged (`launched_by=analyst`, `analyst_round_id`), count against the analyst budget, and are written to section 11's trial ledger for the lineage.
- [ ] Diagnostics APIs return aggregated, size-limited payloads suitable for an LLM context (no raw feature matrices); full artifacts stay in MLflow for the UI.

### Inference
- [ ] Inference uses the persisted pipeline (scalers, feature set version) and the online feature engine; a version mismatch between model and feature set fails closed.
- [ ] Streaming inference for intraday on each final bar; batch inference for swing at a scheduled time after the close (or before the open).
- [ ] Latency metrics per model; for intraday, tree models p99 under 20 ms per batch of the active universe on CPU (revise with U8).
- [ ] Store every live prediction with inputs reference for monitoring (section 13).

## Non-functional requirements

- **Reproducibility**: retraining a run from its MLflow record reproduces metrics within tolerance (exact for trees with fixed seeds and threads).
- **Performance**: LightGBM training on 1 year × 100 symbols × 5m data with walk-forward (≈10 folds) under 10 min on CPU (reference machine); deep models documented separately.
- **Security**: training jobs read data via read-only roles; no broker access from the ML service; artifacts stored with checksums.
- **Tests**: label function tests with synthetic paths (barrier hit order, max holding); dataset builder leakage tests; model interface conformance tests for every family (fit/predict/save/load round trip); inference parity test (offline predictions = online predictions for the same bars).

## Acceptance criteria

- [ ] Docs updated per `AGENTS.md` (master N6) in the same commits as the changes: `docs/file-map.md`, `docs/business-logic/ml.md` (labels, features, model tiers, inference rules), `CHANGELOG.md`, `docs/glossary.md` for new terms, and, for every decision made, an ADR in `docs/adr/` plus its row updated (or added) in `docs/decisions.md`.
- [ ] From the UI (or API until the UI slice lands), the owner trains LightGBM on triple-barrier labels with purged walk-forward; the run is in MLflow with a model card and a tearsheet.
- [ ] Rebuilding the run from its record reproduces metrics.
- [ ] The trained model runs in `shadow` mode in the strategy runtime; online predictions match offline predictions for the same bars.
- [ ] Leakage suite passes for all Tier 1–2 features and labels.
- [ ] At least one Tier 3 model and one Tier 4 (zero-shot) model run end to end through the same pipeline (may be marked experimental).
- [ ] Model families list and hyperparameter schemas are served to the UI.
- [ ] Every training run produces a `DiagnosticsReport` with automated findings; a deliberately overfit fixture run is flagged `overfitting` and a leaky fixture feature is flagged `leakage_suspicion`.
- [ ] An ablation launched via `SubmitAblation` reuses the base run's folds and seeds and is recorded in the trial ledger.

## Pitfalls to avoid

- Training on overlapping labels without purging/embargo; inflated validation scores.
- Fitting scalers, feature selection, or fractional-differencing `d` on the full dataset.
- Early stopping on the test fold.
- Treating high classification accuracy as edge; evaluate through the backtester with costs.
- Deep models on too little data; they often lose to LightGBM on tabular financial features. Require them to beat Tier 1 out of sample.
- Foundation models pretrained on data overlapping the backtest period (contamination).
- RL agents exploiting simulator flaws (unrealistic fills); the environment must use the same pessimistic fill and cost models.
- Different preprocessing in training and inference; persist the pipeline with the model.
- Class imbalance and non-stationarity ignored; monitor calibration over time.
