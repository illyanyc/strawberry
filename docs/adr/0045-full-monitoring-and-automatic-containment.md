# ADR-0045: Full monitoring, health score, and automatic containment (section 13 Phase 5)

- **Status:** Accepted
- **Date:** 2026-10-18
- **Decision register:** D63
- **Related:** MN-030..MN-046, RM-018, SM-006, ML-042, ML-043; ADR-0027 (ops slice, D45), ADR-0044 (feedback slice, D62), ADR-0046 (recorder), ADR-0047 (deployment limits); research [03 §1](../research/03-optimization-and-ai-agent.md), [06 §6](../research/06-architecture-and-stack.md)

## Context

Section 13 Phase 5 asks for performance against the backtest's distribution, live-vs-backtest divergence, data and prediction drift, execution quality, a strategy health score, containment policies that demote or kill automatically, an alert inbox with acknowledgement and silencing, daily reports, and (for section 15) a paper graduation record per candidate. Gate 5 requires breaches to alert and auto-demote to paper, and the owner asked for demotion at 1.5× the backtest's 95th-percentile drawdown.

Constraints found in the code: the monitor had no API, no persistence (windows were in-memory and lost on restart), and no way to act: execution trusted only api-gateway tokens, `SetStrategyMode` cannot express "reduce risk only", and the kill action `demote_to_paper` only moves live → paper. Nothing published live model predictions or feature vectors, so drift had no inputs. Drawdown comparisons had only a single-path backtest maximum.

## Decision

1. **Monitor API and state.** The monitor serves `tb.monitor.v1.MonitorService` over gRPC (`monitor:9090`, `tbcore.rpc`, service tokens; callers api-gateway, and assistant for `GetLiveFeedback`). Policies, containment actions, reports, the alert inbox, and daily reports are protobuf documents in a new Postgres schema `monitor` (`monitor.docs`, `tbcore.docstore`) used only by `svc_monitor`; containment actions and policy changes are append-only and every change is audited through `audit.append`. History survives restarts by replaying the recorder's session files (ADR-0046).
2. **What is monitored.** Strategies in execution's registry (`ListStrategies`) whose mode is shadow, paper, live, or halted, joined with their expectation: the `monitoring` block of the optimizer's research deployment entry (ADR-0047) or, for hand-written strategies such as the ORB baseline, the `feedback.json` entry. A strategy without an expectation is listed with `INSUFFICIENT_DATA` and is never acted on.
3. **Inputs.** Executions (`exec.>`), intents (`ord.intent`: `decision_time`, `reference_price`), OMS orders (`ord.order.>`: `submitted_at`, limit fills), final bars (`md.bar.>`, to mark open lots), reconciliation results, alerts, and two new live streams from strategy-runtime: `Signal` on `sig.<strategy_id>` (score, model version, feature snapshot ID; ML-042) and `FeatureVector` on `feat.<feature_set>.<instrument_id>`. Training references come from the training run's reference sample (training window only, never test or holdout) and its importance ranks.
4. **Statistics.**
   - *Expected distributions* are horizon-matched: a stationary block bootstrap (mean block 5 sessions, 2,000 paths, fixed seed) of the backtest's per-session returns over the same number of sessions the strategy has traded live. Bands report p05/p10/p50/p90/p95/p99 and the live percentile for return, per-session Sharpe, max drawdown, hit rate, payoff, and turnover (MN-030).
   - *Drawdown* is equity (allocation + realized − fees + open lots marked at the last final bar) below its high-water mark, as % of allocation, compared with the bootstrap p95/p99 of the max drawdown over the live horizon (MN-032).
   - *Drift* uses PSI with 10 reference-quantile bins and KS per feature (top-k by training importance), prediction PSI on live scores vs out-of-fold predictions, missing-feature rate, signal frequency, and a rolling IC (MN-038).
   - *Divergence* is the daily parity run (`tbcore.parity.compare_recording`) on the session's recording: match rate, missed/extra intents, slippage vs modeled, P&L gap in bps of allocation (MN-037).
   - *Implementation shortfall* per fill is side-signed `(fill − decision price) / decision price` in bps plus fees, with the decision price taken from the intent's `reference_price`; latency decision → submit → fill; limit fill rate (MN-033).
5. **Health score.** Each component (drawdown, performance, divergence, feature drift, prediction drift, execution quality, decay) has a status and a 0–100 score, piecewise linear: 100 at half the warning threshold, 60 at the warning threshold, 20 at the breach threshold, 0 at 1.5× the breach threshold. The strategy's score is the minimum and its status the worst acting component. Components below their minimum sample are `INSUFFICIENT_DATA` and never act (MN-031).
6. **Containment policy** per strategy with an inherited default (MN-034): drawdown warning at 1.0× and breach at **1.5× the backtest p95**; match rate 0.9 / 0.8; slippage gap 5 / 15 bps; feature PSI 0.25 / 0.5 on the top 5 features; prediction PSI 0.25 / 0.5; shortfall 1.5× / 2.0× modeled with at least 5 bps excess; breach action `demote`; severe (divergence breach together with drawdown beyond the backtest p99) → `kill_cancel_open`. Tightening needs no step-up; loosening (a more permissive threshold, a weaker action, larger minimum samples, or disabling) needs a passkey step-up and is audited (MN-036).
7. **Acting.** Breaches call the new `ExecutionService.DemoteStrategy` with the monitor's own service token (execution now trusts `monitor` for `DemoteStrategy` and `TriggerKillSwitch` only). Execution enforces risk reduction at its boundary (RM-018): live → paper through a latched strategy kill switch `demote_to_paper`; shadow/paper → halted with the strategy's open non-protective orders canceled; halted/disabled unchanged. Severe actions add a latched strategy kill switch (`cancel_open`, or `flatten_strategy_owned` if the policy says so; manual holdings are never touched). Each action is recorded before it is sent, audited, alerted as critical with the evidence and the containment runbook, published on `monitor.containment.<strategy>`, idempotent per (strategy, evidence), and retried with a page if execution cannot be reached. The monitor never promotes, unhalts, or releases (MN-035). The containment decision loop runs every 15 s, so an action follows a breach decision well within the 10 s target from the decision itself.
8. **Alerts.** Alertmanager stays the only router and Pushover sender (D45). The monitor keeps an inbox per dedupe key from bus alerts plus Alertmanager's active alerts (Prometheus rules), records acknowledgements (audited, no step-up), and creates Alertmanager silences through its API for at most 24 h (passkey step-up, audited) (MN-041, MN-042).
9. **Graduation evidence** (MN-040): per strategy, computed only from platform-recorded shadow/paper results: sessions, trades, return, Sharpe, max drawdown vs the horizon-matched p95, divergence history, shortfall vs modeled, reconciliation breaks, critical alerts, and section 15's default criteria marked pass/fail (DSR on the combined record stays `evidence_only` for section 15).
10. **Outputs.** `monitor.strategy.<id>` (health), `monitor.report.<kind>.<id>`, `monitor.containment.<id>`; Prometheus gauges per strategy for the Strategy overview and Model drift dashboards.

## Alternatives considered

| Option | Why not |
|---|---|
| Monitor publishes `risk.kill` on the bus | Bus kill events are fire-and-forget for the monitor (no confirmation, no from/to mode), the subject belongs to execution's outbox, and `demote_to_paper` cannot halt a paper strategy |
| Let the monitor call `SetStrategyMode` | That RPC can raise risk (halted → paper); the "reduce only" rule would live in the caller instead of at the execution boundary |
| Compare live drawdown with the backtest's single max drawdown | Section 13 pitfall; max drawdown grows with the horizon, so a fixed number is too strict early and too loose late |
| Prometheus rules for containment | Thresholds depend on per-strategy bootstrapped distributions and policies; Prometheus keeps the dead-man and service alerts |
| Monitor sends Pushover directly with receipts | Duplicates Alertmanager's routing and would make paging depend on the monitor; Alertmanager already repeats emergencies until acknowledged |
| Files instead of a Postgres schema | No audit linkage, no append-only guarantee, and a second persistence pattern next to `tbcore.docstore` |

## Consequences

- Positive: one reproducible definition of every monitored statistic; containment that can only reduce risk and is enforced in execution; reports and actions survive restarts; section 15 has a ready evidence feed; the UI has a single API.
- Negative / risks: the monitor now holds a service key and can halt strategies (scoped by the execution allowlist and the reduce-only rule); a mis-specified expectation can demote a healthy strategy (re-promotion is manual, and the evidence is attached to every action); bootstrap bands assume the backtest sessions are representative.
- Follow-up: `make secrets` (new `svc_monitor` key and Postgres password, NATS permissions), `make migrate` (schema `monitor`); section 15 consumes `GetGraduationEvidence`.

## Validation

Unit tests per statistic and policy rule (`services/monitor/tests/test_containment.py`, `test_analytics.py`, `test_service.py`), Go tests for `DemoteStrategy` through the broker simulator (`TestRM018*`), and the Gate 5 harness (`make monitoring-gate`): injected feature drift on a paper strategy raises a drift alert; injected divergence demotes paper → halted and live → paper in a simulated live account with audited reasons; a 1.5× p95 drawdown demotes; manual holdings untouched. Revisit the defaults after the first 20 paper sessions of a model strategy.
