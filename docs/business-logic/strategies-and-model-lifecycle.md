# Strategies and model lifecycle

**Prefix:** `SM-`  ·  **Owners:** `ml`, `backtest`, `optimizer`, `execution` (runtime), `monitor` (demotion), `assistant`  ·  **Related:** [data and features](data-and-features.md), [risk](risk-management.md)

## Scope

How a strategy and its models move from research to live and back: modes, validation, holdout discipline, promotion, demotion, reproducibility, and the assistant's limits. Feature and label correctness is in [data and features](data-and-features.md).

## Concepts and states

- **Strategy:** model version(s) + signal-to-intent rules + sizing + limits, bound to one virtual portfolio.
- **Strategy modes:**

```mermaid
stateDiagram-v2
  [*] --> research
  research --> paper: registered + backtest gate
  paper --> live: graduation criteria + human approval + step-up
  live --> paper: auto/manual demotion
  paper --> halted: breach / recon failure
  live --> halted: breach / recon failure
  halted --> paper: manual only
```

- **Model stages (registry):** `candidate → validated → shadow → paper → live_eligible → live → retired` ([optimization](optimization.md), EX-050); the paper champion is one per lineage.

## Rules

| ID | Rule | Status | Code / tests |
|---|---|---|---|
| SM-001 | Every new strategy MUST start in `paper` (or below: `disabled`, `shadow`); nothing may create a strategy directly in `live`. Re-registering an existing strategy never changes its mode. | Implemented | execution `RegisterStrategy`, `Seed` |
| SM-002 | Validation MUST use walk-forward or purged/embargoed CV; random K-fold on time series is forbidden. | Implemented | `tbcore/validation/splitters.py`, `tbcore/ml/config.py` (ML-005; `test_ml005_folds_purge_and_embargo_at_least_the_horizon`) |
| SM-003 | The sealed final holdout MUST NOT be accessible to optimization; each access is logged and counted, and evaluated once per promotion candidate. | Implemented | Holdout service (EX-010..EX-015; `test_ex010_ex012_holdout_is_refused_to_studies_and_the_analyst`, `test_ex011_ex013_*`) |
| SM-004 | Promotion to `live` MUST require: graduation criteria met, explicit human approval with step-up auth, a per-strategy capital cap, the global live flag, and an audit record. | Specified | — |
| SM-005 | Default graduation criteria: ≥ 20 trading days and ≥ 100 trades in paper; live-vs-backtest divergence within tolerance; no unexplained reconciliation breaks; max drawdown within the backtest's 95th percentile. All configurable. | Specified | — |
| SM-006 | Any risk breach, reconciliation failure, or monitoring alarm MAY demote automatically to `paper` (from `live`) or `halted` (from `shadow`/`paper`); re-promotion MUST be manual. Monitoring demotes through the reduce-only `DemoteStrategy` (RM-018) under a per-strategy containment policy (MN-034, MN-035). | Implemented | execution `oms/demote.go`; monitor `containment.py`; `TestRM018*` |
| SM-007 | Every model, backtest, and experiment MUST record code commit, config, dataset snapshot ID, seeds, and environment lockfile; unreproducible runs are not promotable. | Implemented | `tbcore/ml/runs.py` (ML-010, ML-013, ML-015; `test_ml015_rebuild_reproduces_metrics_exactly`) |
| SM-008 | Strategy ranking MUST report multiple-testing-aware statistics (deflated Sharpe, PBO) alongside Sharpe, Sortino, Calmar, max drawdown, turnover, cost drag. | Implemented | `tb_optimizer/stats.py`, `gates.py` (EX-021, EX-024) |
| SM-009 | Challengers run in shadow or paper next to the champion; replacing a live champion follows SM-004. | Implemented | `tb_optimizer/registry.py`, `core.py` (EX-052, EX-056; `test_ex056_shadow_challenger_runs_next_to_the_paper_champion`) |
| SM-010 | The assistant MUST NOT place orders, change live config or limits, promote strategies, read the holdout, or see credentials or account identifiers; its experiments run within a compute budget. | Implemented | Analyst side: `services/assistant` (`tools.py` forbidden names and level checks, budgets; AN-030..AN-032, AN-060); optimizer permission table (EX-031); `test_an032_*` |
| SM-011 | Backtest strategy code MUST be the same code that runs live (shared strategy API in `libs/py/tbcore`). | Specified | — |
| SM-012 | Strategy modes are `disabled`, `shadow`, `paper`, `live`, `halted`. Owner actions may set `disabled`, `shadow`, `paper`, `halted`; `live` only through the section 15 go-live flow (SM-004). A paper strategy may only send to paper accounts and a live strategy only to live accounts, and only to its registered account. | Implemented | execution `SetMode`, risk `mode` |
| SM-013 | A `shadow` strategy's intents are evaluated by the full gate and recorded with `shadow=true`, but never reserve capital or reach a broker. Hypothetical fills are Planned (section 08 parity). | Implemented | OMS `process` |
| SM-015 | Model strategies MUST run on the shared host (`tbcore.strategy.model_signal`: `model_signal`, `meta_filter`, `residual_reversal`) in backtests, shadow, and paper: entries only when the model's score clears the threshold fitted on training data (or the filter takes the primary's entry), a protective stop on every entry (N5), exits after the label horizon (intraday also before the close). Live and shadow scoring uses the persisted bundle on `OnlineFeatureEngine` and fails closed on a feature-set mismatch (ML-040); a bar time's decisions are emitted when its last instrument bar arrives (cross-sectional features need the batch), and an incomplete older batch is skipped, never guessed. | Implemented | `tbcore/strategy/model_signal.py`, `host.py` (`test_ml041_ml042_online_predictions_equal_offline_for_the_same_bars`, `test_ex056_shadow_challenger_runs_next_to_the_paper_champion`) |
| SM-020 | A TradingView webhook strategy (runtime class `tradingview`) MUST have a `tv-<name>` strategy ID and act only on signals for its own ID (`sig.tv-<name>.<instrument>`, new messages only) in instruments listed in its registry entry. A signal MUST be dropped when it is older than `max_signal_age_seconds` (60) at the decision time or from the future, or when the newest final bar of the instrument is older than `max_bar_age_seconds` (1,800) or not yet available. `long` opens a long only when flat and closes a short; `short` opens a short only when flat and `allow_short` is set, and closes a long; `flat` closes the strategy's own position; no signal reverses a position. Opens MUST carry a stop `stop_bps` (0 < x < 5,000; default 150) from the platform's own close and are sized fixed-fractionally (`risk_per_trade` ≤ 0.05) on the strategy's equity basis; intraday strategies refuse opens and flatten inside the last `exit_minutes_before_close` (15). Intent IDs derive from the signal ID. Event blackouts (CX-016) also drop webhook opens. | Implemented | `libs/py/tbcore/src/tbcore/strategy/tradingview.py`, `host.py` (`on_signal`, `signal_subjects`), `services/strategy-runtime`; `test_sm020_*` in `services/strategy-runtime/tests/test_runner.py` and `libs/py/tbcore/tests/test_tradingview.py` |
| SM-014 | Strategies see only their own virtual portfolio (ledger updates filtered to their owner) and data with `available_at <= decision_time`; the runtime uses `tbcore.strategy` so backtests run the same code (SM-011). | Implemented | `tbcore.strategy`, `tbcore.strategy.host.Runner` (shared by the runtime and the backtester); `test_runner_sees_only_its_own_positions_sm014`, `test_orb_ignores_bars_from_the_future_df002` |

## Invariants

| ID | Invariant | Checked by |
|---|---|---|
| SM-I1 | Every `live` strategy has an audited promotion record and a capital cap. | Startup check in execution; audit query |
| SM-I2 | Every registered model links to a reproducible run (SM-007 fields present). | Registry validation |
| SM-I3 | Holdout access count per study is recorded and never decreases. | Holdout API test |

## Edge cases

| Scenario | Expected behavior | Rules |
|---|---|---|
| Optimizer requests data overlapping the holdout period | Refused; logged | SM-003 |
| Paper strategy passes criteria but recon had one explained break | Eligible; explained breaks are recorded with reason | SM-005 |
| Live drift alarm at 15:55 | Auto-demote to `paper` (latched strategy kill switch); strategy-owned positions are held with their broker-side protection unless the containment policy's severe action is `kill_flatten` (ADR-0045) | SM-006, RM-018 |
| Assistant proposes raising a risk limit | Shown as a suggestion only; owner must apply with step-up auth | SM-010, RM-007 |
| TradingView sends `buy` for a ticker the `tv-<name>` strategy does not list | Signal dropped by the strategy (logged); no intent | SM-020 |
| TradingView `exit` while manual and another strategy also hold the instrument | Closes only the `tv-<name>` strategy's own quantity | SM-020, VP-003 |
| Strategy runtime restarts while TradingView alerts arrive | Signals received while it was down are not replayed (new messages only) | SM-020 |

## Open questions

- Demotion policy for open positions (hold under paper rules, or flatten strategy-owned): DECISION PENDING.
- Divergence tolerance definition and retraining cadence/triggers (research §5, §9).
