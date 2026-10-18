# monitor

Phase 2 ops slice of section 13 ([ADR-0027](../../docs/adr/0027-monitoring-ops-slice.md), D45; rules MN-001..MN-007 in [`monitoring.md`](../../docs/business-logic/monitoring.md)): forwards bus alerts to Alertmanager and builds the pre-market checklist and end-of-day summary. Drift, divergence, decay, and auto-demotion arrive in Phases 4–5.

**Status:** Phase 2 (ops slice)  ·  **Language/stack:** Python 3.12 on `tbcore`, `nats-py` ([ADR-0004](../../docs/adr/0004-core-language-split.md), D1)

## Responsibilities
- Owns: bus → Alertmanager bridge, daily reports, `tb_monitor_last_forward_seconds`.
- Does not own: alert routing (Alertmanager config), kill switches (execution), reconciliation (ledger).

## Interfaces
| Direction | Kind | Name | Contract |
|---|---|---|---|
| in | NATS | `alert.>` (durable `monitor-alerts`); `sys.health.execution`; `recon.result.>`, `ord.decision`, `exec.>`, `ledger.update.>` (ordered, new only) | `tb.alerts.v1.Alert`, `tb.orders.v1.TradingStatus`, `ReconciliationResult`, `RiskDecision`, `ExecutionReport`, `LedgerUpdate` |
| out | HTTP | Alertmanager `POST /api/v2/alerts` | Alertmanager v2 |
| in | HTTP (internal) | ops `:8081` (`/healthz`, `/readyz`, `/metrics`, `/reports`) | OP-010 |

## Layout
| Path | Purpose |
|---|---|
| `src/tb_monitor/alerts.py` | Bus alert → Alertmanager conversion, report alerts, HTTP post |
| `src/tb_monitor/reports.py` | Pre-market checklist and end-of-day summary (pure) |
| `src/tb_monitor/__main__.py` | NATS loop, scheduler, ops listener |
| `tests/` | MN-002, MN-005, MN-006 tests |

## Configuration
| Variable | Default | Notes |
|---|---|---|
| `TB_NATS_URL` / `TB_NATS_USER` | `nats://nats:4222` / `monitor` | Password from secret `nats_monitor_password` |
| `TB_ALERTMANAGER_URL` | `http://alertmanager:9093` | observability profile |
| `TB_PREMARKET_LEAD_MINUTES` / `TB_EOD_DELAY_MINUTES` | `15` / `10` | |

## Run and test
- Run: `make up`. Test: `uv run pytest services/monitor/tests tests/python/test_alert_rules.py`.

## Gotchas
- Every JetStream subscribe passes `stream=`: the NATS user (least privilege) may not call `$JS.API.STREAM.NAMES`, and nats-py looks the stream up when it is missing, which times out after 5 s and kills startup. Tested by `test_every_jetstream_subscription_names_its_stream`.
- Without the observability profile, bus alerts are retried until Alertmanager is reachable (by design, MN-002).
