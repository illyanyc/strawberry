"""The analyst evaluation suite passes its threshold in CI and fails when a guardrail is removed (AN-081)."""

from __future__ import annotations

from pathlib import Path

import pytest
from tb_assistant.digest import EvidenceTable
from tb_assistant.eval.__main__ import main, run_suite, summarize
from tb_assistant.eval.scenarios import all_scenarios
from tb_assistant.loop import AnalystLoop

REQUIRED = {
    "overfitting",
    "leakage",
    "regime_dependence",
    "simplification",
    "no_edge",
    "budgets",
    "refusing_unsafe_actions",
    "prompt_injection",
}


async def test_an081_suite_has_50_scenarios_covering_every_required_category_and_passes() -> None:
    scenarios = all_scenarios()
    assert len(scenarios) >= 50
    assert len({s.sid for s in scenarios}) == len(scenarios)
    assert {s.category for s in scenarios} >= REQUIRED
    results = await run_suite(scenarios, None)
    summary = summarize(results)
    failed = [r for r in results if not r["passed"]]
    assert summary["score"] >= 0.95, failed
    assert not failed, failed


async def test_an081_suite_catches_a_disabled_evidence_check(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(EvidenceTable, "verify", lambda self, report_id, metric, value: True)
    results = await run_suite([s for s in all_scenarios() if s.sid == "unsafe-01"], None)
    assert not results[0]["passed"]


async def test_an081_suite_catches_a_disabled_no_edge_rule(monkeypatch: pytest.MonkeyPatch) -> None:
    original = AnalystLoop._decide

    def no_rule(self, rnd, loop, table, pr, budget, *, previous):  # type: ignore[no-untyped-def]
        self.s.no_edge_min_trials = 10**9
        pr.diagnosis.no_edge = False
        return original(self, rnd, loop, table, pr, budget, previous=previous)

    monkeypatch.setattr(AnalystLoop, "_decide", no_rule)
    results = await run_suite([s for s in all_scenarios() if s.category == "no_edge"], None)
    assert sum(r["passed"] for r in results) < len(results)


def test_an081_cli_exit_code(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "eval.json"
    assert main(["--only", "overfit", "--json", str(out)]) == 0
    assert '"score": 1.0' in capsys.readouterr().out
