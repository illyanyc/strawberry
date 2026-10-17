# Runbook: security incidents

Alerts on `alert.security` (SE-041): auth failures, lockouts, recovery-code use, audit chain mismatch.

## Suspected broker-credential leak
1. Freeze trading (Phase 2: global kill switch). Until then, stop broker-gateway: `docker compose -f infra/compose/docker-compose.yml stop broker-gateway`.
2. Revoke the key at the broker (Alpaca dashboard / IBKR), create a new one, store it via Broker connections (step-up).
3. Revoke all sessions (Security → Active sessions) and rotate `gateway_auth_key` if a session token may have leaked.
4. Search logs for the key prefix in VictoriaLogs; there should be none (SE-012). File a bug if found.
5. Record the incident in the audit trail (Phase 2 adds an incident action).

## Lost or stolen device (passkey)
Log in from another passkey (or TOTP/recovery code), Security → remove the lost passkey (passkey step-up), revoke its sessions, register a replacement.

## Audit chain mismatch (`make audit-verify` fails)
Stop all writers; do not "fix" rows. Compare with the last backup's audit head (`backups/*` manifest). Restore into a scratch stack to identify the altered range; treat as host compromise until explained.

## Unknown login or lockout
Check the audit log for `auth.*.failure` / `auth.lockout` with IP and device; revoke unknown sessions; review Tailscale/Cloudflare access logs; rotate credentials if any login succeeded unexpectedly.
