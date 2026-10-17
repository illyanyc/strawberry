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

- **Model stages (registry):** `candidate → challenger (shadow/paper) → champion`, `archived`.

## Rules

| ID | Rule | Status | Code / tests |
|---|---|---|---|
| SM-001 | Every new strategy MUST start in `paper`; nothing may create a strategy directly in `live`. | Specified | — |
| SM-002 | Validation MUST use walk-forward or purged/embargoed CV; random K-fold on time series is forbidden. | Specified | — |
| SM-003 | The sealed final holdout MUST NOT be accessible to optimization; each access is logged and counted, and evaluated once per promotion candidate. | Specified | — |
| SM-004 | Promotion to `live` MUST require: graduation criteria met, explicit human approval with step-up auth, a per-strategy capital cap, the global live flag, and an audit record. | Specified | — |
| SM-005 | Default graduation criteria: ≥ 20 trading days and ≥ 100 trades in paper; live-vs-backtest divergence within tolerance; no unexplained reconciliation breaks; max drawdown within the backtest's 95th percentile. All configurable. | Specified | — |
| SM-006 | Any risk breach, reconciliation failure, or monitoring alarm MAY demote automatically to `paper` or `halted`; re-promotion MUST be manual. | Specified | — |
| SM-007 | Every model, backtest, and experiment MUST record code commit, config, dataset snapshot ID, seeds, and environment lockfile; unreproducible runs are not promotable. | Specified | — |
| SM-008 | Strategy ranking MUST report multiple-testing-aware statistics (deflated Sharpe, PBO) alongside Sharpe, Sortino, Calmar, max drawdown, turnover, cost drag. | Specified | — |
| SM-009 | Challengers run in shadow or paper next to the champion; replacing a live champion follows SM-004. | Specified | — |
| SM-010 | The assistant MUST NOT place orders, change live config or limits, promote strategies, read the holdout, or see credentials or account identifiers; its experiments run within a compute budget. | Specified | — |
| SM-011 | Backtest strategy code MUST be the same code that runs live (shared strategy API in `libs/py/tbcore`). | Specified | — |

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
| Live drift alarm at 15:55 | Auto-demote to `paper`; strategy-owned positions handled per the demotion policy (DECISION PENDING: hold vs flatten) | SM-006 |
| Assistant proposes raising a risk limit | Shown as a suggestion only; owner must apply with step-up auth | SM-010, RM-007 |

## Open questions

- Demotion policy for open positions (hold under paper rules, or flatten strategy-owned): DECISION PENDING.
- Divergence tolerance definition and retraining cadence/triggers (research §5, §9).
- MVP model families and labels (D23, D24).
