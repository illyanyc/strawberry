# ADR-0036: ML training library, model zoo, evaluation, and diagnostics in `tbcore.ml`

- **Status:** Accepted
- **Date:** 2026-10-18
- **Decision register:** D54 (implements D23, D24; settles section 10's tabular-stack, MVP-family, and labeling defaults)
- **Related:** ML-001..ML-016, ML-020..ML-023, DF-004, DF-014, BT-006, SM-002, SM-007; ADR-0021, ADR-0030, ADR-0031, ADR-0035; research [01 §2](../research/01-strategies-and-models.md), [02 §1, §3](../research/02-labeling-features-validation.md)

## Context

Section 10 needs labels, datasets, a model zoo behind one interface, fold-correct training, an after-cost evaluation of every run, and a deterministic `DiagnosticsReport` the analyst reads. Training, backtests, and live inference must share one code path (DF-004, SM-011), and validation must purge on label end times (SM-002). The research defaults are a ridge baseline, LightGBM regression for intraday and LambdaRank for swing, and a rule with an ML filter (D23), on volatility-scaled residual returns (D24).

## Decision

1. **Library in `tbcore.ml`** (shared by the ml worker, the optimizer, the backtester, and live inference): `config` (defaults resolved into the stored `TrainingConfig`, canonical-JSON hash), `featuresets` (named sets over the shared feature registry, feature groups for ablations), `dataset` (features from `tbcore.features.compute` joined to `tbcore.labels` labels; rows whose label is not known before the period end are dropped; content-addressed snapshot IDs), `preprocess` (fold-fitted winsorize/impute/standardize persisted with the model), `models` (zoo + JSON Schemas), `train` (folds, early-stopping tail, out-of-fold predictions, evaluation, final refit), `diagnostics`, `bundle` (pipeline + model + feature-set identity with a checksum), `rules` (primary rules), `synthetic` (seeded universes with an optional planted reversal).
2. **Model zoo** (`Model` protocol: fit, predict, predict_proba, explain, gain importance, interactions, state): `no_skill`, `ridge` (closed form), `logistic` (Newton, L2), `lgbm_regression`, `lgbm_lambdarank` (per-date groups, relevance grades from per-date ranks), `lgbm_binary`; rule + ML filter families `orb_meta` and `residual_reversal_meta`. LightGBM runs with `deterministic`, `force_row_wise`, fixed seeds and threads, so rebuilds reproduce metrics exactly (ML-015). TreeSHAP comes from LightGBM's `pred_contrib`; no SHAP package.
3. **Folds:** walk-forward (default, anchored), purged k-fold, or CPCV from `tbcore.validation`, with eras = session dates and an embargo of at least the label horizon. LightGBM early-stops on the purged last 20 % of each training fold's eras, never the test fold.
4. **Evaluation (ML-012):** out-of-fold scores become decisions with a threshold fitted on the training fold (top quantile for model families; the training base rate, or an explicit threshold, for filters). Model and swing-filter decisions run through a new vectorized signal evaluator in the evaluator tree (`tbcore.backtest.signals`: next-bar-open entries, horizon exits capped at the close intraday, one trade per instrument, shared cost model); the ORB filter masks the existing vectorized ORB engine. Every run reports 1× and 2× cost results and the primary rule alone for filters.
5. **Meta-labels (ML-016):** ORB trades are labeled by their net outcome under the rule's own exits (stop, 2R target, session exit); residual-reversal signals by `tbcore.labels.triple_barrier` in the trade's direction.
6. **Diagnostics:** `build_report` emits the contract `DiagnosticsReport` directly (data quality, PSI/KS shift, correlation clusters, ADF on the cross-sectional mean, leakage flags, learning and size curves, fold metrics, gain importance with rank and direction stability, interaction proxy, error slices, worst samples, findings) with thresholds and size limits as config objects.

## Alternatives considered

| Option | Why not |
|---|---|
| scikit-learn for ridge/logistic, `shap` for explanations | Two large dependencies for a closed-form ridge, a 30-line Newton logistic, and TreeSHAP LightGBM already provides |
| Separate per-family training code in `services/ml` | The optimizer and live inference need the same code; it belongs in `tbcore` (SM-011) |
| Top-k cross-sectional selection at every bar | Needs the whole cross-section at once in live dispatch (bars arrive per instrument); a training-fold threshold gives the same decisions offline and online |
| Evaluate ML signals only event-driven | Too slow for trials; the vectorized evaluator screens and the event-driven tier still runs before any gate (BT-006) |
| Pandas datasets | Polars + NumPy is the accepted stack (section 10 "tabular data stack") |

## Consequences

- Positive: one library drives training, studies, evaluation, and inference; runs are reproducible from the stored config and snapshot; diagnostics are deterministic and analyst-sized.
- Negative / risks: the vectorized signal evaluator does not simulate protective stops (listed as a known difference; the event-driven tier does); permutation importance and size curves add fits to each run; Tiers 3–5 are not built (Planned behind the same protocol).
- Follow-up: the ml worker (ADR-0037) stores runs and serves `MLService`; the optimizer (ADR-0038) reuses `train` for trials.

## Validation

`libs/py/tbcore/tests/test_ml.py` (weights hand example, boundary, snapshot identity, future independence, purging, fold pipeline, early-stopping tail, schema validation, every family's train and round trip, exact rebuild, meta-labels and filter threshold, shared residual z, next-bar entries, overfit and leaky fixtures flagged, size limits).
