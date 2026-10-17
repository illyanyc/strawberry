# Data-quality fixtures

`bars_1m_injected.csv`: synthetic 1-minute bars for the 2026-03-06 regular session. `INSTB` is clean;
`INSTA` has one injected defect of each kind (duplicate, 10-minute gap, inconsistent OHLC,
non-positive price, negative volume, 100× volume spike, a bar off the minute grid). `expected.json`
lists what `dq_check` must report. Both implementations (Go `services/marketdata/internal/dq`,
Python `tbcore.dq`) are tested against these files so they cannot drift (rule ST-010).
