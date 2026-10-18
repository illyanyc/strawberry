# ADR-0035: ML and optimizer contracts and the analyst tool APIs

- **Status:** Accepted
- **Date:** 2026-10-18
- **Decision register:** D53
- **Related:** ML-020..ML-025, EX-010..EX-036, BT-080, BT-081; ADR-0003, ADR-0007, ADR-0034; sections 10, 11, 12; research [03 §3.2](../research/03-optimization-and-ai-agent.md)

## Context

Phase 4 builds sections 10 (ML training), 11 (optimization), and 12 (AI Research Analyst) in parallel. The analyst reads training diagnostics, study diagnostics, the trial ledger, and gate reports, and launches training, ablations, and studies within its autonomy level. Those interfaces must exist and stay stable before either side is finished, and the permission boundary (holdout, scorer, promotions) must be enforceable by the server, not by the analyst's goodwill (D31 rules 5, 9, 11; N4).

## Decision

1. **Contracts first** in `contracts/proto`:
   - `tb.ml.v1` gains `training.proto` (`TrainingConfig`, `TrainingJob`, `JobProgress`, `TrainingRun`, `DatasetManifest`, `ModelFamily`, the full `DiagnosticsReport` with `Finding`s, learning curves, importance stability, error slices, `DatasetProfile`, `AblationVariant`, bus messages `MLRunCompleted` and `BacktestCompleted`, and `CallerContext`/`Actor`/`StepUp`) and `ml_service.proto` (`MLService`).
   - `ml.proto` adds `RegistryStage` (candidate → validated → shadow → paper → live_eligible → live → retired) and reproducibility fields on `ModelVersion`.
   - New package `tb.optimizer.v1`: `optimizer.proto` (`OptimizerService`: lineages, cycles, studies, trials, ledger, cumulative statistics, study diagnostics, gate reports and breakdowns, candidates, transitions, evidence packs, lineage graph, retraining; bus messages `StudyCompleted`, `StageChanged`) and `holdout.proto` (`HoldoutService`).
2. **One RPC per analyst tool** in section 12's table (`get_training_diagnostics` → `MLService.GetDiagnostics`, `get_trial_ledger` → `OptimizerService.GetTrialLedger`, `launch_study` → `CreateStudy` + `StartStudy`, `deploy_challenger_shadow` → `DeployChallengerShadow`, and so on). Responses are aggregated and size-limited (ML-021).
3. **The server decides who is calling.** Every write and every analyst read carries a `CallerContext`, but the actor is resolved from the verified service token (SE-040): calls from service `assistant` are always actor `analyst`; only `api-gateway` may assert actor `owner` and a `StepUp` attestation (EX-030). The optimizer owns the lineage autonomy level (`SetLineageAutonomy`) so the permission table is enforced where the stages live (EX-031); section 12's `SetAutonomy` proxies to it.
4. **Holdout is a separate service** whose allowlist never includes `assistant` (EX-012); no analyst-readable message has a holdout field. Gate reports reach the analyst with failure reasons and thresholds cleared (EX-035), consistent with `analyst_view` (BT-080).
5. **Python gRPC without generated stubs**, like the existing Python services: `tbcore.rpc` builds generic handlers and clients from the proto service descriptors, so the Python services and the analyst share one call path.

These contracts are additive (`buf breaking` clean) and are the interface the analyst track builds against; changes after this commit stay wire-compatible.

## Alternatives considered

| Option | Why not |
|---|---|
| Analyst imports `tb_ml` / `tb_optimizer` Python APIs directly | Bypasses service-token identity, so the permission table and budgets would rely on the caller; also drags evaluator modules into the analyst's import graph (BT-081) |
| REST/JSON between Python services | D3 fixes gRPC + protobuf between services; REST is only gateway ↔ UI |
| Actor taken from the request field | A compromised or prompt-injected analyst could claim `owner`; identity must come from the token |
| Holdout RPCs inside `OptimizerService` | The assistant must call `OptimizerService`; a separate service lets the allowlist exclude it entirely |
| Hyperparameters as typed proto messages per family | Family schemas change with the zoo; `Struct` validated against the family's JSON Schema keeps the contract stable and drives UI forms |

## Consequences

- Positive: the analyst track can build and test against stable messages now; permissions are enforced server-side with one identity source; the UI gets JSON Schemas for forms.
- Negative / risks: `Struct` hyperparameters are validated at runtime, not by the compiler; large diagnostics need explicit size limits (ML-021).
- Follow-up: services implement these RPCs (ADR-0036..0040); api-gateway maps owner REST calls onto them with step-up attestations; section 12 calls them as `assistant`.

## Validation

`make lint-proto` (lint + breaking), `make gen-check`, `contracts/tests/test_contracts.py` round trips; service tests per RPC; the permission table test (EX-031) and holdout isolation tests (EX-012).
