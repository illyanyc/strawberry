# libs/py/tbcore

Shared Python library. Today: the Python service template (settings, JSON logging with redaction, secrets, ops app). Later phases add the code that MUST be shared by training, backtesting, and live inference: instruments, calendar, features, labels, splitters, cost models, strategy API, metrics (rules DF-004, SM-011).

**Status:** In progress (Phase 0 template)  ·  **Language/stack:** Python 3.12, FastAPI, pydantic v2 ([ADR-0004](../../../docs/adr/0004-core-language-split.md), D1 provisional)

## Layout
| Path | Purpose | Rules |
|---|---|---|
| `src/tbcore/service.py` | `ServiceSettings` (`TB_*` env), `create_ops_app` (`/healthz`, `/readyz`, `/metrics`), `run()` for placeholder services | OP-010 |
| `src/tbcore/logging.py` | JSON formatter with `service`, `correlation_id`, redaction | SE-012 |
| `src/tbcore/redact.py` | Redaction (mirrors `libs/go/logging/redact.go`) | SE-012 |
| `src/tbcore/secrets.py` | `get_secret(name)` from mounted files | SE-010 |
| `tests/` | Unit tests | — |

## Run and test
- `uv run pytest libs/py/tbcore/tests`, `uv run ruff check .`, `uv run mypy` (repo root).

## Gotchas
- Keep redaction patterns in sync with the Go version; both have tests citing SE-012.
- Feature/label/strategy code must never be copied into services; import it from here.
