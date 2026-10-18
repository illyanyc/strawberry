"""Evaluator-side checks for analyst candidate features (section 12; AN-071, AN-072, BT-081).

Lives with the evaluator (``services/backtest``), outside the analyst's import graph and view. For a
job written by the assistant into the shared jobs volume it:

* ``prepare``: writes deterministic synthetic OHLCV inputs (``inputs.npz``) plus the prefix lengths
  for the future-shift probe, then ``ready``. Synthetic on purpose: determinism and future-shift
  invariance are properties of the code, not of the data, so the sandbox never sees the lake, real
  prices, or anything near the holdout;
* ``verdict``: after the sandbox wrote ``done``, runs the point-in-time lint on the source and
  compares the outputs: identical across two runs (determinism) and every prefix run equal to the
  full run on that prefix (no value depends on later inputs). Writes ``verdict.json`` with check
  names, pass/fail, and rule IDs or short messages only.

Run as the ``candidate-checker`` service: ``python -m tb_backtest candidates watch /jobs``.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

import numpy as np

from tbcore.features.lint import lint_file

N_BARS = 640
ABS_TOL = 1e-10


def _seed(job: Path) -> int:
    return int(hashlib.sha256(job.name.encode()).hexdigest()[:8], 16)


def synthetic_inputs(n: int = N_BARS, seed: int = 7) -> dict[str, np.ndarray]:
    rng = np.random.default_rng(seed)
    ret = rng.normal(0.0, 0.01, n)
    close = 100.0 * np.exp(np.cumsum(ret))
    spread = np.abs(rng.normal(0.0, 0.004, n)) * close
    open_ = np.concatenate([[close[0]], close[:-1]]) * (1 + rng.normal(0.0, 0.001, n))
    high = np.maximum(open_, close) + spread
    low = np.minimum(open_, close) - spread
    volume = rng.lognormal(10.0, 0.5, n)
    return {"open": open_, "high": high, "low": low, "close": close, "volume": volume}


def prepare(job: Path) -> None:
    data = synthetic_inputs(seed=_seed(job))
    n = data["close"].shape[0]
    prefixes = np.array(sorted({n // 4, n // 2, (3 * n) // 4, n - 1}), dtype=np.int64)
    tmp = job / "inputs.tmp.npz"
    np.savez(
        tmp,
        prefix_lengths=prefixes,
        open=data["open"],
        high=data["high"],
        low=data["low"],
        close=data["close"],
        volume=data["volume"],
    )
    tmp.rename(job / "inputs.npz")
    (job / "ready").write_text("", encoding="utf-8")


def _load(path: Path) -> np.ndarray | None:
    return np.load(path, allow_pickle=False) if path.exists() else None


def verdict(job: Path) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    findings = lint_file(job / "candidate.py")
    checks.append({"name": "pit_lint", "passed": not findings, "messages": sorted({f.rule for f in findings})})
    sb = (
        json.loads((job / "outputs" / "sandbox.json").read_text(encoding="utf-8"))
        if (job / "outputs" / "sandbox.json").exists()
        else {}
    )
    ran = sb.get("status") == "ok"
    checks.append(
        {
            "name": "sandbox_run",
            "passed": ran,
            "messages": [] if ran else [str(sb.get("status", "missing")), *list(sb.get("errors", []))[:3]],
        }
    )
    out = job / "outputs"
    full, again = _load(out / "full.npy"), _load(out / "full_repeat.npy")
    if ran and full is not None and again is not None:
        det = bool(np.array_equal(full, again, equal_nan=True))
        checks.append(
            {"name": "determinism", "passed": det, "messages": [] if det else ["two runs on the same inputs differ"]}
        )
        bad: list[str] = []
        for p in sorted(out.glob("prefix_*.npy")):
            k = int(p.stem.split("_")[1])
            pre = np.load(p, allow_pickle=False)
            if pre.shape != (k,) or not np.allclose(pre, full[:k], atol=ABS_TOL, rtol=0.0, equal_nan=True):
                bad.append(f"value at or before position {k - 1} changes when later inputs are removed")
        checks.append({"name": "future_shift", "passed": not bad, "messages": bad[:3]})
        finite = bool(np.isfinite(full).any())
        checks.append(
            {"name": "output_sanity", "passed": finite, "messages": [] if finite else ["output is never finite"]}
        )
    result = {"passed": all(c["passed"] for c in checks), "checks": checks}
    tmp = job / "verdict.json.tmp"
    tmp.write_text(json.dumps(result, sort_keys=True), encoding="utf-8")
    tmp.rename(job / "verdict.json")
    return result


def step(root: Path) -> int:
    """One pass over the jobs volume; returns the number of actions taken."""
    n = 0
    for job in sorted(p for p in root.iterdir() if p.is_dir()):
        if (job / "request.json").exists() and not (job / "ready").exists():
            prepare(job)
            n += 1
        elif (job / "done").exists() and not (job / "verdict.json").exists():
            verdict(job)
            n += 1
    return n


def watch(root: Path, poll: float = 0.5) -> None:
    while True:
        step(root)
        time.sleep(poll)


def main(args: list[str]) -> int:
    if len(args) != 2 or args[0] not in {"watch", "step"}:
        print("usage: python -m tb_backtest candidates watch|step JOBS_DIR")
        return 2
    root = Path(args[1])
    if args[0] == "step":
        print(step(root))
        return 0
    watch(root)
    return 0
