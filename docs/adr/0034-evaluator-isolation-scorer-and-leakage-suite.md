# ADR-0034: Evaluator isolation, the promotion scorer, and the leakage suite

- **Status:** Accepted
- **Date:** 2026-10-18
- **Decision register:** D52 (implements D31 rules 2, 7, 9 and N2's leakage tests in part)
- **Related:** BT-060..BT-065, BT-080, BT-081, DF-012, SM-010; ADR-0030, ADR-0031; research [03 §3.2](../research/03-optimization-and-ai-agent.md), [02 §3](../research/02-labeling-features-validation.md)

## Context

D31 rule 9 says the backtester, cost model, splitters, and scorer are outside the AI analyst's writable tree and view; rule 7 fixes the promotion tests; rule 5 says the analyst sees pass/fail and DSR only. N2 requires automated leakage tests, including a point-in-time lint for generated feature code, that run in CI. The analyst (section 12) and its sandbox do not exist yet, so the boundary must be defined now in a way section 12 can enforce and CI can check today.

## Decision

1. **Scorer** in the backtest service (`services/backtest/src/tb_backtest/scorer`), never in `tbcore`: `score(CandidateEvidence) -> Verdict` passes only with DSR ≥ 0.95 (effective N by ONC over the lineage's trial returns, cross-trial Sharpe variance), PBO ≤ 0.2 (CSCV, S = 16), SPA p ≤ 0.05 against the champion when one exists, net P&L > 0 at 2× costs, ≥ 30 trades, and max drawdown ≤ 25 %. Thresholds live in `GatePolicy` inside the evaluator tree. `analyst_view(verdict)` returns only `candidate_id`, `passed`, and `dsr`.
2. **Evaluator manifest** `services/backtest/evaluator_manifest.json`: the evaluator paths (backtester, cost and fill models, validation, parity, PIT lint, backtest service, simcore) and modules. CI checks that every path exists, that assistant code imports none of the modules, and that `tbcore` never imports the scorer. Section 12 builds the analyst sandbox so these paths are neither mounted nor writable.
3. **Leakage suite** (`tbcore.backtest.leakage`): future shift with the strategy's `prepare` step re-run on each run's bars, a random-walk test on synthetic martingale prices, the engine's timestamp assertion, a shuffled-label test, and a random-entry baseline. **PIT lint** (`tbcore.features.lint`, part of `make lint-py`): eight static rules with reviewed `# pit: ok <reason>` exemptions.

## Alternatives considered

| Option | Why not |
|---|---|
| Scorer in `tbcore.validation` | `tbcore` is imported by training and research code the analyst drives; the policy must sit outside that tree |
| Enforce isolation only in the sandbox (section 12) | Nothing would stop drift until section 12 lands; an import ban and manifest test catch it now |
| Lint by regex | Misses aliasing and formatting variants; the AST is exact and still small |
| Truncation test only | Cannot see a strategy that peeks through a closure over data it was not given; the random-walk test can |

## Consequences

- Positive: promotion criteria are code with tests; the analyst-facing surface is two values; the leakage suite and lint run on every change in CI.
- Negative / risks: the Python image still contains the evaluator source (all services share one image recipe); the sandbox mount rules are section 12's work and are Planned. PIT003 skips frame features (they aggregate explicitly per session) and relies on their parity and future-shift tests.
- Follow-up: section 11 feeds the trial ledger into `CandidateEvidence`; section 12 mounts the analyst sandbox without the manifest paths and routes scores through `analyst_view`.

## Validation

`services/backtest/tests/test_scorer.py`, `tests/python/test_evaluator_isolation.py`, `libs/py/tbcore/tests/test_leakage.py` (ORB passes; the leaky fixture fails future shift and random walk), `test_pit_lint.py` (registered features clean; six rule families flagged in a leaky generated module).
