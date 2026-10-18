# ADR-0027: Monitoring ops slice: one alert router, bus alerts bridged by the monitor, daily reports as alerts

- **Status:** Accepted
- **Date:** 2026-10-18
- **Decision register:** D45 (refines D22 and D13 for Phase 2)
- **Related:** MN-001..MN-007, RM-009, RM-017, VP-008, [ADR-0014](0014-observability-stack.md), section 13 prompt (ops slice, "Alert channels" decision)

## Context

Phase 2 adds event-type alerts (breaks, halts, kill switches, dead-man) that services publish on the bus, next to metric alerts in Prometheus. Section 13 asks for severity routing, dedupe, grouping, escalation, quiet hours that never suppress critical alerts, a pre-market checklist, and an end-of-day summary, with alerting that keeps working when the UI or gateway is down. Alertmanager with Pushover is already the D22/D13 path.

## Decision

- **Alertmanager is the only router.** The Python `monitor` consumes `alert.>` (durable, acked only after delivery) and posts each alert to Alertmanager's v2 API with `dedupe_key` as a label; critical → Pushover emergency (repeat until acknowledged), warning → Pushover normal priority muted during quiet hours (22:00–07:00 New York), info → log only.
- **Daily reports are alerts** with `kind=report` (Pushover low priority) built by the monitor from bus events only (execution's `TradingStatus` heartbeat, `recon.result`, `ord.decision`, `exec`, `ledger.update`), and also served on the monitor's internal `/reports`.
- **Trading rules in Prometheus** (`rules/trading.yml`) on execution and ledger metrics; every rule carries a runbook (tested).
- **One Grafana dashboard** "Trading: order path, ledger and reconciliation, strategies" for the Phase 2 metrics.

## Alternatives considered

| Option | Why not |
|---|---|
| Monitor sends to Pushover directly | Duplicates grouping/dedupe/escalation logic; the monitor runs on the internal network without egress |
| Web Push as the primary channel now | Planned with the PWA push work (Phase 5); Pushover emergency priority already meets the 60 s critical target |
| Prometheus-only alerting | Event alerts (encroachment, orphan order, dead-man) are not naturally metrics and need their runbook and correlation ID |

## Consequences

- Positive: one routing config; alerts survive gateway/UI outages; reports reuse the same delivery path.
- Negative / risks: if Alertmanager is down, bus alerts wait in the stream (retried); Pushover placeholders mean notifications fail until the owner fills the keys (logged).
- Follow-up: drift/divergence/decay and automatic containment (Phase 5), Web Push, the analyst feedback slice (Phase 4).

## Validation

`services/monitor/tests`, `tests/python/test_alert_rules.py`, and the existing CI step that kills a core container and waits for `ServiceDown`.
