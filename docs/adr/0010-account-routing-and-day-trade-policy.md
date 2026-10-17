# ADR-0010: Account routing, PDT guard off by default, and an intraday-margin guard

- **Status:** Accepted (research)
- **Date:** 2026-10-17
- **Decision register:** D30 (replaces the master prompt's U1 placeholder "PDT guard ON")
- **Related:** research [09 §D30](../research/09-decisions.md#d30), [04 §1](../research/04-brokers-data-ingestion.md#1-pdt-is-gone-account-routing-for-alpaca-20k-and-ibkr-100k); rules RM-003, RM-008, RM-011..RM-013; D19

## Context

The owner has IBKR ≈ $100k and Alpaca ≈ $20k, both margin accounts holding manual positions. The master prompt assumed a margin account under $25k with a PDT guard on (3 day trades per 5 business days). Research found that FINRA's Rule 4210 amendments took effect on 2026-06-04, retiring the PDT rule, and Alpaca removed it that day. IBKR is the fragile broker (weekly 2FA, archived IBC), so it suits swing strategies with broker-side stops better than intraday ones.

## Decision

- **Routing by horizon**: each strategy declares `intraday` or `swing`. Intraday strategies route to **Alpaca** at margin multiplier 1; swing strategies route to the **IBKR bot account** (a dedicated linked account if the owner opens one, D19) with GTC broker-side protective stops. The gate rejects orders to accounts not in the strategy's route (RM-011).
- **PDT guard off by default**, kept as a per-account setting `pdt_guard: off|legacy` for brokers still phasing in; day-trade counts are tracked either way (RM-008).
- **Intraday-margin guard**: per-account caps on leverage and margin utilization, including the strategies' share, so the manual book keeps its buying power (RM-012).
- **Halt on margin events**: an IMD, margin call, or broker restriction halts all strategies on that account until a manual, step-up-authorized release (RM-013).

## Alternatives considered

| Option | Why not |
|---|---|
| Keep the PDT guard on (master placeholder) | The rule no longer exists; it would block legitimate intraday trading on the $20k account. |
| Intraday at IBKR, swing at Alpaca (earlier project context) | IBKR sessions drop weekly and need 2FA; intraday strategies need the more reliable API. |
| Alpaca cash account | Settled-funds rules restrict turnover; no longer needed once PDT is gone. |

## Consequences

- Positive: intraday trading on the smaller account is allowed but unlevered; the fragile broker holds positions protected by broker-side stops; the manual book is shielded from strategy margin use.
- Negative / risks: if a broker reinstates a PDT-like restriction, the owner must set `pdt_guard: legacy` for that account; margin-utilization defaults are starting values to tune in paper.
- Follow-up: implemented in `execution` (section 07, Phase 2) with table-driven tests for every rejection path; the UI status bar shows routing, margin utilization, and day-trade count (section 14).

## Validation

Phase 2 gate: the risk test matrix covers RM-008 (both modes), RM-011, RM-012, and RM-013; the IMD drill halts the account and requires step-up to release.
