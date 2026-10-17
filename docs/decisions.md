# Decision register

One row per platform decision. Defaults come from the build plan and are used until the research report lands and an ADR accepts or replaces them. When a decision is made: write the ADR ([`adr/`](adr/README.md)), set the status to Accepted with the ADR link, and update the `DECISION PENDING` markers in `AGENTS.md`, `.cursor/rules/`, and `architecture/overview.md`.

Status values: **Pending** (not yet relied on; use the default behind an interface when needed), **Provisional** (default adopted and built on, pending the research report; ADR linked and marked provisional), **Accepted** (settled; ADR linked), **Superseded** (link the replacing ADR).

When the research report lands, each Provisional row is either confirmed (status → Accepted, ADR updated) or replaced by a new superseding ADR.

| ID | Decision | Default | Status | ADR |
|---|---|---|---|---|
| D1 | Core language (ingestion, broker adapters, OMS/risk, ledger) | Go core; Python for ML/research | Provisional (default, pending research) | [ADR-0004](adr/0004-core-language-split.md) |
| D2 | Event bus | NATS JetStream | Provisional (default, pending research) | [ADR-0008](adr/0008-event-bus-nats-jetstream.md) |
| D3 | Service RPC and schemas | gRPC + Protobuf via `buf`; REST + WebSocket to UI | Provisional (default, pending research) | [ADR-0003](adr/0003-contracts-protobuf-buf.md) |
| D4 | Analytical market-data store | Parquet lake queried by DuckDB, single writer | Pending | — |
| D5 | Transactional state | PostgreSQL (one `platform` DB, schema per area, `svc_*` least-privilege roles) | Provisional (default, pending research) | [ADR-0005](adr/0005-postgres-layout-and-migrations.md) |
| D6 | Hot cache / online features | Redis-compatible (Valkey/Redis) | Pending | — |
| D7 | Backtesting engine | Vectorized research tier + event-driven tier sharing live strategy code | Pending | — |
| D8 | Experiment tracking / registry | MLflow (self-hosted) | Pending | — |
| D9 | Hyperparameter optimization | Optuna | Pending | — |
| D10 | Job orchestration | Prefect (self-hosted) | Pending | — |
| D11 | Frontend | Next.js + TypeScript + Tailwind + shadcn/ui | Pending | — |
| D12 | Charting | TradingView Lightweight Charts; ECharts/uPlot; Perspective | Pending | — |
| D13 | Mobile | Responsive PWA + Web Push | Pending | — |
| D14 | Real-time UI transport | WebSocket (SSE fallback) | Pending | — |
| D15 | Authentication | Passkeys (WebAuthn) + TOTP; OIDC if multi-user | Pending | — |
| D16 | Secrets | SOPS + age; envelope encryption for broker credentials | Pending | — |
| D17 | Remote network access | Tailscale, no public inbound ports | Pending | — |
| D18 | IBKR connectivity | IB Gateway + IBC (TWS API); Client Portal Web API fallback | Pending | — |
| D19 | Virtual portfolio model | One account per broker, internal virtual ledgers + order tagging | Pending | — |
| D20 | Market data vendors | Alpaca (IEX/SIP) + IBKR; optional vendor adapters | Pending | — |
| D21 | Remote orchestration | Single droplet with Docker Compose | Pending | — |
| D22 | Observability | Prometheus + Grafana + Loki + OpenTelemetry/Tempo | Pending | — |
| D23 | MVP model families | Logistic regression baseline + LightGBM | Pending | — |
| D24 | Default labeling | Triple-barrier + meta-labeling; fixed-horizon baseline | Pending | — |
| D25 | GPU strategy | CPU first; optional GPU profile; on-demand cloud GPU | Pending | — |
| D26 | Assistant LLM provider | Pluggable (local or API); no secrets/account data leave the box by default | Pending | — |
| D27 | Price adjustments | Store raw bars + corporate actions; adjust at read time as of decision date | Pending | — |
| D28 | Monorepo layout, workspaces, pinned toolchain | `go.work` + `uv` + `pnpm` workspaces; versions in `tools/versions.env`; `make` entry point | Accepted | [ADR-0002](adr/0002-monorepo-layout-and-toolchain.md) |
| D29 | Service-to-service authentication | Short-lived Ed25519 service tokens with per-method allowlists (mTLS deferred to Phase 6) | Provisional (default, pending research) | [ADR-0007](adr/0007-service-to-service-auth.md) |
| D30 | Audit log design | Postgres hash chain computed by a SECURITY DEFINER function; Go verifier | Accepted | [ADR-0006](adr/0006-audit-log-hash-chain.md) |
| — | Agent documentation structure and protocol | — | Accepted | [ADR-0001](adr/0001-agent-documentation-structure.md) |
