# ADR-0013: Mac Mini host with Compose on Colima, native ML workers, no GPU profile

- **Status:** Accepted (research)
- **Date:** 2026-10-17
- **Decision register:** D21, D25, D28, D29
- **Related:** research [09 §D21](../research/09-decisions.md#d21), [§D25](../research/09-decisions.md#d25), [§D28](../research/09-decisions.md#d28), [§D29](../research/09-decisions.md#d29), [06 §5](../research/06-architecture-and-stack.md); rules OP-040..OP-042, OP-062

## Context

The owner's primary host is a Mac Mini (Apple Silicon) running 24/7, which must survive reboots and power or network blips, with a later move to a DigitalOcean droplet without redesign. Docker Desktop and OrbStack need a GUI login to start; Lima's LaunchDaemon starts at boot. Containers on macOS cannot reach Metal, so GPU-accelerated ML (MPS, MLX) must run natively.

## Decision

- **Host and orchestration (D21)**: one Docker Compose project (`infra/compose/docker-compose.yml`) with profiles; the same file runs on Linux later. No k3s/Nomad.
- **Runtime (D28)**: Colima (VZ, virtiofs) started at boot by a LaunchDaemon (`infra/macos/com.strawberry.colima.plist`) running as the owner; a second LaunchDaemon (`com.strawberry.stack`) waits for the Docker socket and runs `make up`. Containers use `restart: unless-stopped`. `pmset` enables restart after power loss and disables sleep. Not Docker Desktop.
- **ML workers (D29)**: on macOS, `ml`/`backtest`/`optimizer` run natively under launchd (`com.strawberry.ml-worker.plist`) from the same `uv` lockfile; `make up NATIVE_ML=1` publishes Postgres and NATS on `127.0.0.1` only (`docker-compose.native-ml.yml`). On Linux the same packages run in the `ml-workers` profile.
- **GPU (D25)**: no GPU Compose profile; heavy training bursts to rented cloud GPUs (Phase 4), logging to MLflow over Tailscale.
- **Images**: every image is linux/arm64-capable (`make check-arm64`); bases pinned by digest; our images run non-root, read-only, with all capabilities dropped (`OP-040`).
- **Host data on APFS**: the lake and MLflow artifacts are host bind mounts (`data/`), written by containers running as the owner's UID so native workers read them directly (D4, D8).

## Alternatives considered

| Option | Why not |
|---|---|
| Docker Desktop / OrbStack | Need a GUI login after reboot (OrbStack only with auto-login, which weakens FileVault). |
| Apple `container` | No upstream Compose support, DNS gaps (research 06 §5). |
| GPU profile with NVIDIA runtime | No NVIDIA on Apple Silicon; Metal is not passed into containers. |
| Everything native via launchd | Loses the reproducible, portable stack needed for the droplet move. |

## Consequences

- Positive: unattended boot, portable stack, native MPS/MLX for ML.
- Negative / risks: two process managers (launchd + Docker); loopback ports for native workers are an explicit, documented exception to "no DB ports on the host" (still loopback-only).
- Follow-up: Phase 4 renders worker plists per package; Phase 6 adds Terraform for the droplet.

## Validation

`make up` from a clean clone reaches healthy services; `make port-check` shows only loopback bindings; `infra/macos/install.sh` dry-run checks prerequisites; reboot drill on the Mac Mini (owner action, runbook `docs/runbooks/mac-mini-host.md`).
