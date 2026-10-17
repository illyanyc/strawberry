# Runbook: disk almost full

Alert: `DiskAlmostFull` (> 80% used on the host/VM filesystem).

1. Grafana "Disk used" panel shows which mount (Colima VM disk vs host paths).
2. Docker: `docker system df`; remove dangling images with `docker image prune` (never `docker volume prune` — volumes hold Postgres, NATS, and metrics).
3. Logs and metrics retention: Prometheus and VictoriaLogs keep 30 days; lower `--storage.tsdb.retention.time` / `-retentionPeriod` if needed.
4. Backups: move old `backups/*.age` off-host.
5. Colima VM disk: `colima stop && colima start --disk <bigger>` (disk can grow, not shrink).
6. The lake (`data/lake`) grows with Phase 1 ingestion: plan capacity there.
