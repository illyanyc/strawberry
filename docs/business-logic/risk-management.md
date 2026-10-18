# Risk management

**Prefix:** `RM-`  ·  **Owners:** `execution` (pre-trade risk, kill switches), `monitor` (alarms, demotion)  ·  **Related:** [orders](orders-and-execution.md), [virtual portfolios](virtual-portfolios-and-attribution.md), [lifecycle](strategies-and-model-lifecycle.md), [ADR-0010](../adr/0010-account-routing-and-day-trade-policy.md) (D30)

## Scope

Pre-trade checks, position and loss limits, kill switches, account routing, margin and day-trade guards, and fail-closed behavior. Ownership checks (VP-003 to VP-006) run inside the same pre-trade gate but are specified in the virtual-portfolio doc.

## Concepts and states

- **Pre-trade gate:** ordered list of checks evaluated per intent; result is a `RiskDecision` with each check's name, value, limit, and pass/fail.
- **Limit scopes:** global, broker account, strategy, instrument. The tightest applicable limit wins.
- **Kill switch:** scope (global, account, strategy, instrument) × action (`freeze`, `cancel_open`, `flatten_strategy_owned`).
- **Strategy horizon:** `intraday` (flat by the close) or `swing` (holds overnight). Declared in the strategy config; drives routing (RM-011).
- **Account routing table:** config mapping each horizon to the broker accounts allowed to receive its orders. Default (D30): `intraday → alpaca-*`, `swing → ibkr-bot` (or the IBKR account if no linked bot account exists, D19).
- **Margin utilization:** account margin in use ÷ margin available, from the broker's account snapshot; the strategy share is the margin attributable to strategy-owned positions.

## Rules

| ID | Rule | Status | Code / tests |
|---|---|---|---|
| RM-001 | Every intent (strategy, UI, webhook) MUST pass the pre-trade gate; there is no bypass path. | Specified | — |
| RM-002 | Every risk decision MUST be persisted and audited with all check values, including approvals. | Specified | — |
| RM-003 | The gate MUST include at least: mode/account enabled, session `ready`, data freshness, ownership (VP-003–VP-005), max order notional, max position per instrument, max gross/net exposure per strategy, daily loss limit, max open orders, price-band sanity vs last trade, account routing (RM-011), intraday-margin guard (RM-012), and the PDT guard when enabled (RM-008). Limits are config with documented defaults. | Specified | — |
| RM-004 | If any input to a check is missing or stale, the check MUST fail (fail closed). | Specified | Freshness source: `MarketDataService.IsFresh` (MD-007, fails closed); consumption in execution lands in Phase 2 |
| RM-005 | Breaching a strategy's daily loss limit MUST block new risk-increasing orders for that strategy for the rest of the session and alert. | Specified | — |
| RM-006 | Kill switches MUST freeze new orders within their scope immediately and cancel open orders within the configured target time; `flatten` closes strategy-owned quantity only (VP-006). | Specified | — |
| RM-007 | Disabling a kill switch or raising any limit MUST require step-up auth and an audit record. | Specified | — |
| RM-008 | PDT guard is a per-account setting `risk.accounts.<id>.pdt_guard ∈ {off, legacy}`, **default `off`** (FINRA retired the PDT rule on 2026-06-04; D30). When `legacy`, an order that would create a day trade beyond `pdt_max_day_trades` (default 3) in the rolling `pdt_window_days` (default 5 business days) MUST be rejected. Day-trade counts are tracked per account in both modes so the guard can be switched on for a broker still in phase-in. | Specified | — |
| RM-009 | Broker disconnect, unknown order state, or failed reconciliation MUST block new intents in the affected scope until resolved (see OE-004, OE-005, VP-008). | Specified | — |
| RM-010 | Position sizing is computed by the strategy layer but MUST be capped by the gate; the gate never increases size. | Specified | — |
| RM-011 | An intent MUST be rejected if its strategy's horizon is not routed to the target broker account in the routing table (`risk.routing`). Default: `intraday → alpaca accounts`, `swing → IBKR bot account`. Swing positions at IBKR MUST carry broker-side GTC protective stops. | Specified | — |
| RM-012 | Intraday-margin guard: orders to an account routed for intraday MUST use margin multiplier ≤ `risk.accounts.<id>.max_margin_multiplier` (default **1**, i.e. no leverage at Alpaca). In every account, a strategy order MUST be rejected if it would push account margin utilization above `risk.accounts.<id>.max_margin_utilization` (default 50%) or the strategies' share above `max_strategy_margin_share` (default 25%), so the manual book keeps its buying power. | Specified | — |
| RM-013 | An intraday margin deficiency (IMD), margin call, or broker restriction flag on an account MUST halt all strategies on that account (freeze + cancel open), alert at critical severity, and require a manual, step-up-authorized release. | Specified | — |

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
| Account with `pdt_guard: off` (default) makes a 5th day trade in 5 days | Allowed; count still recorded | RM-008 |
| Account with `pdt_guard: legacy`, count at the limit; strategy wants to open and close same day | Reject the opening order if the close would be the extra day trade | RM-008 |
| Intraday strategy sends an order to the IBKR bot account | Reject: horizon not routed to that account | RM-011 |
| Strategy buy at Alpaca would raise margin utilization from 45% to 55% (cap 50%) | Reject; manual book keeps buying power | RM-012 |
| Broker reports an IMD on the Alpaca account mid-session | Halt every strategy on the account, cancel open orders, critical alert; manual release with step-up | RM-013, RM-007 |

## Open questions

- Default values for all limits, sizing method (fixed fractional, vol targeting, fractional Kelly), correlation limits (research §9).
- Kill switch cancel target time: not yet set; derive from U8 and broker cancel latency.
- Exact margin-utilization defaults (RM-012) are starting values; tune after the first paper sessions.
