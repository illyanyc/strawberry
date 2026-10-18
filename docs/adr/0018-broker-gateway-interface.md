# ADR-0018: One gRPC broker interface for every adapter, with an outbox-backed event path

- **Status:** Accepted
- **Date:** 2026-10-17
- **Decision register:** D36 (implements D1, D2, D3, D18 for section 04)
- **Related:** [brokers.md](../business-logic/brokers.md) (BR-001..BR-021), OE-002..OE-011, VP-001/VP-002, SE-030..SE-040; research [04 §2–3](../research/04-brokers-data-ingestion.md), [06 §1–2](../research/06-architecture-and-stack.md); [ADR-0004](0004-core-language-split.md), [ADR-0008](0008-event-bus-nats-jetstream.md)

## Context

Two adapters are built in parallel in different languages: Alpaca (and the simulator) in the Go `broker-gateway`, IBKR in the Python `broker-gateway-ibkr` on `ib_async` (D1). The OMS (section 07), ledger (section 06), and marketdata (section 05) must not care which broker sits behind an account. Section 04 lists the RPCs; it leaves open the error model, how missed fills reach the bus exactly once, how market data reaches marketdata without giving it credentials, and how a broker's quirks (Alpaca's single data socket, self-cross 403, IBKR `permId`) surface without leaking into callers.

## Decision

1. **Contract.** `tb.broker.v1.BrokerGatewayService` (`contracts/proto/tb/broker/v1/gateway.proto`) is the only order/account interface. Each server lists the accounts it owns (`ListAccounts`); callers route by `broker_account_id`. The IBKR adapter serves the same service; nothing in the contract is Alpaca-specific except documented capability values.
2. **Market data** is a separate service, `BrokerMarketDataService` (`market_data.proto`), served by `broker-gateway` because only it decrypts credentials (SE-031) and Alpaca allows one data socket per feed (BR-015). It is bidirectional for subscriptions, so one upstream connection serves every subscriber. IBKR may answer `UNIMPLEMENTED`.
3. **Error model (BR-004).** Rejections are typed responses (`SubmitOutcome`, `RejectCode` with the raw `broker_code`); a non-ready session is `FAILED_PRECONDITION` + `ErrorInfo{reason: BROKER_UNAVAILABLE}`. `UNAVAILABLE` is avoided for order RPCs because gRPC retry policies retry it automatically. `SUBMIT_OUTCOME_UNKNOWN` makes "the broker could not confirm" explicit so callers fail closed instead of resubmitting (OE-003, OE-005).
4. **Idempotency and dedupe.** Submit is idempotent on `client_order_id` (lookup on timeout, never resubmit). Executions carry a canonical `broker_exec_id` that is identical on the stream and in REST resyncs (Alpaca: `<order_id>:<cum_qty>`, BR-008; IBKR: `execId` with `permId`).
5. **Durable events.** Adapters write each new execution (unique on canonical ID) and an outbox row in one Postgres transaction (schema `broker`, owned by broker-gateway); a relay publishes to `exec.<acct>`, `ord.update.<acct>`, `acct.snapshot.<acct>`, and `recon.request.<acct>` with `Nats-Msg-Id` = the natural key. The gRPC streams are conveniences; the bus is the durable path. This is the D2 transactional outbox, built here as `libs/go/outbox` so the OMS reuses it in Phase 2.
6. **Bus subjects.** New subject `recon.request.<acct>` (in the `LEDGER` stream) carries `tb.ledger.v1.ReconciliationRequest` after every reconnect resync (OE-008). NATS users `broker_gateway` and `broker_gateway_ibkr` publish only their account subjects, `sys.health.<service>`, and `alert.>`.
7. **Additive contract changes** (no new package version): `Bar` gains `source`, `revision`, `adjustment_basis`, `session`, `repaired`, `symbol`; `Quote`/`Trade` gain `symbol` (+ trade `trade_id`, `exchange`); `CorporateAction` gains symbol, dates, `declared_at`, `source_id`, `agreement`, `sources`, and type `SPIN_OFF`; `ExecutionReport` gains `perm_id`, `symbol`, `broker`, `sequence`, `fee_currency`, `correlation_id`; `Order` gains `perm_id`; `OrderState` gains `REPLACED`, `PENDING_REPLACE`; `OrderType` gains `TRAILING_STOP`; `Timeframe` gains `30M`; `AccountSnapshot` gains margin status (IMD, margin call), margin figures, `day_trade_count` (metric only, D30), blocked flags; `Position` gains `symbol`, `market_value`, `broker_instrument_id`.

## Alternatives considered

| Option | Why not |
|---|---|
| A Go `BrokerAdapter` interface only, IBKR via a Go client | No reliable Go IBKR client (research 06 §1); D1 puts IBKR in Python. |
| gRPC `UNAVAILABLE` for not-ready sessions | Retried automatically by gRPC retry policies; a retried submit is exactly the duplicate-order risk OE-003 forbids. |
| Publish executions straight to NATS from the stream handler | A crash between broker event and publish loses the fill on the bus; a crash after publish but before cursor persistence republishes with a new ID. The outbox makes both cases exactly-once by key. |
| Give marketdata its own Alpaca key | Breaks SE-031 (credentials only in broker-gateway) and invites a second data socket (406). |
| Use Alpaca's raw `execution_id` as the dedupe key | REST activities expose a different identifier; the stream and resync would double-count a fill. |

## Consequences

- Positive: the IBKR track and consumers code against one stable contract; the simulator exercises the exact gRPC surface; missed fills after a reconnect are published once by construction.
- Negative / risks: the gateway is in the market-data path (a gateway restart drops the data stream; marketdata repairs gaps from REST, MD-006). The outbox adds write latency (< 5 ms on local Postgres), well inside the 50 ms update-propagation budget.
- Follow-up: IBKR fills in its state-mapping rows in `brokers.md`; section 06 consumes `recon.request.*`; section 15 writes `broker.live_enablement`.

## Validation

The shared contract suite (BR-001) runs against the simulator and an `httptest` Alpaca fake in CI, and against Alpaca paper when `TB_ALPACA_PAPER_KEY_ID`/`TB_ALPACA_PAPER_SECRET` are set. Revisit if the IBKR adapter cannot express its semantics in this contract.
