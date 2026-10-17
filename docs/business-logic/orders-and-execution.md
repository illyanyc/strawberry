# Orders and execution

**Prefix:** `OE-`  ·  **Owners:** `execution` (OMS), `broker-gateway` (adapters)  ·  **Related:** [risk](risk-management.md), [virtual portfolios](virtual-portfolios-and-attribution.md)

## Scope

The path from `OrderIntent` to broker and back: OMS states, submission, cancel/replace, broker state mapping, execution reports, session health. Ownership checks are in [virtual portfolios](virtual-portfolios-and-attribution.md); limit checks are in [risk](risk-management.md).

## Concepts and states

- **OrderIntent** → (risk decision) → **Order** → broker order → **ExecutionReport**s.
- Normalized order states: `pending_submit, accepted, partially_filled, filled, pending_cancel, canceled, rejected, expired, replaced, unknown`.
- Broker session states: `disconnected → connecting → authenticated → syncing → ready → degraded`.

## Rules

| ID | Rule | Status | Code / tests |
|---|---|---|---|
| OE-001 | An intent MUST NOT become a broker order without an approved `RiskDecision` recorded for its `intent_id` (see RM-001). | Specified | — |
| OE-002 | Every outbound order MUST carry a well-formed attribution ID; adapters MUST reject missing or malformed IDs (format in VP-002). | Specified | — |
| OE-003 | On submit timeout, the adapter MUST query the broker by `client_order_id` before any retry and MUST NOT resubmit blindly. | Specified | — |
| OE-004 | Only broker accounts in session state `ready` accept orders; others MUST return a typed `BrokerUnavailable` error. | Specified | — |
| OE-005 | Every broker status MUST map explicitly to a normalized state; unmapped statuses map to `unknown`, which blocks new orders for that instrument and strategy until resolved. | Specified | — |
| OE-006 | Consumers MUST be idempotent on `intent_id`, `client_order_id`, and `broker_exec_id`; duplicate executions MUST NOT change quantities. | Specified | — |
| OE-007 | Quantities and prices MUST be decimals or fixed-point; rounding to broker tick/lot size uses an explicit, documented rounding mode. | Specified | — |
| OE-008 | On reconnect, the adapter MUST fetch open orders and executions since the last acknowledged exec ID, publish missed updates exactly once, and request reconciliation before returning to `ready`. | Specified | — |
| OE-009 | Bracket/OCO child orders MUST be known to the OMS and attributed to the parent's owner. | Specified | — |
| OE-010 | Live adapters MUST NOT start unless the global live flag is set **and** the account is live-enabled by an audited owner action (see SM-004). | Specified | — |
| OE-011 | Every submit, cancel, replace, and reject MUST be written to the audit log with its attribution ID. | Specified | — |

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

- Exact attribution-ID length limits per broker (Alpaca `client_order_id`, IBKR `orderRef`): verify in research §6/§10.
- Default order types and TIF per strategy family: DECISION PENDING (strategy specs).
