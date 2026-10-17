---
title: "Section 06 – Portfolio and Virtual Strategy Ledgers"
phase: "2"
depends_on: [00, 02, 03, 04]
updated: 2026-10-17
---

# Section 06: Portfolio and Virtual Strategy Ledgers

## Role

You are a portfolio accounting engineer with fund-administration instincts. You treat the ledger as double-entry books: every share and every dollar has an owner, every change has a source event, and the books must tie out to the broker every time.

## Context

Each connected broker account holds the owner's long-term manual positions alongside positions opened by bot strategies. The `ledger` service tracks the **full real account** and splits it into **virtual sub-portfolios**, one per strategy, plus a `manual` bucket for everything not owned by a strategy. Strategies may trade only their own quantity (non-negotiable N3). The ledger is the authority that execution (section 07) consults before allowing a sell, and it reconciles continuously against the broker. Non-negotiables N3 and N5 are this section's core.

## Scope

In scope:
- Owner model: `strategy:<id>` and `manual` owners within each `broker_account_id`.
- Capital allocation per strategy, cash and buying-power accounting, over-allocation guards.
- Lot-level position tracking per owner, realized/unrealized P&L, fees, dividends.
- Attribution of executions to owners via the attribution ID.
- Handling of manual trades, corporate actions, transfers, and cash movements.
- Reconciliation against broker snapshots and executions; break detection and resolution workflow.
- Ownership-constraint queries used by pre-trade risk.
- Historical performance series per strategy (equity curve, exposure) for monitoring and UI.

Out of scope:
- Deciding orders and risk limits (section 07), but the ledger provides `max_sellable` and capital availability.
- Tax reporting; record what is needed (lots, wash-sale flags) but do not file anything.

## Inputs and dependencies

- Section 04: `AccountSnapshot`, `ExecutionReport`, `ListOrders`, activities (dividends, fees, corporate-action cash).
- Section 05/03: `CorporateAction` events, instrument master, prices for marking.
- Section 02: audit log, step-up for allocation changes.
- `INPUT PENDING (U1)`: cash vs margin and PDT; drives buying-power rules and settlement handling (cash accounts must respect settled cash and good-faith violations).
- `INPUT PENDING (U2)`: options/futures would need multipliers and margin models; default equities/ETFs only.

## Decisions pending

| Decision | Default | Marker |
|---|---|---|
| Account model | One real account per broker with internal virtual ledgers (D19); a strategy may optionally be mapped to a dedicated broker account or sub-account | `DECISION PENDING (research §10)` |
| Lot relief method inside the ledger | FIFO within each owner; broker-side tax lot method documented separately (see pitfalls) | `DECISION PENDING (research §10, §12)` |
| Same-symbol short vs manual long | **Blocked** in shared accounts (broker nets positions); allowed only in a dedicated account | High confidence, confirm in research §10 |
| Storage | PostgreSQL `ledger` schema, event-sourced (append-only entries + materialized positions) | High confidence (D5) |
| Language | Go (D1) | `DECISION PENDING (research §7)` |

## Interfaces and contracts to expose

gRPC service `Ledger`:

```
GetAccountView(broker_account_id) -> {broker_snapshot, owners[]: {owner, cash_allocated, cash_available, positions[], equity, realized_pnl, unrealized_pnl}, manual, unallocated_cash, recon_status}
GetStrategyPortfolio(strategy_id) -> {allocation, cash, positions[] with lots, exposure, pnl, limits_snapshot}
GetOwnershipConstraints(broker_account_id, instrument_id, strategy_id) ->
    {strategy_qty, other_owners_qty, account_qty, max_sellable_by_strategy, short_allowed: bool, reason}
GetCapitalAvailability(strategy_id) -> {cash_available, buying_power_available, reserved_for_open_orders}
ReserveForOrder(intent_id, strategy_id, instrument_id, side, qty, est_price) -> reservation_id | Reject(reason)
ReleaseReservation(reservation_id)
SetAllocation(strategy_id, amount, actor) -> requires step-up for increases
Reconcile(broker_account_id) -> ReconciliationResult
ListBreaks(status) / ResolveBreak(break_id, resolution, actor)
GetEquityCurve(owner, start, end, interval)
```

Events: `ledger.update.<acct>` (owner position and cash changes), `recon.result.<acct>`, `alert.ledger.*` (break, encroachment, over-allocation).

Invariants (enforced in code and tested):
1. For every account and instrument: `broker_qty == Σ strategy_qty + manual_qty`. Any difference is a **break**.
2. A strategy's quantity never goes below zero unless the strategy is allowed to short in that account **and** `other_owners_qty == 0` for that instrument in that account.
3. `Σ allocations + unallocated == allocatable_capital` (config: total account equity minus a reserve the owner keeps out of reach of strategies).
4. A strategy's open-order reservations plus positions never exceed its allocation and per-strategy buying-power share.
5. Every ledger entry references a source event (`broker_exec_id`, activity ID, corporate action ID, or an audited manual adjustment).

## Functional requirements

### Owners, allocations, and capital
- [ ] Owner registry per broker account: `manual` (always exists) and strategies registered by section 07/11 with a short ID used in attribution IDs.
- [ ] Allocations in account currency; increases require step-up auth and are audited; decreases may require the strategy to reduce exposure first (configurable: block or force-reduce).
- [ ] Account-level reserve: a configurable amount or percentage of equity that no strategy can use (protects manual holdings' margin and the owner's cash needs).
- [ ] Buying-power sharing: each strategy gets `min(allocation-based limit, share of current account buying power)`; the guard prevents the sum of strategies from consuming buying power needed to keep manual positions safe from margin calls.
- [ ] Reservations for open orders so two strategies cannot spend the same cash concurrently; released on fill, cancel, reject, or expiry.

### Attribution and positions
- [ ] Parse attribution IDs from `client_order_id` / `orderRef`; executions with no valid platform ID go to `manual`; executions with the `manual` short ID (placed via the platform UI) also go to `manual` but are flagged as platform-entered.
- [ ] Lot-level tracking per owner (open date, quantity, cost basis including fees); FIFO relief within an owner by default.
- [ ] Mark-to-market using the latest price from the cache; P&L split into realized, unrealized, fees, and dividends per owner.
- [ ] Dividends and fees allocated to the owner(s) holding the position on the record/ex date, pro rata by quantity.
- [ ] Equity curve snapshots per owner (default every minute during market hours and at close).
- [ ] Paper and live accounts tracked identically; backtests use the same ledger library in-process (section 08) so P&L math is shared.

### Manual activity and encroachment
- [ ] Manual buys increase `manual_qty`.
- [ ] Manual sells decrease `manual_qty`. If a manual sell exceeds `manual_qty` (the owner sold shares the ledger attributes to a strategy), raise an **encroachment break**: halt the affected strategy for that instrument, alert, and require the owner to resolve it (reassign quantity to manual, or reduce the strategy's quantity with an audited adjustment).
- [ ] Broker-side events that change quantity without an execution (corporate actions, transfers in/out, assignments) are applied by documented rules; unknown events create breaks rather than guesses.

### Corporate actions
- [ ] Splits and reverse splits adjust every owner's lots (quantity and cost basis) proportionally; cash in lieu allocated pro rata.
- [ ] Symbol changes keep `instrument_id`; mergers/spin-offs create new lots per documented rules, else a break for manual resolution.
- [ ] Delistings mark positions and alert.

### Reconciliation
- [ ] Triggers: every account snapshot (continuous, debounced), on broker reconnect (requested by section 04), at session open and close, and on demand.
- [ ] Compare positions (invariant 1), cash (within tolerance for pending settlements), and open orders (OMS vs broker).
- [ ] Classify breaks: `timing` (fill not yet processed; auto-resolves within a grace window), `encroachment`, `unknown_execution`, `missing_execution`, `corporate_action`, `cash_mismatch`, `orphan_order` (open at broker, unknown to OMS), `ghost_order` (open in OMS, unknown to broker).
- [ ] Breaks that do not auto-resolve within the grace window publish `recon.result` with status `broken`; execution (section 07) halts affected strategies or the whole account per severity.
- [ ] Break resolution UI data: suggested resolutions, required step-up, full audit trail.

### Wash sales and tax-aware flags (informational)
- [ ] Flag potential wash sales when a loss-realizing sell by any owner is within 30 days of a buy of the same or substantially identical security by any owner in any connected account (wash-sale rules apply across the taxpayer's accounts, not per strategy). Show flags; do not block by default. Confirm rules in research §12.

## Non-functional requirements

- **Performance**: `GetOwnershipConstraints` and `ReserveForOrder` p99 under 5 ms (they are on the order path); reconciliation of an account with 500 positions under 1 s.
- **Correctness**: decimal arithmetic only; event-sourced entries are append-only; materialized views rebuildable from events (rebuild test).
- **Concurrency**: reservations are atomic (serializable transaction or row lock per strategy-account-instrument).
- **Security**: allocation and resolution actions require step-up where listed; audited.
- **Tests**: property-based tests over random sequences of strategy fills, manual trades, splits, and dividends asserting all invariants; scenario tests listed in acceptance criteria; replay test from recorded paper sessions.

## Acceptance criteria

- [ ] Docs updated per `AGENTS.md` (master N6) in the same commits as the changes: `docs/file-map.md`, `docs/business-logic/ledger.md` (owners, invariants, break types), `CHANGELOG.md`, `docs/glossary.md` for new terms, and, for every decision made, an ADR in `docs/adr/` plus its row updated (or added) in `docs/decisions.md`.
- [ ] **Same-ticker scenario**: manual holds 100 X; strategy A buys 50 X then sells 50 X; broker ends at 100 X and ledger shows manual 100, A 0. A's attempt to sell 60 X is rejected by `GetOwnershipConstraints` (`max_sellable_by_strategy = 50`).
- [ ] **Short block**: with manual long X in a shared account, strategy A's short request returns `short_allowed=false` with reason.
- [ ] **Two strategies, one symbol**: A and B both hold X; selling A's lots never reduces B's quantity in the ledger.
- [ ] **Encroachment**: owner sells 120 X in the broker app while manual = 100 and A = 50; an encroachment break is raised within one snapshot cycle, A is halted for X, and resolution is audited.
- [ ] **Split**: a 4:1 split on X adjusts all owners' quantities and cost bases; invariant 1 holds after processing.
- [ ] **Over-allocation**: setting allocations whose sum exceeds allocatable capital is rejected; concurrent reservations from two strategies cannot exceed available cash (concurrency test).
- [ ] Five paper sessions with zero unexplained breaks (Gate 2), with daily reconciliation reports stored.
- [ ] Ledger rebuilt from events matches the materialized state exactly.

## Pitfalls to avoid

- Assuming the broker keeps separate positions per strategy. In a single account, the broker sees one net position per symbol; a strategy "sell" closes shares that the broker and the tax lot method may attribute to the owner's long-term lots (FIFO by default at many brokers). Document the broker-side lot method and consider specific-lot identification where supported.
- Allowing a strategy short while the owner is long the same symbol in the same account; the sell just reduces the owner's long.
- Using floats for quantities or money.
- Ignoring open-order reservations; two strategies can over-commit the same cash between fills.
- Silently "fixing" breaks by adjusting numbers; every adjustment is an audited, explained entry.
- Forgetting fractional shares, cash in lieu, and fees when checking invariants.
- Treating cash-account settlement like margin buying power (good-faith violations), if U1 is a cash account.
- Wash-sale effects across strategies and manual trades surprising the owner at tax time; surface them early.
