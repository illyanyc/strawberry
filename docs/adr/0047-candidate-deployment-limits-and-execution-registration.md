# ADR-0047: Candidate deployment limits, monitoring expectations, and execution registration

- **Status:** Accepted
- **Date:** 2026-10-18
- **Decision register:** D65
- **Related:** EX-062..EX-064, RM-003, RM-007, SM-001, SM-016, MN-046; ADR-0040 (registry and deployments, D58), ADR-0025 (OMS limits, D43), ADR-0045

## Context

Phase 4 found that the optimizer's event-driven gate rerun ran every research candidate under the global paper defaults (`MaxDrawdown` 2,500 and `MaxConsecutiveLosses` 5 on a 100,000 allocation, ten positions, exposure caps). Those limits latch quickly for model strategies, so no synthetic candidate could pass the gate even with a strong vectorized estimate. The parity is honest (paper would apply the same limits), so the fix is to size limits for the candidate and use the same limits in the rerun and in paper, not to bypass them. Separately, promotion to paper wrote the research deployment file for strategy-runtime only; the OMS rejected the candidate's intents because nothing registered it with execution (ADR-0040 left that as a Planned owner step), and the runtime read deployments only at start. The monitor also needs each deployed candidate's expectation (backtest session returns, drawdown distribution, modeled costs) and training reference.

## Decision

1. **Deployment limits** are a `tb.optimizer.v1.DeploymentLimits` (a `tb.orders.v1.RiskLimits` plus an allocation) per lineage: the horizon profile from the optimizer's owner-controlled config file `TB_OPT_DEPLOYMENT_LIMITS` (shipped profiles for `intraday` and `swing` model strategies), overlaid field by field with an optional per-lineage override (EX-062).
2. **Who may change them:** only the owner, through `SetDeploymentLimits`; raising any limit or the allocation above the lineage's current effective value needs a passkey step-up (RM-007); the analyst is denied at every autonomy level (AN red-team rule "cannot change risk limits").
3. **The gate rerun uses them** (EX-063): `gates.event_evidence` passes the effective limits as the simulated strategy's limits and allocation to `tb-simcore`, and the gate report records them (`GateReport.deployment_limits`), so the evidence pack shows what the candidate was judged under.
4. **Deployment entries carry them**: the registry entry's `limits` and `allocation` are the effective deployment limits, and a `monitoring` block points at an `expectation.json` written next to the candidate's evidence (event-driven per-session returns in bps of allocation, per-trade returns, modeled slippage and shortfall, training run, reference sample path, feature set, importance ranks) (EX-064, MN-046).
5. **Registration with execution** is an owner action in api-gateway (`POST /api/v1/research/candidates/{id}/register`, policy action `strategy.register`, passkey): the gateway reads the paper-stage candidate's registry entry, calls `ExecutionService.RegisterStrategy` (mode paper; broker account from the D30 routing table for the horizon) and then `SetRiskLimits(strategy:<id>)` with the deployment limits, passing the owner's step-up so execution's RM-007 check accepts raised limits (SM-016). Only `paper` or `shadow` candidates can be registered; `live` stays the section 15 flow.
6. **Limit inheritance in execution (RM-019):** execution used to merge `global` → `account` → `strategy` as "tightest non-zero wins", so a strategy limit could never be looser than the global paper default and candidate limits would have been dead letters on paper (while the gate rerun, which replaces the simcore's global limits, would have judged under looser ones). The owner scope (`strategy:<id>`, `manual`) now replaces the global default field by field and `account:<id>` stays a ceiling. Raise detection compares effective limits, so a first-time strategy limit above the global default needs a passkey. This also makes the documented manual defaults (collar 3 %) effective, which "tightest wins" had silently overridden with the global 2 %.
7. **Runtime hot reload:** strategy-runtime re-reads the research deployment file every 30 s and hosts added shadow/paper entries and drops removed ones (never live, never a seed entry) (SM-016).

## Alternatives considered

| Option | Why not |
|---|---|
| Raise the global paper defaults | Loosens every strategy, including the ORB baseline, to make research candidates pass |
| Let the optimizer register candidates with execution on promotion | The optimizer would need an execution key and authority over risk limits; registration with raised limits is an owner action with step-up |
| Judge candidates with no limits in the rerun | Breaks parity with paper (BT-006); a candidate that only works without limits fails in paper |
| Analyst-proposed limits in pre-registration | The analyst must never change risk limits |
| Restart strategy-runtime after each promotion | Fragile owner step that is easy to forget; the reload is limited to research entries in shadow/paper |
| Keep "tightest wins" and ask the owner to raise the global limits | Loosens every strategy, including the ORB baseline, and the global scope stops describing a default |

## Consequences

- Positive: candidates are judged under the same limits they trade under on paper; promotion to paper becomes tradeable with one passkey action; the monitor gets expectations without a config file.
- Negative / risks: profiles in config can be set too loose; mitigated because paper registration with raised limits always needs a passkey and execution re-checks. The global scope is no longer a hard ceiling for strategies with their own (passkey-approved) limits; account limits are the ceiling, so the owner should set account limits that protect each broker account.
- Follow-up: owner review of the shipped profiles (`infra/compose/deployment-limits.json`).

## Validation

`test_ex062_*` (owner step-up to raise, analyst denied, profile overlay), `test_ex063_rm019_rerun_under_deployment_limits_replaces_the_global_defaults` (drawdown and daily-loss latches that block under the global defaults do not block under the candidate's limits), `TestRM019*`, `TestRM007FirstStrategyLimitAboveGlobalNeedsPasskey`, gateway `TestSM016RegisterCandidateWithExecution`, strategy-runtime `test_sm016_research_registry_reload`.
