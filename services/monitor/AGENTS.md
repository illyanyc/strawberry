# monitor

Section 13 in three slices: the Phase 2 ops slice ([ADR-0027](../../docs/adr/0027-monitoring-ops-slice.md), D45; MN-001..MN-007: bus alerts → Alertmanager, pre-market checklist, end-of-day summary), the Phase 4 analyst feedback slice ([ADR-0044](../../docs/adr/0044-live-feedback-and-retraining-triggers.md), D62; MN-020..MN-026), and Phase 5 full monitoring ([ADR-0045](../../docs/adr/0045-full-monitoring-and-automatic-containment.md), D63; [ADR-0046](../../docs/adr/0046-jetstream-recorder-for-live-and-paper-streams.md), D64; MN-030..MN-046): performance bands, drawdown, drift, daily divergence, implementation shortfall, health, automatic containment, the alert inbox, graduation evidence, and the session recorder. Rules in [`monitoring.md`](../../docs/business-logic/monitoring.md).

**Status:** Phase 5 (full)  ·  **Language/stack:** Python 3.12 on `tbcore`, `nats-py`, gRPC via `tbcore.rpc` ([ADR-0004](../../docs/adr/0004-core-language-split.md), D1)

## Responsibilities
- Owns: bus → Alertmanager bridge and the alert inbox (acks, Alertmanager silences), daily reports, live feedback and retraining triggers, the session recorder, per-strategy health and reports, containment policies and actions, graduation evidence, `MonitorService`.
- Does not own: alert routing (Alertmanager config), kill-switch and mode mechanics (execution enforces reduce-only, RM-018), reconciliation (ledger), the graduation decision (section 15).

## Interfaces
| Direction | Kind | Name | Contract |
|---|---|---|---|
| in | NATS | `alert.>` (durable `monitor-alerts`); `sys.health.execution`; `recon.result.>`, `ord.decision`, `exec.>`, `ledger.update.>` (ordered, new only, reports) | `tb.alerts.v1.Alert`, `tb.orders.v1.*`, `tb.ledger.v1.*` |
| in | NATS (durable pull, `monitor-rec-*`) | recorder: `md.bar.*.*`, `md.bar.correction.>`, `feat.>`, `sig.>`, `ord.>`, `exec.>`, `risk.kill`, `ledger.update.>`, `recon.result.>` | ADR-0046 |
| out | HTTP | Alertmanager `POST /api/v2/alerts`, `GET /api/v2/alerts`, `POST /api/v2/silences` | Alertmanager v2 |
| out | NATS (JetStream, stream `MONITOR`) | `monitor.strategy.<id>`, `monitor.report.<kind>.<id>`, `monitor.containment.<id>`, `monitor.feedback.<id>`, `monitor.retrain_suggested|decay|drift|divergence.<lineage>`; `analyst.autonomy.lower`; `alert.critical|warning` | `tb.monitor.v1`, `tb.analyst.v1.LowerAutonomy`, `tb.alerts.v1.Alert` |
| out | gRPC (service token, audience `execution`) | `ExecutionService.DemoteStrategy`, `TriggerKillSwitch`, `ListStrategies` | RM-018 |
| in | gRPC `:9090` (audience `monitor`) | `tb.monitor.v1.MonitorService` (api-gateway; `GetLiveFeedback` also assistant) | `contracts/proto/tb/monitor/v1/monitor.proto` |
| out | Postgres | schema `monitor` (`monitor.docs`, append-only) as `svc_monitor`; `audit.append` | SE-036 |
| in | files | `strategies.json` (seed), research deployments and their `expectation.json`/`reference.parquet`/`oof.parquet`/`importance.json` (read-only ML artifacts), `feedback.json` | MN-046 |
| out | files | `data/recordings/<date>.tbrec` + `<date>.json` manifests | MN-045 |
| in | HTTP (internal) | ops `:8081` (`/healthz`, `/readyz`, `/metrics`, `/reports`, `/feedback`) | OP-010 |

## Layout
| Path | Purpose |
|---|---|
| `src/tb_monitor/alerts.py` | Bus alert → Alertmanager conversion, report alerts, HTTP post |
| `src/tb_monitor/reports.py` | Pre-market checklist and end-of-day summary (pure) |
| `src/tb_monitor/feedback.py`, `feedback_runner.py` | Live feedback statistics, report builder, triggers, autonomy lowering (MN-020..MN-026) |
| `src/tb_monitor/analytics.py` | Bootstrap bands, drawdown, shortfall and latency, drift, health scores (pure) |
| `src/tb_monitor/book.py` | Per-strategy live state from bus events (FIFO lots, equity, fills joined to intents/orders, signals, features) |
| `src/tb_monitor/strategies.py` | Monitored strategies, expectations, training references |
| `src/tb_monitor/containment.py` | Policies (defaults, loosening, validation), decisions, reduce-only actions through execution |
| `src/tb_monitor/divergence.py` | Daily parity on recorded sessions (`tbcore.parity`) |
| `src/tb_monitor/recorder.py` | Session recorder (file core + JetStream loop) |
| `src/tb_monitor/alertbox.py` | Alert inbox, acks, Alertmanager silences |
| `src/tb_monitor/core.py` | Orchestrator: inputs, reports, health, containment, graduation evidence |
| `src/tb_monitor/service.py` | `MonitorService` servicer and allowlist |
| `src/tb_monitor/__main__.py` | Wiring: NATS, recorder + history replay, gRPC, evaluation loop (15 s), daily runs, metrics |
| `tests/` | MN-002/005/006 (`test_monitor.py`), MN-020..026 (`test_feedback.py`), MN-030..046 (`test_monitoring.py`), gRPC boundaries (`test_monitor_service.py`) |

## Configuration
| Variable | Default | Notes |
|---|---|---|
| `TB_NATS_URL` / `TB_NATS_USER` | `nats://nats:4222` / `monitor` | Password from secret `nats_monitor_password` |
| `TB_ALERTMANAGER_URL` | `http://alertmanager:9093` | observability profile |
| `TB_PREMARKET_LEAD_MINUTES` / `TB_EOD_DELAY_MINUTES` | `15` / `10` | |
| `TB_MONITOR_FEEDBACK_CONFIG` | `/etc/tb/feedback.json` | Hand-written strategies' expectations (MN-026, MN-046) |
| `TB_MONITOR_FEEDBACK_DELAY_MINUTES` | `20` | Minutes after the close for the daily feedback run |
| `TB_MONITOR_FEEDBACK_MIN_TRADES` / `_MIN_RESOLVED` | `30` / `100` | MN-024 |
| `TB_MONITOR_GRPC_ADDR` | empty (Compose `0.0.0.0:9090`) | `MonitorService`; verifier keys `svc_api_gateway_ed25519.pub`, `svc_assistant_ed25519.pub` |
| `TB_EXECUTION_ADDR` | empty (Compose `execution:9090`) | Containment client; signing key `svc_monitor_ed25519` |
| `TB_STRATEGY_REGISTRY` / `TB_RESEARCH_REGISTRY` | `/etc/tb/strategies.json` / empty | MN-046 |
| `TB_RECORDINGS_DIR` | empty (Compose `/data/recordings`) | Recorder on when set (ADR-0046) |
| `TB_RECORDER_RETENTION_DAYS` / `TB_MONITOR_HISTORY_SESSIONS` | `180` / `60` | Retention; sessions replayed at start |
| `TB_MONITOR_EVAL_SECONDS` / `TB_MONITOR_PARITY_DELAY_MINUTES` | `15` / `30` | Health/containment cycle; daily parity after the close |
| `TB_DB_USER` | empty (Compose `svc_monitor`) | Postgres store and audit (password `pg_svc_monitor_password`); empty = in memory (tests) |

## Run and test
- Run: `make up`. Test: `uv run pytest services/monitor/tests tests/python/test_alert_rules.py services/broker-gateway-ibkr/ibkr_tests/test_alert_path.py`; Gate 5 harness: `make monitoring-gate`.
- Existing installs: `make secrets` (key `svc_monitor_ed25519`, password `pg_svc_monitor_password`, NATS permissions), restart `nats`, `make migrate` (schema `monitor`), `make dirs` (`data/recordings`).

## Gotchas
- Every JetStream subscribe passes `stream=`: the NATS user (least privilege) may not call `$JS.API.STREAM.NAMES`, and nats-py looks the stream up when it is missing, which times out after 5 s and kills startup. Tested by `test_every_jetstream_subscription_names_its_stream`.
- Without the observability profile, bus alerts are retried until Alertmanager is reachable (by design, MN-002); inbox sync and silences need Alertmanager too.
- Bars live one hour in JetStream: a monitor outage longer than that leaves an incomplete recording (divergence is then insufficient data for those sessions).
- Containment only reduces risk and acts once per breach episode; the owner re-enables a contained strategy (RM-015).
