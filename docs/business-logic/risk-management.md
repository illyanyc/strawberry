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
| RM-001 | Every intent (strategy, UI, webhook, kill-switch and lifecycle exits) MUST pass the pre-trade gate; there is no bypass path. Kill-switch flatten exits only bypass the freeze they caused. | Implemented | `execution/internal/risk`, OMS `process` |
| RM-002 | Every risk decision MUST be persisted and audited with all check values, including approvals, and published on `ord.decision`. Preview (dry-run) decisions are not recorded. | Implemented | OMS `process`; `TestGate2EveryDecisionAudited` |
| RM-003 | The gate MUST include at least: mode/account enabled, session `ready`, data freshness, ownership (VP-003–VP-005), max order notional, max position per instrument, max gross/net exposure per strategy, daily loss limit, max open orders, price-band sanity vs last trade, account routing (RM-011), intraday-margin guard (RM-012), and the PDT guard when enabled (RM-008). Limits are config with documented defaults (table below). Every check is evaluated for every intent so the audit shows all values. | Implemented | `risk.Checks`; `TestRM003RiskMatrix` |
| RM-004 | If any input to a check is missing or stale, the check MUST fail (fail closed). Data freshness: the latest final bar of the instrument (from `md.bar.*`) must have closed within two bar intervals plus the 60 s finality window (same rule as MD-007 `IsFresh`). For daily (and longer) bars the two intervals count XNYS trading sessions, not wall time: the bar must have closed no earlier than the close of the session before the last closed session (weekends and holidays are not missing bars). | Implemented | risk `dataFresh`, OMS `maxBarAge`; `TestGate2StaleDataBlocksIntents`, `TestRM004DailyFreshnessCountsSessions` |
| RM-005 | Breaching a strategy's daily loss limit (ledger day P&L = equity − session-start equity, realized + unrealized − fees) MUST block new risk-increasing orders for that strategy for the rest of the session; closes stay allowed. Allocation changes are capital flows, not P&L: a change during the session MUST shift the session-start equity and the high-water mark by the same amount, persisted with the allocation event, so an increase cannot hide a loss and a decrease cannot trigger a false halt. | Implemented | risk `dailyLoss`, ledger `engine.shiftDayStart`; `TestRM003RiskMatrix`, `TestRM005AllocationChangesAreNotDayPnL` |
| RM-006 | Kill switches (scopes global, account, strategy, instrument) MUST latch and freeze new orders in scope immediately. `cancel_open` cancels strategy-owned working orders in scope in parallel within 2 s and keeps broker-side protective stops; `flatten_strategy_owned` cancels everything strategies have open in scope (protection included) and exits strategy-owned quantity only (VP-006); `demote_to_paper` also moves live strategies in scope to paper. Manual orders and positions are never touched. Engaging works from the UI, `tbctl kill` (bus `risk.kill`), and automatic triggers. | Implemented | OMS `kill.go`; `TestGate2KillSwitchesLatchFreezeCancelFlatten` |
| RM-007 | Releasing a kill switch MUST require a fresh passkey step-up from a desktop client; raising any limit or allocation MUST require passkey step-up; all are audited. Releases are never accepted from the bus. | Implemented | api-gateway `policy.Authorize`; OMS `Release`, `SetLimits` |
| RM-008 | PDT guard is a per-account setting (`TB_ACCOUNT_POLICIES` `pdt=off|legacy`), **default `off`** (FINRA retired the PDT rule on 2026-06-04; D30). When `legacy`, an order that would create a day trade beyond `pdt_max_day_trades` (default 3) in the rolling `pdt_window_days` (default 5 business days) MUST be rejected. Day-trade counts (same-day buy and sell of an instrument, all owners, last five sessions; or the broker's count when higher) are tracked per account in both modes (`tb_execution_day_trades`). | Implemented | risk `pdtGuard`; `TestRM008PDTGuardBlocksFourthDayTrade` |
| RM-009 | Broker disconnect (session not `ready`, including IBKR status 1100), unknown order state, or failed reconciliation MUST block new intents in the affected scope until resolved; after a session returns to `ready`, intents stay blocked until a reconciliation newer than that transition is clean (OE-008). An account with active strategies that is not ready for `SessionAlertAfter` (10 s) during the session pages. | Implemented | risk `sessionReady`, `reconciliation`; OMS `pollSessions`; `TestGate2LostStreamHaltsPagesAndResumesAfterReconciliation` |
| RM-010 | Position sizing is computed by the strategy layer (`tbcore.strategy`) but MUST be capped by the gate; the gate never increases size (quantities are floored to whole shares when the broker has no fractional support). | Implemented | OMS `buildInput` |
| RM-011 | An intent MUST be rejected if its strategy's horizon is not routed to the target broker account in the routing table (`TB_ROUTING`, default `intraday=alpaca-paper|sim-paper;swing=ibkr-paper-bot`) or differs from the registry. Swing entries MUST carry a GTC protective stop (OE-014: a separate GTC stop at the IBKR adapter). | Implemented | risk `routing`, `protective`; `TestRM011RoutingTable`, `TestGate2RoutingMarginGuardAndIMD` |
| RM-012 | Intraday-margin guard (D30): per account `max_margin_multiplier` (`mult`) and `margin_utilization_cap` (`cap`); defaults Alpaca accounts `mult=1, cap=1` (no leverage), IBKR bot `mult=2, cap=0.5`. An opening intent MUST be rejected if (a) the account's projected gross (long + |short| market value + open buy orders + the order) exceeds `equity × mult`, or (b) the strategies' projected gross exceeds `min(Σ strategy allocations, equity × mult × cap)`. Closes always pass. | Implemented | risk `marginGuard`; `TestRM003RiskMatrix`, `TestGate2RoutingMarginGuardAndIMD` |
| RM-013 | An intraday margin deficiency (IMD), margin call, or broker restriction flag on an account MUST halt all strategies on that account (account kill switch `cancel_open`, actor `risk:margin`), alert at critical severity, and require a manual, step-up-authorized release. | Implemented | OMS `OnSnapshot`; `TestGate2RoutingMarginGuardAndIMD` |
| RM-014 | Symbol segregation (N3): at accounts with `seg=true` (default for Alpaca accounts) a strategy intent in an instrument the `manual` owner holds MUST be rejected unless the instrument is in the strategy's `segregation_allowlist`. | Implemented | risk `segregation`; `TestGate2SymbolSegregationAndSelfCross` |
| RM-015 | Automatic actions only reduce risk: demotion moves `live` to `paper` (never upwards), halts and kill switches only block or cancel; re-enabling is manual. | Implemented | OMS `kill.go` |
| RM-016 | `RejectStorm` (5) broker rejects for one strategy within a minute latch a strategy `freeze` kill switch and page. | Implemented | OMS `noteReject` |
| RM-017 | Dead-man: if the strategy runtime heartbeat (`sys.health.strategy-runtime`) is older than `TB_DEADMAN_TIMEOUT` (30 s) during the regular session, execution latches a `cancel_open` kill switch for every runtime-hosted paper/live strategy and pages. If the execution heartbeat (`sys.health.execution`, every 5 s) is lost for 30 s during the session, broker-gateway cancels working `tb1-` orders (not manual, not protective stops or bracket legs) and pages. | Implemented | OMS `deadmanCheck`, broker-gateway `internal/deadman`; `TestRM017DeadmanCancelsWorkingOrdersKeepsProtection`, `TestGate2DeadmanRuntimeAndExecution` |
| RM-020 | Engaging a kill switch from a phone (`Sec-CH-UA-Mobile` or a mobile user agent) MUST require a fresh **passkey** step-up (TOTP and recovery step-ups do not count); from a desktop it MUST need only the confirmation (RM-006, UI-006). Release stays desktop-only with a passkey (RM-007). | Implemented | `libs/go/policy` (`escalated`), api-gateway `handleKillEngage`; `TestRM020MobileKillEngageNeedsFreshPasskey`, `TestRM007KillSwitchEngageAndDesktopPasskeyRelease`, `phase5.test.tsx` (UI-050) |

## Default limits (scope `global`; `manual` scope in parentheses)

| Limit | Default | Limit | Default |
|---|---|---|---|
| max order notional | 25,000 (manual 10,000) | max daily loss | 1,000 |
| max order qty | 5,000 | max drawdown | 2,500 |
| max position notional | 25,000 | max positions | 10 |
| max position % of allocation | 0.5 | max consecutive losses | 5 |
| max gross / net exposure | 50,000 / 50,000 | orders per minute | 30 (manual 10) |
| price collar | 2 % (manual 3 %) | duplicate window | 5 s (approved intents only) |
| fat finger | 10 × median of the last 20 sizes (≥ 5 history) | max open orders | 20 (protective legs excluded) |

Scopes `global` → `account:<id>` → `strategy:<id>` (or `manual`) merge field by field; the tightest non-zero value wins. Registry seeds may set strategy limits once when none exist.

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
| Swing order at Monday's open (or after a holiday) on Friday's daily bar | Fresh: no session was skipped; a bar two sessions stale is rejected | RM-004 |
| Risk-reducing order (closing own position) while daily loss limit breached | Allowed (does not increase risk) | RM-005 |
| Kill switch triggered while orders are partially filled | Freeze; cancel remainders; fills that already occurred are booked | RM-006, OE-006 |
| Limit config edited to a looser value from mobile | Step-up auth required; audited | RM-007 |
| Owner taps the kill switch on the phone without a fresh passkey | Step-up dialog asks for the passkey, then the engagement is retried once; cancel leaves trading running and shows an error | RM-020, UI-050 |
| Account with `pdt_guard: off` (default) makes a 5th day trade in 5 days | Allowed; count still recorded | RM-008 |
| Account with `pdt_guard: legacy`, count at the limit; strategy wants to open and close same day | Reject the opening order if the close would be the extra day trade | RM-008 |
| Intraday strategy sends an order to the IBKR bot account | Reject: horizon not routed to that account | RM-011 |
| Strategy buy at Alpaca would raise margin utilization from 45% to 55% (cap 50%) | Reject; manual book keeps buying power | RM-012 |
| Broker reports an IMD on the Alpaca account mid-session | Halt every strategy on the account, cancel open orders, critical alert; manual release with step-up | RM-013, RM-007 |

## Open questions

- Default values for all limits, sizing method (fixed fractional, vol targeting, fractional Kelly), correlation limits (research §9).
- Kill switch cancel target time: not yet set; derive from U8 and broker cancel latency.
- Exact margin-utilization defaults (RM-012) are starting values; tune after the first paper sessions.
