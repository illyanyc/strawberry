# ADR-0023: IBKR adapter: pinned IB Gateway, per-environment `ib_async` process, maintenance-aware session supervision

- **Status:** Accepted
- **Date:** 2026-10-17
- **Decision register:** D41 (implements D1 and D18 for section 04 on the D36 interface)
- **Related:** [brokers-ibkr.md](../business-logic/brokers-ibkr.md) (IB-001..IB-073), [brokers.md](../business-logic/brokers.md) (BR-), SE-034, OP-041; [ADR-0018](0018-broker-gateway-interface.md), [ADR-0004](0004-core-language-split.md), [ADR-0011](0011-secrets-management.md), [ADR-0016](0016-remote-access.md); research [04 §3](../research/04-brokers-data-ingestion.md#3-ibkr-integration-notes), [09 D18](../research/09-decisions.md#d18); runbook [ibkr-weekly-2fa](../runbooks/ibkr-weekly-2fa.md)

## Context

D18 accepted IB Gateway + IBC in the gnzsnz container with a Python `ib_async` adapter, a weekly 2FA runbook, and a manual VNC login as the fallback. D1 makes the adapter its own service; D36 (ADR-0018) fixes the gRPC contract it serves. Facts that shape the design (research 04 §3, checked 2026-10-17):

- IBC was archived on 2026-09-01; its last release 3.24.2 is bundled in `ghcr.io/gnzsnz/ib-gateway`, whose `stable` tag (`10.50.1f`, digest `sha256:1e2236…`) ships native `linux/arm64`.
- IBKR invalidates sessions on Sunday after 01:00 ET; IBC's daily `AutoRestartTime` reuses the session, its Sunday-only `ColdRestartTime` performs the full login (2FA push for live). Restarting the container mid-week forces a new 2FA.
- The TWS API socket is unauthenticated: anything that can reach the port can trade.
- `orderRef` length and persistence are undocumented (spike V1); `permId` is IBKR's durable order key.
- No IBKR credentials exist for the build, so everything must be testable against fakes, with real-gateway tests opt-in.

## Decision

1. **Containers.** `ib-gateway-paper` (profile `ibkr`) and `ib-gateway-live` (profile `ibkr-live`) run the pinned image (tag + digest, IB-073), `TIME_ZONE=America/New_York`, `AUTO_RESTART_TIME=11:45 PM`, `TWS_COLD_RESTART=17:00` (Sunday), `TWOFA_TIMEOUT_ACTION=restart` + `RELOGIN_AFTER_TWOFA_TIMEOUT=yes` (IBC re-pushes 2FA until approved), `TWS_MASTER_CLIENT_ID=1`, settings in a volume. Health = the API forwarder is listening; login state is the adapter's job (a pending 2FA must not block `make up --wait`).
2. **Credentials (SE-034).** The IBKR username and password are deploy-time secret files mounted only into the matching gateway; the username is read from its file by the entrypoint, never placed in Compose or the environment. They are not in the envelope-encrypted store (SE-030) because IBC needs them in plaintext at gateway start and the gateway cannot call `broker-gateway`; the adapter itself holds no broker credential.
3. **Network (IB-071, IB-072).** The API port is never published. Gateways and adapters share the internal `ibkr` network; gateways reach IBKR over `ibkr-egress`, which nothing else joins. VNC runs in the gateway (password from a generated secret) and is exposed only by the on-demand `ibkr-vnc` socat proxy on `127.0.0.1:5900` (`make ibkr-vnc`), which does not restart the gateway; the owner tunnels to it over SSH on Tailscale. OP-041 gains this one exception.
4. **Process model.** One `broker-gateway-ibkr` process per environment serves `tb.broker.v1.BrokerGatewayService` for the accounts mapped in `TB_IBKR_ACCOUNTS` (`ibkr-<env>-<alias>`); a live process stays `stopped` without both live gates (BR-013). Client ID 1 is the order connection (master client: sees TWS/Mobile orders), 2 market data. Account and environment guards (IB-005/IB-006) run on every connect.
5. **Supervision.** A per-connection state machine (BR-010) with heartbeats, equal-jitter backoff, 1100/1101/1102 handling, resync before `ready`, and an outage classifier driven by the maintenance calendar: expected outages (daily restart, weekend) are quiet; the weekly cold restart sends a reminder and pages if the session is not ready within 3 minutes; anything else pages after the grace. Alerts go to `alert.<severity>` and to `tb_ibkr_alert_active`, which Prometheus routes to Pushover (the delivery path that exists today).
6. **Attribution (V1 interim).** `orderRef` = attribution ID, limit `TB_IBKR_ORDER_REF_MAX_LEN=44` until spike V1 runs; the first IBKR echo of every `orderRef` is compared with what was sent and a mismatch fails the session closed until restart (IB-002). `permId` is `broker_order_id`.
7. **Events and audit (ADR-0018 §5).** Executions are joined with commission reports (2 s wait). Each new execution, distinct order event, snapshot, and reconciliation request is written with its outbox row in one transaction in the IBKR-owned schema `broker_ibkr` (same shapes as `broker`, one outbox per owning schema as `libs/go/outbox` prescribes); a Python relay (twin of `outbox.Relay`, `FOR UPDATE SKIP LOCKED`) publishes to JetStream with the same subjects, protobuf payloads, and `Nats-Msg-Id` values as the Go adapters (`exec:<acct>:<execId>`, `ord:…`, `acct:…`, `recon:…`). Without a database (dev only) a file cursor gives at-least-once. The live gate reads `broker.live_enablement` (SELECT only) and re-checks it every minute. Submit/cancel/replace are audited through `audit.append` as `svc_broker_gateway_ibkr` *before* sending and fail if the append fails (SE-021).
8. **Testing without credentials.** (a) An in-process fake gateway behind the `IbPort` seam for orders, fills, outages, 2FA, timeouts, truncation; (b) a socket-level fake TWS server so the real `ib_async` client is exercised for handshake, resync, heartbeats, and status codes; (c) recorded-format fixtures replayed through real `ib_async` objects and the production converters, plus a recorder (`TB_IBKR_RECORD_PATH`, account codes replaced) so paper sessions can produce real recordings; (d) an opt-in paper suite and the V1 spike (`make test-ibkr-paper`).

## Alternatives considered

| Option | Why not |
|---|---|
| Client Portal / Web API | OAuth is not available to individual accounts; CPGW needs a same-host browser login daily and a `/tickle` every few minutes (research 04 §3). |
| IBKR credentials in the envelope store, injected by `broker-gateway` | IBC reads them at start inside a third-party image; adding a credential-fetch hook to that image would widen SE-031's boundary for no gain. Mounted files keep custody on the host (SOPS-backed). |
| Publish the API port on `127.0.0.1` | Every local process (and anything with host loopback access) could trade; the internal network limits it to the adapter. |
| VNC always published on loopback | Needed a few minutes a week at most; on-demand proxy keeps the default surface at zero without restarting the gateway (which would force a 2FA). |
| One adapter process for paper and live | A configuration slip could route a paper strategy to live; per-environment processes plus IB-006 make that a hard failure. |
| Write into the Go adapter's `broker.outbox` | Two services sharing one schema's tables blurs ownership (`broker` is owned by broker-gateway) and couples IBKR publication to broker-gateway's relay being up; a separate `broker_ibkr` schema with the same shapes keeps the contract identical for consumers. |
| File cursor + direct JetStream publish only | At-least-once only, and a crash between publish and cursor write can re-publish after the 10-minute dedupe window; kept only as the no-database dev fallback. |
| Bracket orders on IBKR in Phase 1 | Needs child-order attribution and leg tracking (BR-018); IBKR swing strategies use separate GTC stops (D30). Capabilities say `supports_bracket=false`. |

## Consequences

- Positive: IBKR is isolated (process, network, credentials); the expected weekly and daily outages do not page; the owner is paged exactly when only they can fix it (2FA, gateway down); every IBKR behavior is covered by tests that run in CI without IBKR.
- Negative / risks: IBC is archived, so a future Gateway may break it (fallback: stay on the pinned build, community fork, manual VNC login). The fake gateways encode our reading of IBKR's behavior; spike V1 and the paper suite must confirm it before live. The adapter depends on Postgres for audit and the outbox: a database outage refuses orders (SE-021) and delays, but does not lose, event publication.
- Follow-up: run spike V1, the paper rehearsal, and the BR-001 suite in remote mode against the paper account (owner); instrument-master lookup when section 03 serves IBKR IDs; IBKR market-data streams if D20 ever needs them.

## Validation

`services/broker-gateway-ibkr/tests` (fake gateway, wire fake with real `ib_async`, fixtures, gRPC surface, entrypoint, Postgres journal and relay) and the shared Go contract suite against the adapter (`make test-ibkr-contract`) in CI; `make test-ibkr-paper` and one rehearsed weekly cycle on the paper account (runbook) before any live enablement. Revisit if IBKR changes the re-auth cadence, if IBC stops working with the pinned Gateway, or if V1 shows a limit below 44.
