---
title: "Section 04 – Broker Integration (Alpaca + IBKR)"
phase: "1"
depends_on: [00, 01, 02]
updated: 2026-10-17
---

# Section 04: Broker Integration (Alpaca + IBKR)

## Role

You are an engineer who has integrated retail and institutional broker APIs and has been burned by every one of their edge cases: dropped websockets, duplicate fills, ambiguous order states, daily re-auth, and pacing limits. You build a broker abstraction that hides vendor quirks without hiding failures.

## Context

The platform trades through Alpaca and IBKR, in paper and live environments. The `broker-gateway` service is the **only** component that holds decrypted broker credentials and talks to broker APIs for trading and account data. Market-data streaming may use the same connections (section 05 consumes them through this service's market-data interface). Every strategy order must carry an attribution ID so the ledger (section 06) can tell strategy orders from manual ones. Non-negotiables N1 (credentials), N3 (attribution), N4 (paper before live), and N5 (fail closed) apply.

## Scope

In scope:
- Broker abstraction interface and capability model.
- Alpaca adapter: auth, trading API, account/positions, trade-update stream, market-data client (historical + stream) exposed to section 05.
- IBKR adapter: IB Gateway lifecycle (containerized with IBC), connection management, orders, executions, account/positions, market data (historical + stream) with pacing.
- Broker simulator adapter for tests and the `dev` profile.
- Session health, reconnect, and state resync.
- Paper/live environment separation.
- Rate limiting and pacing per broker.

Out of scope:
- Deciding what to trade, risk checks, OMS state machine (section 07).
- Ledger and reconciliation logic (section 06), though this section supplies the raw account and execution data.
- Storing market data (sections 03, 05).

## Inputs and dependencies

- Section 02: `BrokerCredentialStore`, service-to-service auth, audit log.
- Section 01: Compose, IB Gateway container, secrets mounting.
- Master contracts: `OrderIntent`/`Order`, `ExecutionReport`, `AccountSnapshot`, attribution ID format.
- `INPUT PENDING (U1)`: account type (cash vs margin, PDT) affects capability flags.
- `INPUT PENDING (U2)`: asset classes. Default: equities and ETFs; contracts carry `asset_class`.
- `INPUT PENDING (U5)`: Alpaca data plan (IEX vs SIP) is a config value.

## Decisions pending

| Decision | Default | Marker |
|---|---|---|
| Implementation language | Go (if D1 = Go). Note: IBKR's official TWS API has Java/C++/Python/C# clients; Go clients are community-maintained. If no reliable Go client exists, implement the IBKR adapter as a Python sidecar (`ib_async`) behind the same gRPC interface. | `DECISION PENDING (research §6, §7)` (D1) |
| IBKR connectivity | IB Gateway + IBC in a container, TWS API socket; Client Portal Web API as fallback | `DECISION PENDING (research §6)` (D18) |
| Alpaca auth | API key + secret per environment (paper, live); OAuth only if research shows a benefit for self-use | `DECISION PENDING (research §6)` |
| Sub-accounts | Not used by default (D19); the adapter supports multiple `broker_account_id`s so dedicated accounts per strategy can be added | `DECISION PENDING (research §10)` |

Verify all limits named below against current broker documentation during implementation (research §6). Values change.

## Interfaces and contracts to expose

gRPC service `BrokerGateway` (consumed by `execution`, `ledger`, `marketdata`):

```
ListAccounts() -> [BrokerAccount{broker_account_id, broker, environment(paper|live), capabilities}]
GetAccountSnapshot(broker_account_id) -> AccountSnapshot
StreamAccountSnapshots(broker_account_id) -> stream AccountSnapshot
SubmitOrder(BrokerOrderRequest{client_order_id, broker_account_id, instrument_id, side, qty, type, limit, stop, tif, extended_hours, bracket?}) -> SubmitAck{broker_order_id, accepted_at} | Reject{code, message}
CancelOrder(broker_account_id, client_order_id | broker_order_id) -> CancelAck
ReplaceOrder(broker_account_id, client_order_id, changes) -> ReplaceAck{new_client_order_id}
ListOpenOrders(broker_account_id) -> [BrokerOrder]
ListOrders(broker_account_id, since) -> [BrokerOrder]          # includes non-platform (manual) orders
ListExecutions(broker_account_id, since) -> [ExecutionReport]  # for gap fill after reconnect
StreamOrderUpdates(broker_account_id) -> stream BrokerOrderUpdate
StreamExecutions(broker_account_id) -> stream ExecutionReport
GetSessionStatus(broker_account_id) -> SessionStatus{connected, authenticated, last_heartbeat, degraded_reasons[]}
MarketData: GetHistoricalBars(...), StreamBars(...), StreamQuotes(...), StreamTrades(...)  # used by section 05
GetInstrumentInfo(instrument_id | symbol) -> {tradable, shortable, easy_to_borrow, fractionable, min_tick, broker_ids}
```

Capability model per account: `{order_types[], tifs[], supports_bracket, supports_fractional, supports_short, supports_extended_hours, max_client_order_id_len, pdt_applies, is_margin, data_feeds[]}`. Execution consults capabilities before building orders.

Events published to the bus: `acct.snapshot.<acct>`, `ord.update.<acct>`, `exec.<acct>`, `sys.health.broker-gateway` (per-account session status), `alert.*` on auth expiry or disconnect.

## Functional requirements

### Abstraction
- [ ] Define the `BrokerAdapter` interface with the methods above; every adapter declares capabilities.
- [ ] Normalize order states into one enum (`pending_submit, accepted, partially_filled, filled, pending_cancel, canceled, rejected, expired, replaced, unknown`) with a documented mapping per broker.
- [ ] Map broker instrument IDs to `instrument_id` via the instrument master (section 03).
- [ ] Every outbound order requires a `client_order_id` matching the attribution format; the adapter rejects missing or malformed IDs. Manual orders from the UI also get an ID with the reserved `manual` short ID so they are distinguishable from orders placed outside the platform.
- [ ] Idempotent submission: if a submit times out, query by `client_order_id` before any retry; never resubmit blindly.
- [ ] Decimal quantities and prices throughout; round to broker tick/lot rules with explicit rounding mode.

### Alpaca
- [ ] Separate paper and live credentials and base URLs; environment is part of the `broker_account_id` and the credential AAD (section 02).
- [ ] Trading: market, limit, stop, stop-limit, trailing stop; TIF day/gtc/ioc/fok/opg/cls as supported; bracket/OCO/OTO orders where available; extended-hours flag; fractional orders where supported (verify constraints).
- [ ] Trade-update websocket stream for order and fill events; reconnect with backoff and gap fill from REST (`ListOrders`/activities since last seen).
- [ ] Account and positions via REST polling plus stream-driven updates; activities API for fills, dividends, and corporate-action cash flows (consumed by section 06).
- [ ] Market-data client: historical bars (with `feed` and `adjustment=raw`), real-time bars/quotes/trades websocket. Respect the account's connection limit (typically one concurrent stream connection per feed); the gateway multiplexes all subscribers over one connection.
- [ ] Respect trading API rate limits (verify current value, commonly 200 requests/min) with a token bucket and priority (cancels > submits > queries).
- [ ] Surface PDT status and day-trade count from the account endpoint into capabilities and snapshots.

### IBKR
- [ ] Containerize IB Gateway with IBC for automated login; credentials come from the secrets store; 2FA handled per research §6 (default: IBKR Mobile push approval with an alert to the owner when approval is needed). Separate containers for paper and live gateways.
- [ ] Handle the daily restart/reauth window: detect it, mark the session `degraded`, alert, reconnect automatically, and run a full resync (open orders, executions since last seen, positions) before reporting `ready`.
- [ ] Use distinct client IDs per connection purpose; one connection is the "master" for order updates.
- [ ] Orders with `orderRef` set to the attribution ID; contract resolution by `conId`; map IBKR order statuses (including `PreSubmitted`, `Inactive`, API `Cancelled`) carefully.
- [ ] Executions and commission reports joined into `ExecutionReport` (fees included).
- [ ] Account updates and positions via account summary/portfolio subscriptions; multiple accounts under one login supported as separate `broker_account_id`s.
- [ ] Market data: historical bars with a pacing scheduler (verify current pacing rules for small bar sizes and identical requests); real-time bars and tick data within the account's market-data line limit; surface missing subscriptions as clear errors.
- [ ] Handle IBKR error codes by category (connectivity, pacing, order reject, informational) with a documented table.

### Simulator
- [ ] A deterministic broker simulator implementing the same interface: configurable latency, partial fills, rejects, disconnects, duplicate execution messages, out-of-order updates, and manual orders injected "from outside the platform". Used by tests, the `dev` profile, and section 07's chaos tests.

### Session and safety
- [ ] Health state machine per account: `disconnected → connecting → authenticated → syncing → ready → degraded`. Only `ready` accounts accept orders; others return a typed `BrokerUnavailable` error.
- [ ] On reconnect: fetch open orders and executions since the last acknowledged exec ID, publish any missed updates (deduplicated by `broker_exec_id`), then request reconciliation from section 06 before returning to `ready`.
- [ ] Live environment adapters are not started unless `TRADING_LIVE_ENABLED=true` in config **and** the account is marked live-enabled by an audited owner action (section 15).
- [ ] Every submit, cancel, replace, and reject is written to the audit log with the attribution ID.

## Non-functional requirements

- **Performance**: submit path overhead (gateway receive → broker request sent) p99 under 20 ms; order update propagation (broker event → bus) p99 under 50 ms.
- **Reliability**: automatic reconnect with jittered exponential backoff; no tight reconnect loops; the gateway never loses an execution across restarts (persist last seen exec IDs/cursors).
- **Security**: only `broker-gateway` decrypts credentials; credentials never logged; outbound connections only to broker endpoints.
- **Tests**: adapter contract test suite run against the simulator and, behind a flag, against real paper accounts; recorded-message replay tests for both brokers; table-driven status mapping tests; fault-injection tests (disconnect mid-submit, duplicate fills, out-of-order partial fills).

## Acceptance criteria

- [ ] Docs updated per `AGENTS.md` (master N6) in the same commits as the changes: `docs/file-map.md`, `docs/business-logic/brokers.md` (state mapping, attribution, session lifecycle), `CHANGELOG.md`, `docs/glossary.md` for new terms, and, for every decision made, an ADR in `docs/adr/` plus its row updated (or added) in `docs/decisions.md`.
- [ ] The same contract test suite passes for the simulator, Alpaca paper, and IBKR paper.
- [ ] Submit-timeout test: the adapter queries by `client_order_id` and never creates a duplicate order.
- [ ] Disconnect test: orders are refused while not `ready`; after reconnect, missed fills are published exactly once and reconciliation is requested.
- [ ] IBKR daily reset is detected and recovered automatically in a simulated scenario; the owner receives an alert if 2FA approval is needed.
- [ ] A manual order placed directly in the broker's own app appears in `ListOrders` and `StreamExecutions` without a platform attribution ID (so section 06 assigns it to `manual`).
- [ ] Live adapters stay stopped without both the config flag and the audited enablement.

## Pitfalls to avoid

- Treating a timeout as a failure and resubmitting; this creates duplicate orders.
- Assuming fills arrive once and in order; dedupe on `broker_exec_id` and order by broker timestamps plus sequence.
- Ignoring partial fills and order replaces when tracking quantity.
- Using `symbol` as the key; tickers change and are reused. Use `instrument_id` and broker IDs.
- Opening a second Alpaca market-data websocket per subscriber and hitting the connection limit.
- Overlooking IBKR pacing violations, which silently throttle or disconnect historical requests.
- Mixing paper and live in the same process configuration; a typo should not route a paper strategy to live.
- Letting the IBKR daily reset look like a strategy failure, or worse, letting strategies trade during resync.
- Relying on broker-side bracket orders without the OMS knowing their child order IDs; attribution must cover child orders too.
