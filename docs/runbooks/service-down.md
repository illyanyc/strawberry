# Runbook: service down, restart loop, bus lag

Alerts: `ServiceDown` (critical, core), `OptionalServiceDown`, `RestartLoop`, `PostgresDown`, `BusConsumerLag`, `HighHTTPErrorRate`.

1. `make ps`: which container is unhealthy or restarting?
2. Logs: `make logs SERVICE=<name>` or Grafana → Explore → VictoriaLogs `service:<name> level:error`.
3. Readiness detail: `docker compose -f infra/compose/docker-compose.yml exec <name> /app/service healthcheck http://127.0.0.1:8081/readyz` (Go) shows which dependency check fails; the reason is in the service log, never in the HTTP response.
4. Dependencies first: Postgres (`pg_up`), NATS (`/healthz` on 8222), then the service.
5. Restart: `docker compose -f infra/compose/docker-compose.yml restart <name>`. A restart loop usually means bad config or a missing secret: the service logs the failing step at startup (fail fast).
6. Bus lag: check the consumer in Grafana (Bus panels); from Phase 2 the owning service's runbook applies. Never purge `ORDERS`/`LEDGER` streams.

Trading services (from Phase 2): if `execution` or `broker-gateway` is down, new intents are blocked (fail closed). Do not bypass; follow the execution runbooks.
