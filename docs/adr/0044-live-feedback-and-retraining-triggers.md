# ADR-0044: Live feedback reports and retraining triggers for the analyst (section 13 Phase 4 slice)

- **Status:** Accepted
- **Date:** 2026-10-18
- **Decision register:** D62
- **Related:** MN-020..MN-026, AN-001, AN-013, AN-063, EX-040; ADR-0027 (monitoring ops slice), ADR-0041; research [03 §1](../research/03-optimization-and-ai-agent.md) (drift-triggered refits: PSI > 0.25, prediction shift, IC below band)

## Context

Section 13's analyst feedback slice must turn shadow, paper, and live behavior into evidence the analyst can act on, fire retraining triggers on documented thresholds without retraining on noise, route them to the analyst (and to section 11's default policy when no analyst loop runs), and drop a lineage's analyst autonomy on critical alarms. Section 13 left the drift metrics and the decay test as pending decisions (PSI/KS; CUSUM or sequential tests). The full drift and divergence monitoring with auto-demotion is Phase 5. Phase 4 has recorded paper sessions (the five-session ORB recording) but no live model predictions yet.

## Decision

1. **Report:** `tb.monitor.v1.LiveFeedbackReport` per strategy, daily 20 minutes after the close and on demand (`/feedback` on the monitor's ops port): live metrics vs the backtest's expected distribution (p10/p50/p90 and the live percentile), parity summary, a one-sided lower **CUSUM** on standardized per-trade returns (k = 0.5, h = 5), feature drift top-N by **PSI** (reference-quantile bins) with **KS**, prediction drift, calibration (Brier, ECE over 10 bins) on resolved predictions, error slices, regime coverage, execution quality. Returns are per-trade basis points; no balances, positions, quantities, or account IDs.
2. **Triggers** (config `TB_MONITOR_FEEDBACK_*`): decay breach; PSI > 0.25 on a top-5 importance feature; ECE > 0.1; the last 5 sessions or the mean trade below the backtest p10; divergence (trade match rate < 0.9 or slippage gap > 5 bps); current regime < 5 % of training. **Minimum samples:** no trigger with fewer than 30 trades, or fewer than 100 resolved predictions when predictions exist; the report is still published with `sample_sufficient = false`.
3. **Severity:** critical when decay and below-p10 fire together or three or more conditions fire; otherwise warning.
4. **Routing:** `monitor.feedback.<strategy>` carries the report; `monitor.retrain_suggested.<lineage>` carries `RetrainSuggested` (plus `monitor.decay/drift/divergence.<lineage>`), in a durable `MONITOR` stream. The analyst opens a live-feedback round for the lineage (challenger only, AN-013); the optimizer applies its default retraining policy when the lineage is at autonomy 0 (EX-040).
5. **Critical alarm → level 0 and pause** (MN-025): a critical feedback condition, or a critical bus alert whose subject names a configured strategy, publishes `analyst.autonomy.lower` (level 0, paused, actor `monitor`) and a critical alert; the assistant lowers through the optimizer and pauses its loop. Re-raising needs the owner's step-up.
6. **Phase 4 inputs:** per-trade returns are FIFO round trips from the strategy's own executions (attribution prefix); expectations come from a per-strategy configuration file (`TB_MONITOR_FEEDBACK_CONFIG`) filled from the backtest report (distributions or per-trade returns to bootstrap). Feature, prediction, and calibration inputs are wired when section 10's live inference stores predictions (Planned).

## Alternatives considered

| Option | Why not |
|---|---|
| Compare to the single backtest equity curve | Section 13 pitfall: compare to the distribution of plausible outcomes |
| Two-sided or SPRT decay test | We only act on deterioration; CUSUM is simple, well understood, and tunable with k and h |
| Fixed-width PSI bins | Reference-quantile bins avoid empty bins for skewed features |
| Trigger on every breach regardless of sample size | Retraining on noise; section 13 requires minimum samples |
| Monitor calls the optimizer to read expectations | Adds a cross-service dependency to the monitor in Phase 4; the config file is an explicit stub until the backtest report is served by section 11 |

## Consequences

- Positive: live evidence reaches the analyst in a fixed, LLM-safe shape; triggers are reproducible and testable on recorded sessions; critical alarms remove analyst autonomy immediately.
- Negative / risks: expectations from a config file can go stale (owner action to refresh when the champion changes); prediction-based conditions stay silent until inference stores predictions.
- Follow-up: Phase 5 full monitoring (dashboards, auto-demotion), serving expectations from the optimizer's gate reports, prediction stores from section 10.

## Validation

`services/monitor/tests/test_feedback.py`: golden report on the recorded paper sessions (within expectation, deterministic), injected decay (critical trigger, subjects, autonomy lowering), minimum-sample suppression, drift on a top feature only, calibration sample rule, divergence and unseen regime, no account fields, statistics sanity; the analyst-side live-feedback round test (`test_an013_*`).
