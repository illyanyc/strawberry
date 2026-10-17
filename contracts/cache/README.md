# Hot-cache keys (Valkey 9, D6)

Rule ST-009 ([storage.md](../../docs/business-logic/storage.md)); implementation `libs/go/cache` (Go). Values are protobuf binary messages from `contracts/proto`. Times inside values are UTC ns. Staleness is judged from the value's `available_at`, never from whether a key exists.

| Key | Type | Value | TTL | Writer → readers |
|---|---|---|---|---|
| `bar:last:{tf}:{instrument_id}` | string | `tb.marketdata.v1.Bar` (final bar, latest revision) | 7 days | marketdata → execution (freshness), UI, feature engine |
| `bar:ring:{tf}:{instrument_id}` | list, newest first, max 500 | `tb.marketdata.v1.Bar` | 7 days | marketdata → UI charts, online features warm-up |
| `quote:last:{instrument_id}` | string | `tb.marketdata.v1.Quote` | 1 hour | marketdata → UI, execution (spread checks) |
| `feat:{feature_set}:{instrument_id}` | string | `tb.features.v1.FeatureVector` | 24 hours | online feature engine → ml-infer |
| `md:watermark:{tf}` | hash: field = feed, value = ts_open (decimal string) | latest final bar per feed | none | marketdata → monitor, execution |

Rules:
- `{tf}` is one of `1m`, `5m`, `15m`, `30m`, `1h`, `1d`; `{instrument_id}` is the internal ULID, never a ticker.
- `bar:last` never moves backwards in time; a correction of the newest bar replaces the ring head instead of pushing a duplicate.
- Only `marketdata` writes `bar:*`, `quote:*`, `md:*`; only the feature engine writes `feat:*`.
