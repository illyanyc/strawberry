# ADR-0051: Signals keyed by strategy and instrument on the bus, served to charts as overlays

- **Status:** Accepted
- **Date:** 2026-10-18
- **Decision register:** D69 (refines the master prompt's `sig.<strategy_id>` subject; applies D12, D14)
- **Related:** UI-049, UI-012, SM-020, ML-042, [ADR-0028](0028-ui-trading-slice-charts-and-bff.md), [ADR-0052](0052-tradingview-webhook-strategies-and-chart-embedding.md), section 14 prompt (Charts: "overlays of model signals, strategy orders, fills, stops/targets"), research 07 §5

## Context

Section 14 asks the Charts page to overlay model signals next to fills, orders, and stops, now that Phase 4 models exist, and Gate 5's TradingView item produces signals for `tv-<name>` strategies. The master prompt names the subject `sig.<strategy_id>`, stored in the JetStream `SIGNALS` stream (30 days). A chart shows one instrument: with one subject per strategy, finding an instrument's signals means reading and decoding every signal of every strategy since the chart's first bar (for a 300-name universe on 15-minute bars, over 100k messages per strategy for a 500-bar chart).

## Decision

- **Subject `sig.<strategy_id>.<instrument_id>`.** Every producer (strategy runtime model strategies, the gateway's TradingView intake) publishes signals on it with `Nats-Msg-Id` = signal ID. Consumers of `sig.>` (the stream itself, execution's permission, section 13's recorder) are unaffected; a strategy's signals are `sig.<strategy_id>.>`. The proto comment in `tb.signals.v1` states the subject.
- **History: `GET /api/v1/signals?instrument_id=&since=&limit=`.** The gateway creates a short-lived JetStream ordered consumer on `SIGNALS` filtered to `sig.*.<instrument_id>` from `since` (default 24 h, at most 30 days), reads at most 20,000 messages, and returns the newest `limit` (≤ 2,000) oldest first, as protojson. The gateway's NATS user may create consumers on the `SIGNALS` stream only.
- **Live: a `signal` event on the chart SSE stream** (`/api/v1/stream/market`), from a core subscription on `sig.*.<instrument_id>`, under the same back-pressure rule as bars and fills (UI-012: `resync` when the client falls behind).
- **Drawing.** Signals are markers labelled in text with the strategy, direction, and score (circle = long, square = flat/exit, arrow down = short), never conveyed by color alone; model scores (signals with a model version) are plotted per strategy in a score pane. The chart filters overlays by owner (all, `manual`, or one strategy). Signals are information: no UI action uses them.
- **Producers.** The TradingView intake publishes on this subject (ADR-0052). Model strategies' online scorings are published by the strategy runtime (section 13's ML-042 work); its publisher uses this subject.

## Alternatives considered

| Option | Why not |
|---|---|
| Keep `sig.<strategy_id>` and scan in the gateway | Decodes the whole stream window per chart load; latency grows with the universe |
| An in-memory per-instrument index in the gateway | Memory proportional to universe × history in a 256 MB container, and it is empty after a restart |
| A signals table in Postgres | A second store of the same messages with its own retention; the stream already keeps 30 days |
| Overlay out-of-fold research predictions from the ml service | Research decisions, not what the running strategy saw; dates rarely overlap the 500-bar chart cache |

## Consequences

- Positive: chart overlays are a filtered read of the stream; TradingView signals appear on charts with no extra work; per-strategy consumers stay a single wildcard.
- Negative / risks: a producer that still publishes on `sig.<strategy_id>` is invisible to chart history (live overlays use the instrument filter too); the subject is documented in the proto comment and here so producers stay aligned.
- Follow-up: if a producer cannot know the instrument (portfolio-level signals), it publishes one signal per instrument.

## Validation

`TestUI049SignalsEndpoint` (instrument and time filtering), `TestSE064TradingViewWebhookRejectionPaths` (subject `sig.tv-<name>.<instrument>`), the runtime's `test_sm020_*` tests (it consumes `sig.<tv-strategy>.>`), and `phase5.test.tsx` (marker labels, de-duplication, score series). Revisit if the 20,000-message read cap is reached in practice.
