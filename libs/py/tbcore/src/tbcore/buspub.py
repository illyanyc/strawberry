"""Synchronous facade over a JetStream publisher for thread-based Python workers (ml, optimizer).

``NatsPublisher`` runs nats-py on a private event loop thread; ``publish`` blocks until JetStream acks
(with ``Nats-Msg-Id`` for deduplication). ``MemoryPublisher`` records messages for tests and runs
without a bus.
"""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass, field
from typing import Any, Protocol

import nats
from google.protobuf.message import Message


class Publisher(Protocol):
    def publish(self, subject: str, msg: Message, msg_id: str = "", correlation_id: str = "") -> None: ...


@dataclass
class MemoryPublisher:
    sent: list[tuple[str, Message, str]] = field(default_factory=list)

    def publish(self, subject: str, msg: Message, msg_id: str = "", correlation_id: str = "") -> None:
        self.sent.append((subject, msg, msg_id))

    def subjects(self) -> list[str]:
        return [s for s, _, _ in self.sent]


class NatsPublisher:
    def __init__(self, url: str, user: str, password: str, name: str) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, name=f"{name}-bus", daemon=True)
        self._thread.start()
        self._nc: Any = self._run(
            nats.connect(
                servers=[url], user=user or None, password=password or None, name=name, max_reconnect_attempts=-1
            )
        )
        self._js = self._nc.jetstream()

    def _run(self, coro: Any) -> Any:
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout=30)

    def publish(self, subject: str, msg: Message, msg_id: str = "", correlation_id: str = "") -> None:
        headers = {}
        if msg_id:
            headers["Nats-Msg-Id"] = msg_id
        if correlation_id:
            headers["X-Correlation-Id"] = correlation_id
        self._run(self._js.publish(subject, msg.SerializeToString(), headers=headers or None))

    def connected(self) -> bool:
        return bool(self._nc.is_connected)

    def close(self) -> None:
        self._run(self._nc.drain())
        self._loop.call_soon_threadsafe(self._loop.stop)
