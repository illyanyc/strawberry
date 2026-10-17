---
title: "Section 11 – Auto-Optimization Loop and Experiment Tracking"
phase: "4"
depends_on: [00, 07, 08, 10]
updated: 2026-10-17
---

# Section 11: Auto-Optimization Loop and Experiment Tracking

## Role

You are a research-infrastructure engineer who builds "AutoML for alpha" without fooling its owner. Your loop searches broadly, counts every trial, protects the holdout, and promotes only what survives out-of-sample tests and paper trading.

## Context

The `optimizer` service runs studies that search over hyperparameters, feature sets, labeling specs, timeframes, universes, and trade rules. It uses section 08's validation and section 10's training, tracks everything in MLflow, and manages a model/strategy registry with champion/challenger promotion. The danger is data snooping: the more you search, the more likely the best result is noise. Non-negotiables N2 (sealed holdout) and N4 (paper before live) apply.

## Scope

In scope:
- Study definitions, search spaces, samplers, and pruners.
- Nested walk-forward evaluation (inner loop for selection, outer loop for honest estimates).
- Sealed final holdout management with access logging.
- Trial accounting for deflated Sharpe and PBO.
- Experiment tracking conventions (MLflow experiments, tags, lineage).
- Registry stages and champion/challenger lifecycle: `candidate → validated → shadow → paper → live-eligible → live → retired`.
- Retraining policies (scheduled and drift-triggered) and automatic re-validation.
- Compute budgets and scheduling.
- **Research lineages**: a lineage groups every trial for one strategy family on one data domain (owner studies, analyst rounds, retrains, abandoned runs) so trial counting is cumulative.
- Study diagnostics and the tool APIs the AI Research Analyst (section 12) uses; server-side enforcement of the analyst's permissions and autonomy limits on registry stages.

Out of scope:
- Promoting to live (section 15 performs the live switch; this section only marks `live-eligible`).
- AI Research Analyst reasoning and loop control (section 12); this section supplies its data and enforces its limits.
- Monitoring metrics (section 13 supplies drift triggers).

## Inputs and dependencies

- Section 10: `train`, model families and hyperparameter schemas.
- Section 08: backtests, splitters, deflated Sharpe, PBO.
- Section 07: strategy registry and modes (`shadow`, `paper`).
- Section 13: drift and decay signals (trigger retraining) once available.
- Section 12: AI Research Analyst (actor `analyst`) launches studies and requests promotions through this API within its autonomy level.
- Section 01: Optuna storage (Postgres), MLflow, orchestrator.
- `INPUT PENDING (U4)`: compute available drives parallelism and budgets.

## Decisions pending

| Decision | Default | Marker |
|---|---|---|
| HPO library | Optuna (TPE sampler, ASHA/Hyperband pruners, Postgres storage) | `DECISION PENDING (research §5)` (D9) |
| Distributed execution | Orchestrator workers on one machine; Ray only if multi-node | `DECISION PENDING (research §5, §7)` |
| Tracking/registry | MLflow | `DECISION PENDING (research §5)` (D8) |
| Promotion gate metric | Deflated Sharpe probability ≥ 0.95 on outer out-of-sample, PBO ≤ 0.2, max drawdown within limits, positive after-cost returns in most outer folds | `DECISION PENDING (research §4, §5)` |
| Retraining cadence | Scheduled (default weekly for intraday, monthly for swing) + drift-triggered | `DECISION PENDING (research §5)` |

## Interfaces and contracts to expose

gRPC `Optimizer`:

```
CreateStudy(StudySpec) -> study_id
StartStudy(study_id, budget{max_trials, max_wall_time, max_cpu_hours, max_gpu_hours})
PauseStudy / ResumeStudy / StopStudy(study_id)
GetStudy(study_id) -> {status, trials_completed, best_trials (inner metric), trial_count_total, budget_used}
StreamStudyProgress(study_id)
EvaluateOnHoldout(candidate_id, actor, reason) -> HoldoutResult   # step-up required; logged; counted
PromoteCandidate(candidate_id, to_stage, actor) -> requires gate checks; live-eligible requires step-up
GetLineage(model_version | strategy_version) -> graph (data snapshot, features, labels, study, trials, backtests)
SetRetrainingPolicy(strategy_id, policy) / TriggerRetrain(strategy_id, reason)

# Diagnostics and analyst tools (holdout excluded by construction)
GetStudyDiagnostics(study_id) -> {convergence: best-so-far curve (deflated and raw), plateau_estimate,
    param_importance, param_stability (neighbor performance), pareto_front?, pruned_rate, failure_rate}
GetWalkForwardBreakdown(candidate_id) / GetRegimeBreakdown(candidate_id) / GetCostSensitivity(candidate_id)   # wraps section 08
GetTrialLedger(lineage_id) -> {trials_total, by_source{owner, analyst, retrain, abandoned}, by_dimension{hyperparams, features, labels, universes, timeframes, rules}, holdout_evaluations}
GetCumulativeOverfittingStats(lineage_id) -> {n_trials, deflated_sharpe_prob(best), pbo, min_track_record_length, significance_reachable: bool}
NominateLiveEligible(candidate_id, rationale, actor=analyst) -> notifies owner; no stage change
```

Analyst permissions on registry stages (enforced here, not in section 12):

| Transition | Owner | Analyst level 0 | Level 1 | Level 2 |
|---|---|---|---|---|
| `candidate → validated` (gates pass) | yes | no | yes (automatic on gate pass) | yes |
| `validated → shadow` | yes | no | yes | yes |
| `shadow → paper` (replace paper champion) | yes | no | no | yes, within promotion limits and after minimum shadow period |
| `paper → live-eligible` | yes, step-up | no | no | nominate only |
| `live-eligible → live` | section 15 only | no | no | no |
| `EvaluateOnHoldout` | yes, step-up | never | never | never |

`StudySpec` (serializable, versioned): objective (metric + constraints), search space (hyperparameters from the family's JSON Schema; feature set choices; label specs; timeframes; universes; trade-rule params such as stops, holding period, entry thresholds), evaluation protocol (nested walk-forward spec, cost model, fill model), data period with the **holdout boundary excluded**, budget, seed.

Events: `alert.optimizer.*` (study finished, candidate passed gates, retrain triggered), `ml.registry.stage_changed`.

## Functional requirements

### Search
- [ ] Search spaces built from model hyperparameter schemas plus configurable feature/label/rule dimensions; conditional spaces supported (family-specific params).
- [ ] Two-stage evaluation: cheap vectorized screening (section 08) for rule/param sweeps, then event-driven evaluation for the top candidates.
- [ ] Pruning on intermediate fold results to save compute.
- [ ] Multi-objective option (e.g., return vs drawdown vs turnover) with a Pareto front view.
- [ ] Every trial records: params, data snapshot, code commit, fold metrics, compute used, and links to MLflow runs.

### Honest evaluation
- [ ] **Nested walk-forward**: inner folds select hyperparameters; outer folds estimate performance of the whole selection procedure. Reported performance is the outer estimate only.
- [ ] **Sealed holdout**: the most recent period (default: last 6–12 months, configurable, fixed at study creation) is excluded from all studies; the data API refuses holdout access to the optimizer except through `EvaluateOnHoldout`. Every holdout evaluation is step-up gated, logged with reason, counted per strategy family, and shown in the UI. Repeated holdout use triggers a warning and inflates the trial count.
- [ ] **Trial accounting**: every trial is written to its lineage's trial ledger regardless of who launched it (owner, analyst, scheduled retrain); deflated Sharpe and PBO are computed over the whole lineage, not per study; abandoned and failed trials count. Gate reports and analyst tools expose only lineage-level deflated statistics for decisions.
- [ ] Holdout results are returned only to the owner; they are never written to any store or report readable by actor `analyst`.
- [ ] Gate report per candidate: outer OOS metrics with confidence intervals, deflated Sharpe probability, PBO, regime breakdown, cost sensitivity (results at 1.5× and 2× costs), parameter stability (performance of neighboring params), and leakage suite result.

### Registry and champion/challenger
- [ ] Registry stages as listed in Scope; transitions audited; `validated` requires gate report pass; `shadow` and `paper` deploy through section 07; `live-eligible` requires paper graduation metrics (section 15 criteria) and step-up.
- [ ] Challengers run in `shadow` or `paper` next to the champion on the same data; comparison dashboard data (section 14) from live paper results, not backtests.
- [ ] Automatic demotion inputs from section 13 (decay, divergence) move a strategy to `paper` or `retired` per policy; re-promotion after a demotion is always manual (the analyst may not re-promote a demoted strategy).
- [ ] Analyst-initiated transitions follow the permission table above; promotion limits (max promotions per window, minimum shadow sessions, cooldown after demotion) are config and audited.

### Retraining
- [ ] Retraining triggers from section 13 are routed to the analyst loop (section 12) when the lineage has autonomy ≥ 1 or an active loop; otherwise the default policy here runs. Either way the result is a challenger, never an in-place change to a live model.
- [ ] Policies: scheduled, drift-triggered, performance-triggered; retraining uses the same study spec with an extended window and must pass the same gates before replacing the champion (no auto-swap without gates; live swap requires human approval).
- [ ] Rolling holdout policy: when the holdout period is "spent" or aged, a new holdout boundary is set by an audited owner action; the old holdout becomes training data for future studies.

### Diagnostics for the AI Research Analyst
- [ ] Every completed study and candidate evaluation emits `optimizer.study.completed` / `backtest.completed` with report IDs; the analyst is triggered from these events.
- [ ] Study diagnostics computed deterministically (convergence, parameter importance via fANOVA or equivalent, neighbor stability, plateau estimate) and returned as compact, LLM-sized payloads.
- [ ] `significance_reachable` tells the analyst when further trials in a lineage can no longer produce a deflated Sharpe above the gate given the current best; the analyst's stopping rule uses it.

### Budgets
- [ ] Per-study, per-lineage, and global compute budgets; a separate analyst budget (trials, CPU/GPU hours) per lineage and globally; queueing with priorities (live retraining > owner studies > analyst studies).

## Non-functional requirements

- **Throughput**: run trials in parallel up to available cores/GPUs without starving live services (resource limits from section 01; live services have priority).
- **Reproducibility**: any trial can be rerun from its record.
- **Security**: holdout access and promotions are step-up gated and audited; the AI Research Analyst (section 12) has no holdout access, including results.
- **Tests**: nested walk-forward correctness test (synthetic data where the inner-best overfits, outer estimate stays near zero); holdout access denial test; trial accounting test; stage transition tests.

## Acceptance criteria

- [ ] Docs updated per `AGENTS.md` (master N6) in the same commits as the changes: `docs/file-map.md`, `docs/business-logic/optimization.md` (holdout policy, gates, registry stages, retraining), `CHANGELOG.md`, `docs/glossary.md` for new terms, and, for every decision made, an ADR in `docs/adr/` plus its row updated (or added) in `docs/decisions.md`.
- [ ] A study over LightGBM hyperparameters, two feature sets, and two label specs completes within budget and produces a gate report with outer OOS estimates, deflated Sharpe, and PBO.
- [ ] On a pure-noise synthetic dataset, the study's best inner result looks good but the outer estimate and deflated Sharpe correctly flag it as not significant.
- [ ] Optimizer data requests into the holdout period are refused; `EvaluateOnHoldout` requires step-up and appears in the audit log and UI counter.
- [ ] A challenger runs in shadow next to a champion; promotion to `paper` and then `live-eligible` follows the gates; live remains manual via section 15.
- [ ] A scheduled retrain runs end to end and does not replace the champion unless gates pass.
- [ ] Trials launched by the analyst, the owner, and scheduled retrains all appear in one lineage ledger, and the lineage's deflated Sharpe changes accordingly.
- [ ] Analyst transitions are allowed or denied exactly per the permission table at each autonomy level (table-driven test); analyst calls to `EvaluateOnHoldout` are denied and audited.

## Pitfalls to avoid

- Reporting the best inner-loop result as expected performance.
- "Peeking" at the holdout repeatedly until something passes; that turns the holdout into training data.
- Not counting trials from earlier studies, abandoned studies, or manual notebook experiments in the deflated Sharpe calculation.
- Searching over universes or timeframes without accounting for them as trials.
- Selecting parameters on a knife-edge optimum; prefer stable regions.
- Automatic live model swaps; changes to live strategies always require human approval.
- Letting optimization jobs starve the live trading services of CPU or memory.
- Counting trials per study instead of per lineage; an automated analyst running many small studies would otherwise look far more significant than it is.
