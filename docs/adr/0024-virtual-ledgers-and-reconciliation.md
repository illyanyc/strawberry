# ADR-0024: Event-sourced virtual ledgers, broker-lot shadow ledger, and reconciliation with a break taxonomy

- **Status:** Accepted
- **Date:** 2026-10-18
- **Decision register:** D42 (implements D19 and D5 for the ledger)
- **Related:** VP-001..VP-025, OE-006, RM-005, RM-007, [ADR-0018](0018-broker-gateway-interface.md), [ADR-0023](0023-ibkr-adapter-gateway-and-sessions.md), research 05 §2 (D19), section 06 prompt

## Context

Every broker account mixes the owner's long-term manual holdings with strategy positions. Brokers keep one net position per symbol, so the platform must split the real account into per-strategy virtual portfolios and a `manual` bucket, guarantee that automation never touches manual quantity (N3), and tie out to the broker continuously (VP-I1). Section 06 left three choices open: the account model (D19 accepted: virtual ledgers + IBKR bot account + Alpaca segregation), the lot-relief method, and storage shape. Constraints: decimal arithmetic, append-only history, rebuildable state, an order-path query under 5 ms, and a single-owner deployment on one host.

## Decision

1. **Single-writer Go engine with an append-only event log** in Postgres schema `ledger`. Every position/cash/allocation change is a typed event (`execution`, `opening`, `split`, `dividend`, `symbol_change`, `register`, `allocation`, `transfer`, `manual_adjust`, `cash_rebaseline`, `resolve`) with a source ID unique per (account, kind). The engine applies the event in memory, then commits the event, the materialized position rows, operational rows (breaks, reservations, reports, equity points), audit records, and outbox messages in one transaction. A failed commit reloads the state from Postgres. Startup replays the log, then overlays the operational tables. `svc_ledger` has no UPDATE/DELETE on `ledger.events`.
2. **Owners and attribution:** owner = strategy ID or `manual`, resolved from the attribution ID at ingest and stored in the event (replay never depends on later registry changes). Unknown short IDs go to `manual` with a critical break (VP-017). Opening balances go to `manual` (VP-015). The attribution package moves to `libs/go/attribution` so broker-gateway, ledger, and execution share one parser.
3. **Lots:** signed FIFO lots per owner (VP-011), cost = signed notional + fees, pro-rata relief at 10 decimal places with exact relief on lot close. A separate **broker-lot shadow ledger** keeps one FIFO queue per instrument across owners (VP-021) because FIFO is the default broker method at both brokers and the V8 desk research found no per-order lot selection in the IBKR API.
4. **Truth first, breaks second:** executions are always booked as reported; rule violations (encroachment, oversell, unknown execution) become breaks with affected owners. Reconciliation compares snapshot vs Σ owners and expected vs broker cash; differences are `timing` for a grace window (60 s, ≥ 2 snapshots), then breaks; vanished differences auto-resolve. The result carries `halted` pairs (`owner|instrument`, `*` wildcards) that execution enforces (VP-008).
5. **Capital:** allocatable = equity × (1 − reserve %) − reserve; reservations are atomic under the engine lock, buys reserve cash against the strategy's own cash and a cap on the strategies' combined share of broker buying power, sells reserve own quantity; fills shrink reservations through the intent ULID in the attribution ID (VP-020).
6. **Interfaces:** `tb.ledger.v1.LedgerService` over gRPC (svcauth allowlist: execution for the order path, api-gateway for views and owner actions); bus inputs `exec.*`, `acct.snapshot.*`, `recon.request.*`, `ref.corporate_action.*` through durable JetStream consumers; outputs `ledger.update.<acct>`, `recon.result.<acct>`, `alert.critical`. Instrument resolution goes through `refdata` (read-only); unmapped broker positions are tracked as `sym:<SYMBOL>` for manual only.
7. **D19 in code:** the same ledger serves the IBKR bot account (when O1 is done) and Option A. Strategies may be marked `dedicated_account` to allow shorts (VP-005).

## Alternatives considered

| Option | Why not |
|---|---|
| Mutable position tables only (no event log) | Breaks invariant 5 (every entry has a source); no rebuild test; silent fixes possible |
| Full event sourcing of breaks and reservations | Breaks depend on snapshot timing, not only events; replaying them would need snapshots in the log. Operational tables with audit are simpler and still authoritative |
| Specific-lot identification at the broker | Not available per order through the IBKR API (V8 desk research); Alpaca has no lot selection API; FIFO shadow ledger documents the divergence instead |
| Assign unexplained differences to the strategy that traded last | Violates VP-008; fails open |
| Separate ledger per strategy in its own sub-account everywhere | Not available at Alpaca; IBKR linked accounts are the O1 owner decision |

## Consequences

- Positive: invariants are property-tested; state is reproducible from the log; the order-path queries are in-memory (p99 ≪ 1 ms measured; target 5 ms); one code path for paper and live; the backtester can reuse `internal/book` later (Phase 3 exposes it via a public package).
- Negative / risks: one writer process (no horizontal scale, acceptable for one owner); snapshot-vs-execution races surface as short `timing` blocks on the instrument (seconds); cash-in-lieu and fees from activities appear as `cash_mismatch` warnings until activities are ingested (Planned).
- Follow-up: activities ingestion (fees, interest, transfers), manual → strategy transfers, IBKR V8 paper test, Phase 3 public `book` package for the backtester.

## Validation

`TestVPInvariantsProperty` (200 random sequences of strategy, manual, split, and dividend events), the section 06 scenario tests, the rebuild tests (memory and Postgres), the concurrency test, the performance test, and the Phase 2 gate (five simulated sessions with zero unexplained breaks). Revisit if a broker exposes per-order lot selection or if more than one ledger writer is needed.
