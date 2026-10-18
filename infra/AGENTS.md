# infra

Everything that builds, runs, observes, backs up, and hosts the stack. The same Compose project runs on the Mac Mini (primary host, under Colima) and later on a Linux droplet.

**Status:** In progress (Phase 0, section 01 Stage A)  ·  **Stack:** Docker Compose on Colima ([ADR-0013](../docs/adr/0013-mac-mini-host-colima-native-ml.md), D21/D25/D28/D29), Grafana + Prometheus + VictoriaLogs + Alloy ([ADR-0014](../docs/adr/0014-observability-stack.md), D22/D13), data services per [ADR-0015](../docs/adr/0015-phase0-data-services.md) (D4/D6/D8/D10), remote access per [ADR-0016](../docs/adr/0016-remote-access.md) (D17), secrets per [ADR-0011](../docs/adr/0011-secrets-management.md) (D16)

## Layout
| Path | Purpose |
|---|---|
| `compose/docker-compose.yml` | The stack: profiles `core`, `observability`, `ml` (MLflow, Prefect, the AI analyst: `assistant`, `llm-egress`, `analyst-sandbox`, `candidate-checker`), `ml-workers` (Linux), `ops`, `remote-cloudflare`, `ibkr` (paper IB Gateway + adapter), `ibkr-live`, `ibkr-vnc` (on-demand VNC proxy) |
| `compose/feedback.json` | Live-feedback expectations per strategy for the monitor (MN-026); `[]` disables reports |
| `compose/docker-compose.native-ml.yml` | Overlay publishing Postgres/NATS on 127.0.0.1 for native macOS ML workers (`make up NATIVE_ML=1`) |
| `compose/nats/nats.conf` | JetStream config (`sync_interval: always`); users come from the generated secret |
| `compose/env.example` | Non-secret overrides (copy to `compose/.env`): remote RP ID/origins, trusted proxies, ports |
| `docker/go-service.Dockerfile` | Go services (targets `service`, `gateway` = binary + SPA), distroless, non-root, cross-compiled |
| `docker/python-service.Dockerfile` | Python services, uv non-editable venv, slim, non-root |
| `docker/analyst-sandbox.Dockerfile` | Standalone analyst sandbox (pinned Python + numpy + `services/assistant/sandbox/worker.py`; no tbcore or evaluator code) |
| `docker/ops.Dockerfile` | Operator toolbox: `tbctl`, pg client 17, age, sops, jq |
| `docker/mlflow.Dockerfile`, `docker/mlflow-entrypoint.sh` | MLflow 3 server, Postgres backend, filesystem artifacts |
| `ops/backup.sh`, `ops/restore.sh` | Encrypted backup and verified restore (run in the ops container) |
| `ops/secrets-bundle.sh` | SOPS + age bundle of `.secrets/` |
| `observability/prometheus/` | Scrape config and alert rules |
| `observability/alertmanager/` | Routing; critical → Pushover emergency |
| `observability/alloy/config.alloy` | Container logs → VictoriaLogs; cAdvisor + host metrics → Prometheus |
| `observability/grafana/` | Provisioned datasources and the "Stack overview" dashboard |
| `macos/` | LaunchDaemon templates (Colima at boot, stack auto-start, native ML workers), `install.sh`, `start-stack.sh` |
| `secrets/` | SOPS-encrypted bundles only (`make secrets-encrypt`); plaintext never committed |
| `terraform/` | Planned (Phase 6, droplet) |

## Rules enforced here
- `OP-040` hardened images, `OP-041` loopback-only ports, `OP-042` internal backend network → `compose/docker-compose.yml`, `docker/*`; checked by `make port-check`, `make check-arm64`
- `OP-050`/`OP-051` backup and restore drill → `ops/`, `scripts/backup-drill.sh`
- `OP-060` dashboards and alert rules → `observability/`
- `OP-061` smoke test → `scripts/smoke.sh`
- `OP-062` survive reboots → `macos/`, `restart: unless-stopped`
- `SE-010` secrets as files, `SE-051` no public exposure
- `AN-032`, `AN-053`, `AN-071` analyst network isolation (`research` network only), `llm-egress` as the only egress, sandbox and checker with `network_mode: none` → `compose/docker-compose.yml`; checked by `tests/python/test_analyst_compose.py`
- `IB-070`..`IB-073`, `SE-034` IB Gateway login custody, internal `ibkr` network, on-demand VNC, pinned image → `compose/docker-compose.yml`, `Makefile` (`ibkr-vnc`); runbook `docs/runbooks/ibkr-weekly-2fa.md`

## Run and test
- First run: `make bootstrap && make up` → `make setup-token` → http://localhost:8080. Grafana: http://localhost:3000 (admin / `.secrets/grafana_admin_password`).
- `make smoke` (readiness, auth gate, ports, e2e flow + leak canary on a fresh stack, Prometheus targets, audit chain), `make backup-drill`, `make port-check`, `make check-arm64`.
- `make down` keeps volumes; `make clean` deletes them.
- IBKR: fill `.secrets/ibkr_{paper,live}_{username,password}` and `TB_IBKR_*_ACCOUNTS` in `compose/.env`, then `make up PROFILES=core,observability,ibkr` (runbook `docs/runbooks/ibkr-weekly-2fa.md`). Manual login: `make ibkr-vnc IBKR_ENV=live`, then `make ibkr-vnc-stop`.

## Configuration
| Variable | Default | Notes |
|---|---|---|
| `PROFILES` | `core,observability` | Add `ml` for MLflow/Prefect/the analyst (`assistant`, `llm-egress`, `analyst-sandbox`, `candidate-checker`) |
| `TB_LLM_PLANNER_PROVIDER` (and `_CODER_`, `_CHAT_`, `_QUARANTINE_`), `TB_LLM_LOCAL_ONLY`, `TB_LLM_EGRESS_ALLOW`, `TB_MAX_LLM_USD_PER_DAY`/`_MONTH` | `fake`, `false`, providers + host Ollama/MLX, `5`/`50` | Analyst LLM settings (AN-050..AN-053); keys go in `.secrets/llm_anthropic_api_key` / `llm_openai_api_key` |
| `TB_ASSISTANT_ADDR`, `TB_ML_ADDR`, `TB_OPTIMIZER_ADDR` | `assistant:9090`, `ml:9090`, `optimizer:9091` | On macOS with native workers point ML/optimizer at `host.docker.internal` |
| `NATIVE_ML` | unset | `1` publishes Postgres/NATS on loopback (D29) |
| `TB_SECRETS_DIR` / `TB_LAKE_DIR` / `TB_ARTIFACTS_DIR` / `TB_BACKUP_DIR` | repo `.secrets`, `data/lake`, `data/mlflow-artifacts`, `backups` | Host paths (APFS on the Mac) |
| `TB_HOST_UID/GID` | `id -u/-g` | Writers of host bind mounts run as the owner |
| `TB_CONTAINERD_SOCK` | `/run/containerd/containerd.sock` | cAdvisor container discovery |
| `TB_IBKR_PAPER_ACCOUNTS` / `TB_IBKR_LIVE_ACCOUNTS` | unset | `alias=ACCOUNTCODE,…` for the IBKR adapters (required with `ibkr` / `ibkr-live`) |
| `TB_IBKR_COLD_RESTART` / `TB_IBKR_DAILY_RESTART` / `TB_IBKR_DAILY_RESTART_AMPM` | `17:00` / `23:45` / `11:45 PM` | IBC weekly cold restart (Sunday, ET) and daily restart; keep the two daily values equal |
| `TB_IBKR_VNC_TARGET` | `live` | Gateway the `ibkr-vnc` proxy forwards to (`make ibkr-vnc IBKR_ENV=...`) |
| `TB_TRADING_LIVE_ENABLED` | `false` | Live gate 1 for the live IBKR adapter (BR-013) |

## Gotchas
- Compose secret files are bind mounts: files are `0644` inside a `0700` directory so non-root containers can read them.
- VictoriaLogs ships without a shell, so it has no container healthcheck; Prometheus alerts if it disappears.
- Alloy mounts the Docker socket read-only (observability only, internal network, no ports): documented exception in ADR-0014.
- On a Linux VM with stale `iptables-legacy` rules (FORWARD DROP), containers cannot reach each other; fix the host firewall, not the Compose file.
- GPU profile intentionally absent (D25): Metal is not reachable from containers on macOS.
- IB Gateway is a third-party image that renders its IBC/jts config at start, so it is not read-only (non-root, cap-drop-all, `no-new-privileges`, pinned by digest). Restarting or recreating `ib-gateway-<env>` mid-week forces a fresh IBKR login with 2FA; restart the adapter instead.
- Python images install `libgomp1` in the runtime stage: LightGBM (imported by every service that hosts `tbcore` strategies) links the system OpenMP library and fails at import without it.
- Internal networks cannot publish ports, which is why `ibkr-vnc` also joins its own `ibkr-vnc` bridge network.
- The analyst's `research` network is internal and excludes every order-path service; never add `assistant` to `backend` or `edge` (AN-032). Provider traffic goes through `llm-egress` (`TB_LLM_EGRESS_ALLOW`).
- `analyst-jobs` is a named volume shared by three uid-10001 containers; the images pre-create `/jobs` owned by 10001 so the first mount gets that ownership.
