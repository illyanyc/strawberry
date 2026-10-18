# Virtual portfolios and attribution

**Prefix:** `VP-`  ·  **Owners:** `ledger`, `execution`  ·  **Related:** [orders](orders-and-execution.md), [risk](risk-management.md), [ADR-0024](../adr/0024-virtual-ledgers-and-reconciliation.md) (D42, implements D19)

## Scope

Ownership of every position, lot, and order inside real broker accounts: the full real account, one virtual portfolio per strategy, the `manual` bucket, and the broker-lot shadow ledger. Covers attribution IDs, lots, capital and reservations, corporate actions, reconciliation and breaks. Not covered: risk limits (see [risk](risk-management.md)).

## Concepts and states

- **Real account:** broker truth per `broker_account_id` (positions, cash, buying power, equity), from `acct.snapshot.<acct>`.
- **Owner:** a strategy ID or `manual`. Each lot has exactly one owner. `broker` is the pseudo-owner of the shadow ledger.
- **Virtual portfolio:** a strategy's allocation, FIFO lots, realized/unrealized P&L, fees, dividends, and reservations inside one real account.
- **Manual bucket:** everything not attributed to a strategy, including opening balances and activity outside the platform.
- **Broker-lot shadow ledger:** one FIFO lot queue per instrument across all owners, i.e. how a FIFO broker sees tax lots. A strategy sale can relieve the owner's oldest manual lots at the broker even though the virtual ledger relieves the strategy's own lots (tax-lot divergence; informational).
- **Event log:** every change to positions, lots, cash, allocations, and break resolutions is one append-only event with a unique source ID; positions are materialized from it in the same transaction and are rebuildable.
- **Break lifecycle:** `timing` (difference inside the grace window) → `open` break (critical or warning) → `auto_resolved` (difference disappeared) or `resolved` (owner action).
- Model (D19): virtual ledgers in every account, plus a dedicated IBKR linked "bot" account when the owner opens it (O1), and Alpaca symbol segregation (RM-014). Without the bot account IBKR runs "Option A" (one account, virtual ledgers, same-ticker guards load-bearing).

## Rules

| ID | Rule | Status | Code / tests |
|---|---|---|---|
| VP-001 | Orders and executions without the `tb1-` attribution prefix MUST be assigned to `manual`. Platform UI manual orders use the reserved `manual` short ID and are flagged platform-entered. | Implemented | `ledger/internal/engine` `ownerFor`; `libs/go/attribution`; `TestVP016EncroachmentThroughEngine` |
| VP-002 | Attribution ID format: `tb1-<strategyShortId>-<intentULID>-<attempt>` (≤ 44 characters, the IBKR `orderRef` limit of ADR-0023); it MUST be persisted with the intent before submission. | Implemented | `libs/go/attribution`; execution OMS (OE-012) |
| VP-003 | A strategy sell, close, or short MUST be rejected if it would take the account position in that instrument below the quantity owned by all other owners, by the ledger's count or the broker's latest snapshot, whichever is lower. | Implemented | `book.Constraints`; `TestVP003SameTickerManualUntouched`, `TestVP003SameTickerThroughEngine` |
| VP-004 | A strategy MUST NOT sell more than its own open quantity minus its open sell reservations (`max_sellable_by_strategy`). A booked oversell (should be impossible after the gate) raises a critical `oversell` break. | Implemented | `book.Reserve`, `book.applyExec`; same tests |
| VP-005 | A strategy short MUST be rejected unless the strategy allows shorting, is mapped to a dedicated account, and no other owner holds the instrument there. | Implemented | `book.Constraints`; `TestVP005ShortBlockedWhileManualLong` |
| VP-006 | Automated actions (strategies, kill switches, flatten) MUST NOT reduce `manual` quantity. Only an audited owner resolution (`transfer_to_manual`, `book_to_manual`) changes manual quantity outside manual executions. | Implemented | `TestVPInvariantsProperty` (VP-I2), execution flatten (RM-006) |
| VP-007 | Reconciliation MUST run on every broker snapshot (the boundary after each execution batch), on every reconnect resync request, at the session open and close, and on demand: broker qty = Σ owner qty per instrument, and broker cash = expected cash within tolerance. | Implemented | `book.Reconcile`, `engine.OnSnapshot/Tick`; `TestVP013TimingThenMissingExecution` |
| VP-008 | An unexplained difference or ownership break MUST halt new orders for the affected strategy×instrument pairs (instrument-wide when the cause is unknown; account-wide for account-level breaks) and alert critical; it MUST NOT be auto-assigned to a strategy. | Implemented | `haltedPairs`, `ReconciliationResult.halted`; execution `recon_halt` check |
| VP-009 | Σ strategy allocations MUST NOT exceed allocatable capital = equity × (1 − `TB_LEDGER_RESERVE_PCT`, default 0.10) − `TB_LEDGER_RESERVE_ABS`. A decrease below the strategy's invested + reserved capital is blocked (configurable). Increases need step-up (RM-007) and every change is audited. | Implemented | `book.CheckAllocation`; `TestVP009OverAllocation` |
| VP-010 | Positions and lots are keyed by `instrument_id`, never ticker. Broker positions with no instrument in the instrument master are tracked under `sym:<SYMBOL>` for `manual` only; strategies can never trade them. Splits multiply every owner's lots (cost unchanged) on the ex-date; symbol changes keep the position. | Implemented | `book.applySplit`, `UnmappedKey`; `TestVP010Split`, `TestVP010SplitOnExDate` |
| VP-011 | Lot relief for a strategy sell uses only that strategy's lots, FIFO within the owner. | Implemented | `book.applyFill`; `TestVP011TwoStrategiesOneSymbol` |
| VP-012 | Strategy owners are registered per account with a unique 6–8 character short ID (`manual` reserved); the same short ID resolves to the same owner in every account. | Implemented | `engine.RegisterOwner`, `book.applyRegister` |
| VP-013 | A quantity or cash difference is a `timing` difference until it persists for `TB_LEDGER_RECON_GRACE` (default 60 s) and at least 2 snapshots; then it becomes a break. While timing, only the instrument is blocked (status `PENDING`). A difference that disappears auto-resolves. No snapshot for `TB_LEDGER_RECON_STALE_AFTER` (90 s) makes the status `STALE`. | Implemented | `book.Reconcile`; `TestVP013TimingThenMissingExecution` |
| VP-014 | Break types: `timing`, `encroachment`, `unknown_execution`, `missing_execution`, `corporate_action`, `cash_mismatch`, `orphan_order`, `ghost_order` (raised by execution's order reconciliation, OE-019), `oversell`, `unmapped_instrument`. Critical types halt (VP-008); `cash_mismatch` is a warning. | Implemented | `book/breaks.go` |
| VP-015 | The first snapshot of an account books every broker position to `manual` (lots at the broker's average cost) and sets the cash baseline; the opening is audited. | Implemented | `engine.OnSnapshot`; `TestVP003SameTickerThroughEngine` |
| VP-016 | A manual sell that takes manual quantity below zero while a strategy holds the instrument raises a critical `encroachment` break within the same snapshot cycle and halts those strategies for the instrument. | Implemented | `book.applyExec`; `TestVP016Encroachment`, `TestVP016EncroachmentThroughEngine` |
| VP-017 | A `tb1-` execution whose short ID is unregistered (or malformed) is booked to `manual` and raises a critical, instrument-wide `unknown_execution` break. | Implemented | `TestVP017UnknownExecution` |
| VP-018 | A broker quantity change with no execution to explain it becomes a critical, instrument-wide `missing_execution` break after the grace window; a cash difference becomes a `cash_mismatch` warning. | Implemented | `TestVP013TimingThenMissingExecution` |
| VP-019 | Break resolution is an owner action with an audit record and (where quantities move) an event: `accept`, `transfer_to_manual` (moves strategy lots with cost basis to manual), `book_to_manual` (books the broker difference to manual at the mark), `rebaseline_cash`. Resolution needs step-up (RM-007). | Implemented | `engine.ResolveBreak`; `TestVP016EncroachmentThroughEngine` |
| VP-020 | Every order reserves capital before submission, atomically under the ledger lock: buys reserve `qty × price` against `min(cash_available, strategy share of broker buying power − other strategies' reservations)`; sells reserve own quantity. Fills shrink the reservation of their intent; terminal orders release it. `manual` reservations use the account's buying power minus every reservation. | Implemented | `book.Reserve/Capital`; `TestVP020ReservationsShrinkOnFill`, `TestVP020ConcurrentReservations` |
| VP-021 | The broker-lot shadow ledger relieves lots FIFO across all owners per instrument; its total always equals the ledger's (property-tested) and the UI shows it next to the owner split. | Implemented | `Account.applyShadow`; `TestVPInvariantsProperty` |
| VP-022 | A loss-realizing sale by any owner within 30 days before or after a purchase of the same instrument by any owner in any account is flagged as a potential wash sale. Flags are informational and never block. | Implemented | `book.washSale`; `TestVP022WashSaleAcrossOwners` |
| VP-023 | Cash dividends credit every owner holding the instrument at the ex-date, pro rata by quantity; corporate actions without an automatic rule (merger, spin-off, delisting with a position) raise a critical `corporate_action` break. Disputed actions (MD-012) wait for the owner. | Implemented | `engine.applyDueActionsLocked` |
| VP-024 | The ledger records an equity point per owner at most once per `EquityInterval` (default 1 minute) from each snapshot, plus the session-start equity (day P&L) and high-water mark used by RM-005; an owner's first observation is their baseline until a session starts (so an owner first seen outside market hours never reports its whole equity as day P&L), and an allocation change writes an extra point with both shifted by the change. | Implemented | `engine.equityPoints`, `engine.shiftDayStart`; `TestRM005AllocationChangesAreNotDayPnL`, `TestRM005FirstObservationOutsideSessionIsTheBaseline` |
| VP-025 | Every reconciliation run is published on `recon.result.<acct>`; runs that change status, every non-snapshot trigger, and the session-open and session-close runs (each against the first snapshot taken at or after the boundary) are stored as reports (`ledger.recon_reports`, `ListReconciliationReports`). | Implemented | `engine.reconcileLocked`; Phase 2 paper gate |

## Invariants

| ID | Invariant | Checked by |
|---|---|---|
| VP-I1 | For every account and instrument: broker qty = Σ owner qty (after pending executions are applied). | Reconciliation (VP-007); `TestVPInvariantsProperty` |
| VP-I2 | No automated action decreases `manual` quantity. | `TestVPInvariantsProperty`; same-ticker gate test |
| VP-I3 | Every lot has exactly one owner and one source (execution, opening, transfer, adjustment); every change has one append-only event. | Property test; `ledger.events` UNIQUE (account, kind, source), no UPDATE/DELETE grant (`TestVPPostgresStoreRebuild`) |
| VP-I4 | Replaying the event log reproduces the materialized positions exactly. | `TestVPRebuildFromEventsMatchesMaterialized`, `TestVPPostgresStoreRebuild` |

## Edge cases

| Scenario | Expected behavior | Rules |
|---|---|---|
| Owner holds 100 X manually; strategy buys 50 X then sells 50 X | Manual stays 100 at broker and in ledger; the broker shadow ledger relieved manual's oldest lot | VP-003, VP-011, VP-021 |
| Strategy owns 50 X, tries to sell 60 X | Rejected (`max_sellable_by_strategy` = 50) | VP-004 |
| Strategy tries to short X while manual is long X (single account) | Rejected with reason | VP-005 |
| Owner sells 120 X in the broker app (manual 100, A 50) | Manual −20, A 50, account 30; encroachment break; A halted for X; owner transfers 20 from A to manual | VP-016, VP-019 |
| 4:1 split on X held by manual and two strategies | Applied on the ex-date to every owner and the shadow ledger; cost unchanged; recon OK | VP-010 |
| Execution processed after the snapshot that already includes it | `timing` difference, instrument blocked, auto-resolves on the next snapshot | VP-013 |
| Unknown asset held manually (not in the instrument master) | Tracked as `sym:<SYMBOL>` for manual; never tradable by strategies | VP-010 |
| Ledger database lost and restarted on a fresh schema | First snapshot books everything to manual: strategies own nothing and cannot sell (safe direction); restore from backup (OP-051). A manual → strategy transfer resolution is Planned | VP-015 |
| Two strategies reserve cash concurrently beyond broker buying power | Serialized; total accepted ≤ buying power | VP-020 |

## V8 (IBKR tax lots and self-trade prevention)

Desk research, not yet paper-tested (no IBKR credentials; spike row in [verify-early](../verify-early.md)): the TWS API has no per-order tax-lot field; IBKR applies the account's lot-matching method (Account Management default FIFO; alternatives include LIFO, highest cost, maximize losses, and specific-lot changes through the Tax Optimizer until the end of the trade day). The ledger therefore models the broker side as FIFO (VP-021) and never relies on specific-lot selection. Self-trade prevention across linked accounts is unverified; the platform only ever trades the bot account (O1) or, under Option A, prevents same-account opposite-side resting orders itself (OE-018).

## Open questions

- Tax-lot method at each broker after the V8 paper test (owner may set Specific Lot / Tax Optimizer at IBKR).
- Cash-in-lieu on splits arrives as a broker activity and currently shows as a `cash_mismatch` warning until rebaselined.
- Equity-curve sampling during the session is snapshot-driven (15 s snapshots → 1-minute points); a separate close mark is Planned with marketdata EOD marks.
