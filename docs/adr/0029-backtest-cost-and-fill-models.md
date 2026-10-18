# ADR-0029: Backtest cost and fill models

- **Status:** Accepted
- **Date:** 2026-10-18
- **Decision register:** D47
- **Related:** BT-010..BT-025, D7, D20, N2; research [02 §4](../research/02-labeling-features-validation.md); [section 08](../prompts/08-backtesting-and-validation.md)

## Context

Section 08 left the default fill model and default slippage as `DECISION PENDING (research §4)`. Intraday results on 15-minute bars are dominated by costs, and the ORB baseline trades exactly when spreads are widest (the open). Regulatory fees changed three times between May 2025 and January 2027 (SEC Section 31 $0 → $20.60 per million on 2026-04-04; FINRA TAF $0.000166 → $0.000195 on 2026-01-01, paused Oct–Dec 2026; CAT pass-through since 2025), so one static number would misprice multi-year backtests. Both backtest tiers must price trades identically or screening results drift from event-driven results.

## Decision

1. **One cost model** in `tbcore.backtest.costs`, written over NumPy arrays and used by both tiers: half-spread per side with a separate open/close regime (first/last 30 minutes, 5.0 bps vs 1.5 bps by default) or the EDGE OHLC estimator (Ardia, Guidotti, Kroencke 2024); square-root impact `k·σ·sqrt(q/ADV)` (k = 0.1) with a pessimistic 10 bps fallback when ADV or σ is unknown; broker commissions (Alpaca, IBKR fixed, IBKR tiered with exchange, clearing, and pass-through fees); a **dated** regulatory fee schedule (SEC, TAF with cap, CAT), verified 2026-10-18 against SEC, FINRA, Alpaca, and IBKR publications; borrow fees per day held. A single `cost_multiplier` scales every component; every report shows 1× and 2× (D31 rule 7).
2. **Fill model** (event-driven tier): orders arrive at decision time + latency (250 ms) and fill in the first fill bar containing or following the arrival, at its open plus slippage, never in the signal bar. With bars coarser than the 60 s finality delay this is the research's "next bar open" convention and is flagged as `coarse_fills`. Marketable limits honor their collar limit; resting limits fill only when the price trades through the limit by a tick, with a 0.5 queue haircut; stops fill at the worse of stop and open on gaps; stop-first when both legs trigger in one bar; fills per bar capped at 5 % of volume; no fills in halts, zero-volume bars, or outside the session.
3. Prices and fees are floats inside the model and become decimals at the simulated broker boundary (price to $0.0001, fees up to the cent) before the ledger core sees them.

## Alternatives considered

| Option | Why not |
|---|---|
| Fixed bps per trade | Ignores the open/close regime that drives ORB costs and hides capacity (research 02 §4 pitfall) |
| Fill at the signal bar's close | Look-ahead (N2) |
| Strict "first bar opening after arrival" with coarse bars | On 15-minute bars every fill would slip a full bar (15 min) past the decision, far more pessimistic than live (≈1 minute); the coarse-fill warning plus 1-minute fill bars when available is the honest middle |
| Limit fills on touch | Overstates fills (research 02 §4) |
| A separate vectorized cost approximation | Two cost models drift; the screening tier must price like the event-driven tier |

## Consequences

- Positive: both tiers price identically; fee changes are data, not code; 2× stress is one parameter.
- Negative / risks: k and the spread regimes are uncalibrated until real paper fills exist; the parity report's slippage attribution feeds that calibration. Per-fill cent rounding slightly overstates Alpaca fees (Alpaca rounds the daily total).
- Follow-up: calibrate `impact_k` and spread regimes from Alpaca paper fills (Gate 2 owner action); refine pre-2024 fee rows if older intraday history is purchased (O3).

## Validation

`test_costs.py` (regimes, impact, dated fees for each broker, vectorized vs scalar, decimal boundary, EDGE recovery of a known spread); the Gate 3 parity report (modeled vs recorded fills). Revisit if paper slippage exceeds the model by more than 1.5× (SM-005 implementation-shortfall threshold).
