---
title: "Section 01 – Infrastructure and Deployment (Mac Mini host)"
phase: "0 (Stage A: Mac host + local foundation), 6 (Stage B: hardening + optional DigitalOcean)"
depends_on: [00]
updated: 2026-10-17
---

# Section 01: Infrastructure and Deployment (Mac Mini host)

## Role

You are a platform/DevOps engineer who builds small, secure, reproducible stacks that one person can operate. You prefer boring, well-documented tools, you design for unattended 24/7 operation on a single home host, and you keep the Mac Mini and a future Linux server on the same Compose project.

## Context

The platform runs 24/7 on the owner's **Mac Mini (Apple Silicon, no NVIDIA GPU)** `CONFIRMED (U4)`. Containers run in a **Colima** Linux VM started by a boot-time LaunchDaemon (D28). ML workers run **natively under launchd** because containers on macOS can't use the GPU (D29). The host must survive reboots and power or network blips. The owner reaches it from anywhere through **Tailscale** with no open ports (D17). A move to a DigitalOcean droplet later must use the same Compose file (D21).

- **Stage A (Phase 0)**: monorepo skeleton, contracts toolchain, Mac host setup, Colima Compose stack, native worker skeleton, CI, observability, Pushover, external dead-man monitor, backups skeleton.
- **Stage B (Phase 6)**: production hardening, off-site backups with restore drills, upgrade and rollback procedures, optional DigitalOcean target.

## Scope

In scope:
- Monorepo layout from the master prompt, `Makefile`/`justfile` targets, dev setup.
- Contracts toolchain (`buf`, codegen to Go, Python, TypeScript).
- Mac host configuration: power, FileVault, SSH unlock, macOS updates, UPS, sleep settings.
- Colima/Lima runtime with LaunchDaemon autostart; Docker Compose with profiles; hardened images; health checks.
- Native launchd workers (ML training, inference, backtest/optimizer, local LLM) from the shared `uv` lockfile.
- Infrastructure services: PostgreSQL, Valkey, NATS JetStream, MLflow, Prefect, artifact store.
- CI: lint, type-check, test, build, scan, SBOM, doc checks.
- Observability: Grafana, Prometheus or VictoriaMetrics, VictoriaLogs, Grafana Alloy; Tempo from Phase 5.
- Alerting plumbing: Pushover contact point, external uptime monitor.
- Backups and restore (restic).
- Stage B: hardening, deploy/rollback, optional Terraform for DigitalOcean.

Out of scope:
- Authentication logic, secrets encryption design, audit log (section 02; this section provides the plumbing).
- Tailscale/Cloudflare access policy design (section 02); this section installs and runs the daemons.
- Business logic of any service.
- Kubernetes or Nomad.

## Inputs and dependencies

- Master prompt: layout, contracts, decision register.
- Section 02 Stage A runs in parallel and provides the secrets interface (files mounted under `/run/secrets`).
- `VERIFY EARLY (V5)`: Mac Mini RAM. Size the Colima VM at 12–16 GB and leave the rest for native ML; 48–64 GB allows a local LLM alongside training.
- `VERIFY EARLY (V4)`: atomic rename across VirtioFS for the lake bind mount (spike owned by section 03; this section provides the mount).

## Accepted decisions

| Decision | Accepted choice | Research |
|---|---|---|
| Container runtime | `ACCEPTED D28`: **Colima 0.10 on Lima 2.2** with a boot-time `limactl` LaunchDaemon; not Docker Desktop (needs GUI login); OrbStack only if auto-login is accepted (conflicts with FileVault) | 06 §5 |
| ML worker placement | `ACCEPTED D29`: native launchd workers with `KeepAlive`, same `uv` lockfile, talking to NATS/Postgres over loopback-published ports; containerized on Linux | 06 §4 |
| GPU | `ACCEPTED D25`: no GPU Compose profile; native MPS/MLX; cloud GPU bursts (RunPod/Vast) that log to home MLflow over Tailscale | 06 §4 |
| Host and orchestration | `ACCEPTED D21`: Mac Mini + Compose now; same Compose on a DigitalOcean droplet later (General Purpose 8 vCPU / 32 GB ≈ $252/mo); no k3s/Nomad | 06 §6 |
| Event bus | `ACCEPTED D2`: NATS JetStream 2.15 with `sync_interval: always`, replication 1 | 06 §2 |
| Analytical store placement | `ACCEPTED D4`: Parquet lake on host APFS, bind-mounted into the writer container and read natively by ML workers | 06 §3 |
| Transactional/caches | `ACCEPTED D5, D6`: PostgreSQL 17/18 and Valkey 9 on **named volumes** inside the VM (bind mounts are about 3× slower) | 06 §3 |
| Artifacts | `ACCEPTED D8`: MLflow 3 with Postgres backend and a filesystem artifact root (restic-backed) or RustFS; **no MinIO** | 03 §1 |
| Orchestration | `ACCEPTED D10`: Prefect 3 | 03 §1 |
| Observability | `ACCEPTED D22`: Grafana + Prometheus/VictoriaMetrics + VictoriaLogs + Grafana Alloy (Promtail and Grafana Agent are EOL); Tempo from Phase 5 | 06 §6 |
| Remote access | `ACCEPTED D17`: open-source `tailscaled` as a system LaunchDaemon (runs pre-login); `tailscale serve` for UI, Grafana, Prefect, MLflow, Optuna dashboard on the tailnet only; optional `cloudflared` | 08 §1 |
| Tooling | `uv`, `ruff`, `pyright`/`mypy`, `pytest`; Go 1.26, `go.work`, `golangci-lint`, `go test -race`; Python 3.13 | 06 §7 |

## Interfaces and contracts to expose

- `make` targets: `bootstrap`, `gen`, `up`, `down`, `logs`, `workers-up`, `workers-down`, `test`, `lint`, `migrate`, `seed`, `backup`, `restore`, `reboot-drill`, `deploy`, `rollback`.
- Compose profiles: `core` (gateway, broker-gateway, broker-gateway-ibkr, IB Gateway, marketdata, execution, ledger, Postgres, Valkey, NATS), `research` (MLflow, Prefect, holdout service, assistant, agent sandbox, context-data, monitor), `observability`, `dev` (broker simulator, fixtures).
- Native worker units (`infra/launchd/*.plist`): `ml-train`, `ml-infer`, `backtest-worker`, `optimizer-worker`, `local-llm`. Each has `KeepAlive`, log paths shipped by Alloy, resource limits via `nice`/process limits, and health endpoints on loopback.
- Service template: `/healthz`, `/readyz`, `/metrics`, JSON logs with `service`, `level`, `correlation_id`, `trace_id`.
- Standard config loading: typed config from file plus overrides; secrets from `/run/secrets` files; validated at startup with a version and config parity check; unknown flags fail startup (N5).
- Network layout: an internal Docker network; container ports published only on `127.0.0.1`; `tailscale serve` exposes the gateway and admin UIs on the tailnet; nothing listens on the LAN or WAN interfaces.

## Functional requirements

### Stage A: Mac host and local foundation
- [ ] **Power and sleep**: enable "Start up automatically after a power failure" and "Prevent automatic sleeping when the display is off"; `pmset autorestart 1`; UPS on USB with shutdown thresholds, verified to cut output so auto-restart fires.
- [ ] **FileVault on** with Remote Login enabled; **SSH FileVault unlock after reboot** (macOS 26+ on Apple Silicon) over Tailscale using the `tailscaled` system daemon and wired Ethernet; documented in `docs/runbooks/mac-reboot.md`.
- [ ] **macOS updates**: automatic OS installs off (they reboot unattended); Background Security Responses on; patch window on weekends with a checklist (a reboot costs a FileVault unlock and probably an IBKR 2FA).
- [ ] **Colima**: pinned versions, VM sized from V5, VirtioFS mount for the lake directory only, `limactl` LaunchDaemon autostart before login; containers start after the FileVault unlock.
- [ ] Create the monorepo layout with placeholder services that build and pass health checks.
- [ ] `contracts/` with `buf` lint and breaking-change detection; generated Go, Python, and TypeScript clients; CI fails on stale generated code.
- [ ] `infra/compose/docker-compose.yml` with the profiles above, named volumes for Postgres/NATS/Valkey, the lake bind mount, per-service memory limits (so a DuckDB query can't OOM the OMS), `restart: unless-stopped`, and `depends_on: condition: service_healthy`.
- [ ] Hardened images: Docker Hardened Images or distroless, pinned by digest, non-root, read-only rootfs where possible, `cap_drop: [ALL]`, `no-new-privileges`, `tini` as PID 1; native arm64 images (including gnzsnz IB Gateway).
- [ ] PostgreSQL with separate databases/roles per service (least privilege) and migrations per language (`golang-migrate` or `atlas` for Go, `alembic` for Python).
- [ ] NATS JetStream: `sync_interval: always`, file storage on a named volume, durable streams for order-path subjects, limits retention for market data; Postgres outbox relay library in `libs/go`.
- [ ] MLflow 3 with Postgres backend and filesystem (or RustFS) artifact store; Prefect 3 server; optuna-dashboard; all exposed only via `tailscale serve`.
- [ ] **Native workers**: `uv`-built environments from the shared lockfile; launchd plists; workers read the lake directly from APFS and connect to NATS/Postgres on loopback; `make workers-up` installs and starts them.
- [ ] `dev` profile with the broker simulator (section 04) and recorded fixtures.
- [ ] **Observability**: metrics scraping for containers and native workers; VictoriaLogs with Alloy as the only agent (container logs and launchd log files); Grafana with provisioned datasources and a "Stack overview" dashboard (up/down, CPU/mem for VM and host, request rate/errors/latency, bus lag, DB connections, disk).
- [ ] Baseline alert rules: service down, restart loop, disk > 80%, VM memory pressure, Postgres backup failure, bus consumer lag, clock skew, native worker down.
- [ ] **Pushover** contact point in Grafana; execution and monitor services can page directly (section 07, 13).
- [ ] **External dead-man monitor** (Healthchecks.io or similar) receiving a heartbeat from the host (via Tailscale Funnel or the optional Cloudflare tunnel) and paging the phone when it stops.
- [ ] Time sync check: macOS NTP and the VM clock; a clock-skew metric; execution refuses to trade above a threshold.
- [ ] Local backups: `make backup` runs restic (Postgres dump via `--stdin-from-command`, raw lake partitions, MLflow artifacts, audit anchors); SOPS/age material backed up separately; `make restore` restores into a fresh stack.
- [ ] **CI**: lint, type-check, unit tests, contract checks, image builds (arm64 and amd64), Syft SBOM + Grype scan (fail on fixable criticals), gitleaks secret scan; GitHub Actions pinned by commit SHA; dependency cooldown (Renovate `minimumReleaseAge` 3–7 days, `uv` `exclude-newer`); `--locked` installs.
- [ ] CI documentation check (master N6): fail when files are added, moved, or removed without a change to `docs/file-map.md` and `CHANGELOG.md`; fail when `contracts/` change without a change to `docs/architecture/overview.md` or a new ADR; fail when a file is added to `docs/adr/` without a change to `docs/decisions.md`. Allow an explicit, reviewed opt-out label for pure refactors.
- [ ] Seed the repo's `docs/decisions.md` with D1–D31 (status Accepted, chosen option, research link) and one ADR per decision.
- [ ] `README` quickstart: Mac prerequisites → clone → bootstrap → up → workers-up → open the UI over Tailscale.

### Stage B: hardening, rollback, optional DigitalOcean
- [ ] Deploy: images built in CI and pulled by digest; `make deploy` runs migrations, then restarts services in dependency order with health gating; startup parity check confirms versions and config.
- [ ] Rollback: keep the previous release's digests and a pre-deploy Postgres dump; `make rollback` restores both.
- [ ] Trading-safe deploys: refuse to restart `execution`, `broker-gateway`, `broker-gateway-ibkr`, or IB Gateway during market hours unless forced with step-up; before restart, execution freezes and confirms no in-flight orders. Restarting the IB Gateway container mid-week forces a new IBKR 2FA, so schedule it for the weekly re-auth window.
- [ ] Off-site backups: nightly restic to object storage (e.g. DigitalOcean Spaces), client-side encrypted; retention policy; **quarterly restore drill** into a scratch stack with smoke tests.
- [ ] Optional DigitalOcean target (`infra/terraform/`): droplet, attached volume for the lake, Spaces bucket, DO Cloud Firewall denying all inbound except UDP 41641 for Tailscale; cloud-init with non-root admin, no password/root SSH, Docker, `tailscaled`; note that **Docker-published ports bypass UFW**, so bind to `127.0.0.1`/tailnet only. ML workers run as CPU containers there; GPU work bursts to rented GPUs.
- [ ] Migration runbook Mac → droplet: same images, restic restore, Tailscale node swap, broker sessions re-established (IBKR 2FA).
- [ ] Remote observability over Tailscale only; Alertmanager/Grafana routes to Pushover.

## Non-functional requirements

- **Availability**: after a power cut, the stack is healthy without a GUI login once FileVault is unlocked over SSH; target recovery under 10 minutes from power restore plus unlock.
- **Performance**: `make up` (core profile, warm cache) healthy in under 2 minutes; observability stack under ~2 GB RAM (VictoriaLogs chosen for this).
- **Security**: no secrets in images, Compose files, env vars, or CI logs; only `127.0.0.1` and tailnet bindings; non-root containers with dropped capabilities.
- **Reliability**: every stateful service on a named volume and in the backup set; restart policies with backoff so crashes don't hammer brokers.
- **Reproducibility**: images pinned by digest; lockfiles committed; deterministic CI builds.
- **Tests**: CI smoke test starts the core profile and checks every `/readyz`; the reboot drill is scripted and recorded.

## Acceptance criteria

All stages:
- [ ] Docs updated per `AGENTS.md` (master N6) in the same commits as the changes: `docs/file-map.md`, `docs/business-logic/operations.md` (host setup, deploy, backup, restore behavior), `docs/runbooks/mac-reboot.md`, `CHANGELOG.md`, `docs/glossary.md` for new terms, and, for every decision made, an ADR in `docs/adr/` plus its row updated (or added) in `docs/decisions.md`.

Stage A:
- [ ] A clean clone reaches a healthy core stack in Colima with `make bootstrap && make up && make workers-up`.
- [ ] **Reboot drill**: power cut → auto-restart → SSH FileVault unlock over Tailscale → Colima and native workers start → all `/readyz` green, with no GUI login.
- [ ] CI runs lint, tests, contract and doc checks, image builds, SBOM, and scans; a stale generated file fails CI.
- [ ] Grafana "Stack overview" shows every container and native worker; killing a container fires an alert to Pushover; stopping the heartbeat makes the external monitor page.
- [ ] `make backup` then `make restore` on a fresh stack restores Postgres and lake data (checksums match).
- [ ] A port scan from the LAN finds nothing listening; services are reachable only over the tailnet.

Stage B:
- [ ] Deploy refuses to restart trading services during simulated market hours without forced confirmation.
- [ ] Rollback drill restores the previous release and database state.
- [ ] Off-site restore drill passes and is scheduled quarterly.
- [ ] If the DigitalOcean target is used: `terraform apply` + restore produces a working stack reachable only via Tailscale; an external scan of the public IP finds no open TCP ports.

## Pitfalls to avoid

- Docker Desktop or OrbStack on an unattended host: they need a GUI login, so a reboot leaves trading down.
- Running ML in containers on macOS and expecting MPS/MLX: there is no Metal passthrough.
- Bind-mounting Postgres or NATS data from macOS (slow); use named volumes inside the VM.
- Leaving automatic macOS updates on; an unattended reboot costs a FileVault unlock and an IBKR 2FA.
- Publishing ports as `0.0.0.0:5432` "just for debugging". Bind to `127.0.0.1`.
- Secrets in environment variables or Compose `environment:` blocks.
- `latest` tags, unpinned Actions, or same-day dependency upgrades (supply-chain attacks such as the 2026 Trivy tag hijack).
- NATS with default fsync (Jepsen lost acknowledged writes on power failure); set `sync_interval: always`.
- A host that pages about its own death: you need the external monitor.
- MinIO community edition (unmaintained), Promtail/Grafana Agent (EOL), or Kubernetes on one host.
- Backups that were never restored.
