---
title: "Section 13 – Monitoring and Alerting"
phase: "2 (ops slice), 4 (analyst feedback slice), 5 (full)"
depends_on: [00, 01, 04, 05, 06, 07, 08, 10, 11, 12]
updated: 2026-10-17
---

# Section 13: Monitoring and Alerting

## Role

You are an SRE and model-risk engineer combined. You watch two things: whether the system is healthy, and whether the strategies are still doing what their backtests said they would. You make problems loud, actionable, and, where safe, automatically contained.

## Context

The `monitor` service plus the observability stack (section 01) cover: system health, trading operations (orders, fills, rejects, reconciliation, kill switches), strategy performance, live-vs-backtest divergence, data and model drift, and alert routing to the owner's devices. It can demote strategies automatically (to `paper` or `halted`) through section 07; it never promotes. It also feeds live performance back to the AI Research Analyst (section 12) so retraining is driven by how models actually behave in shadow, paper, and live trading. It is built in three slices: an **ops slice** in Phase 2 (health, trading ops, reconciliation, P&L, basic alerts), an **analyst feedback slice** in Phase 4 (live feedback reports and retraining triggers for shadow/paper strategies), and the **full** version in Phase 5 (complete drift, divergence, decay, auto-demotion). Non-negotiables N4 and N5 apply.

## Scope

In scope:
- Metrics, dashboards, and alert rules for services, brokers, data feeds, order path, ledger, and strategies.
- Strategy performance monitoring: P&L, drawdown, exposure, hit rate, slippage vs model, per strategy and per account.
- Live-vs-backtest divergence (via section 08's parity harness) and expected-vs-realized distribution tests.
- Data drift (feature distributions), prediction drift, calibration drift, performance decay detection.
- Alert routing, deduplication, escalation, quiet hours, acknowledgments.
- Automatic containment actions (demotion, kill switch triggers) with policies.
- Daily reports (pre-market checklist, end-of-day summary).
- Live feedback to the AI Research Analyst: `LiveFeedbackReport` per strategy and retraining-trigger events.

Out of scope:
- Kill switch mechanics (section 07), reconciliation logic (section 06), UI rendering (section 14, which consumes this section's APIs).
- Deciding how to retrain (section 12 analyst, section 11 policies); this section supplies evidence and triggers.

## Inputs and dependencies

- Section 01: Prometheus, Grafana, Loki, Tempo, Alertmanager.
- Sections 04, 05, 06, 07: health, feed status, reconciliation results, order events, risk decisions.
- Section 08: `compare_live_vs_backtest`, backtest distributions for expected ranges.
- Section 10: stored live predictions and feature vectors; training-time reference distributions.
- Section 11: registry stages and retraining triggers (this section emits drift/decay events they consume).
- Section 12: AI Research Analyst consumes `LiveFeedbackReport` and retraining triggers; this section can lower a lineage's analyst autonomy on alarms.
- `INPUT PENDING (U6)`: mobile scope affects alert actions available from the phone (default: acknowledge, view, kill switch).

## Decisions pending

| Decision | Default | Marker |
|---|---|---|
| Alert channels | Web Push to the PWA (primary) + one fallback channel (email or a messaging app such as Telegram/Slack/ntfy) | `DECISION PENDING (research §8, §9)` |
| Drift metrics | PSI and KS per feature; prediction distribution PSI; calibration (Brier/reliability) on resolved labels | `DECISION PENDING (research §9)` |
| Decay detection | CUSUM / sequential tests on strategy returns vs backtest-expected distribution | `DECISION PENDING (research §9)` |
| Stack | Prometheus/Grafana/Loki/Tempo + Python `monitor` service for model metrics | D22 |

## Interfaces and contracts to expose

- Bus: consumes `ord.*`, `exec.*`, `ledger.update.*`, `recon.result.*`, `md.status`, `sys.health.*`, `sig.*`; produces `alert.<severity>`, `monitor.strategy.<id>` (health score), `monitor.drift.<id>`, `monitor.decay.<id>`, `monitor.divergence.<id>`, `monitor.retrain_suggested.<id>` (with reason and evidence report IDs), `risk.kill` (automatic triggers per policy).
- gRPC/REST `Monitor` (via gateway): `GetStrategyHealth(strategy_id)`, `GetDriftReport(strategy_id, window)`, `GetDivergenceReport(strategy_id, window)`, `ListAlerts(filter)`, `AckAlert(id)`, `SilenceAlert(rule, duration, actor)`, `GetDailyReport(date)`, `SetContainmentPolicy(strategy_id, policy, actor)` (loosening requires step-up), `GetLiveFeedback(strategy_id, window) -> LiveFeedbackReport` (also exposed as an analyst tool, read-only).
- `Alert` contract from the master prompt with `severity ∈ {info, warning, critical, page}`, `dedupe_key`, `runbook_url`, and suggested actions.
- `LiveFeedbackReport` (contract): strategy and model version, mode (shadow/paper/live), window, live metrics vs the backtest's expected distribution (percentile of each metric), parity summary (trade match rate, missed/extra trades, slippage vs modeled), decay test statistic, feature drift top-N (PSI/KS vs training reference), prediction drift, calibration on resolved labels, error slices on live predictions (by regime, time of day, symbol), current regime vs regimes seen in training, execution quality, and a list of triggered conditions. Aggregated and size-limited for LLM use; no account balances or identifiers.
- Grafana dashboards (provisioned as code): Stack overview, Brokers & feeds, Order path, Ledger & reconciliation, Strategy overview, Strategy detail, Model drift, Data quality.

## Functional requirements

### Ops slice (Phase 2)
- [ ] Metrics from every service; dashboards listed above (strategy and drift dashboards may start minimal).
- [ ] Alert rules: broker session not `ready` during market hours, IBKR re-auth needed, feed stale, bar latency over target, order rejects spike, orders stuck in non-terminal states, reconciliation `broken`, kill switch engaged, daily loss approaching/at limit, clock skew, disk/memory, backup failure, security events from section 02.
- [ ] Alert routing with severity-based channels, dedupe, grouping, escalation (critical unacknowledged for N minutes ⇒ re-notify via fallback), quiet hours that never suppress `critical`/`page`.
- [ ] Pre-market checklist report (brokers ready, recon clean, data fresh, models loaded, limits loaded, scheduled macro/earnings events for held symbols) pushed before the open.
- [ ] End-of-day summary per account and strategy: P&L, fees, trades, rejects, slippage, breaks, alerts.

### Analyst feedback slice (Phase 4)
- [ ] For every strategy in `shadow`, `paper`, or `live`, compute a daily (and on-demand) `LiveFeedbackReport` from stored live predictions, signals, fills, and the parity harness.
- [ ] Retraining triggers with documented thresholds (config): decay test breach, feature drift above threshold on top-importance features, calibration error above threshold, live metric below the backtest's 10th percentile for N sessions, divergence above tolerance, current regime not represented in training data. Each trigger emits `monitor.retrain_suggested` (and `monitor.decay`/`monitor.drift`/`monitor.divergence` as applicable) with evidence report IDs.
- [ ] Triggers route to the analyst loop (section 12) for the lineage, and to section 11's default retraining policy when no analyst loop is active.
- [ ] Minimum-sample rules: no trigger fires on fewer than a configured number of resolved predictions or trades, to avoid retraining on noise.
- [ ] Containment stays here and in section 07: feedback to the analyst never replaces automatic demotion. On any critical alarm for a lineage, lower its analyst autonomy to level 0 and pause its loop pending owner review.

### Full monitoring (Phase 5)
- [ ] **Performance**: rolling P&L, Sharpe, drawdown, hit rate, payoff, turnover per strategy vs the backtest's expected distribution (percentile bands from walk-forward/CPCV paths).
- [ ] **Divergence**: daily parity run (section 08) per live/paper strategy; metrics: trade match rate, missed/extra trades, slippage vs modeled, P&L gap; thresholds per strategy.
- [ ] **Execution quality**: implementation shortfall, fill rate for limit orders, latency decision→submit→fill.
- [ ] **Data drift**: PSI/KS of live feature distributions vs training reference windows; missing-feature rates; online/offline parity spot checks.
- [ ] **Prediction drift and calibration**: prediction distribution shift; calibration on resolved labels; signal frequency anomalies.
- [ ] **Decay detection**: sequential test on live returns vs expected; emits `monitor.decay` events for section 11 retraining policies and the section 12 analyst.
- [ ] **Containment policies** per strategy (configurable, with safe defaults): warning ⇒ alert; breach ⇒ demote to `paper` (live strategies) or `halted`; severe (e.g., divergence + drawdown beyond the backtest's 99th percentile) ⇒ strategy kill switch with `cancel_open` and optional `flatten_strategy_owned`. Every automatic action is audited and alerted with the evidence.
- [ ] Strategy health score summarizing the above for UI and mobile.

## Non-functional requirements

- **Timeliness**: critical alerts delivered to the phone within 60 s of the condition; automatic containment within 10 s of a breach decision.
- **Reliability**: alerting must work when the UI or gateway is down (Alertmanager and the fallback channel run independently); a dead-man alert fires if the monitoring pipeline itself stops (heartbeat to an external check, or a watchdog on a second device).
- **Noise control**: track alert volume; every alert has a runbook link and an owner action; tune thresholds to avoid alert fatigue.
- **Security**: alert payloads avoid sensitive values on lock screens (configurable); silencing and loosening policies require auth/step-up and are audited.
- **Tests**: `LiveFeedbackReport` golden tests on recorded paper sessions; trigger threshold and minimum-sample tests; alert rule unit tests (Prometheus rule testing); synthetic drift injection tests; divergence threshold tests using recorded sessions; containment policy tests through the broker simulator.

## Acceptance criteria

- [ ] Docs updated per `AGENTS.md` (master N6) in the same commits as the changes: `docs/file-map.md`, `docs/business-logic/monitoring.md` (metrics, thresholds, containment policies, alert routing), `docs/runbooks/` entries for every alert, `CHANGELOG.md`, `docs/glossary.md` for new terms, and, for every decision made, an ADR in `docs/adr/` plus its row updated (or added) in `docs/decisions.md`.
- [ ] Analyst feedback slice (Gate 4): an injected decay in a paper strategy's recorded session produces a `LiveFeedbackReport` and a `monitor.retrain_suggested` event that opens an analyst round; below the minimum sample size no trigger fires; a critical alarm drops the lineage's analyst autonomy to level 0.
- [ ] Ops slice (Gate 2): stopping the broker gateway during simulated market hours produces a critical alert on the phone within 60 s; reconciliation break alert fires and links to the runbook; EOD summary delivered.
- [ ] Full (Gate 5): injected feature drift on a paper strategy raises a drift alert; injected divergence beyond threshold demotes a strategy from `paper` to `halted` (and from `live` to `paper` in a simulated live account) with an audited reason.
- [ ] Dead-man alert fires when the monitor service is stopped.
- [ ] Every alert rule has a runbook entry.

## Pitfalls to avoid

- Dashboards without alerts, or alerts without runbooks.
- Comparing live performance to the single backtest equity curve instead of to the distribution of plausible outcomes.
- Alerting on raw P&L noise; use statistically grounded thresholds and minimum sample sizes.
- Automatic actions that can increase risk (auto-promotion, auto-unhalt, automatic position flips). Containment only reduces risk.
- Monitoring that depends on the same process it monitors; keep an independent heartbeat path.
- Drift references taken from the wrong window (e.g., including the test period).
