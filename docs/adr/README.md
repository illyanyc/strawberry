# Architecture Decision Records

One file per significant decision: stack choices, service boundaries, contract shapes, storage layout, security model, and any rule that constrains future work. The current status of every platform decision (D1–D27) is in [`../decisions.md`](../decisions.md).

## Process

1. Copy [`template.md`](template.md) to `NNNN-short-kebab-title.md` with the next free number (4 digits, never reused).
2. Fill it in. Status starts as `Proposed`; set `Accepted` when the owner (or the assigned task) approves.
3. In the same commit: add it to the index below, update `../decisions.md` (if it settles a `D` item), replace affected `DECISION PENDING` markers, and add a changelog entry.
4. A provisional ADR accepted by the research report gets its status updated and a dated "Research outcome" section appended; the original decision text is not edited. A decision the research *changes* gets a new ADR.
5. To change a decision, write a new ADR that supersedes the old one. Set the old one's status to `Superseded by ADR-NNNN`; do not edit its decision text.

Write an ADR when a choice is hard to reverse, affects more than one service, or a future agent would otherwise ask "why is it like this?". Skip it for local implementation details.

## Index

| ADR | Title | Status | Date |
|---|---|---|---|
| [0001](0001-agent-documentation-structure.md) | Agent documentation structure and protocol | Accepted | 2026-10-17 |
| [0002](0002-monorepo-layout-and-toolchain.md) | Monorepo layout, workspaces, and pinned toolchain | Accepted | 2026-10-17 |
| [0003](0003-contracts-protobuf-buf.md) | Contracts in Protobuf with buf; Go/Python/TS codegen | Accepted (research) | 2026-10-17 |
| [0004](0004-core-language-split.md) | Go for core services, Python for ML and research | Accepted (research) | 2026-10-17 |
| [0005](0005-postgres-layout-and-migrations.md) | PostgreSQL layout, least-privilege roles, and migrations | Accepted (research) | 2026-10-17 |
| [0006](0006-audit-log-hash-chain.md) | Tamper-evident audit log as a Postgres hash chain | Accepted | 2026-10-17 |
| [0007](0007-service-to-service-auth.md) | Service-to-service authentication with short-lived Ed25519 tokens | Accepted | 2026-10-17 |
| [0008](0008-event-bus-nats-jetstream.md) | NATS JetStream as the event bus | Accepted (research) | 2026-10-17 |
| [0009](0009-owner-authentication.md) | Owner authentication with built-in passkeys, TOTP fallback, and server-side sessions | Accepted (research) | 2026-10-17 |
| [0010](0010-account-routing-and-day-trade-policy.md) | Account routing, PDT guard off by default, and an intraday-margin guard | Accepted (research) | 2026-10-17 |
| [0011](0011-secrets-management.md) | Secrets: mounted files, SOPS + age at rest, envelope encryption for broker credentials | Accepted (research) | 2026-10-17 |
| [0012](0012-web-ui-spa-sse-pwa.md) | Static Vite + React SPA, SSE for real-time, installed PWA with Pushover for critical alerts | Accepted (research) | 2026-10-17 |
| [0013](0013-mac-mini-host-colima-native-ml.md) | Mac Mini host with Compose on Colima, native ML workers, no GPU profile | Accepted (research) | 2026-10-17 |
| [0014](0014-observability-stack.md) | Observability with Grafana, Prometheus, VictoriaLogs, and Alloy; Pushover for critical alerts | Accepted (research) | 2026-10-17 |
| [0015](0015-phase0-data-services.md) | Phase 0 data services: lake on host APFS, Valkey 9, MLflow with filesystem artifacts, Prefect 3 | Accepted (research) | 2026-10-17 |
| [0016](0016-remote-access.md) | Remote access through host `tailscaled`, optional Cloudflare Tunnel + Access | Accepted (research) | 2026-10-17 |
| [0017](0017-ci-and-supply-chain.md) | CI on GitHub Actions with supply-chain gates | Accepted | 2026-10-17 |
| [0018](0018-broker-gateway-interface.md) | One gRPC broker interface for every adapter, with an outbox-backed event path | Accepted | 2026-10-17 |
| [0019](0019-alpaca-adapter-and-simulator.md) | Hand-written Alpaca adapter, in-process simulator, and the single market-data socket | Accepted | 2026-10-17 |
| [0020](0020-lake-layout-write-path-and-query-layer.md) | Lake layout, single-writer path, reference data, and the point-in-time query layer | Accepted | 2026-10-17 |
| [0021](0021-feature-store.md) | Thin feature store in tbcore with one computation path, finite lookback, and scale invariance | Accepted | 2026-10-17 |
| [0022](0022-market-data-ingestion.md) | Market data ingestion: Alpaca through broker-gateway, FMP `/stable`, finality window, resumable backfill, nightly reconciliation | Accepted | 2026-10-17 |
| [0023](0023-ibkr-adapter-gateway-and-sessions.md) | IBKR adapter: pinned IB Gateway, per-environment `ib_async` process, maintenance-aware session supervision | Accepted | 2026-10-17 |
| [0024](0024-virtual-ledgers-and-reconciliation.md) | Event-sourced virtual ledgers, broker-lot shadow ledger, and reconciliation with a break taxonomy | Accepted | 2026-10-18 |
| [0025](0025-oms-risk-gate-and-kill-switches.md) | OMS, pre-trade gate, D30 routing and margin guard, latched kill switches, and dead-man checks | Accepted | 2026-10-18 |
| [0026](0026-strategy-runtime-and-orb-baseline.md) | Python strategy runtime on `tbcore.strategy`, intents over the bus, and the ORB baseline | Accepted | 2026-10-18 |
| [0027](0027-monitoring-ops-slice.md) | Monitoring ops slice: one alert router, bus alerts bridged by the monitor, daily reports as alerts | Accepted | 2026-10-18 |
| [0028](0028-ui-trading-slice-charts-and-bff.md) | UI trading slice: Lightweight Charts under the strict CSP, protojson BFF resources, and an SSE chart stream | Accepted | 2026-10-18 |
| [0029](0029-backtest-cost-and-fill-models.md) | Backtest cost and fill models: one NumPy cost model with a dated fee schedule; next-tradable-bar fills | Accepted | 2026-10-18 |
| [0030](0030-validation-splitters-and-overfitting-statistics.md) | Validation splitters and overfitting statistics in `tbcore.validation`; DSR as the ranking gate | Accepted | 2026-10-18 |
| [0031](0031-two-tier-backtester-and-simcore.md) | Two-tier backtester on `tbcore` with the live OMS, gate, and ledger core in a Go simcore | Accepted | 2026-10-18 |
| [0032](0032-context-data-pipeline-and-point-in-time-rules.md) | Context data through the bus into the lake, with record keys, snapshot stamping, and per-source availability rules | Accepted | 2026-10-18 |
| [0033](0033-quarantined-news-sentiment.md) | News sentiment through a quarantined local model, with a lexicon baseline and contamination flags | Accepted | 2026-10-18 |
| [0034](0034-evaluator-isolation-scorer-and-leakage-suite.md) | Evaluator isolation, the D31 promotion scorer, and the leakage suite with a point-in-time lint | Accepted | 2026-10-18 |
| [0035](0035-ml-and-optimizer-contracts-and-analyst-tool-apis.md) | ML and optimizer contracts, the separate holdout service, and the analyst tool APIs with server-side actor resolution | Accepted | 2026-10-18 |
| [0036](0036-ml-training-library-model-zoo-and-diagnostics.md) | ML training library in `tbcore.ml`: datasets, model zoo, fold-correct training, after-cost evaluation, diagnostics | Accepted | 2026-10-18 |
| [0037](0037-ml-worker-run-store-and-tracking.md) | ML worker, run store, research and holdout schemas, and MLflow as a REST-fed mirror | Accepted | 2026-10-18 |
| [0038](0038-nested-studies-trial-ledger-and-research-cycles.md) | Nested Optuna studies, one trial ledger per lineage, and MinBTL research cycles | Accepted | 2026-10-18 |
| [0039](0039-sealed-holdout-service.md) | Sealed holdout service with its own schema, role, and audience | Accepted | 2026-10-18 |
| [0040](0040-registry-promotion-gates-and-analyst-permissions.md) | Registry stages, promotion gates, the analyst permission table, evidence packs, and retraining | Accepted | 2026-10-18 |
| [0041](0041-ai-research-analyst-loop-and-tool-server.md) | AI Research Analyst: deterministic Centaur loop, tool server, stop rules, and the research journal | Accepted | 2026-10-18 |
| [0042](0042-analyst-llm-providers-masking-and-egress.md) | Analyst LLM providers per role, prompt masking, the egress guard and allowlisting proxy, and spend caps | Accepted | 2026-10-18 |
| [0043](0043-analyst-coder-sandbox-and-candidate-review.md) | Analyst coder sandbox: AST policy, no-network file-based jobs, synthetic inputs, evaluator-side checks, review before merge | Accepted | 2026-10-18 |
| [0044](0044-live-feedback-and-retraining-triggers.md) | Live feedback reports and retraining triggers for the analyst (section 13 Phase 4 slice) | Accepted | 2026-10-18 |
| [0051](0051-signals-on-the-bus-and-chart-overlays.md) | Signals keyed by strategy and instrument on the bus, served to charts as overlays | Accepted | 2026-10-18 |
| [0052](0052-tradingview-webhook-strategies-and-chart-embedding.md) | TradingView: opt-in `tv-<name>` webhook strategies behind Cloudflare Access, and chart embedding where licensing allows | Accepted | 2026-10-18 |
| [0053](0053-web-push-from-the-gateway-and-the-installed-pwa.md) | Web Push from the gateway, fed by Alertmanager, to the installed PWA | Accepted | 2026-10-18 |
| [0054](0054-phone-halts-need-a-fresh-passkey.md) | A phone halts after a fresh passkey; a desktop with one confirmation; only a desktop resumes | Accepted | 2026-10-18 |
