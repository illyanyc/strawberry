# Threat model (Phase 0)

Scope: the self-hosted platform on the owner's Mac Mini, reachable only over Tailscale (optionally Cloudflare Access). Reviewed in Stage B (Phase 6) before live trading. Mitigations cite rules in [`business-logic/security.md`](business-logic/security.md) and [`operations.md`](business-logic/operations.md).

## Assets
| Asset | Why it matters |
|---|---|
| Broker credentials (Alpaca, IBKR) | Direct control of real money |
| IB Gateway API socket (unauthenticated TWS API) | Anyone who reaches it can trade the IBKR accounts; mitigated by the internal `ibkr` network and no published port (IB-071, SE-034) |
| Owner sessions and passkeys | Equivalent to the owner |
| Order path and risk configuration (Phase 2+) | Unauthorized or unbounded trading |
| Audit log | Accountability; detects tampering |
| Deploy-time secrets (KEK, gateway key, DB passwords) | Unlock the above |
| Market data and models | Integrity of decisions (Phase 1+) |

## Actors
Owner; remote attacker on the internet; attacker on the tailnet (stolen device); malicious or compromised dependency or image; prompt-injected content (news text, webhooks) reaching the assistant (Phase 4); the owner making a mistake (wrong environment, fat finger).

## Entry points
api-gateway HTTP (only published service, `127.0.0.1:8080` behind Tailscale serve / Cloudflare), Grafana (`127.0.0.1:3000`), the host itself (SSH via Tailscale), build supply chain (images, Go/Python/npm packages), the optional TradingView webhook listener (`:8090`, only through a dedicated Cloudflare Access hostname with Service Auth + TradingView IPs; SE-064), the gateway's internal Alertmanager webhook (`:8082`, backend network only; SE-063), and outbound Web Push to the browser vendors' push services (SE-060, SE-061).

## STRIDE
| Threat | Example | Mitigations |
|---|---|---|
| Spoofing | Phished password; forged service call; forged TradingView alert; request around Cloudflare Access | Passkeys only as strong factor (SE-003, SE-014); TOTP/recovery are reduced assurance; Ed25519 service tokens with per-method allowlists (SE-040); webhook IP allowlist, Access JWT, per-strategy body secret, 60 s window, idempotency (SE-064); Access JWT required on the public UI hostname (SE-064) |
| Tampering | Edit audit rows; modify credentials in DB; CSRF | Append-only hash chain verified by `make audit-verify` (SE-020, SE-022); AAD-bound envelope encryption (SE-030); CSRF origin + double-submit (SE-006) |
| Repudiation | "I didn't enable live trading" | Every login, step-up, setting, and credential change audited with IP/device and before/after hashes (SE-023, SE-025) |
| Information disclosure | Keys in logs, error messages, backups, browser; alert text on a lock screen; push endpoints | Redaction on every log path, tested with canaries end to end (SE-012); write-only credential API (SE-031); encrypted backups (OP-050); secrets as files only (SE-010); strict CSP, no inline scripts (SE-013); push redaction on by default (SE-062); push endpoints and keys sealed at rest, never returned (SE-061); webhook secrets terminal-only (SE-064) |
| Denial of service | Credential stuffing; resource exhaustion on the Mac | Rate limits + lockout with alerts (SE-011); container memory limits; no public ports |
| Elevation of privilege | Compromised service reads credentials; container escape; SSRF through push endpoints; a stolen unlocked phone | Least-privilege DB roles (SE-032, SE-050); non-root, read-only, cap-drop-all containers, `no-new-privileges` (OP-040); internal-only backend network (OP-042); single policy function (SE-001); push-host allowlist (SE-061); phone halts need a passkey and phones cannot release or (by default) trade (RM-020, RM-007, UI-015) |

## Residual risks (accepted for Phase 0)
- Host root compromise exposes `.secrets/` (including the KEK). Mitigated by FileVault, Tailscale-only access, no public ports; Phase 6 may move the KEK behind OpenBao or hardware-backed unwrap.
- Internal traffic is not encrypted (single host; ADR-0007). mTLS before multi-host.
- Alloy has read-only Docker socket access (observability only, ADR-0014).
- Pushover and Cloudflare are third parties: no secrets or account data are sent to them (alert texts are redacted summaries).
- Web Push goes through Google, Apple, Mozilla, or Microsoft push services: payloads are end-to-end encrypted (RFC 8291) and redacted by default; the vendors see timing and device only.
- A leaked TradingView strategy secret plus a request from TradingView's IP range can create signals for that one strategy; its limits, paper mode, and kill switch bound the effect; rotate `tv_webhook_key` (runbook `tradingview-webhook.md`).
- The opt-in TradingView widget (SE-065) loads TradingView code in a sandboxed cross-origin frame and tells TradingView which symbols the owner views.
