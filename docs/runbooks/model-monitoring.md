# Runbook: model monitoring (inference, drift, divergence, recordings)

Rules ML-042, ML-043 ([ml](../business-logic/ml.md)), MN-030..MN-046 ([monitoring](../business-logic/monitoring.md)); [ADR-0045](../adr/0045-full-monitoring-and-automatic-containment.md), [ADR-0046](../adr/0046-jetstream-recorder-for-live-and-paper-streams.md). Containment actions themselves: [containment](containment.md).

## InferenceLatencyHigh (warning)

Model scoring for one bar batch is slower than 20 ms at p99 (ML-043). Intents still go out, but a slow model eats into the decision-to-submit budget (U8: p99 under 500 ms after the bar close).

1. Grafana → Model drift → *Inference latency*: which strategy, and is the `features` or `score` stage slow?
2. `score` slow: the model grew (trees, leaves) or the universe grew; check the model card for `num_trees`, and CPU load on the host (`docker stats`, native workers competing for cores).
3. `features` slow: the online feature engine's lookback grew with a new feature set; check the feature set version in the deployment.
4. If it persists, demote the strategy to shadow in the UI while you investigate; re-promotion is manual.
