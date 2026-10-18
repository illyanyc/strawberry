# Runbook: model monitoring (inference, drift, divergence, recordings)

Rules ML-042, ML-043 ([ml](../business-logic/ml.md)), MN-030..MN-046 ([monitoring](../business-logic/monitoring.md)); [ADR-0045](../adr/0045-full-monitoring-and-automatic-containment.md), [ADR-0046](../adr/0046-jetstream-recorder-for-live-and-paper-streams.md). Containment actions themselves: [containment](containment.md).

## InferenceLatencyHigh (warning)

Model scoring for one bar batch is slower than 20 ms at p99 (ML-043). Intents still go out, but a slow model eats into the decision-to-submit budget (U8: p99 under 500 ms after the bar close).

1. Grafana → Model drift → *Inference latency*: which strategy, and is the `features` or `score` stage slow?
2. `score` slow: the model grew (trees, leaves) or the universe grew; check the model card for `num_trees`, and CPU load on the host (`docker stats`, native workers competing for cores).
3. `features` slow: the online feature engine's lookback grew with a new feature set; check the feature set version in the deployment.
4. If it persists, demote the strategy to shadow in the UI while you investigate; re-promotion is manual.

## Feature drift, prediction drift (warning or breach)

The live feature (or prediction) distribution moved away from the model's training reference (PSI over 10 reference-quantile bins, top-5 features by training importance; MN-038). A warning alerts; a breach applies the containment policy (default: demote).

1. UI → Monitoring → strategy → **Drift**: which features, PSI and KS, missing-feature rates; compare the live and reference signal rates.
2. Rule out data problems first: market-data health (`docs/runbooks/market-data.md`), a feature set version change, missing bars (high missing rate).
3. A genuine regime change: the analyst receives a retraining trigger (`monitor.retrain_suggested`); a challenger goes to shadow, never an in-place change (EX-040).

## Divergence (daily parity)

Each paper/live strategy's closed session is replayed from the recorder's file through the event-driven backtester (MN-037). The match rate (matched intents over matched + missed + extra) and the slippage gap of matched fills versus the model are averaged over the last five sessions.

1. UI → Monitoring → strategy → **Divergence**: missed intents (live only) point at a runtime the replay does not reproduce (different model version, restarted runtime, stale or missing bars in the recording); extra intents (replay only) point at intents the live runtime did not send (runtime down, blackout); a positive slippage gap means live fills are worse than modeled.
2. "Recording incomplete" means the recorder missed part of the session; divergence is then insufficient data and never acts.

## MonitorRecorderStalled, MonitorRecordingIncomplete

The recorder (inside the monitor, ADR-0046) writes one file per session under `data/recordings/` from durable JetStream consumers. Bars live only one hour in JetStream, so a monitor outage longer than that loses bars for the session.

1. `make logs SERVICE=monitor`; check NATS and the `monitor` container's memory.
2. `cat data/recordings/<date>.json`: `gaps` lists expired message ranges or a consumer created mid-session; a restart alone is not a gap.
3. Nothing to repair: the next complete session restores divergence. Recordings older than `TB_RECORDER_RETENTION_DAYS` (180) are deleted; they are included in backups.
