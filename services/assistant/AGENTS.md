# assistant

The AI Research Analyst and chat assistant (section 12; [ADR-0041](../../docs/adr/0041-ai-research-analyst-loop-and-tool-server.md) D59, [ADR-0042](../../docs/adr/0042-analyst-llm-providers-masking-and-egress.md) D60, [ADR-0043](../../docs/adr/0043-analyst-coder-sandbox-and-candidate-review.md) D61; rules `AN-` in [`assistant.md`](../../docs/business-logic/assistant.md)). A deterministic Centaur loop: Optuna (section 11) searches, the LLM plans. The analyst can never trade, see the holdout or the scorer internals, or promote to live.

**Status:** Phase 4  ·  **Language/stack:** Python 3.12 on `tbcore`, asyncio, `httpx`, `asyncpg`, `nats-py`, `grpcio` ([ADR-0004](../../docs/adr/0004-core-language-split.md), D1)

## Responsibilities
- Owns: the analyst loop (triggers, rounds, stop rules, budgets, autonomy-gated launches, approvals), the tool server, LLM providers per role with masking and the egress guard, the quarantined role, the coder role and sandbox jobs, the research journal (schema `analyst`), chat, the evaluation suite, the `llm-egress` proxy, the standalone sandbox worker (`sandbox/worker.py`, its own image).
- Does not own: statistics (sections 08/10/11), the trial ledger, autonomy levels and promotion gates (optimizer, EX-031..EX-036), the holdout (never reachable), live feedback computation (monitor), containment (execution, monitor).

## Interfaces
| Direction | Kind | Name | Contract |
|---|---|---|---|
| in | gRPC `:9090` | `tb.analyst.v1.AnalystService` (only `api-gateway`, SE-040) | `analyst.proto` |
| out | gRPC | `MLService` (`ml:9090`), `OptimizerService` (`optimizer:9091`) as actor `analyst` | `tb.ml.v1`, `tb.optimizer.v1` (no `HoldoutService` client) |
| in | NATS (durable) | `ml.run.completed`, `optimizer.study.completed`, `backtest.completed` (`RESEARCH`); `monitor.feedback.>`, `monitor.retrain_suggested.>` (`MONITOR`); `analyst.autonomy.lower` (`ANALYST`) | `MLRunCompleted`, `StudyCompleted`, `BacktestCompleted`, `LiveFeedbackReport`, `RetrainSuggested`, `LowerAutonomy` |
| out | NATS | `analyst.round.completed`, `analyst.plan.ready`, `analyst.escalation` (`ANALYST`); `alert.<severity>` | `RoundCompleted`, `PlanReady`, `Escalation`, `tb.alerts.v1.Alert` |
| out | HTTP (via `llm-egress`) | Anthropic / OpenAI APIs, local Ollama / MLX on the Mac host | provider APIs, allowlisted (AN-053) |
| in/out | files | `analyst-jobs` volume: `request.json`, `candidate.py` → `verdict.json` | ADR-0043 |
| in | HTTP (internal) | ops `:8081` (`/healthz`, `/readyz`, `/metrics`) | OP-010 |

## Business rules enforced here
- AN-001..AN-004, AN-013, AN-020..AN-022, AN-040..AN-042, AN-060..AN-063 → `src/tb_assistant/loop.py` (`tests/test_loop.py`)
- AN-010..AN-012 → `planner.py`, `digest.py` (`tests/test_loop.py`)
- AN-023, AN-050..AN-056 → `guarded.py`, `llm/`, `masking.py`, `quarantine.py`, `egress_proxy.py` (`tests/test_llm_layer.py`, `tests/test_service.py`)
- AN-030..AN-032, AN-056, AN-060 → `tools.py` (`tests/test_loop.py`)
- AN-070..AN-073 → `ast_policy.py`, `coder.py`, `sandbox/worker.py` (`tests/test_coder.py`, `tests/python/test_analyst_sandbox.py`)
- AN-080 → `chat.py` (`tests/test_service.py`); AN-081 → `eval/scenarios.py` (`tests/test_eval.py`)
- BT-081 (no evaluator imports) → `tests/python/test_evaluator_isolation.py`

## Layout
| Path | Purpose |
|---|---|
| `src/tb_assistant/__main__.py` | Service wiring (Postgres, NATS, research clients, providers, loop, gRPC, retention); `egress-proxy` subcommand |
| `src/tb_assistant/loop.py` | Loop controller |
| `src/tb_assistant/tools.py` | Tool server |
| `src/tb_assistant/planner.py`, `digest.py`, `configs.py`, `budget.py` | Planner role, evidence table, config diffs, loop budget |
| `src/tb_assistant/llm/`, `guarded.py`, `masking.py`, `quarantine.py`, `schemas.py` | Providers and the guarded call path |
| `src/tb_assistant/coder.py`, `ast_policy.py` | Coder role and sandbox jobs |
| `src/tb_assistant/journal.py`, `audit.py`, `refdata.py` | Journal, audit, known tickers for masking |
| `src/tb_assistant/service.py`, `bus.py`, `chat.py`, `research.py` | gRPC service, NATS wiring, chat, research client |
| `src/tb_assistant/egress_proxy.py` | `llm-egress` allowlisting proxy |
| `src/tb_assistant/eval/` | Research fakes, the analyst harness, and the evaluation suite (`scenarios.py`, `python -m tb_assistant.eval`) |
| `sandbox/worker.py` | Standalone sandbox worker (image `infra/docker/analyst-sandbox.Dockerfile`) |
| `tests/` | Unit and integration tests (async tests without pytest-asyncio, `conftest.py`) |

## Configuration
Secrets (files only, SE-010): `pg_svc_assistant_password`, `nats_assistant_password`, `svc_assistant_ed25519`, `svc_api_gateway_ed25519.pub`, optional `llm_anthropic_api_key`, `llm_openai_api_key`.

| Variable | Default | Notes |
|---|---|---|
| `TB_LLM_PLANNER_PROVIDER` / `_CODER_` / `_CHAT_` | `fake` | `anthropic`, `openai`, `ollama`, `mlx`, `fake` |
| `TB_LLM_QUARANTINE_PROVIDER` | `fake` | Local only: `ollama`, `mlx`, `fake` (AN-054) |
| `TB_LLM_LOCAL_ONLY` / `TB_LLM_LOCAL_PROVIDER` | `false` / `ollama` | Route every role to the local model |
| `TB_LLM_*_MODEL`, `TB_LLM_*_URL` | see `config.py` | Model names and endpoints per provider |
| `TB_LLM_PROXY` | empty (`http://llm-egress:3128` in Compose) | All provider traffic goes through it |
| `TB_LLM_EGRESS_ALLOW` | Anthropic, OpenAI, host Ollama/MLX | `host:port` list (also read by `llm-egress`) |
| `TB_LLM_PRICES`, `TB_MAX_LLM_USD_PER_DAY` / `_MONTH` | defaults / `5` / `50` | Spend caps (AN-023) |
| `TB_PLATEAU_K`, `TB_PLATEAU_EPSILON`, `TB_NO_EDGE_*`, `TB_UNREACHABLE_DSR`, `TB_MAX_FAILED_ROUNDS`, `TB_MAX_ACTIVE_LOOPS` | 3, 0.05, 20/0.5/0.5, 0.1, 2, 3 | Stop rules (AN-040..AN-042) |
| `TB_BUDGET_*` | 5 trials/round, 8 CPU h/day, 2 jobs, 10 rounds, 24 h | Default loop budget (AN-020) |
| `TB_ML_ADDR` / `TB_OPTIMIZER_ADDR` | `ml:9090` / `optimizer:9091` | Research services |
| `TB_SANDBOX_JOBS_DIR` / `TB_SANDBOX_TIMEOUT_SECONDS` | `/jobs` / `120` | Coder jobs volume |
| `TB_CHAT_RETENTION_DAYS` | `365` | AN-080 |

## Run and test
- Run: `make up PROFILES=core,observability,ml,ml-workers` (assistant, `llm-egress`, `analyst-sandbox`, `candidate-checker`); locally `uv run python -m tb_assistant`.
- Test: `uv run pytest services/assistant/tests tests/python/test_analyst_sandbox.py tests/python/test_evaluator_isolation.py`.
- Evaluation suite: `uv run python -m tb_assistant.eval [--json out.json]` (54 scenarios; fake planner by default; set `TB_LLM_PLANNER_PROVIDER` plus keys as mounted secrets and `TB_SECRETS_DIR` to score a real model; exits 1 below `TB_EVAL_THRESHOLD`, default 0.95).

## Operational notes
- Every round, LLM call, and tool call is in schema `analyst` and the audit log; replay a round from its masked prompt and response (`analyst.llm_calls`).
- With no keys and `fake` providers the analyst still runs end to end (the heuristic reference analyst), which is the CI and default configuration.

## Gotchas
- Never import evaluator modules (`tbcore.backtest`, `tbcore.validation`, `tbcore.parity`, `tbcore.features.lint`, `tb_backtest`) anywhere under `services/assistant/` (BT-081); the candidate checks live in `services/backtest`.
- The optimizer owns autonomy: raising happens only in api-gateway with a step-up; this service can only lower.
- At level 0 the analyst cannot launch: approved actions come back from `ReviewActions` as `PreparedLaunch`es for the gateway to submit as the owner.
- Shared trading logic (features, labels, strategy API) belongs in `libs/py/tbcore`, never here.
