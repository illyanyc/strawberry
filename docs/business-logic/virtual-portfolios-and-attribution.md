# Virtual portfolios and attribution

**Prefix:** `VP-`  ·  **Owners:** `ledger`, `execution`  ·  **Related:** [orders](orders-and-execution.md), [risk](risk-management.md), D19

## Scope

Ownership of every position, lot, and order inside real broker accounts: the full real account, one virtual portfolio per strategy, and the `manual` bucket. Covers attribution IDs, lot tracking, allocation, and reconciliation. Not covered: risk limits (see [risk](risk-management.md)).

## Concepts and states

- **Real account:** broker truth per `broker_account_id` (positions, cash, buying power, equity).
- **Owner:** a `strategy_id` or `manual`. Each lot has exactly one owner.
- **Virtual portfolio:** a strategy's allocated capital, lots, P&L, and limits within one real account.
- **Manual bucket:** everything not attributed to a strategy, including activity outside the platform.
- Default model (D19, DECISION PENDING): one account per broker with internal ledgers; data model allows mapping a strategy to a dedicated sub-account later.

## Rules

| ID | Rule | Status | Code / tests |
|---|---|---|---|
| VP-001 | Orders and executions without the `tb1-` attribution prefix MUST be assigned to `manual`. Platform UI manual orders use the reserved `manual` short ID. | Specified | — |
| VP-002 | Attribution ID format: `tb1-<strategyShortId>-<intentULID>-<attempt>`; it MUST be recorded in the ledger before submission and MUST fit the smallest broker field limit. | Specified | — |
| VP-003 | A strategy sell, close, or short MUST be rejected if it would take the account position in that instrument below the quantity owned by all other owners. | Specified | — |
| VP-004 | A strategy MUST NOT sell more than its own open quantity in an instrument, even if the account holds more. | Specified | — |
| VP-005 | A strategy short in an instrument where another owner is long MUST be rejected, unless the strategy is mapped to a dedicated account. | Specified | — |
| VP-006 | Automated actions (strategies, kill switches, flatten) MUST NOT reduce `manual` quantity. Only an explicit, audited owner action may. | Specified | — |
| VP-007 | Reconciliation MUST run continuously (after every execution batch and on a schedule): broker position = Σ strategy lots + manual lots, per instrument. | Specified | — |
| VP-008 | An unexplained reconciliation difference MUST halt new orders for affected strategies and alert; it MUST NOT be auto-assigned to a strategy. | Specified | — |
| VP-009 | The sum of strategy capital allocations MUST NOT exceed the configured share of account equity and buying power; over-allocation is rejected at configuration time. | Specified | — |
| VP-010 | Positions and lots are keyed by `instrument_id`, never ticker; corporate actions adjust lots for every owner proportionally and are recorded as ledger entries. | Specified | — |
| VP-011 | Lot relief for a strategy sell uses only that strategy's lots; relief method defaults to FIFO within the owner (configurable). | Specified | — |

## Invariants

| ID | Invariant | Checked by |
|---|---|---|
| VP-I1 | For every account and instrument: broker qty = Σ owner qty (after pending executions are applied). | Reconciliation (VP-007) |
| VP-I2 | No automated action decreases `manual` quantity. | Property test over random action sequences; audit query |
| VP-I3 | Every lot has exactly one owner and one source execution or corporate action. | DB constraint |

## Edge cases

| Scenario | Expected behavior | Rules |
|---|---|---|
| Owner holds 100 X manually; strategy buys 50 X then sells 50 X | Manual stays 100 at broker and in ledger | VP-003, VP-011 |
| Strategy owns 50 X, tries to sell 80 X | Rejected | VP-004 |
| Strategy tries to short X while manual is long X (single account) | Rejected (or routed to dedicated account if mapped) | VP-005 |
| Owner sells X in the broker app below the manual quantity | Reconciliation break; strategies on X halted; alert; owner resolves | VP-007, VP-008 |
| 4:1 split on X held by manual and two strategies | Each owner's lots split 4:1; cost basis divided | VP-010 |
| Ticker change on a held instrument | Same `instrument_id`; no position change | VP-010 |
| Kill switch "flatten" on the account | Closes strategy-owned quantity only | VP-006 |

## Open questions

- Single account with virtual ledgers vs dedicated sub-accounts per strategy (D19, research §10).
- Tax lot method interactions (FIFO at broker vs internal specific-lot), wash sales across manual and strategy trades (research §10, §12).
- Buying-power sharing policy between strategies and manual trading (INPUT PENDING U1).
