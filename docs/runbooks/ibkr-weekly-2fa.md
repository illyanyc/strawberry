# Runbook: IBKR weekly login (2FA), manual VNC login, gateway outages

Alerts: `IbkrSessionCritical` (Pushover emergency), `IbkrSessionWarning`, `IbkrReauthRequired`, `IbkrAdapterDown`. Rules: IB-013..IB-015, IB-070..IB-073 ([brokers-ibkr.md](../business-logic/brokers-ibkr.md)); design: [ADR-0023](../adr/0023-ibkr-adapter-gateway-and-sessions.md).

While the IBKR session is not `ready`, IBKR strategies are blocked (fail closed). Swing positions stay protected by their resting GTC stops at IBKR.

## First-time setup (owner)

1. Put the login in the secret files (never in `.env` or Compose): `.secrets/ibkr_paper_username`, `.secrets/ibkr_paper_password` (and `ibkr_live_*` for live), one value per file. `make secrets` created placeholders containing `unset`; `make secrets-encrypt` backs them up.
2. Map accounts in `infra/compose/.env`: `TB_IBKR_PAPER_ACCOUNTS=bot=DU1234567` (and `TB_IBKR_LIVE_ACCOUNTS=bot=U1234567`). Only mapped accounts are visible to the platform; with the D19 linked "bot" account, map only the bot account.
3. In IBKR Mobile, enable IB Key push notifications for the live username.
4. Start: `make up PROFILES=core,observability,ibkr` (paper). Live: add `ibkr-live`; the live adapter stays `stopped` until `TB_TRADING_LIVE_ENABLED=true` **and** the audited per-account enablement exists (section 15).
5. Check: `make logs SERVICE=broker-gateway-ibkr-paper` shows `session status … ready`; Grafana `tb_ibkr_session_ready` = 1.

## Weekly login (Sunday, 17:00 ET by default)

IBKR invalidates sessions every Sunday after 01:00 ET. IBC cold-restarts the gateway on Sunday at `TB_IBKR_COLD_RESTART` (default 17:00 ET) and starts a full login.

1. At 17:00 ET you get a **warning**: "Weekly IBKR live login started: approve the IBKR Mobile 2FA push now."
2. Approve the push in IBKR Mobile. IBC finishes the login; the adapter reconnects, resyncs, requests reconciliation, and the warning resolves.
3. If you miss it, IBC re-sends the push every few minutes (`RELOGIN_AFTER_TWOFA_TIMEOUT`), and after 3 minutes a **critical** page arrives. Approve the newest push.
4. Paper usernames normally log in without 2FA; the same flow runs without a push.

Change the time: set `TB_IBKR_COLD_RESTART=HH:MM` (ET, after 01:00) in `infra/compose/.env` and `docker compose … up -d ib-gateway-live broker-gateway-ibkr-live` **on a Sunday after the session expired** (recreating the gateway mid-week forces a 2FA).

## Push not arriving: manual login over VNC

1. On the Mac Mini: `make ibkr-vnc IBKR_ENV=live` (or `paper`). This starts a proxy on `127.0.0.1:5900` without touching the gateway.
2. From your laptop on the tailnet: `ssh -L 5900:127.0.0.1:5900 <user>@<mac-mini>` and open a VNC client on `localhost:5900`. Password: `.secrets/ibkr_vnc_password` (8 characters; VNC uses only 8).
3. In the IB Gateway window, complete the login (choose another 2FA method if offered, or retry the push). Do not change API settings: IBC manages them.
4. `make ibkr-vnc-stop` as soon as the session is `ready`. Never publish VNC beyond loopback or through Cloudflare (SE-051).

## Gateway down mid-week

Alert text: "IBKR … gateway unreachable for N min (gateway_unreachable)".

1. `make ps`: is `ib-gateway-<env>` running? `make logs SERVICE=ib-gateway-<env>`: look for IBC lines about login, `Second Factor Authentication`, or Java errors.
2. If the gateway is waiting for 2FA (for example after the Mac rebooted or the container was recreated), approve the push or use VNC (above).
3. If the adapter is unhealthy but the gateway is fine, restart **only the adapter**: `docker compose -f infra/compose/docker-compose.yml restart broker-gateway-ibkr-<env>`. Restarting the gateway forces a new 2FA.
4. `daily_restart_overrun` (23:45 ET restart did not come back): the session token could not be reused; log in as above.
5. After recovery check that reconciliation ran (`recon.request.<acct>` in the adapter log) and, if an `execution_gap` warning fired, reconcile the day's fills against the IBKR statement (executions before today cannot be fetched over the API).

## Other critical IBKR alerts

| Alert subject | Meaning | Action |
|---|---|---|
| `…session` + `environment_mismatch` | A paper adapter sees a live account code or vice versa (IB-006) | Fix `TB_IBKR_*_ACCOUNTS` / the login files; restart the adapter |
| `…session` + `account_not_managed` | A mapped account is not under this login (IB-005) | Fix the mapping (linked account opened? correct code?) |
| `…session` + `order_ref_mismatch` | IBKR returned a different `orderRef` than sent (IB-002) | Stop IBKR strategies; run spike V1; set `TB_IBKR_ORDER_REF_MAX_LEN`; restart the adapter (the block is sticky until restart) |
| `…margin_deficit` | Negative excess liquidity (IB-041) | Account halted for new orders; resolve the deficit in IBKR; it clears on the next clean snapshot |

## Gateway upgrades (IB-073)

IBC is archived; treat any image change as a planned change. Pin a new tag **and** digest in `infra/compose/docker-compose.yml`, run it on paper for one full weekly cycle plus `make test-ibkr-paper`, then switch live on a Sunday before the cold restart. If IBC breaks on a new Gateway: stay on the last good pin, look for a maintained IBC fork, or log in manually over VNC each week.

## Paper rehearsal and spike V1 (owner, before live)

1. Run a paper IB Gateway (or TWS paper) whose API is reachable from your dev machine (port 4002 / 7497) — the stack's gateway does not publish its API port.
2. `TB_IBKR_PAPER_ACCOUNT=DU… TB_IBKR_PAPER_PORT=4002 make test-ibkr-paper` (writes `v1-order-ref.json`; add `TB_IBKR_SPIKE_FILLS=1` to also test executions).
3. Restart the paper gateway, then `TB_IBKR_SPIKE_PHASE=after_restart make test-ibkr-paper` to check persistence by `permId`.
4. Record the result in the "Spike V1" section of [brokers-ibkr.md](../business-logic/brokers-ibkr.md).
5. Run the shared contract suite against the paper adapter (IB-063): from a container on the `ibkr`/`backend` network or with the adapter's gRPC port reachable, `cd services/broker-gateway && TB_BROKER_CONTRACT_ADDR=<host>:9090 TB_BROKER_CONTRACT_ACCOUNT=ibkr-paper-bot TB_BROKER_CONTRACT_SEED_FILE=../../.secrets/svc_execution_ed25519 go test -run TestBR001ContractRemote ./internal/contract/`.
6. Rehearse one Sunday cold restart on paper and confirm the reminder, recovery, and reconciliation request.
