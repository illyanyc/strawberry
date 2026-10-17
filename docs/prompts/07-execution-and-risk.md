---
title: "Section 07 – Live Execution and Risk Management"
phase: "2 (paper only); live enablement in section 15"
depends_on: [00, 02, 03, 04, 05, 06]
updated: 2026-10-17
---

# Section 07: Live Execution and Risk Management

## Role

You are a trading-systems engineer who has run an OMS and pre-trade risk in production. You design the order path so that the most likely failure is "no order sent", never "wrong order sent", and you test every rejection path.

## Context

The `execution` service hosts the strategy runtime (or receives signals from it), turns signals into order intents, runs pre-trade risk checks, manages the order state machine, and talks to `broker-gateway`. It owns kill switches and trading modes. It is built and proven on **paper** in Phase 2 with a simple rule-based baseline strategy, before any ML model exists. Live enablement is a separate, gated step in section 15. Non-negotiables N3, N4, and N5 apply in full.

## Scope

In scope:
- Strategy runtime API shared with the backtester (section 08): the same strategy code runs in backtest, paper, and live.
- Strategy registry: metadata, mode (`disabled | shadow | paper | live | halted`), allocation link, limits.
- Signal → order intent translation (sizing, order type selection, bracket/stop logic).
- Pre-trade risk engine and limit configuration.
- OMS: order state machine, idempotency, cancel/replace, timeouts, end-of-day handling.
- Kill switches and circuit breakers at global, account, strategy, and instrument scope.
- Manual order entry path from the UI (attributed to `manual`, still risk-checked with manual-specific limits).
- Rule-based baseline strategy for Phase 2.
- Trading-day lifecycle (pre-open checks, open, close, flatten-at-close for intraday strategies).

Out of scope:
- Model inference (section 10 publishes signals; the runtime can also call the inference API).
- Ledger math (section 06), broker adapters (section 04).
- Promotion to live (section 15) and promotion decisions (section 11).

## Inputs and dependencies

- Section 04: `BrokerGateway` (orders, capabilities, session status).
- Section 06: `GetOwnershipConstraints`, `ReserveForOrder`, `GetCapitalAvailability`, reconciliation status.
- Section 05/03: `IsFresh`, latest bars/quotes, calendar, instrument info (halts, shortable).
- Section 02: policy, step-up, audit.
- `INPUT PENDING (U1)`: PDT guard. Default ON (margin < $25k). Verify current FINRA PDT rule (research §12).
- `INPUT PENDING (U8)`: latency target. Default decision-to-submit p99 under 500 ms.

## Decisions pending

| Decision | Default | Marker |
|---|---|---|
| Language | Go for OMS/risk (D1); strategies in Python (shared with backtester) running in a strategy-runtime process that sends `OrderIntent`s to Go execution over gRPC/bus | `DECISION PENDING (research §7)` |
| Strategy runtime model | Python runtime using `tbcore.strategy` API; same code path in event-driven backtester | `DECISION PENDING (research §4)` (D7) |
| Position sizing default | Volatility-targeted sizing capped by fixed fractional risk per trade; fractional Kelly only as an upper bound | `DECISION PENDING (research §9)` |
| Order type default | Marketable limit orders with a price collar (limit = last ± k × spread/ATR) instead of pure market orders | `DECISION PENDING (research §9)` |

## Interfaces and contracts to expose

Strategy API (`tbcore.strategy`, Python):

```python
class Strategy(Protocol):
    id: str
    def on_start(self, ctx: StrategyContext) -> None: ...
    def on_bar(self, ctx: StrategyContext, bar: Bar) -> list[OrderIntent]: ...
    def on_signal(self, ctx: StrategyContext, signal: Signal) -> list[OrderIntent]: ...
    def on_fill(self, ctx: StrategyContext, fill: ExecutionReport) -> list[OrderIntent]: ...
    def on_session_close(self, ctx: StrategyContext) -> list[OrderIntent]: ...
# ctx exposes: now (decision time), positions (own virtual portfolio only), cash, features(as_of=now),
# params, logger. ctx never exposes data with available_at > now, other strategies' positions,
# or manual holdings.
```

gRPC `Execution`:

```
SubmitIntent(OrderIntent) -> IntentAck{intent_id, risk_decision, order_id?}
CancelIntent(intent_id) / CancelAllForStrategy(strategy_id)
GetOrder(order_id) / ListOrders(filter)
SetStrategyMode(strategy_id, mode, actor)      # live requires section 15 gate + step-up
GetRiskLimits(scope) / SetRiskLimits(scope, limits, actor)   # raising requires step-up
TriggerKillSwitch(scope, action, reason, actor) / ReleaseKillSwitch(scope, actor)  # release requires step-up
GetTradingStatus() -> {global_state, accounts[], strategies[], active_kill_switches[], pdt_counts}
```

Events: `ord.intent`, `ord.decision` (`RiskDecision`), `ord.update.<acct>`, `risk.kill`, `alert.risk.*`.

## Functional requirements

### Strategy runtime
- [ ] Load strategies from the registry with pinned versions (code commit, params hash, model version).
- [ ] Deliver final bars and signals in timestamp order; decision time = bar `available_at` (or signal time); strategies see only their own virtual portfolio from the ledger.
- [ ] `shadow` mode: run the strategy and record intents and hypothetical fills, but never submit (used for challengers, section 11).
- [ ] Rule-based baseline strategy for Phase 2 (default: simple opening-range breakout or moving-average crossover on 5m bars with ATR stops), parameterized and fully deterministic.

### Intent to order
- [ ] Sizing module: target position or quantity from signal strength, volatility, allocation, and limits; round to lot/fractional rules from broker capabilities.
- [ ] Order construction from capabilities: type, TIF, extended-hours flag, collar, optional bracket (take-profit/stop) with child orders attributed to the same intent.
- [ ] Attribution ID generated per master format and persisted with the intent **before** submission.

### Pre-trade risk (every check produces a named result in `RiskDecision`)
- [ ] Global trading state not frozen; account and strategy not halted; strategy mode allows submission (`paper` → paper account only; `live` → live account only; mismatch rejected).
- [ ] Broker session `ready`; reconciliation status for the account not `broken`.
- [ ] Market data fresh for the instrument (`IsFresh`); clock skew within threshold.
- [ ] Market session check (regular vs extended hours allowed per strategy); instrument tradable and not halted; shortable/borrow check for shorts.
- [ ] **Ownership check (N3)**: for sells/closes, `qty <= max_sellable_by_strategy`; shorts only if `short_allowed`; never touch `manual` quantity.
- [ ] Capital check via `ReserveForOrder`; reservation released on terminal states.
- [ ] Limits (configurable per strategy, per account, global): max order notional, max order quantity, max position notional and % of allocation, max gross and net exposure, max positions count, max concentration per instrument and per sector, max daily loss (realized + unrealized) and max drawdown from high-water mark, max orders per minute, max cancels per minute, max consecutive losses.
- [ ] Price collar: limit price within X% (or k × ATR) of last trade/quote; reject market orders when the spread is wider than a threshold.
- [ ] Duplicate/fat-finger detection: identical intent within N seconds; quantity > M × typical size.
- [ ] PDT guard: count day trades per account across all owners (including manual trades); block intents that would create a day trade beyond the allowance; warn at the threshold.
- [ ] Each check is a pure function with table-driven tests; the decision with all check values is audited.

### OMS
- [ ] Order state machine aligned with section 04's normalized states; persisted in Postgres; every transition evented and audited.
- [ ] Idempotency on `intent_id` and `client_order_id`; duplicate executions ignored by `broker_exec_id`.
- [ ] Timeouts: unacknowledged submits are queried, not resubmitted; orders stuck in `unknown` freeze the strategy until resolved.
- [ ] Cancel/replace with new attempt suffix in the attribution ID.
- [ ] Session lifecycle: pre-open checklist (brokers ready, recon clean, data fresh, limits loaded, clock ok); intraday strategies cancel open orders and flatten **their own** positions before close (configurable time); day-order expiry handled.
- [ ] Startup recovery: on restart, load open orders, query the broker, resolve differences, and stay frozen until reconciled.

### Kill switches and circuit breakers
- [ ] Scopes: global, broker account, strategy, instrument. Actions: `freeze` (no new orders), `cancel_open`, `flatten_strategy_owned` (market-safe exit of strategy-owned quantity only), `demote_to_paper`.
- [ ] Triggers: manual (UI/mobile, one tap plus confirm, no step-up needed to **engage**), automatic from risk (daily loss, drawdown, error rates, reject storms), monitoring (section 13: drift, divergence), ledger (breaks), broker (disconnect), infra (clock skew, stale data).
- [ ] Releasing a kill switch requires step-up auth and is audited.
- [ ] Dead-man check: if the strategy runtime or execution heartbeat is lost, cancel open strategy orders (via broker-gateway, or the broker's own facilities if available) and alert.
- [ ] Engaging the global switch cancels all open **platform** orders within the target time (default 2 s) and never cancels or closes manual orders/positions.

### Manual order entry (UI)
- [ ] Manual orders go through the same OMS with owner `manual`, attribution short ID `manual`, separate manual limits (max notional, collar), confirmation step, and audit. Live manual orders above a threshold require step-up.

## Non-functional requirements

- **Performance**: risk evaluation p99 under 10 ms; intent-to-broker-submit p99 under 100 ms excluding broker latency; kill-switch cancel-all under 2 s for 100 open orders.
- **Reliability**: at-least-once event delivery with idempotent handlers; no state held only in memory (crash at any point recovers correctly).
- **Security**: only authorized producers may submit intents; live mode cannot be set by config alone (requires section 15 gate); all changes audited.
- **Tests**: table-driven tests for every risk check (pass and fail); property tests for the state machine (random event orders including duplicates and out-of-order fills); chaos tests with the broker simulator (disconnect mid-order, partial fills then cancel, rejects, duplicate fills); end-to-end paper tests.

## Acceptance criteria

- [ ] Docs updated per `AGENTS.md` (master N6) in the same commits as the changes: `docs/file-map.md`, `docs/business-logic/risk.md` and `docs/business-logic/oms.md` (checks, limits, order states, kill switches), `CHANGELOG.md`, `docs/glossary.md` for new terms, and, for every decision made, an ADR in `docs/adr/` plus its row updated (or added) in `docs/decisions.md`.
- [ ] Risk test matrix: each check rejects its violating case and passes its boundary case; decisions appear in the audit log with values and limits.
- [ ] Same-ticker and short-block scenarios from section 06 enforced end to end through the order path.
- [ ] Kill-switch drill: global freeze + cancel completes within target; manual orders and positions untouched; release requires step-up.
- [ ] Chaos suite passes: no duplicate orders, no lost fills, frozen state on unknown order status.
- [ ] Baseline strategy trades paper for five sessions; every order traceable signal → intent → decision → order → fill → ledger via `correlation_id`.
- [ ] Restart during open orders recovers state without duplicates and stays frozen until reconciliation passes.
- [ ] PDT guard blocks the fourth day trade in five business days in a simulated sub-$25k margin account (or the rule configured from U1/research).

## Pitfalls to avoid

- Strategies reading account-level positions (including manual holdings) and "closing" them.
- Market orders in thin names or at the open; always collar.
- Risk checks only in the strategy layer; the OMS must enforce them regardless of strategy behavior.
- Resubmitting on timeout; querying by `client_order_id` is mandatory.
- Daily loss limits computed only on realized P&L; include unrealized and fees.
- Kill switches that need the UI and auth service to work; engaging must work from a CLI on the server and via mobile even when parts of the stack are degraded.
- A flatten action that sends market sells for the account's total position (would close manual holdings).
- Different sizing or rounding logic in backtest and live; use the shared library.
- Forgetting that manual day trades count toward PDT.
