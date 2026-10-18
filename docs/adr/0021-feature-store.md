# ADR-0021: Thin feature store in tbcore with one computation path, finite lookback, and scale invariance

- **Status:** Accepted
- **Date:** 2026-10-17
- **Decision register:** D39
- **Related:** [data-and-features.md](../business-logic/data-and-features.md) DF-002..DF-004, DF-009, DF-012, DF-013; research [02 §2](../research/02-labeling-features-validation.md), [06 §3](../research/06-architecture-and-stack.md); [ADR-0020](0020-lake-layout-write-path-and-query-layer.md)

## Context

Training, backtesting, and live inference must use identical feature values (N2, DF-004), and nothing may use data unavailable at decision time (DF-002). The section prompt defaults to a custom thin layer over Polars, with Feast only if it clearly fits. Parity is the hard part: a full-history vectorized computation and an incremental live computation drift unless they share code, and corporate actions change price scale between "as known then" and "as known now".

## Decision

1. **Custom layer in `libs/py/tbcore/features`**, not Feast. `@feature(name, version, inputs, lookback, kind)` registers a pure function returning a Polars expression (or, for multi-step session features, a series built from the prepared frame). `FeatureSet` has a content hash over every feature's spec and source-code hash; redefining a feature with different code under the same version is an error.
2. **One computation path.** `compute(feature_set, bars)` is called by both `materialize` (offline, full history) and `OnlineFeatureEngine.update` (live, ring buffers of `max_lookback` bars per instrument). Feature functions never touch storage; they see only frames already filtered to `available_at <= as_of` (DF-003).
3. **Finite lookback (DF-013).** A value depends only on the current and previous `lookback − 1` bars of the instrument, so full-history and ring-buffer computations agree. Exponential smoothers (e.g. Wilder's RSI) are replaced by finite-window forms (Cutler's RSI).
4. **Scale invariance (DF-013).** Features are invariant to multiplying the price (or volume) series by a constant (returns, ratios, z-scores of logs), so backward adjustment as of any later date equals adjustment as of the decision time inside each window. This is why `volume_z_20` uses `log(volume)`, not `log1p`.
5. **Point-in-time materialization.** `materialize` reads inputs with `as_of` (default: end of range) and `versions="first"` (each bar as first published, as live decisions saw it), keeps rows only while the instrument was a listed universe member, and asserts no row's `available_at` exceeds `as_of`.
6. **Immutable snapshots (DF-009).** `snapshot_id` = hash of the feature-set hash, universe membership, range, `as_of`, versions, timeframe, and the exact input file names (immutable ULID names); stored as `snapshots/<id>/features.parquet` + `manifest.json` via temp-dir rename. `gc_snapshots` deletes only snapshots not referenced by runs or backtests and older than a grace period.
7. **Parity tolerance:** `|a − b| ≤ 1e-9 · max(1, |a|, |b|)` (documented, DF-004). Measured on a recorded week with a split and a dividend: max |diff| 4.0e-13 over 3,888 rows × 16 features.
8. **MVP features** (section 10 extends): 1/4/16-bar log returns, 16-bar volatility, RSI(14), ATR%(14), volume z-score(20), session-VWAP distance, gap from the previous regular close, time-of-day and day-of-week encodings, market (SPY) and sector-ETF 4-bar returns, cross-sectional rank of the 4-bar return.

## Alternatives considered

| Option | Why not |
|---|---|
| Feast | Adds a registry and online store service for one owner; its offline/online parity relies on separate implementations; no native point-in-time handling of revisions and corporate actions as we need them. |
| Incremental (streaming) implementations reused offline | Bit-exact, but offline would be Python loops over ~10⁸ rows; vectorized Polars over the same expressions is equivalent under DF-013. |
| Adjust as of each decision time offline | Per-window adjustment is quadratic; scale invariance makes it unnecessary. |

## Consequences

- Positive: one code path; parity tested to 1e-13; leakage tests (future shift, shuffled labels) run in CI; snapshots are reproducible by ID.
- Negative / risks: authors must keep features finite-lookback and scale-invariant; the parity and future-shift tests catch violations, and new features must be added to them.
- Follow-up: section 10 adds label generation on the same point-in-time API; section 12's generated-feature lint (DF-012) checks the same properties statically.

## Validation

`test_df004_offline_online_parity_on_recorded_week`, `test_df012_future_shift_leakage`, `test_df012_shuffled_labels_carry_no_signal`, `test_df003_features_cannot_see_rows_after_as_of`, `test_df009_snapshots_immutable_deterministic_and_gc`, `test_online_update_latency` (0.73 ms per bar per instrument on the reference VM; target 5 ms).
