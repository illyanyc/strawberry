# ADR-0039: Sealed holdout service

- **Status:** Accepted
- **Date:** 2026-10-18
- **Decision register:** D57 (implements N2's sealed holdout and D31 rule 5)
- **Related:** EX-010..EX-015, SE-035, SM-003, SM-I3; ADR-0035, ADR-0037, ADR-0038

## Context

N2 requires a sealed final holdout served only by a holdout service, touched at most once per model family per quarter, never visible to the AI analyst. Studies and training must be unable to read it, every access must be logged and counted, and results must stay out of anything the analyst can read.

## Decision

1. **Boundary per lineage**, fixed at lineage creation (default: the newest data minus `TB_HOLDOUT_MONTHS` = 12 months; for synthetic sources the last 20 % of sessions), stored in the `holdout` schema and mirrored on the lineage. It moves only forward by an owner step-up action; the old holdout becomes ordinary data and is listed as retired.
2. **Data API boundary:** `CheckDataAccess` refuses any period ending after the boundary and audits the refusal; ml and the optimizer call it before building data, and the dataset builder never reads bars at or after the boundary it is given. Only the holdout service builds a dataset past the boundary.
3. **Evaluation:** owner only, step-up attested by api-gateway within 300 s, reason required, one touch per family per calendar quarter (recorded before evaluating, so a crash still spends it). The candidate's frozen bundle scores the holdout through the gate's evaluator (no refit). Pass = DSR (lineage N) ≥ 0.95 and net positive at 2× costs; the response is pass/fail + DSR.
4. **Isolation:** `HoldoutService` is a separate gRPC service and audience (`holdout`, port 9092) whose allowlist never includes `assistant`; touches and results live only in `holdout.docs`, readable by `svc_holdout` alone. The research side receives only the touch count (EX-013: each touch adds one trial to the lineage).

## Alternatives considered

| Option | Why not |
|---|---|
| Holdout RPCs on `OptimizerService` | The assistant must call the optimizer; separating the service lets the allowlist exclude it entirely |
| Return full holdout metrics to the owner | Invites iteration on the holdout; pass/fail + DSR is the D31 contract |
| Refit the candidate on data up to the boundary before evaluating | That is a new model; the holdout must judge the frozen candidate |
| Separate process and container | Same isolation from the database role and allowlist at lower cost on a single host; the separate role keeps the store split |

## Consequences

- Positive: holdout access is impossible for the analyst path at three layers (service allowlist, database role, no result fields in research messages); every touch is audited and counted.
- Negative / risks: the holdout process shares memory with the optimizer (same host trust boundary); quarter limits are per calendar quarter in UTC.
- Follow-up: UI counter and evaluation action (section 14); rolling boundary schedule reminders (Planned).

## Validation

`test_ex010_ex012_holdout_is_refused_to_studies_and_the_analyst`, `test_ex011_ex013_owner_holdout_touch_once_per_quarter_and_counted`, `test_ex014_boundary_moves_only_forward_with_step_up`, `test_ex012_ex030_grpc_boundaries_for_the_assistant`, `TestSE032SchemaIsolation`.
