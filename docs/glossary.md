# Glossary

Domain terms used in code and docs. Add terms in the same commit that introduces them; keep alphabetical.

| Term | Meaning |
|---|---|
| **`available_at`** | The earliest time the platform could have known a datum. All point-in-time filtering uses it, never `event_time`. |
| **`BROKER_UNAVAILABLE`** | The typed error (gRPC `FAILED_PRECONDITION` + `ErrorInfo` reason) a broker adapter returns for order RPCs while the account session is not `ready` (BR-004, OE-004). |
| **`event_time`** | When something happened in the world (trade, bar close, news publication). |
| **`ingested_at`** | When the platform stored a datum. |
| **`instrument_id`** | Internal stable instrument identifier, independent of ticker changes; maps to broker IDs (Alpaca asset ID, IBKR `conId`). |
| **`orderRef`** | Free-text IBKR order field carrying the attribution ID; IBKR's echo of it must match what was sent (IB-001, IB-002, spike V1). |
| **`permId`** | IBKR's permanent order ID; the durable `broker_order_id` for IBKR orders (IB-003). |
| **Ablation** | A training run that changes one factor of a base run (drop or add a feature group, a label spec, hyperparameters) while reusing its dataset snapshot, folds, and seeds, so the difference is attributable (ML-025). |
| **Account hold (IBKR)** | A per-account reason, such as `margin_deficit`, that keeps an otherwise `ready` IBKR session `degraded` for that account until cleared (IB-041, BR-021). |
| **ADR** | Architecture Decision Record in `docs/adr/`; one decision per file, superseded rather than rewritten. |
| **ALFRED** | Archival FRED: every vintage of every FRED series; `series/observations` with `output_type=1` returns each value with the real-time period it was current (CX-006, DF-010). |
| **Allocatable capital** | Account equity minus the owner's reserve (`TB_LEDGER_RESERVE_PCT`, default 10 %, plus `TB_LEDGER_RESERVE_ABS`); the sum of strategy allocations may not exceed it (VP-009). |
| **Analyst round** | One pass of the AI Research Analyst for a lineage: trigger, evidence collection, masked planner call, validated diagnosis and ranked plan, journal entry, approval or launch, decision; stored as `AnalystRound` (AN-002, AN-003). |
| **Attribution ID** | The ID placed in Alpaca `client_order_id` / IBKR `orderRef` that identifies the owning strategy and intent: `tb1-<strategyShortId>-<intentULID>-<attempt>`. |
| **Audit chain** | The hash-linked sequence of audit events: each event's hash covers the previous hash and its own canonical fields; `make audit-verify` recomputes it. |
| **Autonomy level** | Per-lineage setting for the AI analyst: 0 suggest-only (default), 1 auto-run on paper, 2 auto-promote within limits; raising it needs owner step-up; the optimizer enforces it (EX-031, EX-033). |
| **Backfill job** | A resumable marketdata job that fetches historical bars in checkpointed chunks (≤ 100 symbols × a per-timeframe window); a restart skips finished chunks and a rerun writes nothing new (MD-001). |
| **Backward adjustment (as of t)** | Multiplying raw prices before each corporate action's ex-date by the action's factor, using only actions known and effective by t (DF-005). |
| **Bar finality** | A live 1-minute bar becomes final 60 s after its close (Alpaca's update window); only final bars reach the bus, cache, lake, and features (MD-002). |
| **Bar revision** | Version number of a bar's natural key: 0 when first stored, incremented on every content change; superseded versions live in `bar_revisions/` (ST-005, DF-007). |
| **BMO / AMC / DMH** | Earnings timing: before market open, after market close, during market hours. Results are available at 09:30, 20:00, and 16:00 New York on the report date; unknown timing counts as AMC (CX-007). |
| **Break (reconciliation break)** | A recorded difference or ownership violation in an account (`timing`, `encroachment`, `unknown_execution`, `missing_execution`, `corporate_action`, `cash_mismatch`, `orphan_order`, `ghost_order`, `oversell`, `unmapped_instrument`); critical breaks halt the affected strategy×instrument pairs until resolved (VP-008, VP-014). |
| **Broker account (`broker_account_id`)** | One real account at one broker in one environment (paper or live). Paper and live are different accounts. |
| **Broker capabilities** | Per-account declaration of what an adapter supports (order types, TIFs, bracket/OCO/OTO, fractional, short, extended hours, attribution length); execution builds orders against it (BR-002). |
| **Broker simulator** | In-process deterministic broker (`broker-gateway/internal/sim`) implementing the adapter interface with a small matching engine and fault hooks; used by tests and the `sim-paper` development account (ADR-0019). |
| **Broker-lot shadow ledger** | The ledger's model of the broker's own tax lots: one FIFO queue per instrument across all owners, shown next to the owner split so tax-lot divergence (a strategy sale relieving manual's oldest lot at the broker) is visible (VP-021). |
| **Bus recording** | A file of `tb.backtest.v1.RecordedMessage` (subject, payload, message ID, receive time) captured from the bus; the replay input of the event-driven backtester and the parity harness (ADR-0031, BT-070). |
| **Candidate (registry)** | A study's final selection refit on the whole study period, with its gate report, moving through registry stages candidate → validated → shadow → paper → live_eligible → live → retired (EX-050). |
| **Canonical execution ID** | The `broker_exec_id` used to deduplicate fills; identical whether a fill arrives on the stream or from a REST resync (Alpaca: `<order_id>:<cumulative qty>`) (BR-008). |
| **Centaur loop** | The research loop in which a classical optimizer (Optuna) searches and an LLM plans (hypotheses, search spaces, config diffs) without ever being the optimizer or the judge (research 03 §2.3, ADR-0041). |
| **Champion / challenger** | The currently deployed model version for a strategy, and a candidate running in shadow or paper alongside it. |
| **Cloudflare Access token** | The `Cf-Access-Jwt-Assertion` JWT Cloudflare Access adds for the origin after its policy passes; the gateway verifies its signature, audience, issuer, and expiry on the TradingView listener and the public UI hostname (SE-064). |
| **Cold restart (IBC)** | IBC's Sunday-only full gateway restart at `ColdRestartTime` (default 17:00 ET): a fresh login, with an IBKR Mobile 2FA push for live; the platform's weekly IBKR login (IB-014). |
| **Combinatorial purged cross-validation (CPCV)** | Selection splitter that cuts the history into N contiguous groups and tests every combination of k groups (purged and embargoed), yielding C(N−1, k−1) full out-of-sample paths (BT-033). |
| **Command palette** | The ⌘K/Ctrl+K search dialog listing pages, a ticker's chart, and actions; built from the navigation registry (UI-042). |
| **Compaction (lake)** | Merging a partition's staging and part files into one sorted part file with one row per natural key (ST-004). |
| **Compose profile** | Named group of services in `infra/compose/docker-compose.yml` (`core`, `observability`, `ml`, `ml-workers`, `ops`, `remote-cloudflare`); `make up` starts `core,observability`. |
| **Contaminated score** | A model-scored feature (news sentiment) for data published before that model's training cutoff; stored but excluded from features and backtests by default (CX-014, D31 rule 10). |
| **Context record / record key** | One logical context datum (a news item, a series period, a filing) identified by `record_key`; its versions differ in payload and `available_at` (CX-002). |
| **Continuation round** | An analyst round opened by the completion of the previous round's own launches; plateau counting happens on these (AN-040). |
| **Correction (bar)** | An update to a bar after it was final: stored as a new revision and published on `md.bar.correction.*` (MD-002, DF-007). |
| **Correlation ID** | ID carried by every event from signal to fill, for tracing. |
| **Correlation trace** | The UI view of everything recorded for one correlation ID: intent, risk decision with every check, orders, and fills (UI-017). |
| **Cost stress (2×)** | The same backtest with every cost component (spread, impact, fees, borrow) doubled via `cost_multiplier`; reports show 1× and 2×, and D31 promotion requires a positive result at 2× (BT-015). |
| **Daily restart (IBC)** | IBC's daily gateway restart at `AUTO_RESTART_TIME` (default 23:45 ET) that reuses the session token, so no 2FA; outages inside its window do not alert (IB-013). |
| **Dataset snapshot** | Immutable, content-addressed feature extract (`snapshots/<id>/`) whose ID hashes the feature set, universe, range, as-of, and exact input files (DF-009). |
| **Dead-man check** | Heartbeat watch that contains risk when a component stops: execution latches cancel-open kill switches when the strategy runtime's heartbeat is lost; broker-gateway cancels working platform orders when execution's heartbeat is lost (RM-017). |
| **Decimal (contract type)** | `tb.common.v1.Decimal`: an exact base-10 number carried as a string; the only representation for money and quantity in contracts. |
| **Declarative Web Push** | A Web Push payload (`{"web_push": 8030, "notification": {...}}`) the browser can display without a service worker (Safari 18.4+); the gateway sends this shape to every browser (ADR-0052). |
| **Deflated Sharpe ratio (DSR)** | Sharpe ratio corrected for the number of trials and non-normal returns; used to rank strategies honestly. |
| **Demotion** | Automatic or manual move of a strategy to `paper` or `halted`. Re-promotion is always manual. |
| **DiagnosticsReport** | Deterministic, size-limited report produced for every training run: data quality, feature shift and leakage flags, learning curves, fold metrics, importance stability, error slices, and rule-based findings with evidence (ML-020..ML-023). |
| **Effective number of trials** | The number of independent trials behind a search, estimated as the ONC cluster count of the trials' return correlations; DSR deflates against it (BT-044). |
| **Egress guard** | The last check on an outbound LLM payload: blocks known tickers or instrument IDs, dates, epoch timestamps, account identifiers, and currency amounts (AN-052). |
| **Encroachment** | A manual sell (in the broker's app) that takes manual quantity below zero while a strategy holds the instrument, i.e. the owner sold strategy-attributed shares; raises a critical break (VP-016). |
| **End-of-day summary** | Daily report 10 minutes after the close: fills, fees, reconciliation, breaks per account; realized P&L per owner; approved and rejected intents per strategy; alerts (MN-006). |
| **Era** | One cross-sectional date (e.g. a session) treated as an indivisible unit by the splitters; used for swing books (BT-030). |
| **Evaluator manifest** | `services/backtest/evaluator_manifest.json`: the backtester, cost and fill models, validation, parity, lint, scorer, and simcore paths and modules the AI analyst may not see, write, or import (BT-081, D31 rule 9). |
| **Event blackout** | A window around a scheduled event (CPI, FOMC, an instrument's earnings) in which a strategy's opening intents are dropped; closes are never blocked (CX-016). |
| **Evidence pack** | One-page summary (markdown and HTML) of a candidate's gate report, lineage statistics, and risks, generated before any human-gated promotion; its hash is audited (EX-057). |
| **Evidence table** | Per-round index of every citable `(report_id, metric) → value` from section 08/10/11/13 reports; a finding whose citation does not match it is dropped (AN-010). |
| **Fail closed** | On uncertainty (stale data, disconnect, unknown order state), stop opening risk instead of guessing. |
| **Feature lookback** | Number of bars (current included) a feature's value depends on; finite by rule DF-013, it sizes the online ring buffer and offline warm-up. |
| **Feature set** | Versioned collection of feature definitions in `libs/py/tbcore`; identical in train, backtest, and live. |
| **Fill bar** | A bar of the series the event-driven backtester uses to simulate executions (`fill_model.fill_timeframe`, default the decision timeframe); an order fills in the fill bar containing or following its arrival (BT-020). |
| **Flatten (strategy-owned)** | Closing all positions of a strategy (or all strategies). Never includes `manual` quantity. |
| **Gap (market data)** | Regular-session minutes the calendar says should have bars but the lake does not; runs of ≥ 5 are recorded in `marketdata.gaps` and re-fetched (MD-006). |
| **Gate report** | Per-candidate report: outer walk-forward estimate rerun event-driven, DSR and PBO over the lineage ledger, SPA/Romano–Wolf vs the champion, walk-forward and regime breakdowns, 1×/1.5×/2× costs, parameter stability, leakage check, scorer verdict (EX-024, BT-080). |
| **Generated contracts** | Code under `contracts/gen/` produced by `make gen` from `contracts/proto/`; committed, and CI fails if stale (`make gen-check`). |
| **Halt (OMS)** | A block on new intents for a strategy (optionally one instrument) raised by the OMS for an unknown order state, an orphan order, or a protective stop that could not be placed; cleared when the cause is resolved (OE-005, OE-019, OE-014). |
| **Halted pair** | An `owner|instrument` key (with `*` wildcards) on `recon.result` that execution must block until the break behind it is resolved (VP-008). |
| **Holdout boundary** | The UTC time from which a lineage's data is sealed; studies and training must end at or before it; only the owner moves it, only forward (EX-010, EX-014). |
| **IB Gateway** | IBKR's headless trading application exposing the TWS API socket; runs in `ib-gateway-<env>` containers (D18). |
| **IBC** | The open-source tool that logs IB Gateway in and restarts it on schedule; archived 2026-09-01, pinned at 3.24.2 (D18, IB-073). |
| **Intraday-margin guard** | Pre-trade check capping leverage and margin utilization per account so strategies cannot consume the manual book's buying power (RM-012). |
| **Kill switch** | Scoped emergency control (global, broker account, strategy, instrument): freeze new orders, cancel open orders, optionally flatten strategy-owned quantity. |
| **Kill switch latch** | A kill-switch engagement persisted until an owner releases it with a desktop passkey step-up; engaging needs no step-up (RM-006, RM-007). |
| **Leak canary** | A unique fake secret pushed through a real flow so tests can assert it never appears in logs, responses, audit details, or plaintext storage (SE-012). |
| **Leakage suite** | The checks that look for look-ahead in a strategy or pipeline: future shift, random walk, timestamp assertion, shuffled labels, plus the random-entry baseline and the point-in-time lint; it runs in CI and must fail a deliberately leaky fixture (BT-060..BT-065). |
| **Lineage (research lineage)** | All trials for one strategy family on one data domain (timeframe + universe), across owner studies, analyst rounds, retrains, and abandoned runs; trial counting and deflation are per lineage (EX-020, EX-021). |
| **Lot** | A quantity of an instrument acquired at one time and price, owned by exactly one owner. |
| **Manual bucket (`manual` owner)** | All holdings and orders not attributed to a strategy, including anything placed outside the platform. Automated logic never reduces it. |
| **Market-data hub (fan-out)** | broker-gateway component that holds the single upstream market-data socket per feed and multiplexes every subscriber over it (BR-015). |
| **Marketable limit (collar)** | A limit order priced a few basis points through the last price, used instead of a market order so fills are bounded (OE-013). |
| **Masking anchor** | Per-round reference date (round start shifted by a round-specific 0–364 days) from which dates in LLM prompts become day offsets such as `D-30` (AN-051). |
| **Master client ID (IBKR)** | The API client ID (1) configured as `TWS_MASTER_CLIENT_ID`, which receives updates for every order including those placed in TWS or IBKR Mobile (IB-017). |
| **Meta-labeling** | A secondary model that decides whether to act on (and how to size) a primary model's signal. |
| **MinBTL** | Minimum backtest length: the history below which a given number of independent noise trials is expected to produce a target in-sample Sharpe; bounds a research cycle's trial budget (BT-043, D31 rule 3). |
| **MinTRL** | Minimum track record length: observations needed before a Sharpe ratio is significantly above a target at a confidence level (BT-042). |
| **Mode** | `backtest`, `paper`, or `live`; carried by every strategy, order, and fill. |
| **Model family** | One implementation of `tbcore.ml.Model` with a hyperparameter JSON Schema (ridge, LightGBM regression/LambdaRank/binary, rule + ML filter, ...) (ML-014). |
| **Native ML worker** | ML/backtest/optimizer process run directly on macOS under launchd (MPS/MLX available) instead of in a container (D29). |
| **Natural key (bar)** | `(instrument_id, timeframe, ts_open, feed)`: the identity used for idempotent writes and reader deduplication (ST-004). |
| **Navigation registry** | `apps/web/src/lib/nav.ts`: the one list of pages from which the sidebar, phone tab bar, "More" sheet, palette, and shortcuts are built (UI-041). |
| **Nested validation** | Inner CPCV selects hyperparameters on each outer fold's training window; the outer walk-forward tests the whole selection procedure; only the outer estimate is reported (EX-001). |
| **Opening balance** | The first broker snapshot of an account, booked entirely to `manual` with the broker's average cost and cash (VP-015). |
| **Opening-range breakout (ORB)** | The Phase 2 rule-based baseline: the first 15-minute bar's range defines the opening range; the first close above its high opens a long with a stop below the range and a 2R target; flat before the close (ADR-0026). |
| **Order intent** | A strategy's or user's request to trade, before risk checks. Becomes an order only after an approved risk decision. |
| **Outer estimate** | The stitched out-of-sample result of a study's whole selection procedure over its outer walk-forward folds; the only performance a study reports (EX-001). |
| **Outbox (transactional)** | A Postgres table written in the same transaction as the state change it announces; a relay publishes rows to NATS with a dedupe ID, so events are never lost or duplicated by a crash (D2, ADR-0018). |
| **Owner** | The strategy ID or `manual` that a position, lot, or order belongs to. |
| **Ownership note** | The line in an order preview that says whose holdings the order affects: `manual` for UI orders, or only the named strategy's virtual portfolio (UI-015). |
| **Parity report** | The comparison of a recorded paper or live period with its event-driven replay: matched, missed, and extra intents and legs, decision differences, per-leg slippage, and the realized P&L difference split into price, size, and fee effects (BT-070, BT-071). |
| **Partial bar** | A not-yet-final bar published on `md.bar.partial.*` for the UI only; never stored or used for decisions (MD-002). |
| **PBO** | Probability of backtest overfitting (combinatorially symmetric cross-validation). |
| **PDT** | FINRA Pattern Day Trader rule; it limited day trades in margin accounts below $25k and was retired on 2026-06-04. The risk engine keeps an optional per-account `legacy` guard, off by default (RM-008, D30). |
| **Plateau rule** | Stop the analyst loop after K = 3 consecutive continuation rounds whose best deflated Sharpe probability improved by less than ε = 0.05 (AN-040). |
| **Point-in-time (PIT)** | Data as it was knowable at a given moment, including universe membership and corporate actions. |
| **Point-in-time lint** | Static checks on feature source (`python -m tbcore.features.lint`) for constructs that can read the future or outside the frame: negative shifts, centered windows, whole-history aggregates, backward fills, forward as-of joins, I/O and clock access (BT-064). |
| **Pre-market checklist** | Daily report 15 minutes before the open: broker sessions, reconciliation, kill switches, halts, clock, active strategies; fails closed without execution's heartbeat (MN-005). |
| **Pre-registration** | Hypothesis, metric, universe, period, and stop rule fixed before a study starts; its hash is on every trial (D31 rule 4, EX-026). |
| **Price-scale invariance** | Property of a feature that does not change when the price (or volume) series is multiplied by a constant; required so adjustments never change feature values (DF-013). |
| **Promotion** | Audited, human-approved move of a strategy from `paper` to `live`, with step-up auth and a capital cap. |
| **Protective order** | A broker-side stop (bracket/OTO leg, or a separate GTC stop at brokers without brackets) attached to every algorithmic entry so an outage never leaves naked risk (N5, OE-014). |
| **Publication date (short interest)** | The date FINRA publishes short interest for a settlement date, about 7 sessions later; short interest is available from it, never from the settlement date (CX-005). |
| **Publication delay** | Per-feed time between a bar's close and when it counts as available (`available_at` = `ts_close` + delay): Alpaca 60 s, FMP daily 6 h (MD-003). |
| **Purged / embargoed CV** | Time-series cross-validation that removes training samples overlapping test labels (purge) and a gap after the test set (embargo). |
| **Quarantine (symbol-day)** | An instrument's session that failed cross-source reconciliation; excluded from feature snapshots by default, never deleted, resolved by the owner (MD-010). |
| **Quarantined model** | A local, tool-less scoring model that sees only sanitized untrusted text and returns schema-validated numbers; every call is logged (CX-014, ADR-0033). |
| **Quarantined role** | The local, tool-less LLM role that turns untrusted text into enumerated fields; the only path by which notes or news reach the planner (AN-054). |
| **Quiet hours** | 22:00–07:00 New York: warning notifications are muted; critical alerts are never muted (MN-003). |
| **Reconciliation** | Check that broker positions equal the sum of virtual ledgers plus the manual bucket; unexplained differences halt affected strategies. |
| **Reconciliation (cross-source)** | The nightly comparison of Alpaca SIP daily bars with FMP EOD bars within fixed tolerances (MD-010). Distinct from ledger reconciliation. |
| **Reference data mirror** | Parquet copy of a `refdata` table in the lake's `ref/` directory for Python readers (ST-007). |
| **Regime label** | Point-in-time market state (`vol_regime`, `trend_regime`, `stress`) computed only from past SPY bars and VIX data, versioned (`regime_v1`, CX-017). |
| **Repaired bar** | A final bar recovered over REST after a stream outage instead of from the stream; published once, counted in `tb_md_bars_repaired_total` (MD-006). |
| **Research cycle** | A window of research in a lineage with a fixed effective-trial budget from MinBTL; when spent, only the owner opens the next (D31 rule 3, EX-025). |
| **Research deployment registry** | JSON file the optimizer writes for shadow and paper challengers (strategy registry format) and strategy-runtime adds to its seed at start; never hosts live strategies (EX-056). |
| **Research journal** | The analyst's record of every round, LLM call, tool call, code proposal, and conversation in Postgres schema `analyst` (AN-003, AN-055). |
| **Reservation** | Cash (buys) or own quantity (sells) the ledger holds for an intent's open orders, so concurrent intents cannot spend the same capital; shrinks with fills, released on terminal states (VP-020). |
| **Resync** | What a broker adapter does after every (re)connect before accepting orders: list open orders, fetch executions since the last acknowledged one, publish what was missed, request reconciliation (BR-011). |
| **Risk decision** | The recorded outcome of pre-trade checks for an intent: approved or rejected, with each check's value and limit. |
| **Romano–Wolf stepdown** | Bootstrap multiple-testing procedure giving adjusted p-values for each of several challengers against a benchmark (BT-047). |
| **Routing table** | Config mapping strategy horizons (`intraday`, `swing`) to the broker accounts allowed to receive their orders (RM-011, D30). |
| **Rule + ML filter** | A rule-based primary signal (ORB, residual reversal) whose trades are taken only when a meta-label classifier's probability clears a threshold (D23, ML-016). |
| **Rule time** | The vendor or official availability of a context version (publication, release, acceptance, vintage date) plus a configured delay; first-seen versions are stamped with it (CX-004). |
| **Schema owner role** | NOLOGIN Postgres role (`<schema>_owner`) that owns a schema and runs its migrations; services never log in as it. |
| **Scorer (promotion gate)** | The D31 rule 7 test of a challenger (DSR, PBO, SPA, 2× costs, trades, drawdown) in `tb_backtest.scorer`; the analyst only sees its pass/fail and DSR (BT-080). |
| **Sealed holdout** | Final data period that optimization never sees; access is logged and counted. |
| **Self-cross** | Opposite-side orders resting in the same account and instrument across owners; brokers reject them as potential wash trades, so the OMS refuses them and cancels its own before an exit (OE-018). |
| **Service token** | Short-lived Ed25519-signed token identifying the calling service on an internal call (SE-040). |
| **Session state (broker)** | Per-account adapter health: `disconnected`, `connecting`, `authenticated`, `syncing`, `ready`, `degraded`, `stopped`; only `ready` accepts orders (BR-010). |
| **Setup token** | One-time, 30-minute token printed by `make setup-token` that authorizes registering the first owner (SE-007). |
| **Shadow mode** | A model produces signals that are recorded but never sent as orders. |
| **Signal overlay** | A chart marker (and, for models, a score line) drawn from `tb.signals.v1.Signal` messages on `sig.<strategy_id>.<instrument_id>`; information only, never an order (UI-049). |
| **Simcore (`tb-simcore`)** | Go process that runs the production OMS engine, pre-trade gate, and ledger accounting core for the event-driven backtester over a stdio protobuf protocol; it makes every order-path decision while the backtester decides fills (ADR-0031, BT-003). |
| **Snapshot stamping** | Setting a context version's `available_at`: rule time when first seen, fetch time when vendor-restated content changes, official time for authoritative histories (CX-004). |
| **SPA test** | Hansen's test for superior predictive ability: whether any challenger beats the benchmark after accounting for the number of challengers; consistent p-value from the stationary bootstrap (BT-046). |
| **Staging file** | Lake file with newly written rows, visible to readers at once and merged by compaction (ST-004). |
| **Startup recovery** | The state after an execution restart in which no intent is approved until every ready account's open orders were reconciled with the broker (OE-020). |
| **Status strip** | The row under the header showing execution state, account sessions, reconciliation, margin, day trades, halts, clock, runtime heartbeat, open orders, and feed freshness (UI-013). |
| **Step-up auth** | Fresh re-authentication required for dangerous actions. |
| **Strategy registry seed** | JSON file (`infra/compose/strategies.json`) shared by execution and the strategy runtime that registers strategies at startup; it never sets `live` and never changes an existing strategy's mode (SM-001). |
| **Strategy runtime** | The Python service that runs `tbcore.strategy` strategies on final bars and publishes their intents on `ord.intent`; it sees only its strategies' own positions and never talks to brokers (ADR-0026, SM-014). |
| **Syndicated duplicate** | A re-published copy of a news story (same or near-identical headline for the same instrument within 24 h); marked `duplicate_of` and counted once (CX-013). |
| **Timing difference** | A broker vs ledger difference inside the reconciliation grace window (60 s, ≥ 2 snapshots); blocks only that instrument and becomes a break if it persists (VP-013). |
| **Tool server** | The assistant component that executes every analyst read and write: forbidden-name denial, level check against the optimizer, budget check, holdout stripping, sanitization, audit (AN-060). |
| **TradingView webhook strategy (`tv-<name>`)** | A strategy whose signals come from TradingView webhook alerts (runtime class `tradingview`); it has its own virtual ledger, allocation, and limits, and its intents pass the normal gate (SM-020). |
| **Trial ledger** | The append-only record of every trial in a lineage (params, spec and pre-registration hashes, snapshot, commit, seed, source, status, out-of-fold returns) from which deflated statistics are computed (D31 rule 1, EX-020). |
| **Triple-barrier label** | Label set by which of a profit-take, stop-loss, or time barrier is hit first. |
| **VAPID** | Voluntary Application Server Identification (RFC 8292): the ES256-signed JWT and public key a Web Push sender presents to push services (SE-060). |
| **Vintage** | The version of a revised series as published on a given date (ALFRED `realtime_start`); macro reads return the vintage known at `as_of` (DF-010). |
| **Virtual portfolio (sub-portfolio)** | A strategy's own capital allocation, positions, lots, P&L, and limits, carved out of a real broker account. |
| **Walk-forward** | Validation that repeatedly trains on a past window and tests on the following window. |
| **Wash-sale flag** | Informational marker on a loss-realizing sale within 30 days of a purchase of the same instrument by any owner in any account (VP-022). |
| **Web Push** | Browser push notifications through the vendor push services (FCM, Mozilla, Apple, Windows), encrypted end to end with RFC 8291; delivered to the installed PWA from Alertmanager via the gateway (ADR-0052). |
