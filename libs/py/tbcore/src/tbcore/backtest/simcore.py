"""Client for ``tb-simcore``: the live OMS, gate, and ledger core in simulation (ADR-0031, BT-003).

The binary speaks the tb.backtest.v1 simcore protocol (length-delimited protobuf) on stdin/stdout.
It is located by ``TB_SIMCORE_BIN`` or built on demand with ``go build`` into a cache directory keyed
by the content of the Go sources it is made of, so a code change is never served by a stale binary.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from types import TracebackType
from typing import IO

from tb.backtest.v1 import simcore_pb2

GO_SOURCES = (
    "services/execution/cmd/tb-simcore",
    "services/execution/internal/simcore",
    "services/execution/internal/oms",
    "services/execution/internal/risk",
    "services/execution/app",
    "services/ledger/book",
    "libs/go/attribution",
    "libs/go/calendar",
)


class SimCoreError(RuntimeError):
    """The simcore refused a request or died; the backtest stops (fail closed)."""


def repo_root() -> Path:
    here = Path(__file__).resolve()
    for p in here.parents:
        if (p / "go.work").exists() and (p / "services" / "execution").is_dir():
            return p
    raise SimCoreError("tb-simcore: repository root not found; set TB_SIMCORE_BIN")


def _source_hash(root: Path) -> str:
    h = hashlib.sha256()
    for rel in (*GO_SOURCES, "services/execution/go.mod", "services/ledger/go.mod", "go.work"):
        p = root / rel
        files = sorted(p.rglob("*")) if p.is_dir() else [p]
        for f in files:
            if f.is_file() and (f.suffix in {".go", ".mod", ".work", ".csv"} or f.name == "go.work"):
                h.update(str(f.relative_to(root)).encode())
                h.update(f.read_bytes())
    return h.hexdigest()[:16]


def simcore_binary() -> Path:
    """Path of a ``tb-simcore`` binary built from the current sources."""
    env = os.environ.get("TB_SIMCORE_BIN")
    if env:
        return Path(env)
    root = repo_root()
    cache = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "tb" / "simcore"
    out = cache / f"tb-simcore-{_source_hash(root)}"
    if out.exists():
        return out
    go = shutil.which("go")
    if go is None:
        raise SimCoreError("tb-simcore: Go toolchain not found; install Go or set TB_SIMCORE_BIN")
    cache.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=cache) as tmp:
        tmp_bin = Path(tmp) / "tb-simcore"
        proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
            [go, "build", "-trimpath", "-o", str(tmp_bin), "./cmd/tb-simcore"],
            cwd=root / "services" / "execution",
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            raise SimCoreError(f"tb-simcore build failed: {proc.stderr[-2000:]}")
        tmp_bin.replace(out)
    return out


def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _read_varint(f: IO[bytes]) -> int | None:
    shift, n = 0, 0
    while True:
        b = f.read(1)
        if not b:
            return None if shift == 0 else _eof()
        n |= (b[0] & 0x7F) << shift
        if not b[0] & 0x80:
            return n
        shift += 7


def _eof() -> int:
    raise SimCoreError("tb-simcore: truncated response")


class SimCore:
    """One ``tb-simcore`` process. Use as a context manager."""

    def __init__(self, binary: Path | None = None) -> None:
        self.binary = binary or simcore_binary()
        self._stderr = tempfile.TemporaryFile()  # noqa: SIM115 - closed in close()
        self._proc = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
            [str(self.binary)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self._stderr
        )
        self.requests = 0

    def __enter__(self) -> SimCore:
        return self

    def __exit__(self, et: type[BaseException] | None, ev: BaseException | None, tb: TracebackType | None) -> None:
        self.close()

    def close(self) -> None:
        if self._proc.poll() is None:
            assert self._proc.stdin is not None
            self._proc.stdin.close()
            try:
                self._proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self._stderr.close()

    def _stderr_tail(self) -> str:
        self._stderr.seek(0)
        return self._stderr.read()[-2000:].decode(errors="replace")

    def call(self, req: simcore_pb2.SimRequest) -> simcore_pb2.SimResponse:
        assert self._proc.stdin is not None and self._proc.stdout is not None
        data = req.SerializeToString()
        try:
            self._proc.stdin.write(_varint(len(data)) + data)
            self._proc.stdin.flush()
        except BrokenPipeError as e:
            raise SimCoreError(f"tb-simcore exited: {self._stderr_tail()}") from e
        n = _read_varint(self._proc.stdout)
        if n is None:
            raise SimCoreError(f"tb-simcore exited: {self._stderr_tail()}")
        resp = simcore_pb2.SimResponse()
        resp.ParseFromString(self._proc.stdout.read(n))
        self.requests += 1
        if resp.error:
            raise SimCoreError(resp.error)
        return resp
