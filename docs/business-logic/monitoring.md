# Monitoring and alerting

**Prefix:** `MN-`  ·  **Owners:** `monitor`, Prometheus/Alertmanager (`infra/observability`), every service (metrics, bus alerts)  ·  **Related:** [risk](risk-management.md), [virtual portfolios](virtual-portfolios-and-attribution.md), [operations](operations.md), [ADR-0014](../adr/0014-observability-stack.md), [ADR-0027](../adr/0027-monitoring-ops-slice.md) (D45)

## Scope

Phase 2 ops slice of section 13: health and trading-operations alert rules, alert routing (severities, grouping, dedupe, escalation, quiet hours), the pre-market checklist and end-of-day summary, the trading dashboard, and the monitoring pipeline's own dead-man. Phase 4 adds the analyst feedback slice (MN-020..MN-026, [ADR-0044](../adr/0044-live-feedback-and-retraining-triggers.md)): live feedback reports, retraining triggers, and autonomy lowering. Full drift and divergence dashboards and automatic demotion are Planned (Phase 5).

## Concepts

- **Two alert sources:** Prometheus rules on service metrics, and bus alerts (`alert.<severity>`) that services publish for events (breaks, halts, kill switches, dead-man). The monitor forwards bus alerts to Alertmanager, so one router handles both.
- **Severities:** `info` (log only), `warning` (Pushover normal priority outside quiet hours), `critical` (Pushover emergency priority, repeats every 2 minutes until acknowledged, re-notified every 30 minutes while firing). `kind=report` alerts carry the daily reports (Pushover low priority).

## Rules

| ID | Rule | Status | Code / tests |
|---|---|---|---|
| MN-001 | Every alert rule MUST have a summary, a severity (`info`/`warning`/`critical`), and a `runbook_url` pointing at an existing runbook. | Implemented | `tests/python/test_alert_rules.py` |
| MN-002 | Every bus alert MUST be forwarded to Alertmanager with its severity, source, `dedupe_key` (as a label, so repeats group), runbook, and correlation ID; a delivery failure MUST be retried through the bus (durable consumer, nak with delay), never dropped. | Implemented | `tb_monitor/alerts.py`, `__main__.py`; `test_bus_alert_maps_to_alertmanager_with_runbook_and_dedupe`, `test_post_delivers_to_alertmanager_api` |
| MN-003 | Quiet hours (22:00–07:00 New York) MUST mute `warning` notifications only; `critical` alerts are never muted. | Implemented | `infra/observability/alertmanager/alertmanager.yml` |
| MN-004 | Phase 2 trading alert rules: reconciliation broken (critical, 1 min) or stale (warning, 5 min), open breaks (warning), kill switch latched (warning), halts (warning), unknown orders (critical, 2 min), stuck orders (warning, 5 min), strategy-runtime heartbeat lost (critical), trading disabled during market hours (warning), reject spike (warning), daily loss ≥ 80 % (warning) and ≥ 100 % (critical); broker session not ready (critical, 5 min) and service down (critical, 30 s) from earlier phases. | Implemented | `infra/observability/prometheus/rules/trading.yml` |
| MN-005 | The pre-market checklist (15 minutes before the open) MUST report each broker account's session and reconciliation, latched kill switches, halts, clock skew, and the strategies in paper/live, and fail closed when execution's heartbeat was not seen. | Implemented | `tb_monitor/reports.py` `premarket`; `test_premarket_checklist_passes_and_fails_closed` |
| MN-006 | The end-of-day summary (10 minutes after the close) MUST report per account fills, fees, reconciliation status, and open breaks; per owner realized P&L for the day; per strategy approved intents and rejects by first failed check; and alerts by severity. | Implemented | `reports.py` `eod`; `test_eod_summary_per_account_and_strategy` |
| MN-007 | The monitoring pipeline's own liveness MUST be watched: `ServiceDown` for the monitor container and `MonitorPipelineDown` when it has not forwarded an alert or seen an execution heartbeat for 15 minutes. | Implemented | `trading.yml`, `stack.yml` |
| MN-020 | For every configured strategy in `shadow`, `paper`, or `live`, the monitor MUST build a `LiveFeedbackReport` daily `TB_MONITOR_FEEDBACK_DELAY_MINUTES` (default 20) after the close and on demand: live metrics with the backtest's p10/p50/p90 and the live percentile, parity, decay test, top-N feature drift, prediction drift, calibration, error slices, regime coverage, execution quality. | Implemented | `services/monitor/src/tb_monitor/feedback.py` `build_report`, `feedback_runner.py`; `test_mn020_*`, `test_mn022_*` |
| MN-021 | A `LiveFeedbackReport` MUST NOT carry balances, positions, quantities, currency P&L, or account identifiers; returns are per-trade basis points. | Implemented | `feedback.py` `round_trips`; `contracts/tests` (no account fields), `test_mn021_*` |
| MN-022 | Decay MUST be tested with a one-sided lower CUSUM on standardized per-trade returns against the backtest expectation (`cusum_k` 0.5, `cusum_h` 5); drift with PSI (reference-quantile bins) and KS; calibration with Brier and ECE (10 bins). | Implemented | `feedback.py`; `test_statistics_sanity` |
| MN-023 | A retraining trigger MUST fire (publish `RetrainSuggested` on `monitor.retrain_suggested.<lineage>` plus `monitor.decay|drift|divergence.<lineage>`) on: decay breach; PSI > 0.25 on a top-5 importance feature; ECE > 0.1; the last 5 sessions or the mean trade below the backtest p10; trade match rate < 0.9 or slippage gap > 5 bps; current regime below 5 % of training. Critical when decay and below-p10 fire together or three or more conditions fire. | Implemented | `feedback.py` `build_report`, `retrain_suggested`, `subjects_for`; `test_mn023_*` |
| MN-024 | No trigger MUST fire with fewer than `TB_MONITOR_FEEDBACK_MIN_TRADES` (30) trades in the window, or fewer than `TB_MONITOR_FEEDBACK_MIN_RESOLVED` (100) resolved predictions when the strategy has predictions; the report is still published with `sample_sufficient = false`. | Implemented | `feedback.py`; `test_mn024_below_minimum_samples_no_trigger_fires`, `test_mn025_injected_decay_publishes_trigger_and_lowers_autonomy` |
| MN-025 | On a critical feedback condition, or a critical bus alert whose subject, dedupe key, or message names a configured strategy, the monitor MUST publish `analyst.autonomy.lower` (level 0, paused, actor `monitor`) for its lineage once per evidence ID, plus a critical alert with the analyst runbook. Feedback never replaces containment (section 07, Phase 5 auto-demotion). | Implemented | `feedback_runner.py` `lower`, `on_alert`; `test_mn025_*` |
| MN-026 | Phase 4 expectations come from `TB_MONITOR_FEEDBACK_CONFIG` (distributions or backtest per-trade returns to bootstrap); feature, prediction, and calibration inputs are used when section 10 live inference stores predictions. | Implemented (config); prediction inputs Planned | `feedback_runner.py` `load_config` |

## Edge cases

| Scenario | Expected behavior | Rules |
|---|---|---|
| broker-gateway stopped during market hours | `ServiceDown` (30 s) → Pushover emergency within about 45 s; execution pages "broker session not ready" after 10 s with strategies active | MN-004, RM-009 |
| Encroachment break | Ledger publishes `alert.critical` with the reconciliation runbook; Alertmanager pages; `LedgerReconciliationBroken` follows after 1 min | MN-002, VP-016 |
| Alertmanager down | Bus alerts stay unacknowledged and are retried every 10 s; critical risk paths still block trading | MN-002 |
| Same break re-alerted | Same `dedupe_key` label → one notification group | MN-002 |
| Warning at 23:00 | Muted until 07:00; visible in Alertmanager and Grafana | MN-003 |
| Paper strategy decays over 12 sessions (40 trades) | Report shows the CUSUM breach and below-p10; critical `RetrainSuggested`; analyst opens a live-feedback round at level 0 and the loop is paused | MN-023, MN-025, AN-013 |
| Decay visible after 3 sessions (12 trades) | Report published with `sample_sufficient = false`; no trigger | MN-024 |

## Open questions

- Web Push to the PWA and an independent fallback channel (Phase 5, D13).
- Strategy health score, drift and divergence dashboards, auto-demotion (Phase 5); expectations served from section 11 gate reports instead of a config file.
