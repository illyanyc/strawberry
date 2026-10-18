"""End-to-end analyst sandbox pipeline (AN-071, AN-072): assistant job → evaluator-side checker →
sandbox worker → verdict, on a temporary jobs directory. Spans the assistant (writer), the backtest
service (checker, evaluator side), and the standalone sandbox worker, so it lives at the repo level."""

from __future__ import annotations

import asyncio
import hashlib
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np
import pytest
from tb_assistant.coder import FileSandbox
from tb_backtest import candidates

ROOT = Path(__file__).resolve().parents[2]
WORKER = ROOT / "services" / "assistant" / "sandbox" / "worker.py"

GOOD = """import numpy as np


def compute(close):
    out = np.full(len(close), np.nan)
    for i in range(19, len(close)):
        w = close[i - 19 : i + 1]
        out[i] = (close[i] - w.mean()) / (w.std() + 1e-12)
    return out
"""
LEAKY = """import numpy as np


def compute(close):
    nxt = np.concatenate([close[1:], close[-1:]])
    return nxt / close - 1.0
"""
CENTERED = """import numpy as np


def compute(close):
    return close - np.mean(close)
"""
STATEFUL = """import numpy as np

CALLS = [0]


def compute(close):
    CALLS[0] += 1
    return close * CALLS[0]
"""
SPIN = """def compute(close):
    while True:
        pass
"""


def _services(root: Path, stop: threading.Event, env: dict[str, str]) -> threading.Thread:
    def loop() -> None:
        while not stop.is_set():
            candidates.step(root)
            for job in sorted(p for p in root.iterdir() if p.is_dir()):
                if (job / "ready").exists() and not (job / "done").exists():
                    cmd = [sys.executable, str(WORKER), "run", str(job)]
                    subprocess.run(cmd, env=env, check=True, capture_output=True)  # noqa: S603 - fixed test command
            time.sleep(0.05)

    t = threading.Thread(target=loop, daemon=True)
    t.start()
    return t


async def _verdict(tmp: Path, source: str, job: str, env: dict[str, str] | None = None) -> dict[str, object]:
    stop = threading.Event()
    e = {**os.environ, "TB_SANDBOX_WALL_SECONDS": "3", "TB_SANDBOX_CPU_SECONDS": "2", **(env or {})}
    t = _services(tmp, stop, e)
    try:
        return await FileSandbox(tmp, timeout=30, poll=0.05).run(
            job, source, {"name": "x", "inputs": ["close"], "lookback": 20}
        )
    finally:
        stop.set()
        t.join(timeout=5)


def _checks(v: dict[str, object]) -> dict[str, bool]:
    return {str(c["name"]): bool(c["passed"]) for c in v["checks"]}  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("source", "failing"),
    [
        (GOOD, set()),
        (LEAKY, {"future_shift"}),
        (CENTERED, {"future_shift"}),
        (STATEFUL, {"determinism", "future_shift"}),
        (SPIN, {"sandbox_run"}),
    ],
)
def test_an071_an072_sandbox_pipeline_verdicts(tmp_path: Path, source: str, failing: set[str]) -> None:
    v = asyncio.run(_verdict(tmp_path, source, "job-" + hashlib.sha256(source.encode()).hexdigest()[:10]))
    checks = _checks(v)
    assert {k for k, ok in checks.items() if not ok} == failing
    assert v["passed"] is (not failing)
    # The analyst only ever sees names, pass/fail, and short messages or rule IDs.
    for c in v["checks"]:  # type: ignore[attr-defined]
        assert set(c) == {"name", "passed", "messages"}


def test_an072_pit_lint_flags_a_negative_shift(tmp_path: Path) -> None:
    src = "import numpy as np\n\n\ndef compute(close):\n    return np.asarray(close).shift(-1)\n"
    job = tmp_path / "job-lint01"
    job.mkdir()
    (job / "candidate.py").write_text(src)
    (job / "outputs").mkdir()
    (job / "outputs" / "sandbox.json").write_text('{"status": "error", "errors": ["AttributeError"]}')
    v = candidates.verdict(job)
    lint = next(c for c in v["checks"] if c["name"] == "pit_lint")
    assert not lint["passed"] and "PIT001" in lint["messages"]


def test_an071_sandbox_inputs_are_synthetic_and_deterministic(tmp_path: Path) -> None:
    a, b = tmp_path / "job-aaaaaa", tmp_path / "job-aaaaaa2"
    for d in (a, b):
        d.mkdir()
        candidates.prepare(d)
    da, db = np.load(a / "inputs.npz"), np.load(b / "inputs.npz")
    assert set(da.files) == {"open", "high", "low", "close", "volume", "prefix_lengths"}
    assert not np.array_equal(da["close"], db["close"])  # per-job seed
    assert np.array_equal(np.load(a / "inputs.npz")["close"], da["close"])
