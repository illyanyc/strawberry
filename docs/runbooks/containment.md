# Runbook: automatic containment and demotions

Rules RM-018, SM-006 ([risk](../business-logic/risk-management.md), [strategies](../business-logic/strategies-and-model-lifecycle.md)), MN-031..MN-039 ([monitoring](../business-logic/monitoring.md)); [ADR-0045](../adr/0045-full-monitoring-and-automatic-containment.md).

## What happened

The monitor found a breach of the strategy's containment policy and asked execution to reduce risk. Alerts you can get:

| Alert | Meaning |
|---|---|
| `strategy demoted` (from execution) | A shadow or paper strategy is now `halted`; its working entry orders were canceled; open positions keep their broker-side stops |
| `kill switch engaged` with reason `containment: …` | A live strategy was moved to `paper` through a latched strategy kill switch, or a severe breach latched `cancel_open` / `flatten_strategy_owned` |
| `Containment action failed` (from the monitor) | The monitor decided to act but execution was unreachable or refused; it retries every cycle. **Act now:** engage the strategy kill switch by hand (runbook [kill switches](kill-switch.md)) |
| `Strategy health warning` | A component crossed its warning threshold; no action was taken |

The alert message names the component (drawdown, divergence, feature drift, prediction drift, execution quality) with the value, the threshold, and the evidence report ID.

## Triage

1. UI → **Monitoring** → the strategy: read the health components and the evidence report (performance bands, drift, divergence, execution quality) and **Containment actions** for the action record.
2. Classify the cause:
   - **Drawdown beyond 1.5× the backtest p95** for this horizon: the strategy lost more than its research implied. Compare the performance bands; check fills and slippage (execution quality) and the regime.
   - **Divergence**: live trades do not match the replay. Open the divergence report: missed/extra intents point at data or runtime problems (stale bars, a restarted runtime, a different model version); slippage gaps point at execution.
   - **Feature or prediction drift**: the inputs moved away from the training distribution. Check market data health and whether the feature set changed; the analyst gets a retraining trigger.
   - **Execution quality**: implementation shortfall above 2× modeled. Check spreads, order types, and latency (decision → submit → fill).
3. Check that manual holdings are unchanged (Portfolio → per symbol owner split) and that open strategy positions still have their stops in the broker app.

## Recovery (owner only)

- Nothing is re-promoted automatically (RM-015). After the cause is fixed or understood:
  - paper/shadow strategy: UI → Strategies → set mode `paper` (or `shadow`) again;
  - live strategy: release the latched kill switch (desktop passkey); the strategy stays `paper` until it is promoted again through the go-live flow (section 15).
- If the policy is too tight for the strategy, edit it in Monitoring → Containment policy. Loosening needs a passkey and is audited; prefer fixing the expectation (a stale or wrong backtest) over loosening.
- If a severe action flattened strategy quantity, reconcile (Portfolio → Reconcile) before re-enabling.

## Verify

- The strategy's mode is what you expect in Strategies and in `GET /api/v1/monitoring/strategies/{id}`.
- Audit log: `execution.strategy.demote` (or `execution.killswitch.engage`) and `monitor.containment.*` with the same evidence ID.
- No `Containment action failed` alert remains firing.

## MonitorContainmentPending (critical)

The monitor decided an action but could not execute it (execution unreachable or refusing). It retries every 15 s and pages. Engage the strategy kill switch yourself (UI → Strategies → strategy → Kill switch, or the server CLI in [kill switches](kill-switch.md)), then find out why execution refused (`make logs SERVICE=execution`; missing `svc_monitor_ed25519.pub` after an upgrade means `make secrets` was not run).

## StrategyHealthBreach (warning)

A health component is in breach. If the policy is enabled, containment already acted (see the containment alert); if the policy is disabled, only alerts are sent.
