"""Run the analyst evaluation suite (AN-081).

``python -m tb_assistant.eval [--json OUT] [--only PREFIX]``: with ``TB_LLM_PLANNER_PROVIDER=fake``
(default) the deterministic reference planner runs; set it to ``anthropic``/``openai``/``ollama``/``mlx``
(with keys as mounted secrets and ``TB_SECRETS_DIR``) to score a real model. Exit code 1 below
``TB_EVAL_THRESHOLD`` (default 0.95).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from typing import Any

from tb_assistant.config import Settings
from tb_assistant.eval.scenarios import Scenario, all_scenarios
from tb_assistant.llm.base import LLMProvider, Role
from tb_assistant.llm.factory import build_providers


async def run_suite(scenarios: list[Scenario], planner: LLMProvider | None) -> list[dict[str, Any]]:
    results = []
    for sc in scenarios:
        start = time.perf_counter()
        try:
            outcome = await sc.run(planner)
            errors = sc.check(outcome)
        except Exception as exc:
            errors = [f"crashed: {type(exc).__name__}: {exc}"[:300]]
        results.append(
            {
                "id": sc.sid,
                "category": sc.category,
                "description": sc.description,
                "passed": not errors,
                "errors": errors,
                "seconds": round(time.perf_counter() - start, 3),
            }
        )
    return results


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    by_cat: dict[str, list[bool]] = {}
    for r in results:
        by_cat.setdefault(r["category"], []).append(r["passed"])
    passed = sum(r["passed"] for r in results)
    return {
        "scenarios": len(results),
        "passed": passed,
        "score": passed / len(results) if results else 0.0,
        "by_category": {k: f"{sum(v)}/{len(v)}" for k, v in sorted(by_cat.items())},
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m tb_assistant.eval")
    ap.add_argument("--json", help="write per-scenario results to this file")
    ap.add_argument("--only", default="", help="run scenarios whose id starts with this prefix")
    a = ap.parse_args(argv)
    cfg = Settings()
    planner = None if cfg.llm_planner_provider == "fake" else build_providers(cfg)[Role.PLANNER]
    scenarios = [s for s in all_scenarios() if s.sid.startswith(a.only)]
    results = asyncio.run(run_suite(scenarios, planner))
    summary = summarize(results)
    summary["provider"] = cfg.llm_planner_provider
    for r in results:
        print(
            f"{'PASS' if r['passed'] else 'FAIL'}  {r['id']:<12} {r['description']}"
            + ("" if r["passed"] else f"  -> {'; '.join(r['errors'])}")
        )
    print(json.dumps(summary, indent=2))
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump({"summary": summary, "results": results}, f, indent=2)
    return 0 if summary["score"] >= cfg.eval_threshold else 1


if __name__ == "__main__":
    sys.exit(main())
