"""Coder role and the sandbox job protocol (AN-070..AN-074).

The coder (an LLM) writes one candidate feature module. Its output passes the AST policy, then goes
through a file-based job in a shared volume, so no component needs a network path to another:

1. the assistant writes ``request.json`` and ``candidate.py`` into ``<jobs>/<job_id>/``;
2. the evaluator-side checker (``tb_backtest.candidates``, backtest image, no network) writes
   synthetic ``inputs.npz`` and ``ready`` (synthetic data: the checks are data-agnostic properties,
   so no lake, no holdout, no real prices ever reach the sandbox);
3. the ``analyst-sandbox`` worker (no network, read-only, no evaluator code, limits) runs the
   candidate on the full series twice and on truncated prefixes and writes ``outputs/`` and ``done``;
4. the checker runs the point-in-time lint and compares outputs (determinism, future-shift
   invariance) and writes ``verdict.json`` with check names, pass/fail, and rule IDs only.

A passing candidate becomes a ``CodeProposal`` awaiting owner review; nothing merges automatically,
and the patch may only add a file under ``libs/py/tbcore/src/tbcore/features/candidates/``.
"""

from __future__ import annotations

import asyncio
import difflib
import hashlib
import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from tb.analyst.v1 import analyst_pb2
from tb_assistant import ast_policy
from tb_assistant.audit import AuditRecord, AuditSink
from tb_assistant.guarded import GuardedLLM
from tb_assistant.journal import Journal
from tb_assistant.llm.base import Role
from tb_assistant.masking import EgressGuard, Masker
from tb_assistant.schemas import CandidateFeatureOut

CANDIDATES_DIR = "libs/py/tbcore/src/tbcore/features/candidates"
SYSTEM = """You write one candidate feature for a trading research platform as a small Python module. \
Rules: define compute(<series>) where each parameter is one of the listed input series (1-D float arrays \
aligned in time) and return one array of the same length; value i may only use inputs at positions <= i \
(no look-ahead: no negative shifts, no centered windows, no whole-series statistics); import only numpy \
and math; no file, network, process, clock, randomness, classes, try/except, or dunder access; keep it \
short and deterministic. Reply with one JSON object: name, description, inputs, lookback, source."""


class SandboxTimeoutError(RuntimeError):
    pass


class SandboxClient(Protocol):
    async def run(self, job_id: str, source: str, request: dict[str, Any]) -> dict[str, Any]:
        """Submit a candidate and return the checker's verdict (``{"passed": bool, "checks": [...]}``)."""
        ...


class FileSandbox:
    """Shared-volume job queue (Compose volume ``analyst-jobs``)."""

    def __init__(self, jobs_dir: str | Path, timeout: float = 120.0, poll: float = 0.2) -> None:
        self.root = Path(jobs_dir)
        self.timeout = timeout
        self.poll = poll

    async def run(self, job_id: str, source: str, request: dict[str, Any]) -> dict[str, Any]:
        if not re.fullmatch(r"[A-Za-z0-9_-]{6,64}", job_id):
            raise ValueError("invalid job id")
        d = self.root / job_id
        d.mkdir(parents=True, exist_ok=False)
        (d / "candidate.py").write_text(source, encoding="utf-8")
        # request.json last: its presence tells the checker the job is complete.
        tmp = d / "request.json.tmp"
        tmp.write_text(json.dumps(request, sort_keys=True), encoding="utf-8")
        tmp.rename(d / "request.json")
        deadline = time.monotonic() + self.timeout
        verdict = d / "verdict.json"
        while time.monotonic() < deadline:
            if verdict.exists():
                data: dict[str, Any] = json.loads(verdict.read_text(encoding="utf-8"))
                return data
            await asyncio.sleep(self.poll)
        raise SandboxTimeoutError(f"no verdict for job {job_id} within {self.timeout:.0f}s")


def patch_for(name: str, source: str) -> str:
    """Unified diff adding the candidate module (review artifact; never applied automatically)."""
    path = f"{CANDIDATES_DIR}/{name}.py"
    lines = source.splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    return "".join(difflib.unified_diff([], lines, fromfile="/dev/null", tofile=f"b/{path}"))


@dataclass
class Coder:
    llm: GuardedLLM
    sandbox: SandboxClient
    journal: Journal
    audit: AuditSink
    ids: Callable[[], str]
    clock: Callable[[], Any]
    allowed_imports: frozenset[str] = ast_policy.DEFAULT_ALLOWED_IMPORTS

    async def propose(self, rnd: analyst_pb2.AnalystRound, act: analyst_pb2.PlanAction) -> str:
        now_ns = int(self.clock().timestamp() * 1e9)
        diff = json.loads(act.config_diff_json or "{}")
        payload = {
            "task": "write_feature",
            "idea": act.description,
            "hypothesis": act.hypothesis,
            "name": str(diff.get("name", ""))[:40],
            "inputs": ["open", "high", "low", "close", "volume"],
            "lookback": int(diff.get("lookback", 20) or 20),
        }
        masker = Masker(anchor=self.clock())
        res = await self.llm.complete_json(
            Role.CODER,
            system=SYSTEM,
            payload=payload,
            schema=CandidateFeatureOut,
            masker=masker,
            guard=EgressGuard(),
            lineage_id=rnd.lineage_id,
            round_id=rnd.round_id,
            max_tokens=2048,
        )
        cand = res.output
        prop = analyst_pb2.CodeProposal(
            proposal_id=self.ids(),
            lineage_id=rnd.lineage_id,
            round_id=rnd.round_id,
            kind="feature",
            name=cand.name,
            description=cand.description,
            source=cand.source,
            source_sha256=hashlib.sha256(cand.source.encode()).hexdigest(),
            patch=patch_for(cand.name, cand.source),
            status=analyst_pb2.CODE_PROPOSAL_STATUS_PENDING_CHECKS,
            created_at=now_ns,
            updated_at=now_ns,
        )
        violations = ast_policy.check(cand.source, allowed_imports=self.allowed_imports, inputs=tuple(cand.inputs))
        prop.checks.append(
            analyst_pb2.CodeCheck(name="ast_policy", passed=not violations, messages=[str(v) for v in violations][:20])
        )
        if not violations:
            try:
                verdict = await self.sandbox.run(
                    prop.proposal_id,
                    cand.source,
                    {"name": cand.name, "inputs": list(cand.inputs), "lookback": cand.lookback},
                )
            except SandboxTimeoutError as exc:
                verdict = {
                    "passed": False,
                    "checks": [{"name": "sandbox_run", "passed": False, "messages": [str(exc)]}],
                }
            for c in verdict.get("checks", []):
                prop.checks.append(
                    analyst_pb2.CodeCheck(
                        name=str(c.get("name", ""))[:40],
                        passed=bool(c.get("passed")),
                        messages=[str(m)[:200] for m in c.get("messages", [])][:20],
                    )
                )
        ok = all(c.passed for c in prop.checks)
        prop.status = (
            analyst_pb2.CODE_PROPOSAL_STATUS_AWAITING_REVIEW if ok else analyst_pb2.CODE_PROPOSAL_STATUS_CHECKS_FAILED
        )
        prop.updated_at = int(self.clock().timestamp() * 1e9)
        await self.journal.save_proposal(prop)
        await self.audit.append(
            AuditRecord(
                "analyst",
                "analyst.code.proposed",
                rnd.lineage_id,
                {
                    "proposal_id": prop.proposal_id,
                    "sha256": prop.source_sha256,
                    "status": analyst_pb2.CodeProposalStatus.Name(prop.status),
                },
                rnd.correlation_id,
            )
        )
        return prop.proposal_id

    async def review(self, proposal_id: str, *, approve: bool, note: str, actor: str) -> analyst_pb2.CodeProposal:
        """Owner review (api-gateway only). Approval marks the patch ready to merge by a human; it never
        merges or deploys anything (AN-073). Only proposals whose checks all passed can be approved."""
        p = await self.journal.get_proposal(proposal_id)
        if p is None:
            raise KeyError(proposal_id)
        if approve and p.status != analyst_pb2.CODE_PROPOSAL_STATUS_AWAITING_REVIEW:
            raise PermissionError("only proposals that passed every check can be approved")
        p.status = (
            analyst_pb2.CODE_PROPOSAL_STATUS_APPROVED_FOR_MERGE
            if approve
            else analyst_pb2.CODE_PROPOSAL_STATUS_REJECTED
        )
        p.reviewed_by, p.review_note = actor, note[:1000]
        p.updated_at = int(self.clock().timestamp() * 1e9)
        await self.journal.save_proposal(p)
        await self.audit.append(
            AuditRecord(
                actor,
                "analyst.code.review",
                p.lineage_id,
                {"proposal_id": proposal_id, "approve": approve, "sha256": p.source_sha256},
            )
        )
        return p
