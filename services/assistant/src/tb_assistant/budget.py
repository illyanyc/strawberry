"""Experiment budget for the analyst loop (AN-020, AN-004).

Per lineage: trials per round, CPU hours per day, concurrent jobs, rounds per loop, and loop wall
clock. The optimizer separately enforces the lineage's analyst trial/CPU budget and the cycle's
MinBTL budget (EX-025, EX-036); this guard refuses a launch before it is sent, so a plan can never
spend past either limit, and alerts once at 80 % of each limit.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime

from tb.analyst.v1 import analyst_pb2


class BudgetExceededError(RuntimeError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass
class LineageUsage:
    round_id: str = ""
    trials_this_round: int = 0
    day: str = ""
    cpu_hours_today: float = 0.0
    running_jobs: set[str] = field(default_factory=set)
    rounds: int = 0
    loop_started_at: datetime | None = None
    llm_usd: float = 0.0


def default_budget(
    *,
    max_trials_per_round: int = 5,
    max_cpu_hours_per_day: float = 8.0,
    max_concurrent_jobs: int = 2,
    max_rounds: int = 10,
    max_wall_clock_hours: float = 24.0,
    max_llm_usd_per_day: float = 5.0,
    max_llm_usd_per_month: float = 50.0,
) -> analyst_pb2.Budget:
    return analyst_pb2.Budget(
        max_trials_per_round=max_trials_per_round,
        max_cpu_hours_per_day=max_cpu_hours_per_day,
        max_concurrent_jobs=max_concurrent_jobs,
        max_rounds=max_rounds,
        max_wall_clock_hours=max_wall_clock_hours,
        max_llm_usd_per_day=max_llm_usd_per_day,
        max_llm_usd_per_month=max_llm_usd_per_month,
    )


class BudgetGuard:
    def __init__(
        self,
        clock: Callable[[], datetime],
        on_alert: Callable[[str, str], Awaitable[None]] | None = None,
        alert_fraction: float = 0.8,
    ) -> None:
        self._clock = clock
        self._on_alert = on_alert
        self._frac = alert_fraction
        self.usage: dict[str, LineageUsage] = {}
        self._alerted: set[str] = set()

    def _u(self, lineage_id: str) -> LineageUsage:
        u = self.usage.setdefault(lineage_id, LineageUsage())
        day = self._clock().strftime("%Y-%m-%d")
        if u.day != day:
            u.day, u.cpu_hours_today = day, 0.0
        return u

    def start_loop(self, lineage_id: str) -> None:
        u = self._u(lineage_id)
        if u.loop_started_at is None:
            u.loop_started_at = self._clock()
            u.rounds = 0

    def reset_loop(self, lineage_id: str) -> None:
        u = self._u(lineage_id)
        u.loop_started_at, u.rounds = None, 0

    def begin_round(self, lineage_id: str, round_id: str) -> None:
        u = self._u(lineage_id)
        self.start_loop(lineage_id)
        u.round_id, u.trials_this_round = round_id, 0
        u.rounds += 1

    def rounds_exhausted(self, lineage_id: str, budget: analyst_pb2.Budget) -> bool:
        u = self._u(lineage_id)
        if budget.max_rounds and u.rounds >= budget.max_rounds:
            return True
        if budget.max_wall_clock_hours and u.loop_started_at is not None:
            hours = (self._clock() - u.loop_started_at).total_seconds() / 3600
            return hours >= budget.max_wall_clock_hours
        return False

    def check(self, lineage_id: str, budget: analyst_pb2.Budget, *, trials: int, cpu_hours: float) -> None:
        """AN-020: refuse a launch whose estimate would exceed any per-lineage limit."""
        u = self._u(lineage_id)
        if budget.max_trials_per_round and u.trials_this_round + trials > budget.max_trials_per_round:
            raise BudgetExceededError(
                f"trials per round {u.trials_this_round}+{trials} > {budget.max_trials_per_round}"
            )
        if budget.max_cpu_hours_per_day and u.cpu_hours_today + cpu_hours > budget.max_cpu_hours_per_day:
            raise BudgetExceededError(
                f"CPU hours today {u.cpu_hours_today:.2f}+{cpu_hours:.2f} > {budget.max_cpu_hours_per_day}"
            )
        if budget.max_concurrent_jobs and len(u.running_jobs) >= budget.max_concurrent_jobs:
            raise BudgetExceededError(f"concurrent jobs {len(u.running_jobs)} >= {budget.max_concurrent_jobs}")
        if self.rounds_exhausted(lineage_id, budget) and u.rounds > budget.max_rounds:
            raise BudgetExceededError("loop round or wall-clock budget spent")

    async def record_launch(
        self, lineage_id: str, budget: analyst_pb2.Budget, *, job_ids: list[str], trials: int, cpu_hours: float
    ) -> None:
        u = self._u(lineage_id)
        u.trials_this_round += trials
        u.cpu_hours_today += cpu_hours
        u.running_jobs.update(job_ids)
        await self._maybe_alert(lineage_id, "trials_per_round", u.trials_this_round, budget.max_trials_per_round)
        await self._maybe_alert(lineage_id, "cpu_hours_per_day", u.cpu_hours_today, budget.max_cpu_hours_per_day)
        await self._maybe_alert(lineage_id, "rounds", u.rounds, budget.max_rounds)

    def record_done(self, lineage_id: str, job_id: str) -> None:
        self._u(lineage_id).running_jobs.discard(job_id)

    def running(self, lineage_id: str) -> set[str]:
        return set(self._u(lineage_id).running_jobs)

    def usage_proto(self, lineage_id: str) -> analyst_pb2.BudgetUsage:
        u = self._u(lineage_id)
        wall = (self._clock() - u.loop_started_at).total_seconds() if u.loop_started_at else 0.0
        return analyst_pb2.BudgetUsage(
            trials=u.trials_this_round, cpu_hours=u.cpu_hours_today, wall_clock_seconds=wall, llm_usd=u.llm_usd
        )

    async def _maybe_alert(self, lineage_id: str, name: str, used: float, cap: float) -> None:
        if not cap or used < self._frac * cap:
            return
        u = self._u(lineage_id)
        key = f"{lineage_id}:{name}:{u.day}:{u.round_id if name == 'trials_per_round' else ''}"
        if key in self._alerted:
            return
        self._alerted.add(key)
        if self._on_alert:
            await self._on_alert(lineage_id, f"analyst budget {name} at {used / cap:.0%} ({used:g} of {cap:g})")
