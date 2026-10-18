# ADR-0030: Validation splitters and overfitting statistics in `tbcore.validation`

- **Status:** Accepted
- **Date:** 2026-10-18
- **Decision register:** D48 (settles the section 08 "ranking metric" pending decision; implements part of D31)
- **Related:** BT-030..BT-047, BT-080, SM-002, SM-008; research [02 §3](../research/02-labeling-features-validation.md), [03 §3.2](../research/03-optimization-and-ai-agent.md)

## Context

D31 requires ranking by deflated Sharpe with an effective trial count, PBO per study, SPA or Romano–Wolf for challenger promotion, and nested validation (inner CPCV, outer walk-forward) with purging and embargo. Section 08 left the ranking metric pending. The statistics must be reproducible, dependency-light (the native macOS workers share one `uv` lockfile), and testable against published values.

## Decision

1. `tbcore.validation.splitters`: `PurgedKFold`, `CombinatorialPurgedCV` (with path assembly), and `WalkForward` (rolling or anchored, embargo as a gap), all working on decision and label-end times, optionally era-wise (`groups`). No random k-fold.
2. `tbcore.validation.stats`, NumPy and the standard library only (no SciPy or scikit-learn): Sharpe, PSR, DSR, expected maximum Sharpe, MinTRL, MinBTL, ONC clustering (k-means with silhouette t-statistic, one refinement pass kept only if it improves quality) for the effective trial count and cross-trial Sharpe variance, PBO by CSCV (S = 16 default, vectorized block sums), Hansen SPA and Romano–Wolf on the stationary bootstrap, and bootstrap confidence intervals.
3. **Ranking metric:** DSR (net of costs, effective N) is the gate; constraints are PBO ≤ 0.2, positive at 2× costs, drawdown within budget, and a minimum trade count. The scorer that applies them lives outside `tbcore` (ADR on evaluator isolation, BT-080/BT-081).

## Alternatives considered

| Option | Why not |
|---|---|
| `mlfinlab` / `skfolio` / `arch` | Licence (mlfinlab is commercial), heavy dependency trees, and the evaluator must be small and auditable; `arch` has SPA but not CSCV/ONC |
| SciPy / scikit-learn for normal quantiles and k-means | `statistics.NormalDist` and a 30-line k-means suffice; fewer pins in the shared lockfile |
| Rank by raw Sharpe or PSR | Ignores the number of trials; the core failure mode research 02 §3.1 documents |

## Consequences

- Positive: one small library used by training, backtests, the optimizer, and the scorer; values checked against the papers.
- Negative / risks: home-grown k-means/silhouette is O(N²) in trials (fine up to a few thousand trials; the trial ledger can cluster per study). CSCV PBO on a single noise dataset is widely dispersed (documented edge case).
- Follow-up: section 11 feeds the trial ledger (returns matrix, trial counts) into `effective_trials` and `deflated_sharpe`.

## Validation

`test_validation.py`: purging/embargo invariants on synthetic overlapping labels (k-fold, CPCV, era-wise), CPCV path coverage, walk-forward gap and stitching; DSR and E[max SR] against Bailey & López de Prado (2014) (0.9004, 0.1132); MinBTL 5 years for 45 trials; ONC recovers planted clusters; PBO ≈ 0.5 on noise and ≈ 0 with a real edge; SPA and Romano–Wolf detect only the planted superior model.
