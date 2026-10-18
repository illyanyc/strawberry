"""Analyst sandbox worker (AN-071). Runs inside the ``analyst-sandbox`` container only.

Standalone on purpose: stdlib + numpy, no ``tbcore``, no ``tb_assistant``, no evaluator code in the
image. The container has no network, a read-only root filesystem, no secrets, no lake mount, and
CPU/memory/process limits; this worker adds per-job limits (CPU seconds, address space, file size,
wall clock) around a child process that imports the candidate and calls ``compute``.

For each job directory with ``ready`` and no ``done``: run the candidate on the full input series
twice (determinism) and on every prefix length listed in the inputs (future shift), save the outputs
as ``outputs/*.npy``, then write ``outputs/sandbox.json`` and ``done``.

Usage: ``python worker.py watch /jobs`` (service) or ``python worker.py run /jobs/<id>`` (one job).
"""

from __future__ import annotations

import importlib.util
import inspect
import json
import os
import resource
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

CPU_SECONDS = int(os.environ.get("TB_SANDBOX_CPU_SECONDS", "60"))
MEMORY_BYTES = int(os.environ.get("TB_SANDBOX_MEMORY_MB", "1024")) * 1024 * 1024
FILE_BYTES = 64 * 1024 * 1024
WALL_SECONDS = float(os.environ.get("TB_SANDBOX_WALL_SECONDS", "90"))
SERIES = ("open", "high", "low", "close", "volume")


def _limits() -> None:
    resource.setrlimit(resource.RLIMIT_CPU, (CPU_SECONDS, CPU_SECONDS))
    resource.setrlimit(resource.RLIMIT_AS, (MEMORY_BYTES, MEMORY_BYTES))
    resource.setrlimit(resource.RLIMIT_FSIZE, (FILE_BYTES, FILE_BYTES))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def child(job: Path) -> int:
    """Executed in the limited child process."""
    data = np.load(job / "inputs.npz", allow_pickle=False)
    spec = importlib.util.spec_from_file_location("candidate", job / "candidate.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load candidate")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    compute = mod.compute
    params = [p for p in inspect.signature(compute).parameters if p in SERIES]
    out = job / "outputs"
    out.mkdir(exist_ok=True)
    n = int(data["close"].shape[0])

    def run(length: int) -> np.ndarray:
        args = {p: np.array(data[p][:length], dtype=float) for p in params}
        res = np.asarray(compute(**args), dtype=float)
        if res.ndim != 1 or res.shape[0] != length:
            raise ValueError(f"compute returned shape {res.shape}, expected ({length},)")
        return res

    np.save(out / "full.npy", run(n), allow_pickle=False)
    np.save(out / "full_repeat.npy", run(n), allow_pickle=False)
    for k in [int(x) for x in data["prefix_lengths"]]:
        np.save(out / f"prefix_{k}.npy", run(k), allow_pickle=False)
    return 0


def run_job(job: Path) -> dict[str, object]:
    start = time.monotonic()
    env = {"PATH": "/usr/bin:/bin", "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1", "MKL_NUM_THREADS": "1"}
    try:
        proc = subprocess.run(  # noqa: S603 - fixed interpreter and this file; the candidate is data to it
            [sys.executable, "-I", str(Path(__file__).resolve()), "child", str(job)],
            cwd=str(job),
            env=env,
            capture_output=True,
            timeout=WALL_SECONDS,
            preexec_fn=_limits,  # single-threaded worker: limits must apply before exec
            check=False,
        )
        status = "ok" if proc.returncode == 0 else "error"
        err = proc.stderr.decode(errors="replace").strip().splitlines()[-1:] if proc.returncode else []
    except subprocess.TimeoutExpired:
        status, err = "timeout", [f"wall clock limit {WALL_SECONDS:.0f}s exceeded"]
    result = {"status": status, "errors": [e[:300] for e in err], "seconds": round(time.monotonic() - start, 3)}
    (job / "outputs").mkdir(exist_ok=True)
    (job / "outputs" / "sandbox.json").write_text(json.dumps(result), encoding="utf-8")
    (job / "done").write_text("", encoding="utf-8")
    return result


def watch(root: Path, poll: float = 0.5) -> None:
    while True:
        for job in sorted(p for p in root.iterdir() if p.is_dir()):
            if (job / "ready").exists() and not (job / "done").exists():
                run_job(job)
        time.sleep(poll)


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    cmd, path = argv[0], Path(argv[1])
    if cmd == "child":
        return child(path)
    if cmd == "run":
        print(json.dumps(run_job(path)))
        return 0
    if cmd == "watch":
        watch(path)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
