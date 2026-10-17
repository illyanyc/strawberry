# Glossary

Domain terms used in code and docs. Add terms in the same commit that introduces them; keep alphabetical.

| Term | Meaning |
|---|---|
| **ADR** | Architecture Decision Record in `docs/adr/`; one decision per file, superseded rather than rewritten. |
| **Attribution ID** | The ID placed in Alpaca `client_order_id` / IBKR `orderRef` that identifies the owning strategy and intent: `tb1-<strategyShortId>-<intentULID>-<attempt>`. |
| **Audit chain** | The hash-linked sequence of audit events: each event's hash covers the previous hash and its own canonical fields; `make audit-verify` recomputes it. |
| **`available_at`** | The earliest time the platform could have known a datum. All point-in-time filtering uses it, never `event_time`. |
| **Broker account (`broker_account_id`)** | One real account at one broker in one environment (paper or live). Paper and live are different accounts. |
| **Champion / challenger** | The currently deployed model version for a strategy, and a candidate running in shadow or paper alongside it. |
| **Correlation ID** | ID carried by every event from signal to fill, for tracing. |
| **Decimal (contract type)** | `tb.common.v1.Decimal`: an exact base-10 number carried as a string; the only representation for money and quantity in contracts. |
| **Deflated Sharpe ratio (DSR)** | Sharpe ratio corrected for the number of trials and non-normal returns; used to rank strategies honestly. |
| **Demotion** | Automatic or manual move of a strategy to `paper` or `halted`. Re-promotion is always manual. |
| **`event_time`** | When something happened in the world (trade, bar close, news publication). |
| **Fail closed** | On uncertainty (stale data, disconnect, unknown order state), stop opening risk instead of guessing. |
| **Feature set** | Versioned collection of feature definitions in `libs/py/tbcore`; identical in train, backtest, and live. |
| **Flatten (strategy-owned)** | Closing all positions of a strategy (or all strategies). Never includes `manual` quantity. |
| **Generated contracts** | Code under `contracts/gen/` produced by `make gen` from `contracts/proto/`; committed, and CI fails if stale (`make gen-check`). |
| **`ingested_at`** | When the platform stored a datum. |
| **`instrument_id`** | Internal stable instrument identifier, independent of ticker changes; maps to broker IDs (Alpaca asset ID, IBKR `conId`). |
| **Kill switch** | Scoped emergency control (global, broker account, strategy, instrument): freeze new orders, cancel open orders, optionally flatten strategy-owned quantity. |
| **Lot** | A quantity of an instrument acquired at one time and price, owned by exactly one owner. |
| **Manual bucket (`manual` owner)** | All holdings and orders not attributed to a strategy, including anything placed outside the platform. Automated logic never reduces it. |
| **Meta-labeling** | A secondary model that decides whether to act on (and how to size) a primary model's signal. |
| **Mode** | `backtest`, `paper`, or `live`; carried by every strategy, order, and fill. |
| **Order intent** | A strategy's or user's request to trade, before risk checks. Becomes an order only after an approved risk decision. |
| **Owner** | The strategy ID or `manual` that a position, lot, or order belongs to. |
| **PBO** | Probability of backtest overfitting (combinatorially symmetric cross-validation). |
| **PDT** | FINRA Pattern Day Trader rule; limits day trades in margin accounts below the equity threshold. |
| **Point-in-time (PIT)** | Data as it was knowable at a given moment, including universe membership and corporate actions. |
| **Promotion** | Audited, human-approved move of a strategy from `paper` to `live`, with step-up auth and a capital cap. |
| **Purged / embargoed CV** | Time-series cross-validation that removes training samples overlapping test labels (purge) and a gap after the test set (embargo). |
| **Reconciliation** | Check that broker positions equal the sum of virtual ledgers plus the manual bucket; unexplained differences halt affected strategies. |
| **Risk decision** | The recorded outcome of pre-trade checks for an intent: approved or rejected, with each check's value and limit. |
| **Schema owner role** | NOLOGIN Postgres role (`<schema>_owner`) that owns a schema and runs its migrations; services never log in as it. |
| **Sealed holdout** | Final data period that optimization never sees; access is logged and counted. |
| **Service token** | Short-lived Ed25519-signed token identifying the calling service on an internal call (SE-040). |
| **Shadow mode** | A model produces signals that are recorded but never sent as orders. |
| **Step-up auth** | Fresh re-authentication required for dangerous actions. |
| **Triple-barrier label** | Label set by which of a profit-take, stop-loss, or time barrier is hit first. |
| **Virtual portfolio (sub-portfolio)** | A strategy's own capital allocation, positions, lots, P&L, and limits, carved out of a real broker account. |
| **Walk-forward** | Validation that repeatedly trains on a past window and tests on the following window. |
