"""Reconnect backoff: exponential with equal jitter, capped, reset after a stable session (IB-011).

Equal jitter (half fixed, half random) keeps a floor of ``base / 2`` so a flapping gateway can
never produce a tight reconnect loop, while still de-synchronizing the order and market-data
connections.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field


@dataclass
class Backoff:
    base: float = 1.0
    cap: float = 60.0
    factor: float = 2.0
    rng: random.Random = field(default_factory=random.Random)
    _attempt: int = 0

    def next_delay(self) -> float:
        # Bound the exponent: a multi-day outage makes thousands of attempts (2.0**1024 overflows).
        exponent = min(self._attempt, 64)
        ceiling = min(self.cap, self.base * self.factor**exponent)
        self._attempt += 1
        half = ceiling / 2
        return half + self.rng.uniform(0, half)

    def reset(self) -> None:
        self._attempt = 0

    @property
    def attempt(self) -> int:
        return self._attempt
