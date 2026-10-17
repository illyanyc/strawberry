"""Durable execution cursor and dedupe set (IB-031, IB-033; section 04 "never lose an execution").

Per account: every published ``broker_exec_id`` with its event time (pruned after
``retention_days``) and the last execution time; plus the last time the session was ``ready``. The
file is replaced atomically (temp file, fsync, rename) on every change, so a crash leaves either
the old or the new state. Executions are rare (swing strategies), so a write per execution is
cheap.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_NS_PER_DAY = 86_400 * 1_000_000_000


@dataclass
class _AccountCursor:
    seen: dict[str, int] = field(default_factory=dict)
    last_exec_time: int = 0


class CursorStore:
    """In-memory cursor; :class:`FileCursorStore` adds persistence."""

    def __init__(self, retention_days: int = 8) -> None:
        self._accounts: dict[str, _AccountCursor] = {}
        self._retention_ns = retention_days * _NS_PER_DAY
        self.last_ready_at = 0

    def _acct(self, account: str) -> _AccountCursor:
        return self._accounts.setdefault(account, _AccountCursor())

    def seen(self, account: str, exec_id: str) -> bool:
        return exec_id in self._acct(account).seen

    def seen_with_base(self, account: str, base: str) -> list[str]:
        prefix = base + "."
        return [e for e in self._acct(account).seen if e.startswith(prefix)]

    def last_exec_time(self, account: str) -> int:
        return self._acct(account).last_exec_time

    def mark(self, account: str, exec_id: str, event_time: int) -> None:
        cursor = self._acct(account)
        cursor.seen[exec_id] = event_time
        cursor.last_exec_time = max(cursor.last_exec_time, event_time)
        horizon = cursor.last_exec_time - self._retention_ns
        for old in [e for e, t in cursor.seen.items() if t < horizon]:
            del cursor.seen[old]
        self._persist()

    def mark_ready(self, when: int) -> None:
        self.last_ready_at = when
        self._persist()

    def _persist(self) -> None:
        return None

    def _dump(self) -> dict[str, Any]:
        return {
            "version": 1,
            "last_ready_at": self.last_ready_at,
            "accounts": {
                a: {"seen": c.seen, "last_exec_time": c.last_exec_time} for a, c in sorted(self._accounts.items())
            },
        }

    def _load(self, raw: dict[str, Any]) -> None:
        if raw.get("version") != 1:
            raise ValueError(f"unsupported cursor file version {raw.get('version')!r}")
        self.last_ready_at = int(raw.get("last_ready_at", 0))
        for account, c in raw.get("accounts", {}).items():
            self._accounts[account] = _AccountCursor(
                seen={str(k): int(v) for k, v in c.get("seen", {}).items()},
                last_exec_time=int(c.get("last_exec_time", 0)),
            )


class FileCursorStore(CursorStore):
    def __init__(self, path: str | Path, retention_days: int = 8) -> None:
        super().__init__(retention_days)
        self._path = Path(path)
        if self._path.exists():
            self._load(json.loads(self._path.read_text(encoding="utf-8")))

    def _persist(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self._path.parent, prefix=".cursor-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self._dump(), fh, separators=(",", ":"))
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self._path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
