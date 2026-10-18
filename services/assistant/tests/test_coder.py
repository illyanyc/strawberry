"""Coder role, AST policy, and code-proposal review (AN-070, AN-073, AN-074)."""

from __future__ import annotations

from typing import Any

import pytest
from tb_assistant import ast_policy
from tb_assistant.audit import MemoryAudit
from tb_assistant.coder import CANDIDATES_DIR, Coder, FileSandbox, SandboxTimeoutError, patch_for
from tb_assistant.eval.fake_research import World
from tb_assistant.eval.harness import Clock, build
from tb_assistant.guarded import GuardedLLM, SpendLedger
from tb_assistant.journal import MemoryJournal
from tb_assistant.llm.base import Role
from tb_assistant.llm.fake import FakeLLM

from tb.analyst.v1 import analyst_pb2 as A

GOOD = """import numpy as np


def compute(close):
    out = np.full(len(close), np.nan)
    for i in range(19, len(close)):
        w = close[i - 19 : i + 1]
        out[i] = (close[i] - w.mean()) / (w.std() + 1e-12)
    return out
"""


@pytest.mark.parametrize(
    ("src", "rule"),
    [
        ("import os\ndef compute(close):\n    return close\n", "AST001"),
        ("import numpy.random\ndef compute(close):\n    return close\n", "AST001"),
        ("from subprocess import run\ndef compute(close):\n    return close\n", "AST001"),
        ("def compute(close):\n    return eval('close')\n", "AST005"),
        ("def compute(close):\n    return open('/etc/passwd')\n", "AST005"),
        ("def compute(close):\n    return close.__class__\n", "AST003"),
        ("import numpy as np\ndef compute(close):\n    return np.load('x')\n", "AST004"),
        ("import numpy as np\ndef compute(close):\n    return np.random.rand(3)\n", "AST004"),
        ("class X:\n    pass\ndef compute(close):\n    return close\n", "AST006"),
        (
            "def compute(close):\n    try:\n        return close\n    except Exception:\n        return close\n",
            "AST006",
        ),
        ("def other(close):\n    return close\n", "AST007"),
        ("def compute(close, account):\n    return close\n", "AST008"),
        ("x = 1\nprint(x)\ndef compute(close):\n    return close\n", "AST009"),
        ("def compute(close):\n    global y\n    return close\n", "AST002"),
    ],
)
def test_an070_ast_policy_refuses(src: str, rule: str) -> None:
    rules = {v.rule for v in ast_policy.check(src, inputs=("open", "high", "low", "close", "volume"))}
    assert rule in rules


def test_an070_ast_policy_accepts_a_plain_feature() -> None:
    assert ast_policy.check(GOOD, inputs=("close",)) == []


def test_an073_patch_only_adds_a_candidate_file() -> None:
    p = patch_for("close_z", GOOD)
    assert p.startswith("--- /dev/null\n+++ b/" + CANDIDATES_DIR + "/close_z.py")
    assert all(line.startswith(("+", "-", "@", " ")) for line in p.splitlines())


class StubSandbox:
    def __init__(self, verdict: dict[str, Any] | None = None) -> None:
        self.calls: list[str] = []
        self.verdict = verdict or {"passed": True, "checks": [{"name": "future_shift", "passed": True, "messages": []}]}

    async def run(self, job_id: str, source: str, request: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(source)
        return self.verdict


def _coder(mode: str, sandbox: StubSandbox) -> tuple[Coder, MemoryJournal, MemoryAudit]:
    j, a = MemoryJournal(), MemoryAudit()
    llm = GuardedLLM({Role.CODER: FakeLLM(mode=mode)}, spend=SpendLedger())
    n = iter(range(10**6))
    return Coder(llm, sandbox, j, a, ids=lambda: f"prop-{next(n):06d}", clock=Clock()), j, a


def _round() -> tuple[A.AnalystRound, A.PlanAction]:
    rnd = A.AnalystRound(round_id="r1", lineage_id="L1")
    act = A.PlanAction(
        action_id="a1", tool="write_code_proposal", description="trailing z-score", config_diff_json='{"lookback": 20}'
    )
    return rnd, act


async def test_an073_passing_candidate_awaits_review_and_only_owner_approves() -> None:
    sb = StubSandbox()
    coder, journal, audit = _coder("heuristic", sb)
    pid = await coder.propose(*_round())
    p = await journal.get_proposal(pid)
    assert p is not None and p.status == A.CODE_PROPOSAL_STATUS_AWAITING_REVIEW
    assert [c.name for c in p.checks] == ["ast_policy", "future_shift"] and sb.calls
    assert CANDIDATES_DIR in p.patch and p.source_sha256
    approved = await coder.review(pid, approve=True, note="looks right", actor="owner")
    assert approved.status == A.CODE_PROPOSAL_STATUS_APPROVED_FOR_MERGE and approved.reviewed_by == "owner"
    assert "analyst.code.proposed" in audit.actions() and "analyst.code.review" in audit.actions()


async def test_an070_unsafe_coder_output_never_reaches_the_sandbox() -> None:
    sb = StubSandbox()
    coder, journal, _ = _coder("adversarial", sb)
    pid = await coder.propose(*_round())
    p = await journal.get_proposal(pid)
    assert p is not None and p.status == A.CODE_PROPOSAL_STATUS_CHECKS_FAILED
    assert not sb.calls
    with pytest.raises(PermissionError):
        await coder.review(pid, approve=True, note="", actor="owner")
    rejected = await coder.review(pid, approve=False, note="unsafe", actor="owner")
    assert rejected.status == A.CODE_PROPOSAL_STATUS_REJECTED


async def test_an072_failed_sandbox_check_fails_the_proposal() -> None:
    sb = StubSandbox({"passed": False, "checks": [{"name": "future_shift", "passed": False, "messages": ["changes"]}]})
    coder, journal, _ = _coder("heuristic", sb)
    p = await journal.get_proposal(await coder.propose(*_round()))
    assert p is not None and p.status == A.CODE_PROPOSAL_STATUS_CHECKS_FAILED


async def test_file_sandbox_times_out_without_a_verdict(tmp_path: Any) -> None:
    fs = FileSandbox(tmp_path, timeout=0.3, poll=0.05)
    with pytest.raises(SandboxTimeoutError):
        await fs.run("job-000001", GOOD, {"name": "x"})
    assert (tmp_path / "job-000001" / "request.json").exists()
    with pytest.raises(ValueError, match="invalid job id"):
        await fs.run("../escape", GOOD, {})


async def test_an073_loop_turns_a_code_action_into_a_reviewable_proposal() -> None:
    fake = FakeLLM(mode="scripted")
    fake.queue(
        {
            "findings": [],
            "plan": [
                {
                    "category": "features",
                    "description": "trailing z-score of the close",
                    "target": "feature_set",
                    "tool": "write_code_proposal",
                    "config_diff": {"lookback": 20},
                    "expected_gain": 0.02,
                    "est_compute_hours": 0.1,
                    "trial_cost": 0,
                    "overfit_risk": "low",
                }
            ],
        }
    )
    h = build(lineages={"L1": {"level": 1, "world": World(true_sharpe=0.15)}}, planner=fake)
    run = h.research.run_owner_training("L1")
    h.research.completions.clear()
    r = await h.trigger_run("L1", run.run_id)
    assert r is not None
    act = next(a for a in r.plan.actions if a.tool == "write_code_proposal")
    assert act.status == A.ACTION_STATUS_COMPLETED and act.launched_ids[0].startswith("proposal:")
    props = await h.journal.list_proposals("L1")
    assert len(props) == 1 and props[0].status == A.CODE_PROPOSAL_STATUS_AWAITING_REVIEW
