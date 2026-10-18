# ADR-0048: Short borrow accrual and delisting closes in the backtest engines

- **Status:** Accepted
- **Date:** 2026-10-18
- **Decision register:** D66
- **Related:** BT-008, BT-014, BT-015, VP-005; ADR-0029 (cost and fill models, D47), ADR-0031 (two-tier backtester and simcore, D49); research [02 §4](../research/02-labeling-features-validation.md)

## Context

BT-014 (borrow) existed only as a cost function and BT-008 applied splits and dividends but not delistings, so short strategies looked cheaper than they are and a delisted holding simply froze (event tier) or vanished from the results (vectorized ML screen), which is survivorship bias in the screen. The event tier's equity is checked against the Go simcore's ledger core every session (BT-I4), so a cost booked only in Python would break the valuation check and would not be seen by the risk gate (drawdown limits). The simcore also had no way to simulate an account where strategies may short: the OMS registers owners as non-dedicated, and VP-005 allows shorts only in a dedicated account.

## Decision

1. **Borrow in the event tier** is charged at each session close for every short a strategy holds over the close: notional at the close × the instrument's annual rate (`account.borrow_fee_bps`, else `cost_model.borrow_fee_bps_annual`) × calendar days to the next session open / 360 × `cost_multiplier`, rounded up to the cent. It is booked by the ledger core through a new simcore request `SimFee` and a ledger-book event kind `fee` (realized loss, owner fees, cash), so equity, the gate, and reports all see it. Intraday shorts pay nothing (no settlement-date position).
2. **Borrow in the vectorized ML screen** is charged per swing short for the calendar days between the entry and exit sessions; the intraday ORB screen never holds overnight and is unchanged.
3. **Delistings in the event tier** are corporate-action rows of type `delisting`, processed before the next session's open after the effective date: the instrument's working orders end (canceled), and a ledger-book event kind `delisting` closes every owner's position at the last traded price × (1 − `delisting_haircut`), with a closing cash flow and the broker-lot shadow ledger flattened. The trade's exit reason is `delisting`.
4. **Delistings in the vectorized ML screen**: a trade whose instrument never trades again exits at its last close × (1 − haircut) and is counted (`delisted_trades`); a missing exit bar on an instrument that trades later exits at its next open (gap or halt) instead of being dropped.
5. **Dedicated simulated accounts:** `AccountSimConfig.dedicated` (`SimAccount.dedicated`) makes the simcore's ledger register owners as dedicated, so `allow_short` strategies can short under VP-005 in a backtest exactly as they could on a dedicated IBKR bot account (D19).
6. The new book kinds are produced only by the simcore; the live ledger keeps learning about borrow and delistings from broker cash and position snapshots.

## Alternatives considered

| Option | Why not |
|---|---|
| Charge borrow in Python only | Breaks the BT-I4 valuation check; the gate's drawdown limits would not see the cost |
| Model borrow as a negative dividend | Dividends apply to every owner holding the symbol, including manual longs |
| Synthetic closing executions for delistings | Needs attribution IDs per owner and fake order IDs; a ledger event states what happened |
| Daily accrual on intraday shorts | Brokers charge on settlement-date positions; intraday shorts are not borrowed overnight |

## Consequences

- Positive: short strategies carry their real carrying cost at 1× and 2×; delisted holdings show their losses; the screen no longer drops delisted trades.
- Negative / risks: rates come from the account's borrow snapshot or a flat default until a borrow-rate source exists; hard-to-borrow names are underpriced at the default.
- Follow-up: a point-in-time borrow-rate source (IBKR shortable shares/fee rate) would feed `borrow_fee_bps`.

## Validation

`test_bt014_*` (7 calendar days on a Monday–Friday short, 14 at 2×, none for longs, vectorized swing short), `test_bt008_*` (event-tier close at 70 with the stop canceled, vectorized exit at the last close less the haircut), `TestBT014FeeIsRealizedAndIdempotent`, `TestBT008DelistingClosesEveryOwner`; no `valuation_mismatch` warnings.
