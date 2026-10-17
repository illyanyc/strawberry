# ADR-0019: Hand-written Alpaca adapter, in-process simulator, and the single market-data socket

- **Status:** Accepted
- **Date:** 2026-10-17
- **Decision register:** D37 (implements D1, D20 for section 04)
- **Related:** [ADR-0018](0018-broker-gateway-interface.md), [brokers.md](../business-logic/brokers.md) BR-001..BR-021, OE-002..OE-011, SE-012, SE-031, research [04 §2](../research/04-brokers-data-ingestion.md), [06 §1](../research/06-architecture-and-stack.md)

## Context

Section 04 needs an Alpaca adapter (REST trading, `trade_updates` stream, market data REST + stream) and a deterministic simulator for tests and the `dev` setup. Constraints: decimals end to end (OE-007), no blind resubmits (OE-003), one market-data socket per feed and key (Alpaca returns 406 for a second one), no account-wide liquidation endpoint in any code path (N3), credentials only in memory and never logged (SE-012), and a priority budget so data jobs never starve orders (research 04 §6.1). No credentials exist yet, so everything must be testable offline.

## Decision

1. **Hand-written REST/WebSocket client** (`services/broker-gateway/internal/alpaca`) instead of the official `alpaca-trade-api-go` SDK. The adapter owns: the route table (BR-020 is enforced by refusing any route not listed), the ambiguity classification of failures (`ErrNotSent` vs ambiguous vs `RejectError`, BR-007), `json.Number` decoding so prices stay exact decimals, priority token buckets (cancel > submit > query > bulk, BR-016) with `Retry-After`, and redaction of every error string.
2. **Canonical execution ID** `<order_id>:<cumulative filled qty>`, identical on the stream (`order.filled_qty` after the fill) and in `GET /v2/account/activities/FILL` (`cum_qty`) (BR-008).
3. **Snapshots** by polling `GET /v2/account` + `/v2/positions` every 15 s and after every fill. The removed PDT fields are not read (`day_trade_count = -1`, D30). A margin call is derived as `equity < maintenance_margin`; the intraday-margin-deficit field of Alpaca's 2026-06 framework is read when present (name to be confirmed by the opt-in paper test).
4. **Session loop per account** (`adapter.Account`): connect (REST) → subscribe stream → resync → `READY`; stream loss → `DEGRADED` (cancels still allowed) → resubscribe → resync. Missed executions are recovered by the resync and published once through the outbox (ADR-0018).
5. **Simulator** (`internal/sim`) implements the same `adapter.Broker` interface in-process with a small matching engine (market/limit/stop/stop-limit, IOC/FOK, bracket/OCO/OTO legs, replace) and fault hooks (ambiguous or lost submits, disconnects, duplicate and out-of-order execution messages, rejects, margin flags, manual orders). Local stacks run one simulator account (`TB_SIM_ACCOUNTS=sim-paper`) instead of a separate Compose profile: the simulator is a configuration of broker-gateway, not a service.
6. **Market-data hub** (`internal/mdhub`): one upstream `DataStream` per feed, created on first use, subscribing the union of all clients' symbols; bars and updated bars are never dropped (a slow client is disconnected and repairs from REST), quotes and trades may be dropped for a slow client. A 406 sets `FEED_STATE_CONNECTION_LIMIT`, alerts, and backs off at least the 60 s cap.
7. **Testing without credentials**: `internal/alpaca/alpacatest` is an `httptest` fake that speaks Alpaca's JSON shapes for REST and both WebSockets, backed by the simulator's matching engine. The shared contract suite (`internal/contract`, BR-001) runs against the simulator, the fake, Alpaca paper (opt-in via `TB_ALPACA_PAPER_KEY_ID`/`TB_ALPACA_PAPER_SECRET`), and any remote adapter (`TB_BROKER_CONTRACT_ADDR`, used by the IBKR track).
8. **Credentials** are decrypted from the store at most once per minute per account and registered with the redactor; a missing credential leaves the account `DISCONNECTED` with reason "no credential" and a 60 s retry, so adding keys in the UI brings it up without a restart.

## Alternatives considered

| Option | Why not |
|---|---|
| Official Go SDK | Hides the HTTP layer we must control (timeouts vs ambiguity, route allowlist, priority budget, raw error codes); exposes `CloseAllPositions`/`CancelAllOrders` helpers we must never call; float64 in some models. |
| Separate simulator service in a `dev` Compose profile | Another container and gRPC hop for no benefit; the in-process simulator exercises the exact gRPC surface and session logic. |
| WireMock-style recorded HTTP replays only | Cannot model state (resting orders, fills, cancels) or the streams; the simulator-backed fake can. |
| One data socket per subscriber | Alpaca allows one per feed and key (406); the hub is required by research 04 §2 gotcha 1. |

## Consequences

- Positive: the whole adapter, including WebSockets and the contract suite, runs in CI in seconds without secrets; account-wide endpoints cannot be reached; prices never pass through float64.
- Negative / risks: we maintain the Alpaca client ourselves (API drift). Mitigation: the opt-in paper contract run, and fixtures to be re-recorded from real responses once keys exist (`tests/fixtures/alpaca/README.md`). The fake's JSON is hand-built from public docs and may differ in fields we do not use.
- Follow-up: section 07 consumes `ord.update.*`/`exec.*` and the `SUBMIT_OUTCOME_UNKNOWN` path; section 15 writes `broker.live_enablement`.

## Validation

`TestBR001ContractSimulator`, `TestBR001ContractAlpacaFake`, and rule tests `TestBR002`..`TestBR021` in CI; `TestBR001ContractAlpacaPaper` when the owner sets paper keys. Revisit if the IMD field name differs, or if Alpaca changes the activities/stream fill fields that the canonical ID depends on.
