# Glossary

Domain terms used in code and docs. Add terms in the same commit that introduces them; keep alphabetical.

| Term | Meaning |
|---|---|
| **Account hold (IBKR)** | A per-account reason, such as `margin_deficit`, that keeps an otherwise `ready` IBKR session `degraded` for that account until cleared (IB-041, BR-021). |
| **ADR** | Architecture Decision Record in `docs/adr/`; one decision per file, superseded rather than rewritten. |
| **Allocatable capital** | Account equity minus the owner's reserve (`TB_LEDGER_RESERVE_PCT`, default 10 %, plus `TB_LEDGER_RESERVE_ABS`); the sum of strategy allocations may not exceed it (VP-009). |
| **Attribution ID** | The ID placed in Alpaca `client_order_id` / IBKR `orderRef` that identifies the owning strategy and intent: `tb1-<strategyShortId>-<intentULID>-<attempt>`. |
| **Audit chain** | The hash-linked sequence of audit events: each event's hash covers the previous hash and its own canonical fields; `make audit-verify` recomputes it. |
| **`available_at`** | The earliest time the platform could have known a datum. All point-in-time filtering uses it, never `event_time`. |
| **Break (reconciliation break)** | A recorded difference or ownership violation in an account (`timing`, `encroachment`, `unknown_execution`, `missing_execution`, `corporate_action`, `cash_mismatch`, `orphan_order`, `ghost_order`, `oversell`, `unmapped_instrument`); critical breaks halt the affected strategy×instrument pairs until resolved (VP-008, VP-014). |
| **Broker account (`broker_account_id`)** | One real account at one broker in one environment (paper or live). Paper and live are different accounts. |
| **Broker simulator** | In-process deterministic broker (`broker-gateway/internal/sim`) implementing the adapter interface with a small matching engine and fault hooks; used by tests and the `sim-paper` development account (ADR-0019). |
| **Broker capabilities** | Per-account declaration of what an adapter supports (order types, TIFs, bracket/OCO/OTO, fractional, short, extended hours, attribution length); execution builds orders against it (BR-002). |
| **`BROKER_UNAVAILABLE`** | The typed error (gRPC `FAILED_PRECONDITION` + `ErrorInfo` reason) a broker adapter returns for order RPCs while the account session is not `ready` (BR-004, OE-004). |
| **Broker-lot shadow ledger** | The ledger's model of the broker's own tax lots: one FIFO queue per instrument across all owners, shown next to the owner split so tax-lot divergence (a strategy sale relieving manual's oldest lot at the broker) is visible (VP-021). |
| **Canonical execution ID** | The `broker_exec_id` used to deduplicate fills; identical whether a fill arrives on the stream or from a REST resync (Alpaca: `<order_id>:<cumulative qty>`) (BR-008). |
| **Backfill job** | A resumable marketdata job that fetches historical bars in checkpointed chunks (≤ 100 symbols × a per-timeframe window); a restart skips finished chunks and a rerun writes nothing new (MD-001). |
| **Bar finality** | A live 1-minute bar becomes final 60 s after its close (Alpaca's update window); only final bars reach the bus, cache, lake, and features (MD-002). |
| **Bar revision** | Version number of a bar's natural key: 0 when first stored, incremented on every content change; superseded versions live in `bar_revisions/` (ST-005, DF-007). |
| **Champion / challenger** | The currently deployed model version for a strategy, and a candidate running in shadow or paper alongside it. |
| **Compaction (lake)** | Merging a partition's staging and part files into one sorted part file with one row per natural key (ST-004). |
| **Cold restart (IBC)** | IBC's Sunday-only full gateway restart at `ColdRestartTime` (default 17:00 ET): a fresh login, with an IBKR Mobile 2FA push for live; the platform's weekly IBKR login (IB-014). |
| **Compose profile** | Named group of services in `infra/compose/docker-compose.yml` (`core`, `observability`, `ml`, `ml-workers`, `ops`, `remote-cloudflare`); `make up` starts `core,observability`. |
| **Correction (bar)** | An update to a bar after it was final: stored as a new revision and published on `md.bar.correction.*` (MD-002, DF-007). |
| **Correlation ID** | ID carried by every event from signal to fill, for tracing. |
| **Daily restart (IBC)** | IBC's daily gateway restart at `AUTO_RESTART_TIME` (default 23:45 ET) that reuses the session token, so no 2FA; outages inside its window do not alert (IB-013). |
| **Dead-man check** | Heartbeat watch that contains risk when a component stops: execution latches cancel-open kill switches when the strategy runtime's heartbeat is lost; broker-gateway cancels working platform orders when execution's heartbeat is lost (RM-017). |
| **Decimal (contract type)** | `tb.common.v1.Decimal`: an exact base-10 number carried as a string; the only representation for money and quantity in contracts. |
| **Deflated Sharpe ratio (DSR)** | Sharpe ratio corrected for the number of trials and non-normal returns; used to rank strategies honestly. |
| **Demotion** | Automatic or manual move of a strategy to `paper` or `halted`. Re-promotion is always manual. |
| **`event_time`** | When something happened in the world (trade, bar close, news publication). |
| **Encroachment** | A manual sell (in the broker's app) that takes manual quantity below zero while a strategy holds the instrument, i.e. the owner sold strategy-attributed shares; raises a critical break (VP-016). |
| **Fail closed** | On uncertainty (stale data, disconnect, unknown order state), stop opening risk instead of guessing. |
| **Feature lookback** | Number of bars (current included) a feature's value depends on; finite by rule DF-013, it sizes the online ring buffer and offline warm-up. |
| **Feature set** | Versioned collection of feature definitions in `libs/py/tbcore`; identical in train, backtest, and live. |
| **Flatten (strategy-owned)** | Closing all positions of a strategy (or all strategies). Never includes `manual` quantity. |
| **Dataset snapshot** | Immutable, content-addressed feature extract (`snapshots/<id>/`) whose ID hashes the feature set, universe, range, as-of, and exact input files (DF-009). |
| **Gap (market data)** | Regular-session minutes the calendar says should have bars but the lake does not; runs of ≥ 5 are recorded in `marketdata.gaps` and re-fetched (MD-006). |
| **Generated contracts** | Code under `contracts/gen/` produced by `make gen` from `contracts/proto/`; committed, and CI fails if stale (`make gen-check`). |
| **Halt (OMS)** | A block on new intents for a strategy (optionally one instrument) raised by the OMS for an unknown order state, an orphan order, or a protective stop that could not be placed; cleared when the cause is resolved (OE-005, OE-019, OE-014). |
| **Halted pair** | An `owner|instrument` key (with `*` wildcards) on `recon.result` that execution must block until the break behind it is resolved (VP-008). |
| **IB Gateway** | IBKR's headless trading application exposing the TWS API socket; runs in `ib-gateway-<env>` containers (D18). |
| **IBC** | The open-source tool that logs IB Gateway in and restarts it on schedule; archived 2026-09-01, pinned at 3.24.2 (D18, IB-073). |
| **`ingested_at`** | When the platform stored a datum. |
| **`instrument_id`** | Internal stable instrument identifier, independent of ticker changes; maps to broker IDs (Alpaca asset ID, IBKR `conId`). |
| **Intraday-margin guard** | Pre-trade check capping leverage and margin utilization per account so strategies cannot consume the manual book's buying power (RM-012). |
| **Kill switch** | Scoped emergency control (global, broker account, strategy, instrument): freeze new orders, cancel open orders, optionally flatten strategy-owned quantity. |
| **Kill switch latch** | A kill-switch engagement persisted until an owner releases it with a desktop passkey step-up; engaging needs no step-up (RM-006, RM-007). |
| **Leak canary** | A unique fake secret pushed through a real flow so tests can assert it never appears in logs, responses, audit details, or plaintext storage (SE-012). |
| **Lot** | A quantity of an instrument acquired at one time and price, owned by exactly one owner. |
| **Manual bucket (`manual` owner)** | All holdings and orders not attributed to a strategy, including anything placed outside the platform. Automated logic never reduces it. |
| **Market-data hub (fan-out)** | broker-gateway component that holds the single upstream market-data socket per feed and multiplexes every subscriber over it (BR-015). |
| **Marketable limit (collar)** | A limit order priced a few basis points through the last price, used instead of a market order so fills are bounded (OE-013). |
| **Master client ID (IBKR)** | The API client ID (1) configured as `TWS_MASTER_CLIENT_ID`, which receives updates for every order including those placed in TWS or IBKR Mobile (IB-017). |
| **Meta-labeling** | A secondary model that decides whether to act on (and how to size) a primary model's signal. |
| **Native ML worker** | ML/backtest/optimizer process run directly on macOS under launchd (MPS/MLX available) instead of in a container (D29). |
| **Mode** | `backtest`, `paper`, or `live`; carried by every strategy, order, and fill. |
| **`orderRef`** | Free-text IBKR order field carrying the attribution ID; IBKR's echo of it must match what was sent (IB-001, IB-002, spike V1). |
| **Opening balance** | The first broker snapshot of an account, booked entirely to `manual` with the broker's average cost and cash (VP-015). |
| **Opening-range breakout (ORB)** | The Phase 2 rule-based baseline: the first 15-minute bar's range defines the opening range; the first close above its high opens a long with a stop below the range and a 2R target; flat before the close (ADR-0026). |
| **Outbox (transactional)** | A Postgres table written in the same transaction as the state change it announces; a relay publishes rows to NATS with a dedupe ID, so events are never lost or duplicated by a crash (D2, ADR-0018). |
| **Natural key (bar)** | `(instrument_id, timeframe, ts_open, feed)`: the identity used for idempotent writes and reader deduplication (ST-004). |
| **Order intent** | A strategy's or user's request to trade, before risk checks. Becomes an order only after an approved risk decision. |
| **Owner** | The strategy ID or `manual` that a position, lot, or order belongs to. |
| **Partial bar** | A not-yet-final bar published on `md.bar.partial.*` for the UI only; never stored or used for decisions (MD-002). |
| **PBO** | Probability of backtest overfitting (combinatorially symmetric cross-validation). |
| **PDT** | FINRA Pattern Day Trader rule; it limited day trades in margin accounts below $25k and was retired on 2026-06-04. The risk engine keeps an optional per-account `legacy` guard, off by default (RM-008, D30). |
| **Backward adjustment (as of t)** | Multiplying raw prices before each corporate action's ex-date by the action's factor, using only actions known and effective by t (DF-005). |
| **`permId`** | IBKR's permanent order ID; the durable `broker_order_id` for IBKR orders (IB-003). |
| **Point-in-time (PIT)** | Data as it was knowable at a given moment, including universe membership and corporate actions. |
| **Protective order** | A broker-side stop (bracket/OTO leg, or a separate GTC stop at brokers without brackets) attached to every algorithmic entry so an outage never leaves naked risk (N5, OE-014). |
| **Publication delay** | Per-feed time between a bar's close and when it counts as available (`available_at` = `ts_close` + delay): Alpaca 60 s, FMP daily 6 h (MD-003). |
| **Promotion** | Audited, human-approved move of a strategy from `paper` to `live`, with step-up auth and a capital cap. |
| **Price-scale invariance** | Property of a feature that does not change when the price (or volume) series is multiplied by a constant; required so adjustments never change feature values (DF-013). |
| **Purged / embargoed CV** | Time-series cross-validation that removes training samples overlapping test labels (purge) and a gap after the test set (embargo). |
| **Quarantine (symbol-day)** | An instrument's session that failed cross-source reconciliation; excluded from feature snapshots by default, never deleted, resolved by the owner (MD-010). |
| **Reconciliation** | Check that broker positions equal the sum of virtual ledgers plus the manual bucket; unexplained differences halt affected strategies. |
| **Reconciliation (cross-source)** | The nightly comparison of Alpaca SIP daily bars with FMP EOD bars within fixed tolerances (MD-010). Distinct from ledger reconciliation. |
| **Reservation** | Cash (buys) or own quantity (sells) the ledger holds for an intent's open orders, so concurrent intents cannot spend the same capital; shrinks with fills, released on terminal states (VP-020). |
| **Risk decision** | The recorded outcome of pre-trade checks for an intent: approved or rejected, with each check's value and limit. |
| **Schema owner role** | NOLOGIN Postgres role (`<schema>_owner`) that owns a schema and runs its migrations; services never log in as it. |
| **Repaired bar** | A final bar recovered over REST after a stream outage instead of from the stream; published once, counted in `tb_md_bars_repaired_total` (MD-006). |
| **Routing table** | Config mapping strategy horizons (`intraday`, `swing`) to the broker accounts allowed to receive their orders (RM-011, D30). |
| **Resync** | What a broker adapter does after every (re)connect before accepting orders: list open orders, fetch executions since the last acknowledged one, publish what was missed, request reconciliation (BR-011). |
| **Reference data mirror** | Parquet copy of a `refdata` table in the lake's `ref/` directory for Python readers (ST-007). |
| **Sealed holdout** | Final data period that optimization never sees; access is logged and counted. |
| **Self-cross** | Opposite-side orders resting in the same account and instrument across owners; brokers reject them as potential wash trades, so the OMS refuses them and cancels its own before an exit (OE-018). |
| **Service token** | Short-lived Ed25519-signed token identifying the calling service on an internal call (SE-040). |
| **Setup token** | One-time, 30-minute token printed by `make setup-token` that authorizes registering the first owner (SE-007). |
| **Session state (broker)** | Per-account adapter health: `disconnected`, `connecting`, `authenticated`, `syncing`, `ready`, `degraded`, `stopped`; only `ready` accepts orders (BR-010). |
| **Staging file** | Lake file with newly written rows, visible to readers at once and merged by compaction (ST-004). |
| **Shadow mode** | A model produces signals that are recorded but never sent as orders. |
| **Startup recovery** | The state after an execution restart in which no intent is approved until every ready account's open orders were reconciled with the broker (OE-020). |
| **Step-up auth** | Fresh re-authentication required for dangerous actions. |
| **Strategy registry seed** | JSON file (`infra/compose/strategies.json`) shared by execution and the strategy runtime that registers strategies at startup; it never sets `live` and never changes an existing strategy's mode (SM-001). |
| **Strategy runtime** | The Python service that runs `tbcore.strategy` strategies on final bars and publishes their intents on `ord.intent`; it sees only its strategies' own positions and never talks to brokers (ADR-0026, SM-014). |
| **Timing difference** | A broker vs ledger difference inside the reconciliation grace window (60 s, ≥ 2 snapshots); blocks only that instrument and becomes a break if it persists (VP-013). |
| **Triple-barrier label** | Label set by which of a profit-take, stop-loss, or time barrier is hit first. |
| **Virtual portfolio (sub-portfolio)** | A strategy's own capital allocation, positions, lots, P&L, and limits, carved out of a real broker account. |
| **Walk-forward** | Validation that repeatedly trains on a past window and tests on the following window. |
| **Wash-sale flag** | Informational marker on a loss-realizing sale within 30 days of a purchase of the same instrument by any owner in any account (VP-022). |
