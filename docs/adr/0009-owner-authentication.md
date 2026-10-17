# ADR-0009: Owner authentication with built-in passkeys, TOTP fallback, and server-side sessions

- **Status:** Accepted (research)
- **Date:** 2026-10-17
- **Decision register:** D15
- **Related:** research [09 §D15](../research/09-decisions.md#d15), [08 §1](../research/08-security-remote-access-risk.md); rules SE-003..SE-014; [ADR-0006](0006-audit-log-hash-chain.md)

## Context

A single technical owner logs in from a desktop and a phone, locally and remotely (Tailscale, optionally Cloudflare Access). A compromise means trading real accounts. Research confirmed the default (built-in passkeys via go-webauthn in the Go gateway; OIDC later only for multi-user) and recommended OWASP ASVS 5.0 V7 session handling.

## Decision

- **Passkeys (WebAuthn, discoverable, user verification required)** are the primary factor, implemented with `go-webauthn` in `api-gateway`. The owner should register at least two (laptop + phone).
- **TOTP** (RFC 6238, SHA-1, 6 digits, 30 s, ±1 step, replay-protected by counter) and **10 single-use recovery codes** are fallbacks. Sessions they create are *reduced assurance*: they can recover the account (register a passkey, re-enroll TOTP, regenerate codes) but cannot satisfy a passkey step-up (SE-014).
- **First-run setup** needs a one-time token printed by `make setup-token` on the host (30 min, hashed at rest, single use). Registration closes once an owner exists.
- **Sessions**: opaque 256-bit token in an `__Host-` `HttpOnly` `Secure` `SameSite=Strict` cookie; SHA-256 stored; idle 30 min, absolute 12 h; new ID on every login; list and revoke from the UI.
- **CSRF**: origin allowlist plus double-submit token header on every state-changing request.
- **Step-up**: 5-minute window bound to the session; passkey for money/safety actions, any fresh factor for recovery actions; enforced by `policy.Authorize`, not the UI.
- **Lockout**: 5 failures per 15 min per account and per IP → 15-min lock, audit event, `alert.security`; plus a per-IP token bucket on `/auth/*`.
- Gateway secrets (TOTP sealing key, code HMAC key) are HKDF subkeys of `gateway_auth_key`, a mounted secret.

## Alternatives considered

| Option | Why not |
|---|---|
| External IdP now (Authentik, Keycloak, Pocket ID) | Another stateful service to secure and back up for one user. Pocket ID or Keycloak if multi-user (U7). Zitadel is now AGPL. |
| Password + TOTP | Phishable; passkeys are strictly better for one owner. |
| JWT in local storage | XSS-exfiltratable, no server-side revocation. |
| TOTP as full-strength step-up | A stolen TOTP seed would unlock live trading; passkeys need the device and biometrics. |

## Consequences

- Positive: phishing-resistant login; every dangerous action needs a fresh passkey; revocable sessions.
- Negative / risks: losing all passkeys and the TOTP device leaves only recovery codes; the owner must store them offline.
- Follow-up: Stage B adds TLS hostnames for remote RP IDs (Tailscale `*.ts.net`, Cloudflare hostname) and the incident runbooks.

## Validation

`TestSEAuthEndToEnd` drives real WebAuthn ceremonies with a software authenticator against Postgres: setup, second passkey, TOTP enrollment and replay, logout, discoverable login, step-up expiry and renewal, reduced-assurance limits, recovery-code reuse, lockout, audit coverage, and absence of secrets in logs.
