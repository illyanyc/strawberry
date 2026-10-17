# Risk management

**Prefix:** `RM-`  ·  **Owners:** `execution` (pre-trade risk, kill switches), `monitor` (alarms, demotion)  ·  **Related:** [orders](orders-and-execution.md), [virtual portfolios](virtual-portfolios-and-attribution.md), [lifecycle](strategies-and-model-lifecycle.md)

## Scope

Pre-trade checks, position and loss limits, kill switches, PDT handling, and fail-closed behavior. Ownership checks (VP-003 to VP-006) run inside the same pre-trade gate but are specified in the virtual-portfolio doc.

## Concepts and states

- **Pre-trade gate:** ordered list of checks evaluated per intent; result is a `RiskDecision` with each check's name, value, limit, and pass/fail.
- **Limit scopes:** global, broker account, strategy, instrument. The tightest applicable limit wins.
- **Kill switch:** scope (global, account, strategy, instrument) × action (`freeze`, `cancel_open`, `flatten_strategy_owned`).

## Rules

| ID | Rule | Status | Code / tests |
|---|---|---|---|
| RM-001 | Every intent (strategy, UI, webhook) MUST pass the pre-trade gate; there is no bypass path. | Specified | — |
| RM-002 | Every risk decision MUST be persisted and audited with all check values, including approvals. | Specified | — |
| RM-003 | The gate MUST include at least: mode/account enabled, session `ready`, data freshness, ownership (VP-003–VP-005), max order notional, max position per instrument, max gross/net exposure per strategy, daily loss limit, max open orders, price-band sanity vs last trade, PDT guard. Limits are config with documented defaults. | Specified | — |
| RM-004 | If any input to a check is missing or stale, the check MUST fail (fail closed). | Specified | — |
| RM-005 | Breaching a strategy's daily loss limit MUST block new risk-increasing orders for that strategy for the rest of the session and alert. | Specified | — |
| RM-006 | Kill switches MUST freeze new orders within their scope immediately and cancel open orders within the configured target time; `flatten` closes strategy-owned quantity only (VP-006). | Specified | — |
| RM-007 | Disabling a kill switch or raising any limit MUST require step-up auth and an audit record. | Specified | — |
| RM-008 | PDT guard: in an account flagged below the PDT equity threshold, an order that would create a day trade beyond the allowed count in the rolling window MUST be rejected. Default: max 3 per 5 business days (INPUT PENDING U1; verify current FINRA rule). | Specified | — |
| RM-009 | Broker disconnect, unknown order state, or failed reconciliation MUST block new intents in the affected scope until resolved (see OE-004, OE-005, VP-008). | Specified | — |
| RM-010 | Position sizing is computed by the strategy layer but MUST be capped by the gate; the gate never increases size. | Specified | — |

## Invariants

| ID | Invariant | Checked by |
|---|---|---|
| RM-I1 | No broker order exists without an approved risk decision. | Audit query; OE-I3 |
| RM-I2 | While a kill switch is active in a scope, no new order in that scope is approved. | Property test |
| RM-I3 | Strategy exposure never exceeds its configured limits after any approved order. | Table-driven tests per check |

## Edge cases

| Scenario | Expected behavior | Rules |
|---|---|---|
| Last price is older than the freshness threshold | Reject; alert if persistent | RM-004 |
| Risk-reducing order (closing own position) while daily loss limit breached | Allowed (does not increase risk) | RM-005 |
| Kill switch triggered while orders are partially filled | Freeze; cancel remainders; fills that already occurred are booked | RM-006, OE-006 |
| Limit config edited to a looser value from mobile | Step-up auth required; audited | RM-007 |
| PDT count at the limit; strategy wants to open and close same day | Reject the opening order if the close would be the extra day trade | RM-008 |

## Open questions

- Default values for all limits, sizing method (fixed fractional, vol targeting, fractional Kelly), correlation limits (research §9).
- Kill switch cancel target time: not yet set; derive from U8 and broker cancel latency.
