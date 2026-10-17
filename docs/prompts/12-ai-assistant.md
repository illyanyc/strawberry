---
title: "Section 12 – AI Research Analyst and Assistant"
phase: "4"
depends_on: [00, 02, 08, 10, 11, 13]
updated: 2026-10-17
---

# Section 12: AI Research Analyst and Assistant

## Role

You are an applied LLM engineer and quantitative research lead. You are building an AI research analyst that reads every training and backtest result, figures out what is wrong, and drives the next round of experiments, the way a skeptical senior quant would. You assume the model will sometimes be wrong, overconfident, or manipulated, so you design it to be bounded: at worst it wastes compute on paper; it can never trade, never see the holdout, and never reach live.

## Context

The `assistant` service has two capabilities:

1. **AI Research Analyst (core).** After every training run, backtest, or study round, and whenever monitoring reports live degradation, the analyst reads structured diagnostics, writes a **diagnosis** and a **ranked improvement plan**, and, depending on its autonomy setting, launches the next retrain/optimization round itself. It repeats until gains plateau, the budget runs out, or the evidence says the strategy has no edge. The goal is to make each model as good as it can honestly be, not as good as it can look in-sample.
2. **Assistant (chat).** An interactive chat in the UI where the owner asks questions, reviews the analyst's journal, and approves proposals.

The analyst does **not** compute statistics itself. Deterministic diagnostics come from sections 10 (training), 11 (studies, trial accounting), 08 (backtests), and 13 (live feedback). The LLM interprets them, forms hypotheses, ranks actions, and writes configs. Non-negotiables N1 (no secrets to the LLM, prompt-injection defense), N2 (sealed holdout, trial counting), N4 (no live without the section 15 gate), and N5 (reproducible, logged) apply.

## Scope

In scope:
- Analyst loop controller: triggers, rounds, stopping rules, budgets, autonomy levels.
- Diagnosis and improvement-plan generation from structured diagnostics.
- Research journal: every round's evidence, reasoning, decision, and outcome, logged and auditable.
- Constrained tool set over sections 08, 10, 11, and 13 APIs.
- Promotion within limits among non-live registry stages (autonomy level 2 only).
- Chat UI backend, proposals, and approvals.
- Guardrails: holdout isolation, experiment budgets, cumulative trial accounting, input sanitization, output validation, egress redaction.
- Provider abstraction for local and API LLMs.
- Evaluation harness for analyst and assistant quality.

Out of scope (permanently excluded from every autonomy level):
- Placing, modifying, or canceling orders; changing strategy modes to `live`; changing risk limits, allocations, or kill switches; accessing credentials.
- Seeing holdout data or holdout results.
- Promotion to `live-eligible` or `live` (owner-only, via sections 11 and 15).
- Containment of live risk (section 13 and section 07 own demotion and kill switches).

## Inputs and dependencies

- Section 10: `DiagnosticsReport` per training run (data quality, feature diagnostics, learning curves, fold metrics, importance/SHAP, error slices, calibration), training and ablation APIs, hyperparameter schemas.
- Section 11: study diagnostics (convergence, parameter importance and stability, Pareto fronts), cumulative trial ledger with deflated Sharpe and PBO per research lineage, gate reports, study and promotion APIs.
- Section 08: backtest reports with walk-forward and regime breakdowns, cost sensitivity, parity reports.
- Section 13: `LiveFeedbackReport` and retraining-trigger events (decay, drift, divergence, calibration loss).
- Section 02: actor `analyst` (and `assistant` for chat), policy function, audit log, step-up for raising autonomy or budgets.
- Section 01: orchestrator for durable analyst workflows.
- `INPUT PENDING (U4)`: local GPU decides whether a local LLM is practical and how much compute the loop can use.
- `INPUT PENDING (U5)`: budget may include LLM API costs.

## Decisions pending

| Decision | Default | Marker |
|---|---|---|
| LLM provider | Pluggable: API provider for reasoning quality, local model (Ollama/vLLM) as fallback and for sensitive deployments | `DECISION PENDING (research §5)` (D26) |
| Agent framework | Pydantic AI (typed tools, structured outputs) running inside an orchestrator workflow, or a minimal custom tool loop | `DECISION PENDING (research §5)` |
| Default autonomy | Level 0 (suggest-only) for every strategy until the owner raises it | High confidence |
| Plateau rule | Stop after K = 3 consecutive rounds where the best **deflated** outer-OOS metric improves by less than ε (default 0.05 Sharpe-equivalent), or when added trials would push the deflated Sharpe threshold out of reach | `DECISION PENDING (research §4, §5)` |
| Data egress policy | Only aggregated metrics, configs, diagnostics, and run metadata leave the box with an API provider; no positions, balances, or account identifiers | High confidence |

## Autonomy levels

Set per strategy lineage. Raising a level requires step-up auth and is audited; lowering is instant and can be done by the owner, by monitoring (section 13), or by the analyst's own guardrails.

| Level | Name | What the analyst may do without asking |
|---|---|---|
| 0 | **Suggest-only** (default) | Diagnose, write plans, draft `TrainingConfig`/`StudySpec` proposals. Every launch needs owner approval. |
| 1 | **Auto-run on paper** | Everything in level 0, plus launch training runs, ablations, backtests, and optimization studies within budget, and deploy resulting challengers to `shadow` or paper-shadow next to the champion. No stage changes beyond `shadow`. |
| 2 | **Auto-promote within limits** | Everything in level 1, plus promote a challenger that passes section 11's gates to replace the **paper** champion, subject to limits: max promotions per strategy per window (default 1 per 2 weeks), minimum shadow period (default 10 sessions), paper allocation unchanged, cooldown after any demotion. Can **nominate** a candidate for `live-eligible`; the owner decides via sections 11 and 15. |

No level permits live promotion, holdout access, or any order-path action.

## Interfaces and contracts to expose

Contracts (in `contracts/`):
- `AnalystRound`: round_id, lineage_id, trigger (run completed, study completed, live feedback, schedule, owner request), inputs (report IDs), diagnosis, plan, actions taken, budget used, trials added, outcome metrics (deflated), decision (continue, plateau, stop-no-edge, stop-budget, escalate-to-owner), reasoning, model/provider used, prompt and tool-call hashes.
- `Diagnosis`: findings[] each with {category (data_quality, leakage_suspicion, overfitting, underfitting, instability, regime_dependence, cost_sensitivity, calibration, drift, execution_gap), severity, evidence (metric refs with values and report IDs), explanation}.
- `ImprovementPlan`: ranked actions[] each with {category (features, labels, model_type, search_space, data_window, universe, trade_rules, stop_or_retire), description, config_diff (against the current `TrainingConfig`/`StudySpec`), hypothesis, expected effect, estimated compute, trial cost (how many trials it adds to the lineage), risk of overfitting, rank score}.

gRPC `Analyst` (via gateway for UI):

```
GetJournal(lineage_id, filter) -> [AnalystRound]
GetLatestDiagnosis(lineage_id | run_id) -> Diagnosis
GetPlan(round_id) -> ImprovementPlan
ApproveActions(round_id, action_ids[], actor) / RejectActions(round_id, action_ids[], reason, actor)
SetAutonomy(lineage_id, level, budget, actor)          # raising needs step-up
PauseLoop(lineage_id | all, actor) / ResumeLoop(lineage_id, actor)
RequestAnalysis(run_id | study_id | strategy_id, question?, actor)
```

Chat endpoints: `POST /assistant/conversations`, `POST /assistant/conversations/{id}/messages` (streamed), `GET /assistant/conversations/{id}`.

Tools available to the analyst (typed inputs and outputs; each declares permission, cost, and minimum autonomy level):

| Tool | Kind | Min level | Source |
|---|---|---|---|
| `get_training_diagnostics(run_id)`, `get_learning_curves`, `get_feature_importance(method)`, `get_error_slices(slice_by)`, `get_dataset_profile(snapshot_id)` | read | 0 | section 10 |
| `get_backtest_report`, `get_walk_forward_breakdown`, `get_regime_breakdown`, `get_cost_sensitivity`, `get_parity_report` | read | 0 | section 08 |
| `get_study_diagnostics`, `get_gate_report`, `get_trial_ledger(lineage)`, `get_cumulative_overfitting_stats(lineage)` | read | 0 | section 11 |
| `get_live_feedback(strategy_id)` | read | 0 | section 13 |
| `list_model_families`, `get_hyperparam_schema`, `list_feature_sets`, `describe_feature`, `list_label_specs` | read | 0 | section 10 |
| `draft_training_config`, `draft_study_spec`, `draft_feature_set` | draft | 0 | sections 10, 11 |
| `launch_training`, `launch_ablation`, `launch_backtest`, `launch_study` | write | 1 | sections 08, 10, 11 |
| `deploy_challenger_shadow(candidate_id)` | write | 1 | sections 07, 11 |
| `promote_paper_champion(candidate_id)` | write | 2 | section 11 (gates enforced there) |
| `nominate_live_eligible(candidate_id, rationale)` | notify | 2 | section 11 (owner decides) |
| `annotate_run`, `write_journal_entry` | write | 0 | MLflow, journal |

Every tool call emits an `AuditEvent` (actor `analyst` or `assistant`, inputs hash, outcome). Policy enforcement is server-side (section 02); the tool list shown to the LLM is filtered by level, and the server rejects anything above the level regardless.

Events consumed: `ml.run.completed`, `backtest.completed`, `optimizer.study.completed`, `monitor.decay`, `monitor.drift`, `monitor.divergence`, `monitor.retrain_suggested`. Events produced: `analyst.round.completed`, `analyst.plan.ready` (needs approval), `analyst.escalation` (to the owner), `alert.analyst.*`.

## Functional requirements

### Diagnosis (after every training run, backtest, study round, or live-feedback trigger)
- [ ] Collect the relevant structured reports by ID; never load raw data or holdout periods into the prompt.
- [ ] Read and reason over, at minimum:
  - **Data quality and feature diagnostics**: missingness, outliers, label balance and uniqueness, train/validation distribution shift per fold, redundant/correlated feature clusters, stationarity, leakage-suspicion flags.
  - **Learning curves**: train vs validation loss/metric by epoch or boosting round, and by training-set size where available (overfitting vs underfitting vs data-starved).
  - **Walk-forward and regime breakdowns**: per-fold and per-regime performance, dispersion across folds, dependence on one period or regime.
  - **Feature importance and SHAP**: top drivers, stability of importance across folds, features with high importance but unstable sign, suspiciously dominant features (possible leakage).
  - **Error analysis**: error slices by regime, time of day, symbol, sector, volatility bucket, label class; calibration; worst trades and their context.
  - **Cost and execution**: cost sensitivity, turnover, slippage vs modeled; live-vs-backtest drift and divergence from section 13 when the model is in shadow/paper/live.
  - **Cumulative overfitting stats**: lineage trial count, deflated Sharpe, PBO.
- [ ] Produce a `Diagnosis` where every finding cites metric values and report IDs; findings without evidence are rejected by output validation.
- [ ] State plainly when the evidence indicates no edge (e.g., deflated Sharpe probability low after many trials, edge only in one regime, edge disappears at 1.5× costs).

### Improvement plan
- [ ] Generate a ranked `ImprovementPlan` covering the relevant categories: **features** (add, remove, transform, ablate groups), **labels** (horizon, barrier widths, meta-labeling, classification vs ranking), **model type** (move up or down the tiers; simpler models when overfitting), **hyperparameter search space** (narrow, widen, shift, add regularization), **data window** (training length, recency weighting, retrain cadence), **universe** (liquidity filters, sector focus, exclusions), **trade rules** (entry thresholds, holding period, stops, regime filters, event blackouts), and **stop/retire**.
- [ ] Rank by expected improvement in the **deflated** outer-OOS objective per unit of compute and per trial added; prefer actions that test a clear hypothesis and that reduce complexity when overfitting is found.
- [ ] Each action carries a concrete, schema-validated config diff so it can run without manual editing.
- [ ] Prefer ablations and controlled changes (one factor at a time, or a small designed study) over large unfocused searches.

### Closing the loop
- [ ] Loop controller runs as a durable orchestrator workflow per lineage: trigger → diagnose → plan → (approve or auto-launch per autonomy) → wait for results → evaluate → journal → next round or stop.
- [ ] Stopping rules: plateau (see decision table), budget exhausted, trial count makes significance unreachable, no-edge conclusion, repeated failures, owner pause, or monitoring lowers autonomy. On stop, write a final summary and recommendation (keep champion, promote challenger within level, nominate for live-eligible, or retire).
- [ ] At level 1, challengers that pass gates are deployed to `shadow`; at level 2, promotion to paper champion after the minimum shadow period and within promotion limits; section 11 enforces the gates server-side.
- [ ] Live feedback (section 13) re-opens the loop for that lineage with the live evidence attached; retraining proposals for live strategies always produce challengers, never in-place changes to the live model.
- [ ] Concurrency limits: max active loops and max concurrent jobs launched by the analyst; analyst jobs run at lower priority than live retraining and owner jobs (section 11 queue).

### Guardrails
- [ ] **Sealed holdout**: the analyst's data and tool access excludes the holdout period by construction (section 11 boundary); holdout results are never returned to the analyst, not even as pass/fail. Only the owner can request holdout evaluation.
- [ ] **Experiment budget**: per-lineage and global limits on trials, CPU/GPU hours, and LLM spend per day/week; hard stop at limits; alert at 80%.
- [ ] **Trial counting**: every analyst-launched trial is written to section 11's trial ledger for the lineage; the analyst must rank and report candidates by deflated Sharpe and PBO computed over **all** trials in the lineage (including its own previous rounds, owner studies, and abandoned runs). Raw best-trial metrics may be shown but never used for decisions.
- [ ] **No live without section 15**: the analyst cannot call any live-promotion API; the policy layer denies actor `analyst` for `strategy.promote.live`, `registry.stage.live_eligible`, `registry.stage.live`, and every order-path action.
- [ ] **Decision log**: every round is an `AnalystRound` record with the full reasoning, evidence references, actions, and outcomes; stored in Postgres, linked from MLflow runs, and audited. The journal is human-readable in the UI.
- [ ] **Input hygiene**: external text (news, run notes, webhook payloads, feature descriptions written by users) is wrapped as untrusted data, never as instructions; tool outputs are length-limited and sanitized.
- [ ] **Output validation**: diagnoses and plans validated against schemas; config diffs validated against hyperparameter and study schemas; out-of-scope requests refused.
- [ ] **Egress redaction**: account identifiers, positions, and balances removed before any call to an external provider.
- [ ] **Self-limiting**: repeated failed rounds, rising PBO, or a monitoring alert automatically drop the lineage to level 0 and notify the owner.

### Assistant (chat)
- [ ] Owner can ask questions about any run, study, or journal entry; answers cite run IDs.
- [ ] Proposals from the analyst appear in chat and in the Experiments page with diff, estimated compute, and trial-count impact; approve/reject with reason.
- [ ] Conversation history stored with retention settings and exportable.
- [ ] LLM API cost tracking with a monthly cap and alert.

## Non-functional requirements

- **Latency**: diagnosis for a completed run within 2 minutes of the completion event; first chat token under 2 s with API providers.
- **Security**: the analyst's service account has read-only DB roles, no network path to `broker-gateway` or `execution` order endpoints (enforced at the network level, not only by policy), and no holdout data access.
- **Reproducibility**: each round records provider, model version, prompts (hashed and stored), tool inputs/outputs, and config diffs so it can be audited and replayed.
- **Quality**: an evaluation set of at least 50 scripted scenarios runs in CI against the configured provider or a recorded mock, covering: recognizing overfitting from learning curves, spotting leakage from a dominant feature, recognizing regime dependence, recommending simplification, correctly concluding "no edge" on noise, respecting budgets, refusing unsafe actions, resisting prompt injection.
- **Tests**: policy tests (every forbidden action denied for `analyst` at every level); autonomy-level tests; holdout isolation tests; budget enforcement tests; trial-ledger integration test; redaction tests; schema validation tests.

## Acceptance criteria

- [ ] Docs updated per `AGENTS.md` (master N6) in the same commits as the changes: `docs/file-map.md`, `docs/business-logic/assistant.md` (analyst loop, autonomy levels, tools, budgets, stopping rules, egress policy), `CHANGELOG.md`, `docs/glossary.md` for new terms, and, for every decision made, an ADR in `docs/adr/` plus its row updated (or added) in `docs/decisions.md`.
- [ ] After a LightGBM training run completes, the analyst produces a diagnosis with evidence-backed findings and a ranked plan with valid config diffs, visible in the journal.
- [ ] At level 1 on a paper strategy, the analyst runs at least three rounds autonomously, deploys a gated challenger to shadow, and stops on the plateau rule with a written summary.
- [ ] At level 2, a challenger that passes gates and the minimum shadow period replaces the paper champion; promotion limits and cooldown are enforced.
- [ ] On a pure-noise dataset, the analyst concludes "no edge" and stops instead of searching indefinitely; the lineage's deflated Sharpe reflects all its trials.
- [ ] Analyst requests for holdout data, live promotion, orders, risk limits, or credentials are denied at the server and audited.
- [ ] A section 13 decay event re-opens the loop for that lineage and produces a challenger proposal, not a change to the live model.
- [ ] Budget limits stop the loop; a prompt-injection string in a run note does not change behavior.
- [ ] No account balances, positions, or identifiers appear in requests to an external provider.
- [ ] The evaluation suite passes its threshold.

## Pitfalls to avoid

- An analyst that keeps searching until something looks good; that is automated p-hacking. Count every trial and stop on plateau or no edge.
- Letting the LLM compute or recall statistics; numbers come from tools and must be cited.
- Ranking candidates by raw best-trial metrics instead of deflated, all-trials-counted metrics.
- Feeding holdout results back to the analyst in any form, including pass/fail.
- Big unfocused searches when a targeted ablation would test the hypothesis cheaply.
- Adding complexity to fix overfitting; usually the fix is fewer features, stronger regularization, simpler models, or better labels.
- Treating analyst conclusions as validated; they pass through the same gates as owner experiments.
- Any path from the analyst to the order flow or live configuration "for convenience".
- Passing raw news, webhook, or note text into the instruction channel.
