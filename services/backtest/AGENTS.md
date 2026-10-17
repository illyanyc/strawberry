# backtest

Vectorized and event-driven backtests on tbcore (section 08, Phase 3).

**Status:** Planned (Phase 0 placeholder: ops surface only)  ·  **Language/stack:** Python 3.12 on `tbcore` ([ADR-0004](../../docs/adr/0004-core-language-split.md), D1)

## Responsibilities
- Owns (when built): see the section prompt in `docs/prompts/` and the system map in the root `AGENTS.md`.
- Today: `/healthz`, `/readyz`, `/metrics` on `:8081` via `tbcore.service.run` so the stack, dashboards, and alerts cover it.

## Interfaces
| Direction | Kind | Name | Contract |
|---|---|---|---|
| in | HTTP (internal) | ops endpoints | OP-010 |

## Business rules enforced here
None yet.

## Layout
| Path | Purpose |
|---|---|
| `pyproject.toml` | uv workspace member `backtest` |
| `src/tb_backtest/__main__.py` | Entrypoint (`python -m tb_backtest`) |

## Configuration
| Variable | Default | Notes |
|---|---|---|
| `TB_SERVICE` | `backtest` | Set by the entrypoint |
| `TB_LOG_LEVEL` | `info` | |

## Run and test
- Run: `make up` (container) or `uv run python -m tb_backtest`.
- Test: `uv run pytest` (template covered by `libs/py/tbcore/tests` and `tests/python/test_placeholder_services.py`).

## Operational notes
Placeholder: readiness has no dependency checks yet.

## Gotchas
Shared trading logic (features, labels, strategy API) belongs in `libs/py/tbcore`, never here.
