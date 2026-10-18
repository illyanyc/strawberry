# ADR-0038: Nested Optuna studies, one trial ledger per lineage, and MinBTL research cycles

- **Status:** Accepted
- **Date:** 2026-10-18
- **Decision register:** D56 (implements D9 and D31 rules 1–4, 6, 8; refines D9's storage)
- **Related:** EX-001..EX-006, EX-020..EX-026, EX-060, BT-041..BT-045, BT-080; ADR-0030, ADR-0034, ADR-0036; research [03 §3.2](../research/03-optimization-and-ai-agent.md)

## Context

Section 11 must search broadly without fooling the owner: every trial counted (including pruned, failed, and abandoned), statistics deflated over everything a lineage ever tried, the selection procedure evaluated out of sample, a fixed effective-trial budget per research cycle, and a pre-registered hypothesis and metric. The analyst (section 12) launches many small studies, so counting per study would overstate significance.

## Decision

1. **Nested evaluation.** A study cuts its period (ending at the lineage holdout boundary) into anchored outer walk-forward folds by era. For each outer fold an Optuna sub-study (TPE by default, seeded per fold; median or successive-halving pruner on inner splits) selects by inner CPCV on that fold's training window only: training labels end an embargo before the test window and bars are cut at the test start, so selection never sees the fold's test period. The winner is refit on the window and tested on the fold; stitched fold results are the study's estimate. A final sub-study over the whole period chooses the deployable candidate. Budgets split as `max_trials // (K+1)` per outer fold, the remainder to the final selection.
2. **Search space** from the family JSON Schemas (default ranges when the spec gives none) plus categorical feature-set, label-spec, and family dimensions and `primary.*` rule parameters; trials record which dimensions the study varied.
3. **Trial ledger.** Every trial of every sub-study, every single training run in a lineage (`RecordTrial` from ml), and every scheduled retrain trial is a `Trial` document in `research.docs` with spec and pre-registration hashes, snapshot, commit, seed, source, status, params, and its out-of-fold session-return series as a parquet artifact. Rows are never deleted.
4. **Lineage statistics** (`tb_optimizer.stats`): series aligned on the union of session days (missing = 0); ONC effective N over usable series, unusable trials counted at the same redundancy ratio, holdout touches added as full trials; DSR, PBO (CSCV, S = 16), MinTRL, MinBTL; `significance_reachable` from the Sharpe needed at the cycle's maximum effective N. The scorer receives the lineage matrix and the extra trials (`CandidateEvidence.extra_trials`).
5. **Research cycles:** `OpenCycle` (owner) sets the effective-trial budget to the largest N with MinBTL(N, target Sharpe) within the available years; studies stop, and new ones are refused, once the cycle's effective trials reach it.
6. **Pre-registration:** hypothesis, metric, and stop rule are required to start; the objective must equal the pre-registered metric.
7. **Optuna storage:** in-memory per sub-study by default (the ledger is the system of record and sub-studies are reproducible from their seeds); `TB_OPT_OPTUNA_STORAGE` accepts an RDB URL for optuna-dashboard. A dedicated `optuna` Postgres database is Planned with the dashboard.

## Alternatives considered

| Option | Why not |
|---|---|
| One Optuna study whose objective averages inner scores over all outer folds | Later trials would be proposed from scores that include earlier folds' test periods: selection leaks into the outer estimate |
| Report the best inner trial | Exactly the bias D31 forbids; shown only as "inner" |
| Count trials per study | An analyst running many small studies would look far more significant than it is |
| Optuna RDB storage as the ledger | Optuna drops failed-trial context, has no lineage, cycle, or source, and its schema is not append-only |
| Effective N from all trials including ones without series (as independent) | Overcounts redundant pruned trials; ignoring them undercounts; the redundancy-ratio rule is conservative and deterministic |

## Consequences

- Positive: the honest estimate, the deflation, and the cycle budget are enforced by code with tests; the analyst's trials and the owner's share one ledger.
- Negative / risks: nested studies cost K+1 searches; small budgets mean few trials per fold. Studies run on the optimizer's threads; a priority queue across studies (EX-061) is Planned with the Prefect adapter.
- Follow-up: optuna-dashboard database; parallel trials.

## Validation

`services/optimizer/tests/test_optimizer.py`: a study over LightGBM hyperparameters, two feature sets, and two label specs completes within budget with outer estimate, DSR, and PBO; on pure noise the best inner IC is positive while the gate's DSR fails; owner, analyst, and scheduled-retrain trials share one ledger and move the lineage statistics; pre-registration and cycle refusals; statistics count unusable trials and holdout inflation.
