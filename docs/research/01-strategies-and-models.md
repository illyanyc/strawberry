---
title: Research 01 – Strategy horizons and model ranking
covers: research prompt §1, §2; deliverable 2
updated: 2026-10-17
---

# 01 · Strategy horizons, timeframes, and model ranking

[← Report index](../research-report.md)

**Bottom line.** Published evidence supports gradient-boosted trees on engineered, residualized, cross-sectional features as the first model family for both horizons. Online or rolling retraining delivered more than architecture changes in the strongest public financial competitions. Deep sequence models are a second-tier add-on. Time-series foundation models are useful as *volatility features*, not return predictors. Deep RL and autonomous LLM trading agents have negative independent evidence. On the strategy side, intraday edges are small relative to costs, so the universe has to be restricted to the tightest-spread names or to "in play" event days. Swing trading with residualized reversal and momentum features is the more forgiving horizon for this capital base.

Evidence tags: **[IS]** in-sample or author-run only · **[NoCost]** ignores or understates costs · **[Unrep]** not independently replicated · **[Unvetted]** non-peer-reviewed replication.

---

## 1. Strategy families and horizons

### 1.1 Intraday (5m / 15m)

| Family | Best evidence | Status in 2026 | Fit for this platform |
|---|---|---|---|
| **Market intraday momentum** (first half-hour predicts last half-hour) | OOS R² 1.4–2.0% on SPY 1993–2013 ([Gao, Han, Li, Zhou, JFE 2018](https://doi.org/10.1016/j.jfineco.2018.05.009)); present in 60+ futures and tied to dealer-gamma and leveraged-ETF hedging flows ([Baltussen et al., JFE 2021](https://doi.org/10.1016/j.jfineco.2021.04.029)) | Flow-driven, so it depends on market structure | Use as a **feature and regime gate**, not a standalone strategy |
| **"Beat the Market" SPY noise-area breakout** | Sharpe 1.33 over 2007–2024 ([Zarattini, Aziz, Barbon, SSRN 2024](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4824172)) **[IS]** | An independent OOS test for May 2024–Mar 2026 found Sharpe 0.39 versus 1.34 in-sample ([WATO 2026](https://wato.it.com/papers/spy-intraday-momentum-oos-2026.pdf)) **[Unvetted]**. The 0DTE options boom is the suspected cause | Treat as a decaying benchmark rule |
| **Opening-range breakout on "stocks in play"** | 5-minute ORB on the top-20 relative-volume names: Sharpe 2.81 for 2016–2023 ([Zarattini, Barbon, Aziz, SSRN 2024](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4729284)) **[IS][Unrep]** | Without the relative-volume filter, ORB made about 29% in total; **the filter is the edge** | Good **primary rule for meta-labeling**. Opening spreads on small caps are the main cost risk |
| **Intraday periodicity** (same half-hour return persists across days) | [Heston, Korajczyk, Sadka, JF 2010](https://doi.org/10.1111/j.1540-6261.2010.01573.x) | Robust, small | **Feature** (same-slot lags) and execution timing |
| **Overnight vs intraday split** | The equity premium accrues overnight ([Cliff, Cooper, Gulen 2008](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1004081)); strategies earn either overnight or intraday ([Lou, Polk, Skouras, JFE 2019](https://doi.org/10.1016/j.jfineco.2019.03.011)) | Weaker after 2010 | A flat-at-close book gives up the overnight premium. **Label and benchmark the two legs separately** |

### 1.2 Swing (1–10 day holds)

| Family | Best evidence | Status in 2026 | Fit |
|---|---|---|---|
| **Residual (factor-adjusted) short-term reversal** | Conventional reversal is about zero net; residual reversal earns more than 7%/yr net ([Blitz et al. 2013](https://www.sciencedirect.com/science/article/abs/pii/S1386418112000468)); industry and factor controls more than double risk-adjusted performance ([Blitz et al., JPM 2024](https://doi.org/10.3905/jpm.2024.1.588)); a composite of short-term signals holds OOS ([Blitz et al., FAJ 2023](https://doi.org/10.1080/0015198x.2023.2173492)) | **Strongest evidence-backed swing family** | Core label and feature design (see [02](02-labeling-features-validation.md)) |
| **Cross-sectional momentum, liquidity, volatility** | Dominant ML signals in [Gu, Kelly, Xiu, RFS 2020](https://doi.org/10.1093/rfs/hhaa009); ranking objectives roughly tripled momentum Sharpe ([Poh et al., JFDS 2021](https://doi.org/10.3905/jfds.2021.1.060)) | Robust, but concentrated in microcaps ([Avramov, Cheng, Metzker 2023](https://doi.org/10.1287/mnsc.2022.4449)) | Core features; report results with and without microcaps |
| **PEAD (earnings drift)** | Non-existent in large caps since about 2006 ([Martineau, CFR 2022](https://doi.org/10.1561/104.00000122)) | **Dead as a standalone** | Use earnings as **gap and volatility context** only |
| **Pairs / stat-arb** | Returns decayed from 0.86%/mo to 0.24%/mo ([Do & Faff 2010](https://www.tandfonline.com/doi/abs/10.2469/faj.v66.n4.1)); 5–38 bps/mo after costs ([Rad, Low, Faff 2016](https://www.tandfonline.com/doi/full/10.1080/14697688.2016.1164337)) | Mostly cost-eaten | Low priority |
| **Time-series momentum on ETFs** | Asset-by-asset evidence is weak; much of the profit comes from volatility scaling ([Huang et al., JFE 2020](https://doi.org/10.1016/j.jfineco.2019.08.004)) | Weak | Regime and sizing input, not alpha |

**Publication decay.** Anomaly returns fall 26% out-of-sample and 58% after publication ([McLean & Pontiff, JF 2016](https://doi.org/10.1111/jofi.12365)). 65% of 452 anomalies fail at t = 1.96 once microcaps are handled properly ([Hou, Xue, Zhang, RFS 2020](https://doi.org/10.1093/rfs/hhy131)). Assume any published rule is weaker today than in its paper.

### 1.3 Bar size versus signal-to-noise and cost drag

- **5-minute bars are the noise floor for OHLCV-only models.** Across 400+ realized-variance estimators, 5-minute sampling is hard to beat; 1-second to 1-minute sampling is contaminated by microstructure noise ([Liu, Patton, Sheppard, J. Econometrics 2015](https://doi.org/10.1016/j.jeconom.2015.02.008)). 1-minute bars are useful as *inputs* aggregated into features, not as the prediction step, unless you add quote data.
- **Spreads.** Median quoted spread is 5.1 bps for S&P 500 names and 19.6 bps for Russell 2000 names, excluding the first and last half-hour ([Virtu 2023](https://virtu-www.s3.amazonaws.com/uploads/documents/Virtu_US_MarketStructure_March2023.pdf)). The top-10 S&P names average about 2.5 bps ([Nasdaq 2024](https://www.nasdaq.com/articles/sampling-sp-500-minimize-spreads)).
- **Impact.** Negligible at this account size (mean 11 bps at AQR scale, square-root law; [Frazzini, Israel, Moskowitz 2018](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3229719)). Spread and opening and closing slippage dominate.
- **Arithmetic.** A 15-minute strategy that round-trips twice a day in mid/large caps pays about 3–10 bps per round trip. Published intraday ML signals carry 1–5 bps of gross edge per trade ([Fischer & Krauss 2018](https://doi.org/10.1016/j.ejor.2017.11.054); [pairs replication](https://github.com/bryandjoufack/intraday-pairs-trading-ml) **[Unvetted]**). **Most intraday ML is cost-dominated.**

**Recommendation.**
- **Intraday:** predict on **15-minute** decision bars, with 5-minute bars as the finest prediction step. Compute features from 1-minute and 5-minute bars. Restrict the universe to roughly 100–300 names and ETFs with a median spread of 5 bps or less, plus a relative-volume "in play" sleeve with an explicit opening-spread model.
- **Swing:** predict on daily bars and execute near the close or the next open, using a liquid top-1,000 universe with point-in-time membership.

### 1.4 Regimes

- Regime variables work best as **features or gates, not as separate models**. Intraday momentum is strongly state-dependent on volatility and news days ([Gao et al. 2018](https://doi.org/10.1016/j.jfineco.2018.05.009)).
- A 2-state Gaussian HMM with model-predictive control beat static allocation out-of-sample after costs, mainly by cutting drawdowns ([Nystrup et al., QF 2017](https://doi.org/10.1080/14697688.2017.1342857); [JPM 2018](https://doi.org/10.3905/jpm.2018.44.2.062)).
- I found no rigorous OOS evidence for simple VIX-threshold filters on equity strategies **[unverified]**. Treat them as risk overlays.
- **Design:** a `regime` feature set covering realized volatility percentile, VIX level and term-structure slope, trend versus chop (ADX or variance ratio), HMM state probability, and macro-event-day flags. It is available to every model and used by the risk engine to scale exposure. It does not switch between separately trained models.

---

## 2. Model survey (deliverable 2)

### 2.1 What each family actually delivers

| Family | Typical inputs | Train cost on this host (Apple Silicon) | Inference latency | Overfit risk | Interpretability | Best-fit horizon | Evidence summary |
|---|---|---|---|---|---|---|---|
| **GBDT: LightGBM / CatBoost / XGBoost** | Engineered bar, cross-sectional, time-of-day, volatility, alt-data features | CPU, minutes; M-series CPUs are fast for GBDT ([gbm-bench](https://github.com/pgagarinov/gbm-bench)) | <10 ms | Medium (early stopping on purged folds) | High (SHAP) | Both | State of the art on tabular data ([Grinsztajn et al., NeurIPS 2022](https://proceedings.neurips.cc/paper_files/paper/2022/file/0378c7692da36807bdec87ab043cdadc-Paper-Datasets_and_Benchmarks.pdf)); CatBoost had the largest blend weight in Optiver 1st place ([Kaggle 2024](https://www.kaggle.com/competitions/optiver-trading-at-the-close/writeups/hyd-1st-place-solution)); Numerai's benchmark models are LightGBM ([docs](https://docs.numer.ai/numerai-tournament/models)); beat zero-shot Chronos and TimesFM on US daily returns ([Rahimikia et al. 2025](https://arxiv.org/abs/2511.18578)) |
| **Linear / ridge / logistic** | Same features | Seconds | <1 ms | Low | High | Both | DLinear beat long-horizon Transformers ([Zeng et al., AAAI 2023](https://doi.org/10.1609/aaai.v37i9.26317)). **A mandatory baseline** |
| **Shallow MLP (2–3 layers)** | Same features | CPU or MPS, minutes | <5 ms | Medium-high | Low | Swing | NN3 was best in [Gu, Kelly, Xiu 2020](https://doi.org/10.1093/rfs/hhaa009), but gains concentrate in microcaps and distressed stocks ([Avramov et al. 2023](https://doi.org/10.1287/mnsc.2022.4449)) |
| **LSTM / GRU / TCN** | Sequences of 50–200 bars, multi-asset | MPS hours, or cloud GPU | 10–100 ms | High | Low | Intraday | Fischer & Krauss: 0.46%/day gross, about zero net after 2010; the LSTM mostly rediscovered reversal ([EJOR 2018](https://doi.org/10.1016/j.ejor.2017.11.054)) **[NoCost headline]**. A Jane Street 2025 GRU ensemble got its gains mostly from online learning ([8th place](https://www.kaggle.com/competitions/jane-street-real-time-market-data-forecasting/writeups/evgeniia-grigoreva-private-lb-8th-solution)) |
| **Transformers (TFT, PatchTST, iTransformer, axial)** | Sequences plus covariates | MLX/MPS (MLX 1–3× faster than MPS for PatchTST/iTransformer on M4 Pro; [ECMS 2026](https://www.scs-europe.net/dlib/2026/ecms2026acceptedpapers/0638_dis_ecms2026_0035.pdf)) or cloud GPU | 10–100 ms | High | Low | Intraday | The reported Jane Street 2025 winner used an axial Transformer (GRU over time, attention across assets) plus daily online updates **[secondary source]**. No finance-specific, net-of-cost evidence that PatchTST, iTransformer, or TFT beat GBDT **[unverified]** |
| **Time-series foundation models (Chronos-2, Chronos-Bolt, TimesFM 2.5, Moirai 2, Toto, Kronos)** | Univariate or multivariate price history | Inference on CPU, MPS, or MLX (Chronos-2: 30 ms per batch-1 forecast with an MLX int8 port on M4 Max, [tsfm.ai](https://tsfm.ai/blog/chronos-2-apple-silicon-mlx)); LoRA fine-tune on MPS or cloud | 30–500 ms | n/a zero-shot; medium fine-tuned | Low | Volatility at both horizons | **Returns:** zero-shot R² was negative (Chronos −1.37%, TimesFM −2.80%) on 10k+ US securities ([Rahimikia et al. 2025](https://arxiv.org/abs/2511.18578)). **Volatility:** fine-tuned TimesFM competes with econometric benchmarks ([Goel et al. 2025](https://arxiv.org/html/2505.11163)). **Kronos** fails independent US checks; a survivors-only universe inflated its results 20× ([jiripik 2026](https://jiripik.com/2026/08/31/kronos-foundation-model-validation/)) **[Unvetted]**. **License flag:** Moirai is CC BY-NC 4.0 |
| **Deep RL (PPO, SAC, DQN, FinRL)** | State = features and positions | GPU, very sample-hungry | ms | Very high | Very low | — | SAC did not beat 1/N even before costs and lost money net of 0.1% costs ([Kruthof & Müller, FRL 2025](https://doi.org/10.1016/j.frl.2025.106866)); most papers assume zero slippage ([meta-review 2024](https://arxiv.org/pdf/2411.12746)); FinRL contests use a single withheld window ([2025](https://arxiv.org/html/2504.02281)) |
| **LLM news sentiment (FinBERT, LLM scoring)** | Headlines and articles, scored point-in-time | Local small model (MLX) or API | 0.1–2 s per item | High (look-ahead) | Medium | Swing, event days | GPT-4 news long-short Sharpe decayed from 6.54 (2021) to 1.22 (2024) **[NoCost]** ([Lopez-Lira & Tang, JFE 2026](https://doi.org/10.1016/j.jfineco.2026.104335)); OPT 74% vs FinBERT 72% accuracy ([Kirtac & Germano 2024](https://www.sciencedirect.com/science/article/pii/S1544612324002575)) **[Unrep]** |
| **Autonomous LLM trading agents (FinMem, TradingAgents, …)** | Everything | API | Seconds | Very high | Low | — | Lose 51–62% of Sharpe after the LLM's training cutoff ([Profit Mirage 2025](https://arxiv.org/html/2510.07920)); TradingAgents reproduction underperformed buy-and-hold ([IC-AIF 2026](https://dl.acm.org/doi/epdf/10.1145/3800973.3801029)) |
| **Meta-labeling** (primary rule decides direction; ML decides take/size) | Rule signal plus context features | CPU | <10 ms | Medium | High | Both | Practitioner and synthetic evidence ([Joubert, JFDS 2022](https://doi.org/10.3905/jfds.2022.1.098); [Meyer et al. 2022](https://doi.org/10.3905/jfds.2022.1.108)). Good fit for ORB and breakout rules |
| **Learning-to-rank (LambdaRank/LambdaMART)** | Cross-sectional features per date | CPU | <10 ms | Medium | High | Swing | Roughly 3× Sharpe over heuristic momentum ranking ([Poh et al. 2021](https://doi.org/10.3905/jfds.2021.1.060)) |

### 2.2 Ranking table: intraday (5m/15m decision bars)

| Rank | Model | Why it ranks here | Use in platform |
|---|---|---|---|
| 1 | **LightGBM/CatBoost** on residualized cross-sectional, time-of-day, volatility, and relative-volume features, **retrained daily or weekly** | Strongest, most replicated tabular evidence; cheap; interpretable; online retraining is where the competition gains came from | MVP champion |
| 2 | **Rule + meta-label classifier** (ORB on stocks in play; intraday momentum gate) | Explicit economic prior, few degrees of freedom, easy to sanity-check | MVP challenger |
| 3 | **Ridge / logistic baseline** | Must be beaten; detects leakage (if ridge looks amazing, suspect the data) | MVP baseline, always on |
| 4 | **Small GRU or axial-Transformer ensemble with online updates**, blended with GBDT | Kaggle evidence that cross-asset attention plus online updating helps; expensive to validate | Phase 4+ (cloud GPU or MLX) |
| 5 | **Fine-tuned TSFM volatility forecast** as a feature or sizing input | Volatility evidence is positive; return evidence negative | Phase 4+ |
| 6 | Zero-shot TSFM or Kronos for returns | Negative independent evidence | Avoid (research sandbox only) |
| 7 | Deep RL; autonomous LLM trading agents | Negative evidence, unvalidatable | Avoid |

### 2.3 Ranking table: swing (daily bars, 1–10 day holds)

| Rank | Model | Why | Use |
|---|---|---|---|
| 1 | **LightGBM with LambdaRank or per-date ("era-wise") regression** on residualized targets | Gu-Kelly-Xiu, Numerai practice, Poh et al.; ranking matches how the book is built (top-k long, optionally bottom-k short) | MVP champion |
| 2 | **Shallow MLP (NN2–NN3) ensembled with GBDT** | Strong but microcap-concentrated evidence | Phase 4 |
| 3 | **Meta-labeled residual-reversal or breakout rules** | Robust family, explicit prior | MVP challenger |
| 4 | **LLM news sentiment as one feature**, post-cutoff and point-in-time only | Real but decaying, look-ahead-prone | Phase 4 (local quarantined model) |
| 5 | **Fine-tuned TSFM or HAR volatility** as a feature or sizing input | Positive volatility evidence | Phase 4 |
| 6 | Zero-shot TSFM / Kronos returns | Negative | Avoid |
| 7 | Deep RL portfolio agents; LLM trading agents | Negative | Avoid |

**When is RL worth it?** Only for problems with a cheap, faithful simulator and a well-defined reward, such as execution scheduling with a calibrated impact model. Even there, overfitting is documented ([Zhang et al., IJCAI 2023](https://www.ijcai.org/proceedings/2023/553)). For signal generation, supervised learning plus explicit sizing rules dominates on evidence, stability, and auditability.

### 2.4 Mac Mini implications (no NVIDIA GPU)

- Every rank-1 to rank-3 model trains on CPU in minutes. **The host change does not affect the MVP.**
- Ranks 4–5 run on PyTorch MPS or MLX at a slower but workable speed for small models. PyTorch MPS lacks float64 and some ops ([PyTorch MPS notes](https://docs.pytorch.org/docs/main/notes/mps.html)), so do feature engineering in float64 on CPU and train in float32. Heavy sweeps should burst to a rented RTX 4090/5090 (about $0.34–1.19/h on RunPod; [pricing](https://www.runpod.io/pricing)). See [06 · Architecture](06-architecture-and-stack.md#compute).
- **Containers on macOS cannot see the GPU** (no Metal passthrough; [apple/container #62](https://github.com/apple/container/discussions/62), [Red Hat 2025](https://developers.redhat.com/articles/2025/06/05/how-we-improved-ai-inference-macos-podman-containers)). ML workers that want MPS or MLX must run **natively under launchd**, not in Docker.
