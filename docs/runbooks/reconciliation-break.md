# Runbook: ledger reconciliation break

Alerts: `reconciliation break: <type>` (bus `alert.critical`, source `ledger`), Prometheus `LedgerReconciliationBroken`, `LedgerReconciliationStale`. Rules VP-007..VP-019 ([virtual portfolios](../business-logic/virtual-portfolios-and-attribution.md)).

## What the platform already did

- Execution blocks new intents for the halted pairs on `recon.result.<acct>` (strategy×instrument, the whole instrument, or the whole account for account-level breaks). Exits that reduce risk through a kill switch still need the session to be `ready`.
- Nothing was "fixed": the ledger booked every execution as the broker reported it. Manual quantity was never reduced by automation (VP-006).

## Triage (UI → Portfolio → account → Breaks)

| Type | Usual cause | Resolution |
|---|---|---|
| `timing` (status PENDING) | An execution and the snapshot crossed; clears on the next snapshot | Wait one snapshot interval (15 s); if it persists past 60 s it becomes `missing_execution` |
| `encroachment` | You sold more of X in the broker app than the `manual` bucket holds | **transfer_to_manual**: move the shares you actually sold from the strategy to manual (qty = how far manual went below zero). The strategy unhalts after the next clean snapshot |
| `missing_execution` | Shares moved without an execution (ACATS transfer, journal, assignment, corporate action not ingested), or an execution was lost | Compare with the broker's activity history. If it is your transfer: **book_to_manual**. If an execution is missing: check `broker-gateway` resync logs (`docs/runbooks/broker-outage.md`), then **accept** only after the late execution has arrived |
| `unknown_execution` | A `tb1-` order whose strategy short ID is not registered (deleted strategy, foreign platform instance on the same account) | Find the order in the broker app; register the strategy or **accept** (shares stay in manual) |
| `oversell` | A strategy sold more than it owned; should be impossible after the gate | Treat as a P1 bug: engage the strategy kill switch, export the trace (`GetTrace`), file an issue, then **accept** |
| `corporate_action` | Merger, spin-off, or delisting on a held instrument | Book the result with **book_to_manual** per instrument once the broker shows the new positions |
| `cash_mismatch` (warning) | Fees, interest, dividends paid as activities, cash in lieu, deposits/withdrawals | Verify in the broker's activity list, then **rebaseline_cash** |

Every resolution needs a fresh passkey (step-up) and is written to the audit log with the break ID and your note.

## STALE status

No snapshot for 90 s: broker-gateway or the bus is down. Follow `docs/runbooks/broker-outage.md`; execution refuses new intents for the account until a fresh, clean reconciliation arrives after the session is `ready` again (OE-008, RM-009).

## Verify

- `recon.result` status `OK`, no open breaks, `halted` empty (UI status bar shows "recon ok").
- `make audit-verify` passes; the resolution appears in the audit viewer.
