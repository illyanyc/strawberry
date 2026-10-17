# ADR-0016: Remote access through host `tailscaled`, optional Cloudflare Tunnel + Access

- **Status:** Accepted (research)
- **Date:** 2026-10-17
- **Decision register:** D17
- **Related:** research [09 §D17](../research/09-decisions.md#d17), [08 §1](../research/08-security-remote-access-risk.md); rules SE-051, SE-004; [ADR-0009](0009-owner-authentication.md)

## Context

The owner must log in from anywhere (desktop and phone) with no open ports. Research: the open-source `tailscaled` system daemon runs before login (unlike the App Store variants) and enables Tailscale SSH for remote FileVault unlock; Cloudflare Access gained independent passkey MFA and is the only practical path for TradingView webhooks (ports 80/443). App-level passkey auth applies in every case. IB Gateway and databases are never tunnelled.

## Decision

- **Primary**: `tailscaled` on the Mac host; `tailscale serve --bg --https=443 http://127.0.0.1:8080` gives an HTTPS `*.ts.net` URL. The gateway's `TB_AUTH_RP_ID` / `TB_AUTH_ORIGINS` are set to that hostname; `TB_TRUSTED_PROXIES` to the proxy's source network so client IPs are logged correctly.
- **Optional**: Compose profile `remote-cloudflare` runs `cloudflared` with a tunnel token from secrets; the Cloudflare public hostname routes only to `http://api-gateway:8080`, protected by a Cloudflare Access application with its own passkey MFA. A webhook-only relay for TradingView (section 14) is added later behind Access Service Auth, never Bypass.
- Services publish only `127.0.0.1` ports (gateway 8080, Grafana 3000); Grafana is reached over Tailscale or an SSH tunnel, never Cloudflare.
- Tailnet ACLs and tailnet MFA are configured by the owner (Stage B checklist).

## Alternatives considered

| Option | Why not |
|---|---|
| Port forwarding / public IP | Violates N1 (no public exposure). |
| Tailscale App Store app | Does not run before login; no SSH unlock. |
| Cloudflare only | Third party terminates TLS for the whole UI; keep it optional. |
| WireGuard by hand | More operational work than Tailscale for one owner. |

## Consequences

- Positive: zero inbound ports; phishing-resistant auth at two layers when Cloudflare is used.
- Negative / risks: WebAuthn RP ID is bound to one hostname; passkeys registered on `localhost` do not work on the `ts.net` name, so register passkeys on the hostname you will use remotely.

## Validation

`make port-check` (loopback-only publications); Stage B external scan of the public IP (Phase 6).
