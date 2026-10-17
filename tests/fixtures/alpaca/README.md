# Alpaca fixtures

Hand-built from the response shapes in Alpaca's public API reference (docs.alpaca.markets: market
data v2 bars, corporate actions v1, trading `trade_updates`, account activities). They contain no
account data and no credentials. Prices are illustrative.

Replace them with real recordings once the owner adds paper keys: run the opt-in tests with
`TB_ALPACA_PAPER_KEY_ID` / `TB_ALPACA_PAPER_SECRET` and `TB_RECORD_FIXTURES=1`
(`go test ./internal/alpaca/ -run Record`), which writes scrubbed responses here.

| File | Shape |
|---|---|
| `bars_1min.json` | `GET /v2/stocks/bars` page with two symbols, decimal prices, `next_page_token` |
| `corporate_actions.json` | `GET /v1/corporate-actions` with a forward split, a reverse split, a cash dividend, a name change |
| `data_stream.json` | Market-data stream messages: `b` (bar), `u` (updated bar), `t` (trade), `q` (quote) |
