# ADR-0011: Secrets: mounted files, SOPS + age at rest, envelope encryption for broker credentials

- **Status:** Accepted (research)
- **Date:** 2026-10-17
- **Decision register:** D16
- **Related:** research [09 §D16](../research/09-decisions.md#d16); rules SE-010, SE-012, SE-030..SE-033; [ADR-0005](0005-postgres-layout-and-migrations.md)

## Context

Broker credentials are the crown jewels: a leak lets an attacker trade real accounts. Deploy-time secrets (DB passwords, service keys, the gateway key, the KEK) must not appear in images, Compose files, `docker inspect`, env vars, logs, or the repo. Research confirmed SOPS + age and app-level AES-256-GCM envelope encryption (not pgcrypto), with OpenBao instead of Vault if a secrets server is ever needed.

## Decision

- **Delivery**: every secret is a file under `/run/secrets/<name>` (Compose `secrets:`), read through `libs/go/secrets` / `tbcore.secrets`, which register each value with the log redactor. Environment variables carry only non-secret config.
- **At rest on the host**: `make secrets` generates random values into `.secrets/` (gitignored; directory `0700`). `make secrets-encrypt` writes a SOPS + age encrypted bundle (`infra/secrets/<env>.sops.yaml`, safe to commit or back up off-host); `make secrets-decrypt` restores `.secrets/` from it. The age identity lives outside the repo (`~/.config/sops/age/keys.txt`) with an offline copy.
- **Broker credentials**: stored in Postgres schema `secrets` (only `svc_broker_gateway`), encrypted per record with a random 256-bit data key (AES-256-GCM), AAD `tb1|<account>|<environment>`; the data key is wrapped by a KEK from the mounted `broker_kek` ring (`<id>:<key>` lines, first active) with AAD `tb1-dek|<account>|<environment>`. Rotation re-wraps data keys (`broker-gateway rewrap-keys`).
- **No plaintext API**: `CredentialService` has put/delete/list-status only; plaintext is decrypted inside broker-gateway for its own adapters.

## Alternatives considered

| Option | Why not |
|---|---|
| pgcrypto | Keys travel in SQL and can land in server logs; no AAD binding. |
| Vault / OpenBao now | An extra stateful service to unseal after every reboot of a home host; revisit for multi-host. |
| macOS Keychain for the KEK | Containers cannot reach it; a native helper would add an IPC path. Possible later via the launchd layer. |
| Env vars | Visible in `docker inspect`, `/proc`, crash dumps. |

## Consequences

- Positive: database compromise alone does not expose credentials; environment confusion is cryptographically impossible; every secret value is redacted from logs.
- Negative / risks: the KEK sits in a host file readable by the Docker VM; host compromise (root) still exposes it. Mitigated by FileVault, Tailscale-only access, and no public ports.
- Follow-up: Phase 6 hardening may move the KEK behind OpenBao or a hardware-backed unwrap.

## Validation

`TestSE030EnvelopeAADBinding`, `TestSE030StoreIntegration` (including a paper→live relabel attack and KEK rotation), `TestSE040CallerAllowlistAndSE012ErrorPath`, and the Compose smoke test that greps all container logs for a canary credential.
