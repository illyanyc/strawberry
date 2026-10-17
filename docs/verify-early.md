# Verify-early spikes (V1–V9)

The master prompt's Phase 1 spikes: facts the research could not confirm from a primary source. Each row records the method, the result so far, and what the build does until the result is final. A result that changes a decision gets an ADR. Spikes that need the owner's credentials have their tooling in the repo so they run as soon as keys exist; until then the platform takes the conservative default.

| ID | Unknown | Owner (section) | Status | Result and evidence | Default until resolved |
|---|---|---|---|---|---|
| V1 | IBKR `orderRef` max length and persistence across executions, open orders, Gateway restarts | IBKR track (04) | Owned by the IBKR adapter branch | — | Always store `permId` as the durable key (BR, IBKR section) |
| V2 | Whether Alpaca historical bars include delisted symbols | marketdata (05) | Pending (section 05 tooling) | — | Backtests stay labeled "survivorship status: incomplete" |
| V3 | Owner's FMP key: legacy `/api/v3` vs `/stable`, plan tier, limits, intraday entitlement | marketdata (05) | Pending (section 05 tooling) | — | Build against `/stable` only |
| V4 | Atomic rename semantics across VirtioFS for the lake on host APFS | storage (03) | Verified on Linux; Mac run pending (owner) | Harness `services/marketdata/internal/spikes` (writer alternates rename-over-same-name and new-name-then-delete; readers fully parse every visible file). Linux ext4, 2 s, 1 writer + 4 readers: **175 writes, 1,311 full reads, 0 partial reads**, 1 file vanished between listing and open (expected for the delete pattern). Mac: `make spike-v4` runs the writer inside the marketdata container on the bind-mounted lake and a native reader (`scripts/spike_v4_reader.py`) at the same time. | Readers already tolerate a vanished file and deduplicate across the compaction overlap (ST-002, ST-006); if VirtioFS shows partial reads, move the lake to a named volume and export snapshots for native readers (ADR change) |
| V5 | Mac Mini RAM | infra (01, 12) | Not in this scope | — | Colima VM sized per ADR-0013 |
| V6 | FMP earnings BMO/AMC timing and real-time WebSocket entitlement | context data (09) | Not in this scope (Phase 3) | — | Not depended on |
| V7 | Whether a TradingView broker-panel connection occupies Alpaca's single market-data WebSocket slot | marketdata (05), UI (14) | Detection built (BR-015); owner test pending | The gateway's single data socket reports `FEED_STATE_CONNECTION_LIMIT` on Alpaca's 406 and alerts; `TestBR015ConnectionLimit` proves detection against the fake. | If it collides: separate Alpaca key for the panel |
| V8 | IBKR per-order tax-lot selection and self-trade prevention across linked accounts | ledger (06) | Not in this scope (Phase 2) | — | — |
| V9 | Price and coverage of a one-off survivorship-free intraday history | marketdata (05) | Pending (section 05) | — | O3 |
