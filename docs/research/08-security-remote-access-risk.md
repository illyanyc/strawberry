---
title: Research 08 – Security, remote access, risk management, and live operations
covers: research prompt §9, §12; deliverable 9; confirmed input (secure remote login to the Mac Mini)
updated: 2026-10-17
---

# 08 · Security, remote access, risk management, and live operations

[← Report index](../research-report.md)

**Bottom line.**
- **Remote access.** Use two layers, neither of which opens an inbound port:
  - **Tailscale** is the private admin plane: SSH, FileVault unlock, Grafana, VNC, and the UI from your own devices.
  - **Cloudflare Tunnel + Access**, with independent passkey MFA, is optional. Use it for a public UI URL on devices without Tailscale, and as the only path for TradingView webhooks.
- **Authentication.** App-level passkeys and server-side sessions sit behind both tunnels, with step-up auth for dangerous actions.
- **Credentials.** Broker credentials use application-level envelope encryption with SOPS + age for deploy secrets.
- **Risk.** Pre-trade controls follow the SEC 15c3-5 model, with a latched kill switch, a dead-man heartbeat, and the Knight Capital lessons on deploy hygiene.

---

## 1. Secure remote web login to the Mac Mini

### 1.1 Options compared

| Option | Inbound port | Client needed | Auth / MFA | Works pre-login on Mac | Public webhook | Who sees traffic | Cost (solo) | Notes |
|---|---|---|---|---|---|---|---|---|
| **Tailscale** (tailnet + Serve) | None | Tailscale app on each device | IdP login + device approval; grants/ACLs; SSH check mode ([grants](https://tailscale.com/kb/1324/grants)) | **Yes, only with open-source `tailscaled`**; App Store and Standalone apps don't run pre-login ([KB](https://tailscale.com/kb/1065/macos-variants)) | Funnel on `*.ts.net`, ports 443/8443/10000 only ([Funnel](https://tailscale.com/kb/1223/funnel)) | End-to-end WireGuard; TLS terminates on your Mac | Free Personal (6 users), **non-commercial only** ([pricing](https://tailscale.com/pricing)) | `tailscale cert` names land in public CT logs, so use non-descriptive hostnames ([KB](https://tailscale.com/kb/1153/enabling-https)) |
| **Cloudflare Tunnel + Access** | None | **None** (browser) | IdP + **independent TOTP / WebAuthn / passkey MFA** per app (since 2026-04-15) ([changelog](https://developers.cloudflare.com/changelog/post/2026-04-15-independent-mfa/)); Service Auth for machines | No; cloudflared runs in the VM after the FileVault unlock | **Yes**: separate Access app with Service Auth + IP-range selector; avoid *Bypass* (not logged) ([policies](https://developers.cloudflare.com/cloudflare-one/access-controls/policies/)) | **Cloudflare terminates TLS and can see traffic** | Free up to 50 users ([plans](https://www.cloudflare.com/plans/zero-trust-services/)) | Needs your own domain; dashboards are fine under the ToS ([ToS update](https://blog.cloudflare.com/updated-tos/)) |
| Pangolin (self-hosted) | 443 on a VPS, none at home | None | Built-in SSO; MFA **[UNVERIFIED]** | Newt connector (container) | Yes | Your VPS | VPS cost | Good fit once a droplet exists; one more server to secure ([repo](https://github.com/fosrl/pangolin)) |
| Headscale | Control server needs a port (VPS) | Tailscale clients | OIDC | Via tailscaled | No Funnel | You | VPS | Only if avoiding Tailscale's coordinator matters ([releases](https://github.com/juanfont/headscale/releases)) |
| NetBird / ZeroTier / Twingate / ngrok | None | Client (ngrok: none) | Varies | Varies | ngrok yes | Varies | Varies | Not researched in depth **[UNVERIFIED]** |
| Raw WireGuard / port forward | **Yes** | Client | Keys only | Yes | — | You | Free | Rejected: open port, and CGNAT on home ISPs |

### 1.2 Recommendation (confirms and extends D17)

1. **Tailscale is primary.**
   - Install open-source **`tailscaled` as a system LaunchDaemon** on the Mac, with the Tailscale app on the phone and laptop.
   - `tailscale serve` publishes the UI (HTTPS, MagicDNS certificates), Grafana, Prefect, MLflow, and the Optuna dashboard *on the tailnet only*.
   - It also gives Tailscale SSH (check mode) and SSH-based **FileVault unlock after reboot**.
   - Grants let the phone reach only the UI port and nothing else.
2. **Cloudflare Tunnel + Access is optional, for "log in from any browser".**
   - It publishes **only** the UI hostname behind Access.
   - Access policy: your identity plus **independent passkey MFA**, short session, country/device rules.
   - Cloudflare can see the decrypted traffic. Accept that for the UI, or leave this layer off. It never carries admin tools.
3. **Webhook ingress (only if TradingView alerts are enabled)** runs on a *separate* Access application and path.
   - Service Auth with an IP-range selector for TradingView's 4 IPs, plus a body HMAC/secret and client-cert check, plus enqueue-and-200 within 3 s.
   - Tailscale Funnel is the alternative if no custom domain is wanted.
4. **The app's own auth always applies** behind both tunnels: passkeys, server-side sessions, step-up. Network identity is a second factor, never the only one.
5. **Never route** IB Gateway, Postgres, Valkey, NATS, MLflow write APIs, or the agent sandbox through any tunnel. Bind container ports to `127.0.0.1`.
6. **Everything moves unchanged to DigitalOcean.** Add a DO Cloud Firewall that denies all inbound except UDP 41641 for Tailscale ([DO firewalls](https://docs.digitalocean.com/products/networking/firewalls/)), and note that **Docker-published ports bypass UFW** ([Docker](https://docs.docker.com/engine/network/packet-filtering-firewalls/)).

**Where a different answer would change this:**
- If the owner can't install Tailscale on a work laptop, Cloudflare Access becomes the primary UI path.
- If the platform becomes a business, Tailscale Personal's non-commercial terms require a paid plan, and data licences change too (see [04](04-brokers-data-ingestion.md)).

## 2. Authentication (confirms D15)

- **Primary factor:** passkeys (WebAuthn, user verification required). **Recovery:** a second passkey or TOTP. **No SMS.**
- **Implementation in the Go gateway:** **go-webauthn** v0.18 ([releases](https://github.com/go-webauthn/webauthn/releases)). With the SPA there is no Node server, so Better Auth (now Vercel-owned, MIT; [Vercel](https://vercel.com/blog/vercel-acquires-better-auth)) isn't needed.
- `rpID` must match the hostname, so register passkeys per hostname (`*.ts.net` and the Cloudflare domain).
- **Sessions per OWASP ASVS 5.0** ([V7](https://asvs.dev/v5.0.0/V7-Session-Management/)):
  - Server-side, with a 128-bit or larger CSPRNG ID.
  - `HttpOnly; Secure; SameSite=Strict` cookies.
  - Rotate on login and on step-up.
  - 15–30 min idle and 12 h absolute timeouts.
  - A session list with revoke-all.
- **Step-up auth (V7.5.3)** for:
  - Enabling live trading or a strategy
  - Raising limits or allocations
  - Clearing a kill-switch latch
  - Viewing or rotating broker keys
  - Adding passkeys
  - Holdout scheduling
  - Model promotion
- **Multi-user later:**
  - **Pocket ID** (passkey-only OIDC, BSD-2, light; [repo](https://github.com/pocket-id/pocket-id)) or Authelia for small setups.
  - Keycloak 26 (Apache-2.0) or Authentik for full IAM.
  - Zitadel is AGPL since v3 ([Zitadel](https://zitadel.com/blog/apache-to-agpl)).
  - Every table already carries `owner_id`/`tenant_id` (master U7). Data licences are the bigger multi-user blocker.

## 3. Secrets (confirms D16)

- **SOPS + age** for deploy-time config committed encrypted (SOPS is CNCF; v3.12 adds hybrid post-quantum age; [releases](https://github.com/getsops/sops/releases)). The age private key stays off the repo and is protected at rest by FileVault on the Mac.
- **Broker credentials:**
  - **Application-level AES-256-GCM envelope encryption**: one data key per record, wrapped by a KEK held outside Postgres.
  - Avoid pgcrypto. Keys can leak via `log_statement`/`pg_stat_activity`, and raw functions lack integrity ([PostgreSQL](https://www.postgresql.org/docs/current/pgcrypto.html)).
  - Only `broker-gateway` processes can unwrap.
  - Separate paper and live credentials and containers.
  - The IBKR username and password live only in the IB Gateway container's secret file.
- **Never use env vars for secrets.** Docker deliberately mounts secrets as files under `/run/secrets` because env vars leak via `docker inspect`, logs, and child processes ([Docker](https://docs.docker.com/compose/how-tos/use-secrets/)). Compose file secrets are unencrypted bind mounts, so FileVault plus file mode 0400 is the at-rest control. Builds use `RUN --mount=type=secret`.
- **Vault CE is BSL** (IBM). **OpenBao 2.7** (MPL, Linux Foundation) is the drop-in if a secrets server is ever wanted ([OpenBao](https://openbao.org/)).
- **Broker-side least privilege:** disable withdrawals or require extra verification where the broker allows, enable broker-side alerts, and rotate Alpaca keys quarterly **[per-broker options UNVERIFIED]**.

## 4. Risk management and live operations

### 4.1 Position sizing (evidence in [01](01-strategies-and-models.md), [02](02-labeling-features-validation.md))
- **Volatility targeting is for risk control, not alpha.** It doesn't systematically outperform out-of-sample ([Cederburg et al. 2020](https://doi.org/10.1016/j.jfineco.2020.04.015)), but it reduces extreme returns and vol-of-vol ([Harvey et al. 2018](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3175538)).
- **Fractional Kelly (¼–½)** caps sizing when the edge estimate is reliable. Otherwise use fixed fractional sizing: risk per trade ≤ 0.25–0.5% of strategy capital, using the stop distance.
- **Limits per strategy and per account:**
  - Max position (% of strategy capital and % of ADV)
  - Gross and net exposure
  - Max positions
  - Sector cap
  - Correlation cap (pairwise ρ of position returns above 0.7 counts toward a cluster limit)
  - Daily loss limit (e.g. 2% of strategy capital → halt for the day)
  - Max drawdown (e.g. 1.5× the backtest p95 → demote to paper)

### 4.2 Pre-trade checks (SEC 15c3-5 as the model)
SEC Rule 15c3-5 requires *pre-trade* controls that reject orders before they reach the market. "Chase and cancel" doesn't comply ([eCFR](https://www.ecfr.gov/current/title-17/chapter-II/part-240/subject-group-ECFR3dd95cf4d3f1f9b/section-240.15c3-5); [SEC FAQ](https://www.sec.gov/divisions/marketreg/faq-15c3-5-risk-management-controls.htm)). The engine implements:
- **Order limits:** max notional per order and per day; max shares; price band (limit within X% of last/NBBO); orders-per-second throttle; duplicate `client_order_id`/intent detection.
- **Instrument and market:** symbol allowlist, market-hours/session checks, halt/LULD status, ETB/locate for shorts.
- **Ownership and capital:** virtual-ledger ownership guards (N3), symbol segregation, buying-power and intraday-margin guard (see [04 §1.2](04-brokers-data-ingestion.md#12-recommended-routing)).
- **Data freshness:** staleness of the strategy's inputs, plus the health of the broker session and market data.

### 4.3 Kill switch, circuit breakers, disconnects
- **Latched halt flag** in Postgres, checked by the OMS before every submission. Scopes: global, account, strategy, instrument. Actions: freeze → cancel strategy-owned open orders → optionally flatten strategy-owned quantity → reconcile → audit.
- Broker primitives:
  - Alpaca `DELETE /v2/orders`.
  - IBKR per-order cancel by `orderRef`, with `reqGlobalCancel` only for account scope.
  - Never account-wide liquidation endpoints.
- Clearing the latch needs desktop step-up auth.
- **Dead-man heartbeat** auto-halts and pages via Pushover emergency on any of:
  - Stale market data (more than 2 bar intervals)
  - A stale strategy loop
  - IBKR 1100 or a lost Alpaca stream
  - Reconciliation failure
  - A broker-reported margin call or IMD
- An **external** uptime monitor covers host death.
- **On reconnect:** block new intents → REST snapshot of orders, positions, and cash → reconcile → resume only if clean. Never guess order state (N5). Dedupe at-least-once trade updates on `broker_exec_id`.

### 4.4 Live monitoring and demotion
- **Live vs backtest divergence:** replay live bars through the backtester daily. Compare signals (should match exactly), fills (implementation shortfall vs the cost model), and P&L (should sit inside the backtest's simulated distribution).
- **Drift:** feature PSI (above 0.25 alerts), prediction-distribution shift, rolling IC vs its backtest band, and hit rate × payoff drift.
- **Auto-demotion** to paper or halted on drawdown, divergence, or recon breaks (N4). Re-promotion is manual.

### 4.5 Paper-trading graduation (confirms the master's Gate 6 defaults, with additions)
- At least 20 sessions **and** at least 100 trades in paper (swing: at least 3 months or MinTRL, whichever is longer).
- Live-vs-backtest divergence within tolerance.
- No unexplained reconciliation breaks.
- Max drawdown within the backtest's 95th percentile.
- DSR ≥ 0.95 on the combined research + paper record.
- Implementation shortfall within 1.5× the modeled cost.
- Then go live with a **small capital cap** (e.g. 10–20% of the intended allocation), stepping up after further MinTRL-based checkpoints.

### 4.6 Knight Capital lessons (deploy hygiene)
- 212 parent orders turned into 4M+ executions in 45 minutes, for a loss of over $460M.
- Causes: dead code left deployed, a deploy that missed 1 of 8 servers, a repurposed flag, and 97 unread error emails ([SEC 34-70694](https://www.sec.gov/files/litigation/admin/2013/34-70694.pdf)).
- **Mapping:**
  - Immutable image digests.
  - Version and config parity check at startup.
  - Refuse to trade on unknown flags.
  - Delete dead code paths.
  - Alerts go to a paging channel, never email.

## 5. Risk and security checklist (deliverable 9)

**Access and auth**
1. No inbound ports at home or on the droplet; Tailscale (tailscaled daemon) primary; Cloudflare Access optional; container ports bound to 127.0.0.1.
2. Passkeys (UV required) + second passkey/TOTP recovery; no SMS.
3. Server-side sessions per ASVS 5.0 V7; idle 15–30 min / absolute 12 h; revoke-all.
4. Step-up for live enable, limit or allocation changes, latch clear, key view or rotation, passkey add, promotion, and holdout scheduling.
5. Authorization in every API handler, never in edge middleware alone (CVE-2025-29927 lesson).
6. CSP without inline scripts; `Origin` checks on SSE/WS/POST; `SameSite=Strict` + CSRF tokens.
7. Tailscale grants restrict the phone to the UI; SSH in check mode; non-descriptive hostnames.

**Secrets and data**

8. Broker keys under app-level AES-GCM envelope encryption; KEK outside the DB; only broker-gateway unwraps.
9. Secrets as `/run/secrets` files (0400), never env vars, `ARG`, or `ENV`; SOPS + age in git.
10. Separate paper and live credentials and containers; live adapters disabled by default (N4).
11. FileVault on; SSH unlock path tested; age key protected.
12. No secrets or account data in logs, traces, LLM prompts, or errors (tested; N1).
13. LLM egress allowlist; API planner gets de-identified aggregates only.

**Integrity and supply chain**

14. HMAC hash-chained audit table; UPDATE/DELETE revoked; daily anchor of the chain head off-box.
15. GitHub Actions pinned by SHA and images by digest. Lesson from the Trivy tag hijack of 2026-03 ([GHSA](https://github.com/aquasecurity/trivy/security/advisories/GHSA-69fq-xp46-6x23)).
16. Syft SBOM + Grype scan; fail on fixable criticals.
17. Dependency cooldown of 3–7 days (Renovate `minimumReleaseAge`, uv `exclude-newer`); `--locked`, `go.sum`.
18. Docker Hardened Images (free, Apache-2.0 since 2025-12) or distroless; non-root; read-only rootfs; `cap_drop: ALL`; `no-new-privileges`.
19. Agent sandbox: no network, read-only data, quotas; scorer invisible to the agent.

**Trading risk**

20. Pre-trade checks (15c3-5 model) on every intent; every decision audited.
21. Virtual-ledger guards: sell, short, symbol segregation, no opposite resting orders, over-allocation.
22. Latched kill switch at four scopes; phone can halt only; resume from desktop with step-up; weekly paper drill.
23. Dead-man heartbeat plus external uptime monitor; Pushover emergency paging.
24. Reconnect → snapshot → reconcile → resume; idempotent on `broker_exec_id`.
25. Broker-side protective orders on every algorithmic entry.
26. Intraday-margin guard; any broker IMD or margin call halts the account.
27. Weekly IBKR 2FA runbook; IBKR strategies blocked until the session is healthy.

**Operations and compliance**

28. Immutable deploys, startup parity check, fail closed on unknown config (Knight).
29. restic encrypted off-site backups (Postgres, raw lake, artifacts, audit anchors); restore drill each quarter.
30. macOS: auto-install updates off, UPS shutdown and auto-restart, sleep disabled.
31. Records retained 7 years; cross-account wash-sale report; §475(f) decision reviewed annually before the deadline.
32. Data licences reviewed before any second user (FMP, Massive, IBKR pro/non-pro).
