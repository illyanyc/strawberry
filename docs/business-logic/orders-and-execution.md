# Orders and execution

**Prefix:** `OE-`  ·  **Owners:** `execution` (OMS), `broker-gateway` (adapters)  ·  **Related:** [risk](risk-management.md), [virtual portfolios](virtual-portfolios-and-attribution.md)

## Scope

The path from `OrderIntent` to broker and back: OMS states, submission, cancel/replace, broker state mapping, execution reports, session health. Ownership checks are in [virtual portfolios](virtual-portfolios-and-attribution.md); limit checks are in [risk](risk-management.md).

## Concepts and states

- **OrderIntent** → (risk decision) → **Order** → broker order → **ExecutionReport**s.
- OMS order roles: `entry`, `exit`, `protective_stop`, `take_profit`; intent states `received → rejected | shadow | approved → submitted → done | failed`.
- Normalized order states: `pending_submit, accepted, partially_filled, filled, pending_cancel, canceled, rejected, expired, replaced, unknown`.
- Broker session states: `disconnected → connecting → authenticated → syncing → ready → degraded`.

## Rules

| ID | Rule | Status | Code / tests |
|---|---|---|---|
| OE-001 | An intent MUST NOT become a broker order without an approved `RiskDecision` recorded for its `intent_id` (see RM-001). Protective child orders placed by the OMS for an approved entry (OE-014) and kill-switch/lifecycle exits run under their own intent's decision. | Implemented | `execution/internal/oms/engine.go` `process`; `TestGate2EveryDecisionAudited` |
| OE-002 | Every outbound order MUST carry a well-formed attribution ID; adapters MUST reject missing or malformed IDs (format in VP-002). | Implemented (adapter) | `services/broker-gateway/internal/attribution`; BR-003 |
| OE-003 | On submit timeout, the adapter MUST query the broker by `client_order_id` before any retry and MUST NOT resubmit blindly. | Implemented (adapter) | `broker-gateway/internal/adapter/account.go`; BR-007 |
| OE-004 | Only broker accounts in session state `ready` accept orders; others MUST return a typed `BrokerUnavailable` error. | Implemented (adapter) | `broker-gateway/internal/session`, `grpcsvc`; BR-004, BR-010 |
| OE-005 | Every broker status MUST map explicitly to a normalized state; unmapped statuses and ambiguous submits map to `unknown`, which halts new orders for that strategy and instrument until the order reconciler resolves it by `client_order_id` (an unknown submit not found at the broker after `UnknownGiveUp`, 30 s, becomes `rejected: never reached the broker`). | Implemented | adapter `mapping.go`; OMS `submit`, `ReconcileOrders`; `TestGate2UnknownSubmitHaltsUntilResolved` |
| OE-006 | Consumers MUST be idempotent on `intent_id`, `client_order_id`, and `broker_exec_id`; duplicate executions MUST NOT change quantities. | Implemented (adapter) | `broker-gateway/internal/execstore`; BR-008 |
| OE-007 | Quantities and prices MUST be decimals or fixed-point; rounding to broker tick/lot size uses an explicit, documented rounding mode. | Implemented (adapter) | `broker-gateway/internal/adapter/rounding.go`; BR-017 |
| OE-008 | On reconnect, the adapter MUST fetch open orders and executions since the last acknowledged exec ID, publish missed updates exactly once, and request reconciliation before returning to `ready`. | Implemented (adapter) | `broker-gateway/internal/adapter/account.go`; BR-011 |
| OE-009 | Bracket/OCO child orders MUST be known to the OMS and attributed to the parent's owner (one OMS order per leg, role `protective_stop` / `take_profit`). | Implemented | adapter legs (BR-018); OMS `upsertLeg` |
| OE-010 | Live adapters MUST NOT start unless the global live flag is set **and** the account is live-enabled by an audited owner action (see SM-004). | Implemented (adapter) | `broker-gateway/internal/livegate`; BR-013 |
| OE-011 | Every submit, cancel, replace, and reject MUST be written to the audit log with its attribution ID. | Implemented (adapter) | `broker-gateway/internal/adapter/account.go`; BR-014 |
| OE-012 | The OMS MUST persist each order with its attribution ID (state `pending_submit`) before sending it; child orders of an intent use attempt numbers 50–99 (`tb1-<short>-<intent>-50` is the first protective stop), replacements increment the attempt (VP-002). | Implemented | OMS `newOrder`, `submit` |
| OE-013 | Order type default: a strategy `MARKET` intent is sent as a marketable limit with a collar of `EntryCollarBps` (10 bps) for entries and `ExitCollarBps` (50 bps) for exits from the last final bar close, buys rounded up and sells down to the cent; explicit `LIMIT` prices are kept. The gate's `price_collar` (default 2 %) bounds every limit. | Implemented | OMS `buildInput` |
| OE-014 | Every algorithmic entry MUST carry broker-side protection (N5): at brokers with bracket support (Alpaca, simulator) a bracket (stop + take-profit) or OTO (stop only) with the entry; at brokers without it (IBKR adapter, ADR-0023) a separate stop order with the intent's protective TIF (GTC for swing) placed as soon as the entry is final with a filled quantity. If the stop cannot be placed the strategy is halted for the instrument and paged. Partially filled entries are protected when they reach a final state (Planned: protect partials while resting). | Implemented | OMS `execute`, `maybeProtect`; `TestGate2RoutingMarginGuardAndIMD` |
| OE-015 | Bracket legs held until their parent fills (`broker_status = held`) are not resting orders for self-cross purposes. | Implemented | OMS `buildInput` |
| OE-016 | Intraday strategies (paper or live) MUST exit their own positions `TB_INTRADAY_FLATTEN_BEFORE_CLOSE` (10 min) before the regular close, once per session: own resting orders are canceled, exits are sized from the ledger (strategy-owned quantity only). | Implemented | OMS `lifecycle` |
| OE-017 | Broker order updates and executions MUST be applied idempotently: states only move forward (final states are never left except to `filled`), filled quantity never decreases, duplicates change nothing. | Implemented | OMS `OnOrderUpdate`, `OnExecution`; `TestOE006StateMachineConvergesUnderReordering` |
| OE-018 | The OMS MUST NOT leave opposite-side resting orders in the same account and instrument across owners: an intent facing another owner's resting opposite-side order (or the owner's own resting protective legs, for an open) is rejected (`self_cross`); an exit first cancels the owner's own resting orders in the instrument (legs included), waits up to `CancelWait` (5 s) for them to be final, then re-sizes the exit to what the owner still owns. | Implemented | risk `selfCross`, OMS `cancelOwn`; `TestGate2SymbolSegregationAndSelfCross` |
| OE-019 | Open orders MUST be reconciled with each broker every 30 s and after every reconnect: `tb1-` orders open at the broker but unknown to the OMS are orphans (halt + page); OMS orders the broker no longer lists are looked up by client order ID and adopt the broker state, or become `unknown` (ghost) if not found. | Implemented | OMS `ReconcileOrders` |
| OE-020 | After a restart the OMS MUST approve no intent until every READY account's open orders were reconciled (startup recovery), and a replayed intent is answered from the stored decision (no duplicate order). | Implemented | OMS `New`, `reconcileAll`; `TestGate2RestartRecoversWithoutDuplicates` |

## Invariants

| ID | Invariant | Checked by |
|---|---|---|
| OE-I1 | Each `client_order_id` maps to at most one broker order. | Contract test suite; submit-timeout test |
| OE-I2 | An order's `filled_qty` equals the sum of its deduplicated execution quantities and never exceeds order quantity. | Property test on random fill sequences |
| OE-I3 | Every order has exactly one `intent_id` and one approved risk decision. | DB constraint + audit query |

## Edge cases

| Scenario | Expected behavior | Rules |
|---|---|---|
| Submit request times out; order actually reached the broker | Query by `client_order_id` finds it; no second order | OE-003, OE-I1 |
| Duplicate or out-of-order partial fills | Dedupe on `broker_exec_id`; order by broker timestamp + sequence | OE-006, OE-I2 |
| Fill arrives after a cancel was requested | Fill is applied; state goes to `filled` / `partially_filled` then `canceled` for the remainder | OE-006 |
| IBKR daily reset during market hours | Session `degraded`, strategies blocked, alert; resync before `ready` | OE-004, OE-008 |
| Order placed in the broker's own app | Appears without attribution prefix; owned by `manual` | VP-001 |
| Broker returns a status we have not seen | Maps to `unknown`, fail closed, alert | OE-005 |

## Open questions

- Attribution-ID length: Alpaca `client_order_id` ≤ 128 (capabilities); IBKR `orderRef`: spike V1 (IBKR track).
- Default order types and TIF per strategy family: DECISION PENDING (strategy specs).
