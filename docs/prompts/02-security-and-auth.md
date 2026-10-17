---
title: "Section 02 – Security and Authentication"
phase: "0 (Stage A: auth, secrets, audit, remote access), 6 (Stage B: hardening)"
depends_on: [00, 01]
updated: 2026-10-17
---

# Section 02: Security and Authentication

## Role

You are a security engineer who has protected systems that move money. You design for a single owner today without blocking multi-user later, you assume the host is reachable from the internet through tunnels, and you treat broker credentials as the crown jewels.

## Context

A compromise of this platform means an attacker can trade the owner's real brokerage accounts. The threat model includes: stolen sessions, leaked API keys (logs, backups, LLM prompts), malicious dependencies, prompt injection through news text reaching the AI analyst, a compromised TradingView webhook, a stolen phone, and the owner making a dangerous change by accident. The UI is reached from anywhere through Tailscale (primary) or Cloudflare Tunnel + Access (optional) with no open ports `CONFIRMED`. Non-negotiable N1 applies in full.

- **Stage A (Phase 0)**: passkey authentication, sessions, authorization policy, secrets, audit log, remote-access configuration, security baseline in CI.
- **Stage B (Phase 6)**: hardening against the research checklist, threat-model review, incident drills.

## Scope

In scope:
- Owner authentication: passkeys primary, second passkey or TOTP recovery, recovery codes; no SMS.
- Server-side sessions, CSRF, `Origin` checks, step-up authentication.
- Authorization policy layer (single policy function, RBAC-ready), applied in every handler.
- Service-to-service authentication on the internal network.
- Secrets management for deploy-time secrets and broker credentials.
- HMAC hash-chained audit log with off-box anchoring.
- Remote access policy: Tailscale grants, `tailscale serve`, optional Cloudflare Access, webhook ingress rules.
- Web security headers, rate limiting, input validation.
- Supply-chain security in CI.
- Threat model and the research security checklist.

Out of scope:
- Broker session flows (section 04 uses the secrets interface defined here).
- Infra provisioning (section 01 installs `tailscaled` and `cloudflared`).
- Trading risk limits (section 07), though step-up guards their changes.

## Inputs and dependencies

- Section 01 Stage A: Colima stack, Postgres, CI, `tailscaled`.
- `ASSUMED (U7)`: single owner; `owner_id` on every row; all checks through `authorize(actor, action, resource)`.
- `ASSUMED (U6)`: phone can monitor and halt; mobile order entry off by default.

## Accepted decisions

| Decision | Accepted choice | Research |
|---|---|---|
| Authentication | `ACCEPTED D15`: built-in passkeys via **go-webauthn** in the Go gateway (user verification required); second passkey or TOTP for recovery; no SMS; server-side sessions per OWASP ASVS 5.0 V7. Multi-user later: Pocket ID (passkey-only OIDC) or Keycloak/Authentik; not Zitadel (AGPL) | 08 §2 |
| Secrets | `ACCEPTED D16`: **SOPS + age** for deploy secrets (age key off-repo, protected by FileVault); app-level **AES-256-GCM envelope encryption** for broker credentials with a KEK outside Postgres (not pgcrypto); secrets as `/run/secrets` files (mode 0400), never env vars, `ARG`, or `ENV`; **OpenBao** if a server is ever needed (not Vault, BSL) | 08 §3 |
| Remote access | `ACCEPTED D17`: **Tailscale** (`tailscaled` daemon) as the private admin plane with grants; optional **Cloudflare Tunnel + Access** with independent passkey MFA for a public UI hostname and as the only TradingView webhook path (Service Auth + IP selector, never Bypass) | 08 §1 |
| UI surface | `ACCEPTED D11, D14`: static SPA served by the Go gateway (one auth surface; no React Server Components); REST commands + SSE streams | 07 §1, §3 |
| Service-to-service auth | Signed short-lived service tokens (or mTLS) on the internal network; `execution` accepts intents only from authorized producers; broker adapters accept orders only from `execution`'s outbox relay | 08 §5 |

## Interfaces and contracts to expose

- **Auth API (gateway)**: `POST /auth/webauthn/register/{begin,finish}`, `POST /auth/webauthn/login/{begin,finish}`, `POST /auth/totp/{enroll,verify}`, `POST /auth/recovery`, `POST /auth/logout`, `POST /auth/step-up/{begin,finish}`, `GET /auth/sessions`, `DELETE /auth/sessions/{id}`, `DELETE /auth/sessions` (revoke all).
- **Passkey relying party**: `rpID` must match the hostname, so passkeys are registered per hostname (the `*.ts.net` name and, if used, the Cloudflare domain). Use non-descriptive hostnames (Tailscale certificate names land in public CT logs).
- **Session**: HTTP-only, `Secure`, `SameSite=Strict` cookie with a ≥128-bit CSPRNG ID stored server-side; rotated on login and step-up; idle timeout 15–30 min; absolute 12 h; step-up grants valid ~5 min and bound to the session.
- **Authorization**: `authorize(actor, action, resource) -> Allow | Deny(reason) | RequireStepUp`. Actions are enumerated (e.g., `orders.submit.manual`, `strategy.enable`, `strategy.promote.paper`, `strategy.promote.live`, `risk.limits.raise`, `allocation.raise`, `killswitch.clear_latch`, `killswitch.engage`, `broker.credentials.write`, `passkey.add`, `holdout.schedule`, `holdout.evaluate`, `analyst.autonomy.raise`). Actors: `owner`, `service:<name>`, `analyst`, `assistant`, `webhook:tradingview:<name>`.
- **Secrets interface** (Go and Python): `get_secret(name) -> bytes` from `/run/secrets`; `BrokerCredentialStore.put/get/rotate/delete(broker_account_id)` usable only by `broker-gateway` and `broker-gateway-ibkr`. The IBKR username/password live only in the IB Gateway container's secret file.
- **Audit log**: `AuditEvent` contract; an `audit` schema with INSERT-only privileges (UPDATE/DELETE revoked); HMAC chain (`hmac = HMAC(k, prev_hmac || canonical_event)`); `make audit-verify`; daily anchor of the chain head off-box (backup store).
- **Security events**: `alert.security` for failed logins, new device or passkey, step-up failures, credential access outside the broker adapters, audit chain mismatch.

## Functional requirements

### Stage A
- [ ] Passkey registration and login (resident keys, UV required); setup encourages two authenticators (laptop + phone) per hostname.
- [ ] TOTP enrollment as fallback; 10 single-use recovery codes (hashed), shown once.
- [ ] First-run bootstrap: a one-time setup token printed to the server console is required to register the first owner; registration then closes.
- [ ] Sessions: server-side, rotated on login and privilege change; list and revoke-all from the UI.
- [ ] CSRF protection (SameSite=Strict + token) and `Origin` checks on every POST, SSE, and WebSocket request.
- [ ] Authorization enforced in every API handler (CVE-2025-29927 lesson: never rely on edge middleware alone); route-inventory test.
- [ ] Step-up enforced by the policy layer for: enabling live trading or a strategy, promotion to paper or live, raising limits or allocations, clearing a kill-switch latch, viewing or rotating broker keys, adding passkeys, holdout scheduling and evaluation, raising AI-analyst autonomy or budgets, enabling mobile order entry.
- [ ] Phone halt path: engaging a kill switch from the phone requires a fresh passkey assertion with user verification (not a full step-up flow); clearing the latch is desktop-only with step-up.
- [ ] Rate limiting on auth endpoints with lockout and alert.
- [ ] Broker credentials: envelope encryption, per-record data key, AAD bound to `broker_account_id` and environment (`paper|live`) so a paper credential can never decrypt as live; write-only from the UI.
- [ ] Log redaction filter for API keys, tokens, `Authorization` headers, cookies; a test injects fake secrets through every service's error path and asserts they never appear in logs, traces, SSE payloads, or HTTP responses.
- [ ] Service-to-service authentication for the order path.
- [ ] Audit coverage: logins, session revocations, step-ups, config changes (before/after), credential operations (metadata only), strategy mode changes, risk limit changes, kill switch events, every order intent and risk decision, promotions, holdout requests, every analyst tool call.
- [ ] **Remote access (Tailscale)**: `tailscale serve` publishes the UI, Grafana, Prefect, MLflow, and optuna-dashboard on the tailnet only; grants restrict the phone to the UI port; Tailscale SSH in check mode; tailnet identity MFA required.
- [ ] Security headers: CSP without inline scripts, HSTS, `X-Content-Type-Options`, `Referrer-Policy`, `Permissions-Policy`, `frame-ancestors 'none'`.
- [ ] Input validation at the gateway against generated schemas; reject unknown fields on the order path.
- [ ] Supply chain: pinned dependencies with a 3–7 day cooldown, Actions pinned by SHA, Syft SBOM + Grype (fail on fixable criticals), gitleaks, optional cosign signing.
- [ ] LLM egress allowlist (only configured API hosts) and a test that planner requests contain no account identifiers, balances, positions, tickers, or calendar dates (section 12 masking).
- [ ] `docs/threat-model.md`: assets, actors, entry points (tailnet, Cloudflare hostname, webhook path, LLM API egress, broker APIs), STRIDE table, mitigations mapped to the research checklist (research 08 §5).

### Stage B
- [ ] Optional **Cloudflare Tunnel + Access** for the UI hostname only: identity plus independent passkey MFA, short session, country/device rules. Cloudflare terminates TLS and can see UI traffic; document the trade-off. Admin tools never go through it.
- [ ] TradingView webhook ingress (if enabled, Phase 5): a separate Access application and path with Service Auth and an IP-range selector for TradingView's published IPs; verify the client certificate CN where supported, a body HMAC/shared secret, timestamp + nonce replay protection; enqueue and return 200 within 3 s. The webhook actor can only create intents for its own `tv-<name>` strategy, which go through normal risk.
- [ ] Never route IB Gateway, Postgres, Valkey, NATS, MLflow write APIs, VNC, or the agent sandbox through any tunnel; VNC for IBKR recovery is bound to localhost and reached over Tailscale only.
- [ ] Broker-side least privilege: disable withdrawals or require extra verification where possible, enable broker-side alerts, rotate Alpaca keys quarterly (per-broker options to confirm during setup).
- [ ] Backup encryption keys and the age key escrowed outside the host (password manager or hardware key); restore drill uses them.
- [ ] Incident runbooks: suspected credential leak (rotate keys, revoke sessions, freeze trading), lost phone (revoke passkey and sessions), audit chain mismatch, unknown login.
- [ ] Run the research checklist (research 08 §5, items 1–32) and record the result; section 15 consumes it.

## Non-functional requirements

- **Performance**: auth check overhead under 2 ms p99 (session lookup cached).
- **Security**: OWASP ASVS 5.0 Level 2 as the reference for the web surface; no plaintext credentials at rest; least-privilege DB roles.
- **Usability**: passkey login on desktop and phone; recovery path documented and tested.
- **Tests**: table-driven policy tests (every action × actor × step-up state, including `analyst` and webhook actors); integration tests for login, step-up, session expiry, CSRF/Origin rejection, SSE auth; redaction test; audit tamper test.

## Acceptance criteria

All stages:
- [ ] Docs updated per `AGENTS.md` (master N6) in the same commits as the changes: `docs/file-map.md`, `docs/business-logic/security.md` (auth, step-up actions, remote access, audit), `docs/threat-model.md`, `CHANGELOG.md`, `docs/glossary.md` for new terms, and, for every decision made, an ADR in `docs/adr/` plus its row updated (or added) in `docs/decisions.md`.

Stage A:
- [ ] All API routes except `/healthz` reject unauthenticated requests (automated route inventory test).
- [ ] Owner registers two passkeys, logs in from desktop and phone over Tailscale, and recovers with the second passkey or TOTP.
- [ ] Every step-up action returns `RequireStepUp` without a fresh step-up; phone halt requires a fresh UV assertion; latch clearing from a mobile session is denied.
- [ ] Broker credentials are readable only by the broker adapters; other DB roles can't read the table; paper/live AAD separation tested.
- [ ] Redaction test passes for all services; LLM egress masking test passes.
- [ ] `make audit-verify` passes on a clean chain and fails after tampering; the daily anchor exists off-box.
- [ ] CI blocks a PR that adds a vulnerable dependency with a fixable critical or a committed secret.

Stage B:
- [ ] External scan finds no reachable services; if enabled, the Cloudflare UI hostname demands independent MFA and the webhook path rejects unsigned, replayed, or off-IP requests.
- [ ] Research security checklist and threat model signed off in the repo.
- [ ] Credential-leak drill completed (rotate paper keys end to end).

## Pitfalls to avoid

- Enforcing permissions or step-up only in the UI or in edge middleware.
- Broker keys in `.env` files, Compose files, env vars, or browser storage.
- JWTs in local storage for session auth.
- pgcrypto for credential encryption (keys leak via `log_statement`/`pg_stat_activity`).
- Logging request bodies on order or credential endpoints.
- LLM prompts containing credentials, account numbers, balances, positions, tickers, or calendar dates.
- Combining private data, untrusted text, and external communication in one agent path (the "lethal trifecta").
- Cloudflare Access *Bypass* policies (not logged) for the webhook.
- Registering passkeys on one hostname and then switching hostnames (`rpID` mismatch locks you out).
- Losing the age key or KEK with no escrow.
