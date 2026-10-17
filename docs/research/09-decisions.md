---
title: Research 09 – Resolution of decisions D1–D27 (plus proposed D28–D31)
covers: master prompt decision register
updated: 2026-10-17
---

# 09 · Decision register resolution: D1–D27, plus proposed D28–D31

[← Report index](../research-report.md)

How to read this:
- **Verdict** says whether the research **confirms** the master's default, **refines** it (same choice, with mandatory configuration or scope changes), or **changes** it (a different choice).
- **Confidence** is how likely the recommendation is to hold through implementation.
- Evidence is summarized here, with full citations in the linked sections.
- Each row is written to drop into the repo's `docs/decisions.md`, with one ADR per decision (N6).

## Summary

| ID | Decision | Master default | Recommendation | Verdict | Confidence |
|---|---|---|---|---|---|
| D1 | Core language | Go core + Python ML | Go core + Python ML; **IBKR adapter in Python (`ib_async`)** | Refines | Medium-high |
| D2 | Event bus | NATS JetStream | NATS JetStream with **`sync_interval: always`**, Postgres as system of record, outbox | Refines | Medium-high |
| D3 | RPC and schemas | gRPC + Protobuf + buf; REST + WS to UI | gRPC + Protobuf + buf; **REST + SSE** to UI | Confirms (UI transport per D14) | High |
| D4 | Analytical store | Parquet + DuckDB, single writer | Same, DuckDB 1.5.x pinned; **DuckLake-on-Postgres** upgrade path; lake on host APFS | Refines | Medium-high |
| D5 | Transactional state | PostgreSQL | PostgreSQL | Confirms | High |
| D6 | Hot cache | Valkey/Redis | **Valkey 9** (not Redis 8); optional at MVP | Confirms | High |
| D7 | Backtesting engine | Vectorized + event-driven (vectorbt / Nautilus or custom) | **Custom two-tier on `tbcore`**; NautilusTrader not adopted | Changes | Medium |
| D8 | Tracking / registry | MLflow, Postgres, object store | MLflow 3 + Postgres + **filesystem/RustFS artifacts (no MinIO)** | Refines | High |
| D9 | HPO | Optuna (Postgres) | **Optuna 5** (RDB on Postgres) + trial ledger; LLM as advisor only | Confirms | High |
| D10 | Orchestration | Prefect | Prefect 3 | Confirms | Medium-high |
| D11 | Frontend | Next.js + Tailwind + shadcn | **Vite + React 19 SPA** + TanStack Router/Query + Tailwind + shadcn | Changes | Medium-high |
| D12 | Charting | Lightweight Charts; ECharts/uPlot; Perspective | Same; **Advanced Charts ruled out (licence)** | Confirms | High |
| D13 | Mobile | Responsive PWA + Web Push | PWA + Web Push, **plus Pushover emergency for critical alerts** | Refines | Medium-high |
| D14 | Real-time transport | WebSocket (SSE fallback) | **SSE primary**, REST commands, WS only where needed | Changes | Medium |
| D15 | Authentication | Built-in passkeys + TOTP; OIDC later | Same, via **go-webauthn** in the Go gateway; Pocket ID/Keycloak later | Confirms | Medium-high |
| D16 | Secrets | SOPS + age; envelope encryption; Vault optional | Same; **OpenBao instead of Vault** if a server is needed | Confirms | Medium-high |
| D17 | Remote access | Tailscale | Tailscale (**open-source `tailscaled` daemon**) **+ optional Cloudflare Tunnel/Access** for public UI URL and webhooks | Refines | High |
| D18 | IBKR connectivity | IB Gateway + IBC container; CPAPI fallback | IB Gateway + IBC (gnzsnz arm64, **pinned; IBC archived 2026-09-01**); weekly 2FA runbook; **fallback = manual VNC login, not CPAPI** | Refines (fallback changed) | Medium-low |
| D19 | Virtual portfolio model | One account per broker + virtual ledgers | Virtual ledgers always **+ dedicated IBKR linked "bot" account** + Alpaca symbol segregation | Changes (deployment default) | Medium |
| D20 | Market data vendors | Alpaca IEX free / SIP paid + IBKR; Polygon/Databento optional | **Alpaca Algo Trader Plus (SIP, $99)** + **FMP** (fundamentals/calendars/universe) + one-off survivorship-free intraday history; IBKR minimal | Changes | Medium |
| D21 | Remote orchestration | Single DO droplet + Compose | **Mac Mini + Compose (Colima)** now; same Compose on a droplet later | Changes (host) | High |
| D22 | Observability | Prometheus + Grafana + Loki + Tempo + OTel | Grafana + Prometheus/VictoriaMetrics + **VictoriaLogs** + **Alloy**; Tempo later | Refines | Medium |
| D23 | MVP model families | Logistic baseline + LightGBM | Linear baseline + LightGBM (**LambdaRank for swing**) + **rule + meta-label** challenger | Confirms (extended) | High |
| D24 | Default labeling | Triple barrier + meta-labeling; fixed horizon baseline | **Volatility-scaled residual forward returns** (regression/rank) default; triple barrier and meta-labeling as tested alternatives | Changes | Medium |
| D25 | GPU strategy | CPU first; optional local GPU profile; cloud GPU | **Native macOS ML workers** (CPU GBDT, MPS/MLX small nets); **cloud GPU burst**; no GPU Compose profile | Changes | Medium-high |
| D26 | Assistant LLM | Pluggable local or API | Pluggable **hybrid**: API planner on de-identified data + local MLX model for sensitive/quarantined roles; Pydantic AI + MCP | Refines | Medium |
| D27 | Adjustments | Raw unadjusted + corporate actions | Same; **`adjustment_basis` flag** (FMP intraday is split-adjusted only) | Confirms | High |
| **D28** (new) | Container runtime on macOS | — | **Colima/Lima with boot-time LaunchDaemon** | New | Medium-high |
| **D29** (new) | ML worker placement | — | **Native launchd workers** on Mac; containers on Linux | New | High |
| **D30** (new) | Account routing and day-trade policy | U1 placeholder: PDT guard ON | **PDT guard OFF** (rule retired 2026-06-04); intraday → Alpaca, swing → IBKR; intraday-margin guard | New (replaces U1 placeholder) | Medium-high |
| **D31** (new) | Autonomous optimization governance | — | **12-rule honest-optimization policy** (trial ledger, DSR, PBO, holdout service, human gates) | New | High |

---

## Details

<a id="d1"></a>
### D1 · Core language (Refines; medium-high)
- **Recommendation:**
  - **Go** for api-gateway, marketdata, execution (OMS + risk), ledger, and the Alpaca adapter.
  - **Python** for the IBKR adapter (`broker-gateway-ibkr`, `ib_async`), ML, backtest, optimizer, assistant, and context data.
- **Evidence:**
  - Go GC pauses are sub-millisecond, irrelevant at seconds-level latency.
  - The official Alpaca Go SDK is maintained (v3.11, 2026-05).
  - **There is no official IBKR Go API.** `scmhub/ibapi` has four contributors, while `ib_async` is active.
  - All-Python is viable but couples the order path to ML dependencies.
  - Rust is not justified at this latency.
- **See:** [06 §1](06-architecture-and-stack.md#1-go-vs-rust-vs-python-clear-answer).
- **Would change if:** sub-100 ms decision latency becomes a requirement (consider Rust or Nautilus), or the owner prefers one language over isolation (choose all-Python).

<a id="d2"></a>
### D2 · Event bus (Refines; medium-high)
- **Recommendation:** NATS JetStream 2.15, configured as follows:
  - `sync_interval: always`
  - Replication factor 1
  - Order and fill events published through a Postgres transactional outbox
  - Consumers idempotent and able to reconcile from the brokers
- **Evidence:**
  - Apache-2.0 under CNCF (settled 2025-05).
  - About 6 MiB footprint.
  - **Jepsen lost about 14% of acknowledged writes on power failure under the default 2-minute fsync.**
- **See:** [06 §2](06-architecture-and-stack.md#2-messaging-and-contracts).

<a id="d3"></a>
### D3 · RPC and schemas (Confirms; high)
- **Recommendation:** gRPC + Protobuf managed with `buf` between services. The gateway exposes REST plus SSE to the UI (see D14).
- **Evidence:** typed cross-language contracts; IBKR's TWS API itself moved to protobuf.

<a id="d4"></a>
### D4 · Analytical store (Refines; medium-high)
- **Recommendation:**
  - Hive-partitioned Parquet queried by DuckDB 1.5.x (pinned; 2.0 ships 2026-10-21).
  - A single writer service using temp-file plus atomic rename, with a Postgres manifest.
  - Migrate to **DuckLake 1.0 with a Postgres catalog** once there are concurrent writers.
  - On the Mac, the lake sits on host APFS (bind-mounted for the writer, read natively by ML).
  - QuestDB is the server alternative.
- **Evidence:**
  - DuckDB's concurrency model is one writer process or many readers.
  - DuckLake 1.0 shipped 2026-04-13.
  - ArcticDB is BSL and needs a commercial licence for business use.
  - TimescaleDB features are under the TSL licence.
- **See:** [06 §3](06-architecture-and-stack.md#3-storage).

<a id="d5"></a>
### D5 · Transactional state (Confirms; high)
- PostgreSQL holds the OMS, ledger, users, and audit data, plus the catalogs for MLflow, Optuna, Prefect, and DuckLake. Nothing in the research contradicts this.
- Because it concentrates risk, back it up properly.

<a id="d6"></a>
### D6 · Hot cache (Confirms; high)
- **Recommendation:** Valkey 9 (BSD, Linux Foundation). Redis 8 is AGPL/RSAL/SSPL, Dragonfly is BSL, and KeyDB is abandoned.
- It is optional at the MVP if NATS KV covers "latest bar/position".

<a id="d7"></a>
### D7 · Backtesting engine (Changes; medium)
- **Recommendation:** a custom two-tier design on `tbcore`:
  - **Vectorized tier:** Polars/NumPy (+numba) for sweeps. VectorBT PRO (about $240/yr) is an optional accelerator.
  - **Event-driven tier:** replays recorded bus messages into the **same strategy API and cost model** as live.
  - NautilusTrader is kept as a reference, not a dependency.
- **Evidence:**
  - Nautilus has **no Alpaca adapter** ("not in the near term").
  - Nautilus would own order state, duplicating the Go OMS.
  - Nautilus 2.0 is still a release candidate.
  - The LEAN CLI requires a paid tier.
  - vectorbt OSS is frozen under Commons Clause, and backtrader is inactive.
- **See:** [02 §4.1](02-labeling-features-validation.md#41-engine-comparison-this-decides-d7).
- **Would change if:** the platform drops Alpaca and goes IBKR + Databento only. Nautilus then becomes attractive.

<a id="d8"></a>
### D8 · Experiment tracking and registry (Refines; high)
- **Recommendation:** MLflow 3.x with a Postgres backend and a filesystem (restic-backed) or RustFS artifact store.
- **Evidence:**
  - **MinIO's community edition is unmaintained** (source-only, then maintenance mode, repo marked unmaintained). MLflow's own compose uses RustFS.
  - The W&B self-hosted free tier is personal-only.

<a id="d9"></a>
### D9 · Hyperparameter optimization (Confirms; high)
- **Recommendation:** Optuna 5.0 with RDBStorage on Postgres, plus optuna-dashboard. Add the D31 trial ledger. The LLM acts only as an advisor (Centaur pattern); a LLAMBO warm-start is optional.
- **Evidence:** in a 2026 head-to-head, TPE and CMA-ES beat LLM-only HPO, and the hybrid did best.

<a id="d10"></a>
### D10 · Job orchestration (Confirms; medium-high, up from low)
- **Recommendation:** Prefect 3.
- **Evidence:**
  - Lowest idle footprint, about 410 MiB versus about 650 MiB for Dagster and 1.5 GiB for Airflow.
  - **Prefect acquired Dagster (2026-07-13)**, which removes vendor risk.
  - It is a Pydantic AI durable-execution backend.
  - Temporal is overkill, and Ray is unnecessary on one host.

<a id="d11"></a>
### D11 · Frontend framework (Changes; medium-high)
- **Recommendation:** a static **Vite + React 19 SPA** with TanStack Router/Query, Tailwind, and shadcn/ui, served by the Go gateway.
- **Evidence:**
  - The UI is a private dashboard with no SSR or SEO need.
  - **CVE-2025-29927** allowed a middleware auth bypass on self-hosted Next.js.
  - **React2Shell (CVE-2025-55182, CVSS 10)** is a pre-auth RCE through React Server Components that was exploited in the wild.
  - A static SPA leaves one auth surface: the Go gateway.
- **Impact:** section prompt 14 assumes Next.js. Its routing and data-fetching guidance should switch to TanStack.
- **Would change if:** public marketing pages or SEO become goals. Use Next.js static export for those pages only.

<a id="d12"></a>
### D12 · Charting (Confirms; high)
- **Recommendation:**
  - Lightweight Charts 5.2 (Apache-2.0, with attribution).
  - uPlot for dense series and ECharts 6 for analytics.
  - Perspective 4 (`@perspective-dev`, now OpenJS) for grids.
- **Evidence:** the Advanced Charts and Trading Platform licences **forbid private or internal use**, so this is resolved as "never" for this app.

<a id="d13"></a>
### D13 · Mobile (Refines; medium-high)
- **Recommendation:** an installed PWA (declarative Web Push on iOS 18.4+) for the dashboard and kill switch. **Critical alerts go through Pushover emergency priority**, which repeats until acknowledged.
- **Evidence:** iOS Web Push works only for installed PWAs, there is EU platform-policy risk, and Pushover provides acknowledgement receipts.

<a id="d14"></a>
### D14 · Real-time UI transport (Changes; medium)
- **Recommendation:** SSE over HTTP/2 for all server-to-client streams and REST for commands. WebSocket only for a proven bidirectional high-rate need.
- **Evidence:**
  - SSE auto-reconnects and replays via `Last-Event-ID`.
  - It works with cookie auth and needs no custom heartbeats.
  - Over HTTP/2 the connection limit disappears.
  - Seconds-level latency is fine.

<a id="d15"></a>
### D15 · Authentication (Confirms; medium-high)
- **Recommendation:**
  - Built-in passkeys (go-webauthn), with a second passkey or TOTP for recovery.
  - Server-side sessions per OWASP ASVS 5.0 V7, with step-up for dangerous actions.
  - For multi-user later: Pocket ID (light, passkey-only OIDC) or Keycloak/Authentik. Zitadel is now AGPL.

<a id="d16"></a>
### D16 · Secrets management (Confirms; medium-high)
- **Recommendation:**
  - SOPS + age for deploy secrets.
  - App-level AES-256-GCM envelope encryption for broker credentials, not pgcrypto.
  - Secrets mounted as `/run/secrets` files, never env vars.
  - **OpenBao (MPL)** instead of Vault (BSL) if a server is ever needed.

<a id="d17"></a>
### D17 · Remote network access (Refines; high)
- **Recommendation:**
  - Tailscale is primary: run the **open-source `tailscaled` system daemon** on the Mac, which runs pre-login and enables SSH FileVault unlock and Funnel.
  - Add **Cloudflare Tunnel + Access with independent passkey MFA** for an optional public UI URL and as the TradingView webhook path (Service Auth + IP selector, never Bypass).
  - App-level auth applies in all cases.
  - Never tunnel IB Gateway or the databases.
- **Evidence:**
  - Tailscale's App Store and Standalone variants don't run before login.
  - Cloudflare Access gained independent MFA on 2026-04-15.
  - TradingView webhooks only reach ports 80/443.
- **See:** [08 §1](08-security-remote-access-risk.md#1-secure-remote-web-login-to-the-mac-mini).

<a id="d18"></a>
### D18 · IBKR connectivity (Refines; medium-low)
- **Recommendation:**
  - IB Gateway + IBC in the gnzsnz container (native arm64), pinned to the *stable* tag and IBC 3.24.2.
  - A Python `ib_async` adapter.
  - Weekly Sunday 2FA runbook with a Pushover reminder.
  - IBKR strategies are blocked until the session is healthy and reconciled.
  - Fallback is manual login over VNC via Tailscale. **CPAPI is not a useful fallback.**
- **Evidence:**
  - **IBC was retired and archived on 2026-09-01.**
  - Sessions are invalidated Sunday 01:00 ET, and restarting the container mid-week forces a new 2FA.
  - Web API OAuth is **not available to individuals**.
  - CPAPI needs a same-host browser login every day and a `/tickle` call every few minutes.
- **Risk:** this is the most fragile component. It is why IBKR gets swing strategies with GTC stops. See [04 §3](04-brokers-data-ingestion.md#3-ibkr-integration-notes).

<a id="d19"></a>
### D19 · Virtual portfolio model (Changes the deployment default; medium)
- **Recommendation:**
  - Keep per-strategy virtual ledgers everywhere.
  - Open **one dedicated IBKR linked account** for all bot capital.
  - At Alpaca, which has no sub-accounts, use one account with **symbol segregation** from manual holdings by default.
  - Keep a broker-lot shadow ledger and a cross-account wash-sale report.
- **Evidence:**
  - IBKR linked accounts are free, separately funded, and charged for market data once, and "two strategies" is an explicitly supported use.
  - Alpaca is FIFO-only, has a 403 self-cross guard, and has no retail sub-accounts.
  - Cross-account wash sales are not on the 1099-B.
  - §475(f) needs segregated investment positions.
- **See:** [05](05-virtual-portfolios-and-tax.md).
- **Would change if:** the owner declines a second IBKR account. Option A then applies, with the same-ticker guards.

<a id="d20"></a>
### D20 · Market data vendors (Changes; medium)
- **Recommendation:**
  - **Alpaca Algo Trader Plus ($99/mo)**: SIP real-time plus intraday history since 2016.
  - **FMP** (existing plan; Premium minimum) for fundamentals, estimates, calendars, the delisted and symbol-change universe, the macro calendar, and as the daily-bar reconciliation source. Not for intraday truth.
  - **One-off survivorship-free intraday history** (FirstRate about $500, price unverified, or a short Massive subscription). First verify Alpaca's delisted coverage.
  - Optional Sharadar ($29) for point-in-time daily data and fundamentals.
  - IBKR data kept minimal.
  - Year-one extra cost is about $170–185/mo.
- **Evidence:**
  - IEX is about 3–4% of consolidated volume, so mixing IEX live data with SIP history causes train/serve skew.
  - FMP intraday is RTH-only, split-adjusted only, filed under the current ticker, and has no delisted intraday.
  - FMP fundamentals are restated in place.
  - IBKR history excludes delisted symbols.
- **See:** [04 §5](04-brokers-data-ingestion.md#5-data-source-plan-with-costs-deliverable-8).

<a id="d21"></a>
### D21 · Remote orchestration (Changes the host; high)
- **Recommendation:** the primary host is the **Mac Mini running Docker Compose in Colima** (see D28), with native ML workers (see D29). The same Compose file runs on a DigitalOcean droplet later; General Purpose 8 vCPU/32 GB costs $252/mo. No k3s or Nomad.
- **Evidence:** user input, plus k3s's roughly 1.6 GB overhead and Nomad's BUSL licence.

<a id="d22"></a>
### D22 · Observability (Refines; medium)
- **Recommendation:**
  - Grafana with Prometheus or VictoriaMetrics.
  - **VictoriaLogs** instead of Loki.
  - **Grafana Alloy** (or the OTel Collector) as the only agent.
  - Tempo from Phase 5.
  - Pushover contact point, and risk paging directly from the execution service.
- **Evidence:**
  - Promtail reached EOL on 2026-03-02 and Grafana Agent on 2025-11-01.
  - VictoriaLogs used about 1.3 GB of RAM versus 4–7 GB for Loki in one test, which matters on a shared Mac.

<a id="d23"></a>
### D23 · MVP model families (Confirms, extended; high)
- **Recommendation:**
  - A ridge/logistic baseline.
  - **LightGBM**: regression for intraday, **LambdaRank** for swing.
  - A **rule + meta-label** challenger (ORB on stocks in play; residual reversal).
  - Sequence models, TSFM volatility features, and LLM sentiment in Phase 4. RL and LLM trading agents are excluded.
- **See:** [01 §2](01-strategies-and-models.md#2-model-survey-deliverable-2).

<a id="d24"></a>
### D24 · Default labeling (Changes; medium)
- **Recommendation:**
  - The default target is a **volatility-scaled forward return, residualized** against market and sector, with fixed horizons.
  - Intraday uses regression with multi-horizon auxiliary targets.
  - Swing uses a per-date rank.
  - Triple barrier and meta-labeling are optimizer alternatives. Meta-labeling is the default *only* for rule-based primary signals.
- **Evidence:**
  - Comparisons of triple barrier and fixed horizon are mixed, and triple barrier hurt on 5-minute bars in one study.
  - Ranking and residualized targets have stronger evidence (Poh et al., Blitz et al., Numerai).
- **See:** [02 §1](02-labeling-features-validation.md#1-labels-and-targets).

<a id="d25"></a>
### D25 · GPU strategy (Changes; medium-high)
- **Recommendation:**
  - No GPU Compose profile.
  - **Native macOS workers**: CPU for GBDT, PyTorch MPS or MLX for small networks and TSFM inference.
  - **Cloud GPU bursts** (RunPod/Vast RTX 4090 at about $0.34–0.89/h) for heavy sweeps and fine-tunes, logging to home MLflow over Tailscale.
- **Evidence:**
  - Containers on macOS can't access Metal.
  - MPS lacks float64.
  - MLX is 1–3× faster than MPS for time-series Transformers.

<a id="d26"></a>
### D26 · Assistant LLM provider (Refines; medium)
- **Recommendation:** a pluggable hybrid.
  - **API planner and coder** (Claude Sonnet 5.5, gpt-6.1-sol, or gemini-4-argon; all $2/$10 per MTok today) that sees only de-identified, ticker-masked, date-shifted aggregates.
  - **Local MLX/Ollama model** (gpt-oss-20b or Qwen3.5-35B-A3B, sized to RAM) for account-sensitive summaries and for the quarantined news→features role.
  - Pydantic AI with MCP tools and an egress allowlist.
  - Never use the Gemini free tier.
- **See:** [03 §5](03-optimization-and-ai-agent.md#5-which-llms-on-a-mac-mini-host-resolves-d26).

<a id="d27"></a>
### D27 · Adjustment storage (Confirms; high)
- **Recommendation:**
  - Store raw unadjusted bars plus a corporate-action table, adjusted as-of the decision date.
  - Add an `adjustment_basis` column (`raw` / `split` / `split_div`), because FMP intraday is split-adjusted only. Alpaca supports `adjustment=raw`.
  - Corporate actions are cross-checked between Alpaca and FMP.

<a id="d28"></a>
### D28 (new) · Container runtime on macOS (medium-high)
- **Recommendation:** Colima 0.10 on Lima 2.2 with **`limactl` boot-time LaunchDaemon autostart**. OrbStack only if auto-login is accepted. Not Docker Desktop. Apple `container` deferred.
- **Evidence:**
  - Docker Desktop and OrbStack require a GUI login.
  - Lima's LaunchDaemon starts before login.
  - Apple `container` has no upstream Compose and has DNS gaps.
- **See:** [06 §5](06-architecture-and-stack.md#5-docker-on-macos-runtime-choice-and-247-reliability).

<a id="d29"></a>
### D29 (new) · ML worker placement (high)
- **Recommendation:** on macOS, ML training and inference workers run **natively under launchd** from the same `uv` lockfile, talking to NATS and Postgres over loopback. On Linux the same code runs containerized.
- **Evidence:** there is no GPU or Metal passthrough into containers.

<a id="d30"></a>
### D30 (new) · Account routing and day-trade policy (medium-high)
- **Recommendation:**
  - **PDT guard off by default** (the FINRA 4210 amendments took effect 2026-06-04, and Alpaca removed PDT that day). Keep a per-account `pdt_guard: off|legacy` flag for brokers in phase-in.
  - **Intraday strategies → Alpaca** at `max_margin_multiplier=1`.
  - **Swing strategies → IBKR bot account** with GTC broker-side stops.
  - Add an **intraday-margin guard**, with utilization caps protecting the manual book.
  - Any IMD or margin call halts the account.
- **Evidence:** FINRA RN 26-10, Alpaca's 2026-06 changelog, and IBKR's PDT guide.
- **See:** [04 §1](04-brokers-data-ingestion.md#1-pdt-is-gone-account-routing-for-alpaca-20k-and-ibkr-100k).

<a id="d31"></a>
### D31 (new) · Autonomous optimization governance (high)
- **Recommendation:** adopt the 12-rule honest-optimization policy:
  - A trial ledger.
  - DSR over the effective N.
  - PBO ≤ 0.2.
  - Budgets derived from MinBTL.
  - Pre-registration.
  - A sealed holdout service with at most one touch per family per quarter.
  - Nested CPCV and walk-forward, then paper trading.
  - SPA/Romano–Wolf promotion tests.
  - Evaluator isolation.
  - Masked inputs to the LLM.
  - Human gates.
  - Full replay and hard caps.
- **Evidence:**
  - Agents overfit validation by 9–13 points in AIRA.
  - Reward hacking is 43× more frequent when the scorer is visible (METR).
  - LLM look-ahead premiums are documented.
  - No LLM alpha agent has replicated live results.
- **See:** [03 §3](03-optimization-and-ai-agent.md#3-keeping-an-automated-search-honest-multiple-testing).

## User-input placeholders affected (U1–U8)

| ID | Master placeholder | Now |
|---|---|---|
| U1 | Margin <$25k, PDT guard ON | IBKR ≈ $100k, Alpaca ≈ $20k; **PDT retired** → guard OFF, intraday-margin guard ON (D30) |
| U2 | Stocks + ETFs | Unchanged (assumed) |
| U3 | ~100-symbol dev universe | Intraday: 100–300 tightest-spread names + an in-play sleeve; swing: liquid top 1,000, point-in-time |
| U4 | CPU-only; optional GPU profile | Mac Mini, Apple Silicon; native MPS/MLX; cloud GPU burst (D25, D29). **RAM still to confirm** (≥32 GB recommended) |
| U5 | $0 tier | Alpaca SIP $99 + existing FMP + one-off history; about $170–185 extra in year 1 |
| U6 | Monitor + alerts + kill switch | Unchanged; Pushover for critical alerts |
| U7 | Single owner, tenant-ready schema | Unchanged; note data-licence and Tailscale-plan implications |
| U8 | p99 < 500 ms after bar close | Unchanged; comfortably achievable |
