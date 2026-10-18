# ADR-0054: Go-live graduation, two-stage enablement, and a single promotion path

- **Status:** Accepted
- **Date:** 2026-10-18
- **Decision register:** D72
- **Related:** ADR-0019 (credential AAD), ADR-0025 (OMS and kill switches), ADR-0045 (monitor graduation evidence, MN-040), GL-001..GL-052, N4

## Context

Section 15 is the only way a strategy reaches `live`. The monitor already computes paper evidence and deliberately leaves combined DSR to this section. Phase 5 UI and monitoring took ADR-0049..0053 and D67..D71, so this decision is ADR-0054 / D72. Deployment (Mac Mini versus a server) is not decided and is not part of this ADR. Tests use simulators only; no account is turned live by this change.

## Decision

- Graduation report = monitor `GetGraduationEvidence` criteria (except the evidence-only DSR row) plus optimizer `GetGraduationStatistics` (DSR ≥ 0.95 deflated by lineage effective N, registry stage `live_eligible`). A missing input fails the criterion.
- Live enablement has two stages on `broker.live_enablement`: `enabled` (adapter may stream and reconcile) and `orders_enabled` (orders allowed). Both require the deploy flag, an owner passkey, a typed confirmation, and an audit sequence. Trading also requires the complete readiness checklist, including one clean read-only session.
- `PromoteToLive` is the only writer of strategy mode `live`. Cap is 10–20% of intended allocation. A paper twin stays on by default. A tick demotes any `live` row that lacks the flag, trading enablement, promotion timestamp, and cap.
- Demote and account disable need no step-up. The analyst cannot call any of these.
- A paper-environment adapter rejects `mode=live` orders before send. A live adapter rejects every other mode, and rejects all orders while read-only.
- The Go live screen reads the same report and keeps its promote control disabled until every gate passes. There is no setting that flips an account to live.

## Alternatives considered

| Option | Why not |
|---|---|
| Recompute paper sessions inside execution | Duplicates MN-040 and can disagree with the monitor |
| One flag that both starts the adapter and allows orders | Skips the read-only day (GL-012) |
| Let `SetStrategyMode` set `live` behind step-up | A second path; GL-020 requires one writer |
| Step-up on demote | Rollback must work from a phone without a ceremony (GL-030) |

## Consequences

- Positive: config, a direct mode edit, a paper credential, and the analyst all fail closed. The paper evidence has one source.
- Negative / risks: promotion needs the monitor and the optimizer up; either being down refuses promotion, which is the intended failure.
- Follow-up: the owner runs the readiness checklist and the first live day. Host choice stays open.

## Validation

`TestGL020PromoteIsTheOnlyPathToLive`, `TestGL052PaperCredentialCannotPlaceLiveOrder`, `TestLiveGate` (`make live-gate`), and the gateway step-up policy matrix. A red test blocks merge.
