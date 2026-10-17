---
title: Research 02 – Labeling, features, validation, and backtest realism
covers: research prompt §3, §4; deliverable 3
updated: 2026-10-17
---

# 02 · Labeling, features, validation, and backtest realism

[← Report index](../research-report.md)

**Bottom line.** Use volatility-scaled, residualized forward returns as the default target, with per-date (cross-sectional) ranking for swing trading. Test triple-barrier labels and meta-labeling as alternatives; don't make them the default. Validate with purged and embargoed walk-forward plus CPCV. Rank candidates by **deflated Sharpe over a full trial ledger**, report PBO, and keep a sealed holdout plus forward paper trading. The validation scheme matters less than how many trials you run, and cost realism matters more than either.

---

## 1. Labels and targets

### 1.1 What the evidence says
- **Triple barrier versus fixed horizon is mixed.**
  - Triple barrier gave the best buy-signal F1, but fixed horizon gave the best accuracy ([Li et al. 2022](https://doi.org/10.1109/icsess54813.2022.9930328)).
  - Triple barrier helped on 15m/30m bars and **hurt on 5m** ([arXiv 2404.01866](https://doi.org/10.48550/arxiv.2404.01866)).
  - A trend-scanning advocate reported −0.29 / −0.03 / 0.00 Sharpe for fixed horizon / triple barrier / trend scanning ([Applied Sciences 2025](https://doi.org/10.3390/app152413204)), with author bias.
- **Ranking labels** suit cross-sectional swing books. Numerai scores per era ([data docs](https://docs.numer.ai/numerai-tournament/data)), and LambdaMART roughly tripled momentum Sharpe ([Poh et al. 2021](https://doi.org/10.3905/jfds.2021.1.060)).
- **Residualize targets** against market and industry. Factor-adjusted reversal more than doubles risk-adjusted performance ([Blitz et al. 2024](https://doi.org/10.3905/jpm.2024.1.588)).
- **Auxiliary multi-horizon targets** helped the Jane Street 2025 GRU solutions ([8th place](https://www.kaggle.com/competitions/jane-street-real-time-market-data-forecasting/writeups/evgeniia-grigoreva-private-lb-8th-solution)). **Subtracting the cross-sectional mean prediction** helped Optiver 1st place ([writeup](https://www.kaggle.com/competitions/optiver-trading-at-the-close/writeups/hyd-1st-place-solution)).

### 1.2 Recommended defaults (this changes D24; see [09](09-decisions.md#d24))

| Horizon | Default target | Task | Alternatives tested by the optimizer |
|---|---|---|---|
| Intraday (15m decision, 5m finest) | Forward return over *h* bars (h ∈ {1, 2, 4} decision bars, capped at the close), **minus the SPY/sector-ETF return**, divided by trailing realized volatility (computed out-of-fold only) | Regression, with multi-horizon auxiliary heads for neural models | Triple barrier with volatility-scaled barriers; meta-label on ORB / momentum rules |
| Swing (daily) | 1/3/5/10-day forward **residual** return (market + industry), volatility-scaled | **Per-date rank** (LambdaRank) or per-date regression; score with per-date IC mean / std | Triple barrier; meta-label on residual-reversal or breakout rule |

Rules:
- Barrier widths and volatility scalers use only data known at `decision_time`.
- Overlapping labels set the **purge/embargo length** (at least the label horizon).
- Keep **overnight and intraday return legs** as separate label columns, so a flat-at-close book isn't benchmarked against buy-and-hold total return.

---

## 2. Features

### 2.1 Feature families, by evidence

| Family | Examples | Evidence | Horizon |
|---|---|---|---|
| Momentum / reversal (residualized) | 1/5/20/60-day residual returns; intraday return since open; first-half-hour return | [Gu-Kelly-Xiu 2020](https://doi.org/10.1093/rfs/hhaa009); [Gao et al. 2018](https://doi.org/10.1016/j.jfineco.2018.05.009); [Blitz 2024](https://doi.org/10.3905/jpm.2024.1.588) | Both |
| Liquidity / volume | Relative volume versus same time-of-day average; dollar volume; Amihud | [Zarattini et al. 2024](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4729284) (relative volume *is* the ORB edge) | Both |
| Volatility | Realized volatility over several windows; Parkinson/Garman-Klass; vol-of-vol; fine-tuned TSFM or HAR forecast | [Goel et al. 2025](https://arxiv.org/html/2505.11163) | Both |
| Time-of-day / seasonality | Minutes since open; same-half-hour lag returns; day-of-week; pre-holiday | [Heston et al. 2010](https://doi.org/10.1111/j.1540-6261.2010.01573.x) | Intraday |
| Microstructure (bar-level) | VWAP distance; estimated spread from OHLC; close-location value | [Ardia, Guidotti, Kroencke, JFE 2024](https://doi.org/10.1016/j.jfineco.2024.103916) (OHLC spread estimator) | Intraday |
| Order-flow imbalance | OFI from quotes | Explains about 65% of *contemporaneous* moves ([Cont, Kukanov, Stoikov 2014](https://doi.org/10.1093/jjfinec/nbt003)); near-zero *predictive* R² at bar horizons **[Unvetted]**. Needs quote data (SIP) | Optional |
| Cross-sectional ranks | Per-date percentile ranks of all of the above | Numerai / GKX practice | Both |
| Market / sector context | SPY/QQQ/IWM returns; sector-ETF residual; breadth; VIX level and slope; HMM regime probability | [Nystrup et al. 2017](https://doi.org/10.1080/14697688.2017.1342857) | Both |
| Options-implied | IV skew; call-put IV spread; IV rank | Skew: −10.9%/yr for steep smirks ([Xing, Zhang, Zhao 2010](https://doi.org/10.1017/s0022109010000220)); IV spread decayed to 2–9 bps/wk ([Cremers & Weinbaum 2010](https://doi.org/10.1017/s002210901000013x)) | Swing; costs money (see [04](04-brokers-data-ingestion.md#data-plan)) |
| Short interest | Days-to-cover; SI change | 1.19%/mo long-short ([Hong et al. 2015](https://www.nber.org/papers/w21166)); **use the FINRA publication date**, not the settlement date | Swing; free ([FINRA](https://www.finra.org/finra-data/browse-catalog/equity-short-interest)) |
| Earnings / estimates | Days to/from earnings; SUE as context; analyst revisions | Drift is gone ([Martineau 2022](https://doi.org/10.1561/104.00000122)); useful for gap and volatility context and for event exclusion | Both; from FMP |
| Macro events | Event-day flags (CPI, FOMC, NFP); surprise = actual − consensus | Event days carry stronger intraday momentum ([Gao et al. 2018](https://doi.org/10.1016/j.jfineco.2018.05.009)) | Both; FMP economic calendar plus ALFRED vintages |
| News sentiment | Headline score from a local quarantined LLM | Decaying ([Lopez-Lira & Tang 2026](https://doi.org/10.1016/j.jfineco.2026.104335)); look-ahead risk before the model's training cutoff ([Profit Mirage](https://arxiv.org/html/2510.07920)) | Swing / event days, Phase 4 |
| Fractional differentiation | FFD series of log price | Weak supportive evidence ([Walasek & Gajda 2021](https://link.springer.com/article/10.1007/s12572-021-00299-5)). **Choosing *d* on the full sample leaks future information** | Optional, select *d* in-fold |

**Affordable alternative data for a solo trader:** FINRA short interest and Reg SHO volume (free), FRED/ALFRED (free), SEC EDGAR (free), and FMP (existing plan) for earnings, estimates, grades, economic calendar, insider and 13F data. Options-derived signals are the main paid add-on (ThetaData from $40/mo; ORATS $199+). Defer them until the core is proven.

### 2.2 Point-in-time feature store

Required by N2 and confirmed by the evidence:
- **Bitemporal timestamps** on every datum (`event_time`, `available_at`, `ingested_at`), with **as-of joins only**.
- For vendors that revise in place, `available_at` comes from the vendor field where one exists: FMP `acceptedDate`/`filingDate` for fundamentals, the FINRA publication date for short interest, and the ALFRED vintage for macro. Otherwise use our own first-seen `ingested_at`. **FMP fundamentals are restated in place and have no `as_of` parameter** ([FMP education](https://site.financialmodelingprep.com/education/analyst/reproducibility-in-financial-research-why-data-versioning-determines-institutional-credibility)), so we must **snapshot every fetch**.
- **Survivorship:** missing delisting returns average about −30% and inflate small-stock returns by up to 5.2 pp/yr ([Shumway 1997](https://doi.org/10.1111/j.1540-6261.1997.tb03818.x)). Universes must include delisted tickers with point-in-time membership.
- **One feature library** (`libs/py/tbcore`) computes features for training, backtest, and live, from the same code path. Feature definitions are versioned and hashed.
- **Online/offline parity test:** recompute yesterday's live feature vectors offline and assert equality within tolerance, nightly.

---

## 3. Validation and overfitting control

### 3.1 Why it matters
- Across 888 Quantopian algorithms, backtest Sharpe explained **less than 2.5%** of the variance in out-of-sample Sharpe. More backtesting produced bigger shortfalls ([Wiecki et al. 2016](https://doi.org/10.3905/joi.2016.25.3.069)).
- With 5 years of data, about **45 independent trials** are enough to produce an in-sample Sharpe of 1 from noise alone (MinBTL; [Bailey et al., Notices AMS 2014](https://doi.org/10.1090/noti1105)).

### 3.2 Splitters
- **Purged + embargoed walk-forward** (expanding or rolling) is the realistic, deployment-shaped test.
- **CPCV** gave lower PBO and better DSR than walk-forward, but only in a *synthetic* study ([Arian et al., KBS 2024](https://doi.org/10.1016/j.knosys.2024.112477)). A practitioner runbook found both recover true Sharpe within about 0.1, versus a 0.3–0.5 overstatement from in-sample selection ([OpenQuant](https://open-quant.github.io/openquant/runbooks/cpcv-deflated-sharpe/)) **[Unvetted]**.
- **Recommendation:** inner **CPCV for model selection**, outer **walk-forward for realism**. Set the embargo to at least the label horizon. Swing books use per-date ("era") observations, following Numerai practice: purge 8 eras for 20-day targets ([Numerai](https://docs.numer.ai/community/office-hours-with-arbitrage)).

### 3.3 Statistics
| Statistic | Use | Source |
|---|---|---|
| **Deflated Sharpe Ratio (DSR)** | Primary ranking metric; inputs are the *effective* trial count and the cross-trial Sharpe variance; pass at DSR ≥ 0.95 | [Bailey & López de Prado 2014](https://doi.org/10.3905/jpm.2014.40.5.094) |
| **Effective N via ONC clustering** | Turns raw trial counts into independent-trial counts | [López de Prado 2019](https://doi.org/10.3905/jfds.2019.1.099); [López de Prado & Lewis 2019](https://doi.org/10.1080/14697688.2019.1622311) |
| **PBO via CSCV** (S = 16 blocks) | Per study; PBO near 0.5 means selection is a coin flip; gate at ≤ 0.2 | [Bailey et al. 2017](https://doi.org/10.21314/JCF.2016.322) |
| **Hansen SPA / Romano–Wolf** (block bootstrap) | Challenger versus champion promotion | [Hansen 2005](https://doi.org/10.1198/073500105000000063); [Romano & Wolf 2005](https://doi.org/10.1111/j.1468-0262.2005.00615.x) |
| **t > 3 hurdle** | New factors/features | [Harvey, Liu, Zhu 2016](https://doi.org/10.1093/rfs/hhv059) |
| **Benjamini–Hochberg FDR** | Feature screening | [BH 1995](https://doi.org/10.1111/j.2517-6161.1995.tb02031.x) |
| **MinTRL** | Paper and live track-record length before promotion or scaling | [Bailey & López de Prado](https://www.davidhbailey.com/dhbpapers/sharpe-frontier.pdf) |

### 3.4 Metrics to rank strategies
Rank by **DSR** (net of costs), gated by:
- PBO ≤ 0.2
- Max drawdown within budget
- Turnover-adjusted return (return ÷ annual turnover)
- **Cost-stress survival:** still positive at 2× modeled costs
- Capacity, estimated as the notional at which modeled impact eats half the edge

Report alongside: Sortino, Calmar, profit factor, hit rate × payoff, per-date IC mean/std (swing), and exposure. Volatility and drawdown predicted live behavior better than Sharpe did ([Wiecki et al. 2016](https://doi.org/10.3905/joi.2016.25.3.069)).

---

## 4. Backtest realism

| Effect | Model | Notes |
|---|---|---|
| Fill timing | Decision at bar close plus publication delay; fill at **next bar open** (or next-bar VWAP for patient orders) | N2 rule; never at the signal bar |
| Spread | Half-spread per side, from quotes (SIP) or the OHLC estimator ([Ardia et al. 2024](https://doi.org/10.1016/j.jfineco.2024.103916)); **opening/closing 30 minutes get a separate, wider model** | Opening spreads drive ORB costs |
| Impact | Square-root term, k·σ·√(q/ADV) ([Frazzini et al. 2018](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3229719)) | Small at this size, but keeps capacity honest |
| Commissions / fees | Alpaca $0 + SEC/TAF fees; IBKR tiered or fixed | Config per broker |
| Partial fills / limits | Limit fills only if price trades *through* the limit; queue-position haircut | Conservative |
| Shorting | ETB/HTB from broker borrow status; borrow fee; locate failures | Alpaca HTB requires locates ([docs](https://docs.alpaca.markets/docs/margin-and-short-selling)) |
| Halts / LULD | No fills during halts; gap through stop | Corporate action and halt calendars |
| Corporate actions | Raw bars plus an action table, adjusted as-of decision date (D27) | FMP intraday is split-adjusted only, so store an `adjustment_basis` flag |
| Survivorship | Delisted symbols included; point-in-time universe | See [04](04-brokers-data-ingestion.md#data-plan) for sources |
| Feed parity | **Train and serve on the same feed** (SIP vs IEX) | IEX is about 3–4% of consolidated volume ([IEX 2026](https://www.iex.io/news)); mixing feeds corrupts volume and VWAP features |

**Backtest↔live parity gate (Gate 3):** the event-driven engine replays the recorded paper-trading bus stream into the same strategy code. Trades must match and P&L differences must be explained by recorded slippage.

### 4.1 Engine comparison (this decides D7)

| Engine | Vectorized | Event-driven, live parity | Alpaca adapter | IBKR adapter | License / cost | Verdict |
|---|---|---|---|---|---|---|
| **NautilusTrader** 1.231 (2.0 in RC) | No | Yes (Rust core) | **None** (RFC open, "not in the near term") ([#3374](https://github.com/nautechsystems/nautilus_trader/issues/3374)) | Yes (Rust) | LGPL-3.0 | Excellent engine, but it would own the OMS (duplicating the Go OMS), has no Alpaca adapter, and is mid-2.0 migration |
| **LEAN** | No | Yes | Yes | Yes | Apache-2.0; **CLI needs a paid tier** ([docs](https://www.quantconnect.com/docs/v2/lean-cli/key-concepts/getting-started)) | C#-centric; paid tooling |
| **VectorBT PRO** | Yes, fast | No | — | — | About $240/yr, proprietary ([pricing](https://vectorbt.pro/become-a-member/)) | Optional accelerator |
| vectorbt OSS | Yes | No | — | — | Apache-2.0 + Commons Clause; development moved to PRO | Avoid for new work |
| backtrader | No | Partial | Community | Community | Inactive since 2023 | Avoid |
| zipline-reloaded | No | No live | — | — | Low cadence | Avoid |
| **Custom (recommended)** | Polars/NumPy (+numba) | Python replay of recorded `md.bar.*` bus messages through the **same `tbcore` strategy API** the live runtime uses | via Go broker-gateway | via broker-gateway | Ours | **Chosen**: one strategy API, one cost model, no second OMS |

**D7 resolution:** two tiers, both custom on `tbcore`. Keep NautilusTrader as a *reference* (and possible future IBKR-only research engine), not a dependency. See [09 · D7](09-decisions.md#d7).
