# Monitoring and alerting

**Prefix:** `MN-`  ·  **Owners:** `monitor`, Prometheus/Alertmanager (`infra/observability`), every service (metrics, bus alerts)  ·  **Related:** [risk](risk-management.md), [virtual portfolios](virtual-portfolios-and-attribution.md), [operations](operations.md), [ADR-0014](../adr/0014-observability-stack.md), [ADR-0027](../adr/0027-monitoring-ops-slice.md) (D45)

## Scope

Phase 2 ops slice of section 13: health and trading-operations alert rules, alert routing (severities, grouping, dedupe, escalation, quiet hours), the pre-market checklist and end-of-day summary, the trading dashboard, and the monitoring pipeline's own dead-man. Drift, divergence, decay, and automatic demotion are Planned (Phases 4–5).

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

## Edge cases

| Scenario | Expected behavior | Rules |
|---|---|---|
| broker-gateway stopped during market hours | `ServiceDown` (30 s) → Pushover emergency within about 45 s; execution pages "broker session not ready" after 10 s with strategies active | MN-004, RM-009 |
| Encroachment break | Ledger publishes `alert.critical` with the reconciliation runbook; Alertmanager pages; `LedgerReconciliationBroken` follows after 1 min | MN-002, VP-016 |
| Alertmanager down | Bus alerts stay unacknowledged and are retried every 10 s; critical risk paths still block trading | MN-002 |
| Same break re-alerted | Same `dedupe_key` label → one notification group | MN-002 |
| Warning at 23:00 | Muted until 07:00; visible in Alertmanager and Grafana | MN-003 |

## Open questions

- Web Push to the PWA and an independent fallback channel (Phase 5, D13).
- Strategy health score, drift, divergence, and decay (Phases 4–5).
