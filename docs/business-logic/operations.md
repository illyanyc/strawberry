# Operations

**Prefix:** `OP-`  ·  **Owners:** `Makefile`, `scripts/`, `infra/`, CI (`.github/workflows/`)  ·  **Related:** [ADR-0002](../adr/0002-monorepo-layout-and-toolchain.md), [ADR-0013](../adr/0013-mac-mini-host-colima-native-ml.md), [ADR-0014](../adr/0014-observability-stack.md), [ADR-0015](../adr/0015-phase0-data-services.md), [security.md](security.md), runbooks in [`../runbooks/`](../runbooks/)

## Scope

How the platform is built, checked, run, backed up, and restored on the owner's host (a Mac Mini running Docker; later a remote server). Covers toolchain pinning, the documentation gate, the local stack, networking exposure, backups, and host survival (reboot, power, network blips). Authentication, secrets design, and audit are in `security.md`. Trading-time behavior (freeze before restart) is in [risk-management.md](risk-management.md) and [orders-and-execution.md](orders-and-execution.md).

## Concepts and states

- **Pinned toolchain**: every tool version lives in `tools/versions.env`; `make tools` installs them into `.tools/bin` (never system-wide).
- **Docs gate**: `scripts/docs_check.py` (`make docs-check`), run in CI on every PR.
- **Compose profiles**: `core` (gateway, broker-gateway, marketdata, execution, ledger, context-data, monitor, Postgres, NATS, Valkey, init jobs), `observability`, `ml` (MLflow, Prefect, assistant), `ml-workers` (Linux only), `ops` (one-shot toolbox), `remote-cloudflare`. `make up` = `core,observability`.
- **Networks**: `backend` (internal: no egress, never published) carries every service; `edge` adds egress/publishing for the gateway, Grafana, Alertmanager, and services that will call brokers or NTP.
- **Host data**: `.secrets/`, `data/lake`, `data/mlflow-artifacts`, `backups/` live on the host (APFS on the Mac).

## Rules

| ID | Rule | Status | Code / tests |
|---|---|---|---|
| OP-001 | Toolchain versions MUST be pinned in `tools/versions.env`; scripts and CI MUST read versions from there rather than hardcoding them. | Implemented | `tools/versions.env`, `scripts/bootstrap-tools.sh` |
| OP-002 | A change set that adds, deletes, or renames a tracked file MUST also change `docs/file-map.md` and `CHANGELOG.md`, or CI fails. | Implemented | `scripts/docs_check.py` (`diff`) |
| OP-003 | A change set that touches `contracts/` MUST also change `docs/architecture/overview.md` or add an ADR, or CI fails. | Implemented | `scripts/docs_check.py` (`diff`) |
| OP-004 | A change set that adds a file under `docs/adr/` MUST also change `docs/decisions.md`, or CI fails. | Implemented | `scripts/docs_check.py` (`diff`) |
| OP-005 | Every git-tracked file MUST be covered by an entry in `docs/file-map.md` (exact path or a covering directory entry); a `Planned` row MUST NOT point at an existing path. | Implemented | `scripts/docs_check.py` (`coverage`) |
| OP-010 | Every service MUST expose `/healthz` (liveness), `/readyz` (dependency checks; 503 with only check names, never error details), and `/metrics` (Prometheus) on an internal ops listener (`:8081`), and log JSON with `service`, `level`, `correlation_id`, `trace_id`. | Implemented | `libs/go/ops`, `libs/go/svc`, `libs/py/tbcore/src/tbcore/service.py`; `TestHealthReadyMetrics`, `test_op010_ops_endpoints` |
| OP-020 | Host clock skew MUST be measured against NTP and exported as `tb_clock_skew_seconds`; with no fresh measurement or skew above `clock.max_skew` (default 250 ms), `clock.Healthy()` is false so trading services refuse to open risk (section 07). | Implemented | `libs/go/clock`; `TestOP020HealthyFailsClosed` |
| OP-030 | Order-path subjects (`ord.>`, `exec.>`, `risk.kill`) and ledger subjects MUST live in durable file-backed JetStream streams with a dedupe window; market data uses memory streams with bounded retention. | Implemented | `libs/go/bus/bus.go` |
| OP-040 | Every image MUST pin its base by digest; platform images MUST run as non-root with a read-only root filesystem, `cap_drop: [ALL]`, `no-new-privileges`, an init process, and memory limits; exceptions are listed in `infra/AGENTS.md` with the reason. | Implemented | `infra/docker/*.Dockerfile`, `infra/compose/docker-compose.yml` |
| OP-041 | Only `api-gateway` (8080) and Grafana (3000) MAY publish ports, and only on `127.0.0.1`; the native-ML overlay MAY add Postgres/NATS on `127.0.0.1` only. | Implemented | `infra/compose/*.yml`; `scripts/port-check.sh` (`make port-check`, smoke test) |
| OP-042 | Databases, the bus, the cache, and exporters MUST sit only on the internal `backend` network (no egress). | Implemented | `infra/compose/docker-compose.yml` |
| OP-043 | Every pinned third-party image MUST provide `linux/arm64`; platform images MUST build for `linux/arm64` and `linux/amd64`. | Implemented | `scripts/check-arm64.sh`; CI multi-arch build |
| OP-050 | `make backup` MUST produce one age-encrypted archive with logical dumps of `platform`, `mlflow`, `prefect`, role definitions without passwords, the lake, MLflow artifacts, per-table checksums, the audit head, and `SHA256SUMS`, and MUST record `tb_backup_last_success_timestamp_seconds`. | Implemented | `infra/ops/backup.sh` |
| OP-051 | `make restore` MUST verify file checksums before restoring and MUST fail unless restored table checksums and the audit head equal the manifest; `make backup-drill` proves restore onto a wiped stack, including the lake byte-for-byte. | Implemented | `infra/ops/restore.sh`, `scripts/backup-drill.sh` |
| OP-060 | Grafana MUST provision a "Stack overview" dashboard (service status, alerts, restarts, container CPU/memory, HTTP rate/errors/latency, bus lag, DB connections, clock skew, disk, backup age, error logs), and Prometheus MUST alert on: core service down (30 s), restart loop, disk > 80 %, Postgres down, backup stale/never, bus consumer lag, clock skew, HTTP 5xx > 5 %. Critical alerts route to Pushover. | Implemented | `infra/observability/` |
| OP-061 | CI MUST run the smoke test against a freshly started stack: every core service ready, anonymous API calls rejected, security headers present, loopback-only ports, the end-to-end passkey/credential flow with a leak canary absent from all logs and storage, all core Prometheus targets up, and the audit chain verifying. | Implemented | `scripts/smoke.sh`, `tests/e2e/` |
| OP-062 | On the Mac Mini, the stack MUST come back without a login after reboot or power loss: Colima and stack LaunchDaemons, `restart: unless-stopped`, `pmset autorestart 1`. | Implemented | `infra/macos/`; runbook `mac-mini-host.md` |
| OP-007 | Every PR and push to `main` MUST pass CI: docs gate, contract freshness and buf checks, Go/Python/web lint and tests (Go with Postgres integration tests), security scans, multi-arch image builds, and the stack smoke job. | Implemented | `.github/workflows/ci.yml` |
| OP-006 | The OP-002..OP-004 diff checks MAY be bypassed only through the reviewed PR label `docs-check-skip` (sets `DOCS_CHECK_SKIP=1`); coverage (OP-005) is never skipped. | Implemented | `scripts/docs_check.py` |

## Invariants

| ID | Invariant | Checked by |
|---|---|---|
| OP-I1 | `docs/file-map.md` covers every tracked path. | `make docs-check` in CI |
| OP-I2 | No container publishes a non-loopback port. | `make port-check` (smoke test, CI) |

## Edge cases

| Scenario | Expected behavior | Rules |
|---|---|---|
| A PR only edits existing files' contents | Diff check passes without file-map changes; changelog is still required by review for behavior changes. | OP-002 |
| A PR renames `a.go` to `b.go` | Counts as structural; file map and changelog must change. | OP-002 |
| A new ADR without a decisions-register edit | CI fails. | OP-004 |
| Mac reboots after a power cut | Colima starts at boot, the stack daemon runs `make up`, services restart; FileVault unlock over Tailscale SSH if enabled | OP-062 |
| Restore of a backup taken before a schema migration | Restore replaces the schemas with the backup's (including `schema_migrations`); run `make migrate` afterwards to move forward | OP-051 |
| A core container is killed | `ServiceDown` fires after 30 s and pages via Pushover | OP-060 |

## Open questions

None yet.
