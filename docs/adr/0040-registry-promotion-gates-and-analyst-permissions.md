# ADR-0040: Registry stages, promotion gates, the analyst permission table, evidence packs, and retraining

- **Status:** Accepted
- **Date:** 2026-10-18
- **Decision register:** D58 (implements D31 rules 7, 9, 11 and N4 for the registry)
- **Related:** EX-024, EX-030..EX-036, EX-040, EX-041, EX-050..EX-057, BT-080, SM-004, SM-006, SM-009, SM-015; ADR-0034, ADR-0035, ADR-0038

## Context

Section 11 owns champion/challenger and must enforce the analyst's autonomy server-side. N4 requires human approval with step-up and an evidence pack for promotion of a new spec to paper and for live; section 15 alone sets live. Gate 4 requires SPA/Romano–Wolf, DSR ≥ 0.95, PBO ≤ 0.2, and positive at 2× costs, on event-driven evidence (BT-006).

## Decision

1. **Stages:** candidate → validated → shadow → paper → live_eligible → live → retired. One permission function (`tb_optimizer.registry.decide`) serves `CheckTransition`, `PromoteCandidate`, and `DeployChallengerShadow`; `live` is always refused.
2. **Permission table** exactly as section 11 (owner; analyst levels 0, 1, 2), plus: scheduled retrains may reach validated and shadow only; a demoted candidate is re-promoted only by the owner; a **new spec** means a lineage without a paper champion, so analyst level 2 may only replace an existing paper champion, within promotion limits (1 per 14 days, ≥ 10 shadow sessions, 14-day cooldown after a demotion).
3. **Gates:** validated needs a passing scorer verdict (BT-080) on the outer walk-forward estimate rerun event-driven, a clean leakage check (no `leakage_suspicion` finding and no skill on era-shuffled labels), and the lineage-level DSR/PBO; paper additionally needs survivorship `complete` (O3) and SPA or Romano–Wolf p ≤ 0.05 against the paper champion when one exists; owner promotions to paper and live_eligible need a fresh step-up; live_eligible needs non-synthetic data and ≥ 20 observed paper sessions.
4. **Evidence pack** (markdown + HTML) generated before every human-gated promotion; its hash goes into the audit event. Every stage change is audited and published on `ml.registry.stage_changed`.
5. **Deployment:** shadow and paper write the candidate's registry entry (runtime class `model_signal` or `meta_filter`, bundle path, universe) to the research deployment registry (`TB_OPT_DEPLOY_REGISTRY`), which strategy-runtime adds to its seed at start (`TB_RESEARCH_REGISTRY`; shadow and paper entries only, never live); one paper champion per lineage (the replaced champion is retired). Registering the strategy with execution (`ExecutionService.RegisterStrategy` through an owner action) is Planned; until then the OMS rejects the challenger's intents (fail closed).
6. **Retraining:** policies (scheduled cadence default 7 days intraday / 30 days swing, drift and performance triggers) run as a study with the same spec on the extended window; when the lineage autonomy is ≥ 1 the trigger is published on `optimizer.retrain_requested.<lineage>` for the analyst instead. A retrain yields a challenger; it never changes the champion by itself.
7. **Analyst view:** gate reports reach actor `analyst` with failure reasons and thresholds cleared; the evidence pack is not on the analyst's allowlist.

## Alternatives considered

| Option | Why not |
|---|---|
| Permission checks in the assistant | A prompt-injected analyst could skip them; enforcement belongs where the stages live |
| Gate on vectorized evidence | BT-006: screening only |
| Automatic champion swap after a passing retrain | Section 11 pitfall; paper replacement stays human or analyst level 2 within limits, live always human |
| Survivorship waiver for synthetic data | Synthetic universes have no delistings by construction, so they are `complete`; real lake data stays `unknown` until O3, which correctly blocks paper |

## Consequences

- Positive: every transition is one tested function; the table-driven test pins the section 11 table; promotions leave evidence and audit trails.
- Negative / risks: graduation evidence is the session count recorded on the candidate until section 15 supplies paper P&L and divergence; strategy-runtime reads deployments at start only (a restart picks up new challengers); execution registration is a separate owner step.
- Follow-up: section 13 demotion inputs call `DemoteCandidate` as service `monitor`; section 15 consumes live_eligible.

## Validation

`test_ex031_permission_table` (20 rows), `test_ex033_ex052_ex053_ex055_promotion_limits_and_special_cases`, `test_ex050_ex056_ex057_transitions_deploy_shadow_and_paper_with_evidence`, `test_ex020_ex041_owner_analyst_and_retrain_trials_share_one_ledger`, `test_ex012_ex030_grpc_boundaries_for_the_assistant`.
