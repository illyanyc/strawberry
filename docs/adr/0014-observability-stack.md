# ADR-0014: Observability with Grafana, Prometheus, VictoriaLogs, and Alloy; Pushover for critical alerts

- **Status:** Accepted (research)
- **Date:** 2026-10-17
- **Decision register:** D22 (and D13 for alert delivery)
- **Related:** research [09 §D22](../research/09-decisions.md#d22), [§D13](../research/09-decisions.md#d13); rules OP-010, OP-060

## Context

Every service must expose health and metrics, Grafana must show a stack overview, and killing a container must fire an alert (section 01 Stage A). The Mac Mini shares RAM with ML work. Research replaced Loki with VictoriaLogs (about 1.3 GB vs 4–7 GB RAM in one test), made Grafana Alloy the only agent (Promtail EOL 2026-03-02), deferred Tempo to Phase 5, and added Pushover for critical paging.

## Decision

- **Metrics**: Prometheus 3 scrapes every service's ops listener (`:8081/metrics`), `nats-exporter` (JetStream lag), `postgres-exporter` (connections, as `svc_metrics` with `pg_monitor`), and infra components. Core targets are static (absence alerts); optional-profile targets use DNS discovery (no false alerts when a profile is off). Remote-write receiver enabled for Alloy.
- **Logs**: services log JSON to stdout; **Alloy** reads container logs via the Docker socket (read-only mount, the only such exception) and pushes to **VictoriaLogs** (Loki push API, `_msg_field=msg`). Logs are already redacted at the source (SE-012).
- **Host/container metrics**: Alloy's built-in cAdvisor and unix exporters (VM disk, CPU, memory, plus the backup textfile metric) remote-write to Prometheus.
- **Alerts**: baseline rules (service down, restart loop, disk > 80%, Postgres down, backup stale/never, bus consumer lag, clock skew, HTTP 5xx) in `infra/observability/prometheus/rules/stack.yml`; Alertmanager routes `critical` to **Pushover emergency priority**.
- **Dashboards**: provisioned "Stack overview" (status, alerts, restarts, CPU/memory per container, HTTP rate/errors/latency, bus lag and stream sizes, DB connections, clock skew, disk, backup age, error logs).
- **Traces**: services already create OpenTelemetry spans and export when `TB_OTLP_ENDPOINT` is set; Tempo (or Alloy → Tempo) is added in Phase 5.
- Only Grafana (and the gateway) publish ports, on `127.0.0.1`.

## Alternatives considered

| Option | Why not |
|---|---|
| Loki + Promtail | Higher memory; Promtail is end-of-life. |
| VictoriaMetrics instead of Prometheus | Viable and allowed by D22; Prometheus kept for ecosystem familiarity. Swap later behind the same scrape config. |
| Tempo now | No cross-service request paths worth tracing until the order path exists. |

## Consequences

- Positive: full stack visibility in about 1.5 GB RAM; alert on any core service in ~30 s.
- Negative / risks: Docker socket access by Alloy (read-only, internal network, no ports); Pushover delivery requires the owner's keys (placeholders until filled).

## Validation

Stopping `ledger` fired `ServiceDown` (critical) in about 30 s and Alertmanager routed it to Pushover; smoke test asserts every core target is up; dashboard screenshot in the Phase 0 PR.
