# Context-data fixtures (section 09)

Recorded or documented-shape vendor responses used by `services/context-data/tests` so CI runs
without keys. Real-API tests are opt-in (`TB_CTX_LIVE_TEST=1`, see `services/context-data/AGENTS.md`).
No credentials, account numbers, or P&L appear here.

| Path | Source | Provenance |
|---|---|---|
| `finra/consolidated_short_interest_*.json` | FINRA Query API `otcMarket/consolidatedShortInterest` (no key) | **Recorded** 2026-10-18 (AAPL, MSFT, NVDA; public data) |
| `edgar/submissions_CIK0000320193.json` | SEC `data.sec.gov/submissions` (AAPL) | **Recorded** 2026-10-18, trimmed to 12 recent filings; note the `acceptanceDateTime` offset (CX-009) |
| `edgar/company_tickers.json` | SEC `company_tickers.json` | **Recorded** 2026-10-18, trimmed |
| `alfred/series_observations_*.json` | FRED/ALFRED `series/observations` (`output_type=1`) | Documented shape. VIXCLS values and PAYEMS latest-vintage values are real (ALFRED download); earlier PAYEMS revision values are illustrative |
| `fmp/*.json` | FMP `/stable` earnings calendar, economic calendar, statements | Documented shape; values illustrative (no key available). `earnings_calendar_report_times.json` assumes the `time` field that spike V6 checks |
| `llm/ollama_chat_*.json` | Ollama `/api/chat` (local quarantined model) | Hand-written: one valid, one free-text prompt-injection echo, one out-of-range/extra-key reply |

Alpaca news lives in `tests/fixtures/alpaca/news.json` (shared with the Go adapter tests).
