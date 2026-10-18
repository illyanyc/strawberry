# Go-live and paper graduation

Rules for the only path from paper to live (section 15, [ADR-0054](../adr/0054-go-live-graduation-and-live-enablement.md), D72). Live orders use the same risk gate, kill switch, and virtual-portfolio rules as paper (RM-*, VP-*, OE-*). Deployment (which host) is out of scope. `TB_TRADING_LIVE_ENABLED` defaults to false.

| ID | Rule | Status | Code / tests |
|---|---|---|---|
| GL-001 | A graduation report MUST be computed from platform-recorded paper and shadow results. The paper criteria (sessions, trades, divergence, reconciliation breaks, drawdown vs the backtest p95, implementation shortfall vs 1.5× modeled) MUST come from the monitor's `GetGraduationEvidence` (MN-040). Backtests alone MUST NOT pass the report. | Implemented | `oms/golive.go` `GraduationReport`; `TestGL020PromoteIsTheOnlyPathToLive` |
| GL-002 | The report MUST add, and fail closed when unavailable: DSR ≥ 0.95 on the paper session returns deflated by the lineage's effective N (`GetGraduationStatistics`), and registry stage `REGISTRY_STAGE_LIVE_ELIGIBLE`. The monitor's DSR row is evidence-only and MUST NOT be copied as passed. | Implemented | `tb_optimizer/core.py` `graduation_statistics`; `TestGL020…` |
| GL-003 | Thresholds, from the monitor and not re-decided here: intraday ≥ 20 sessions and ≥ 100 trades; swing ≥ 40 sessions and ≥ 30 trades. | Implemented | monitor `graduation` (MN-040) |
| GL-010 | Readiness is a stored checklist. Owner-attested items (`gl-security`, `gl-ops`, `gl-data`, `gl-risk-drill`, `gl-monitoring`, `gl-compliance`, `gl-docs`) are `pending` until the owner marks `passed` or `failed` with a passkey; there is no waiver. Automated items (`gl-flag`, `gl-env`, `gl-readonly`, `gl-breaks`, `gl-ownership`) are recomputed on every read. The record is complete only when every item is `passed`. | Implemented | `LiveReadiness`, `SetReadinessItem`; `TestGL020…` |
| GL-011 | `EnableLiveAccount` MUST require actor `owner`, a fresh passkey step-up, confirmation equal to the account id, `TB_TRADING_LIVE_ENABLED=true`, and a live-environment adapter. It writes `broker.live_enablement` only after `audit.append`, and stores that sequence. Stage `read_only` sets `enabled` and not `orders_enabled`. Stage `trading` additionally requires a complete readiness record and sets `orders_enabled`. | Implemented | `EnableLiveAccount`; `TestGL020…` |
| GL-012 | While `orders_enabled` is false, a live adapter MAY stream and reconcile and MUST reject every order. A clean ready reconciliation on a distinct session date counts as one read-only session. The trading stage requires at least one. | Implemented | `livegate.OrdersAllowed`, adapter `modeGate`, IBKR `_reject_mode`; `TestGL052PaperCredentialCannotPlaceLiveOrder` |
| GL-020 | `PromoteToLive` is the only writer of `STRATEGY_MODE_LIVE`. It MUST require owner + passkey, confirmation equal to the strategy name, current mode `paper`, a passing graduation report, the live account in the trading stage, and no other strategy already live. `SetStrategyMode`, registry seed, and `RegisterStrategy` MUST NOT set `live`. | Implemented | `PromoteToLive`, `SetMode`, `RegisterStrategy`; `TestGL020…`, `TestLiveGate` |
| GL-021 | The capital cap MUST be between 10% and 20% of the intended allocation (inclusive) and becomes the ledger allocation on the live account. | Implemented | `PromoteToLive`; `TestGL020…` |
| GL-022 | Promotion MUST register a paper twin on the paper account (same horizon and code, mode `paper`) unless `skip_paper_twin` is set. The twin is the divergence reference and is not itself promotable as a second live strategy while one is live. | Implemented | `paperTwin`; `TestGL020…` |
| GL-023 | Promotion MUST install a live limit profile at least as tight as: half the strategy's order notional, daily loss, and drawdown; half the position count (at least 1); price collar ≤ 1%; at most 10 orders per minute. Overrides that loosen it MUST be rejected. Regular hours stay the market-session check (manual orders are the only extended-hours path). | Implemented | `liveProfile` |
| GL-030 | `DemoteFromLive` needs no step-up. It MUST be callable by `owner` (any device), `service:monitor`, `service:execution`, and `cli:*`, and MUST be denied to the analyst. It cancels the strategy's open orders, optionally flattens strategy-owned quantity (`hold` or `flatten`), and returns the strategy to its paper account in mode `paper`. It MUST NOT flatten manual holdings. | Implemented | `DemoteFromLive`; `TestGL020…`, `TestLiveGate` |
| GL-031 | `DisableLiveAccount` needs no step-up (same actors as GL-030 except it is gateway-only for the monitor: the monitor demotes via `DemoteStrategy` / `DemoteFromLive`). It sets enablement off, engages an account `cancel_open` kill switch (platform orders only), and demotes every live strategy on the account with policy `hold`. | Implemented | `DisableLiveAccount` |
| GL-040 | A capital-cap increase is one ladder step: greater than the current cap, at most 2×, never above the intended allocation, only after the graduation report still passes and covers at least 10 sessions since the step started, with owner passkey and typed confirmation. | Implemented | `RaiseLiveCap` |
| GL-050 | Once go-live wiring is loaded (`SetLiveSources`), config, `SetStrategyMode`, and a stored mode of `live` without the flag, `orders_enabled`, a promotion timestamp, and a positive cap MUST NOT stay live. The next execution tick demotes that strategy and raises a critical alert. A process that has not loaded the go-live store does not rewrite registry modes (containment tests persist a simulated live mode and demote it themselves). | Implemented | `guardLive`; `TestGL020…`, `TestLiveGate`, `TestRM018DemoteLiveStrategyToPaperThroughLatchedKill` |
| GL-051 | Actor names `assistant` or containing `analyst` MUST be denied on enable, promote, raise, demote, and disable. The analyst has no allowlisted RPC for these methods. | Implemented | `notAnalyst`; `TestGL020…`, `TestLiveGate` |
| GL-052 | A paper-environment account MUST reject an order whose mode is `live` before it is sent (the account decrypts only the paper credential). A live-environment account MUST reject an order whose mode is not `live`. | Implemented | adapter `modeGate`, IBKR `_reject_mode`; `TestGL052…`, `TestLiveGate` |

## Invariants

- GL-I1: `strategies.mode = live` implies `TB_TRADING_LIVE_ENABLED`, an `orders_enabled` enablement row with `audit_seq > 0` for that account, `live_promoted_at > 0`, and `live_capital_cap > 0`. `guardLive` restores this within one tick.
- GL-I2: At most one strategy is in mode `live` (first go-live). A second `PromoteToLive` is rejected.

## Edge cases

- Flag true and no enablement row: the live adapter stays stopped (BR-013) and promotion is rejected.
- Readiness item id that is automated or unknown: `SetReadinessItem` rejects it. Marking an item `failed` keeps the record incomplete.
- Confirmation typo: enable and promote reject before any write.
- Cap of 9% or 21% of intended: rejected. 10% and 20% are accepted.
- Promote of an already-live strategy on the same account returns the current strategy.
- Demote of a strategy that is not live is a no-op success.
- A hand-edited strategy payload with mode `live` and no promotion timestamp is demoted on the next tick.
- Paper twin short id: the paper short id plus `p`, truncated to 8 characters.
- Optimizer or monitor down: the matching criteria fail and promotion is refused.
- Disable while orders are in flight: the account kill switch cancels platform orders and does not touch manual orders (RM-006, VP-006).
