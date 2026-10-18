"""Daily reports (section 13 ops slice): the pre-market checklist and the end-of-day summary per
account and strategy, built from bus events only (execution's trading status heartbeat,
reconciliation results, risk decisions, fills, ledger updates). Pure and unit-tested."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal

from tb.ledger.v1 import ledger_pb2
from tb.orders.v1 import execution_pb2, orders_pb2


@dataclass(slots=True)
class Day:
    """What the monitor saw during one session date."""

    date: str
    fills: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    fees: dict[str, Decimal] = field(default_factory=lambda: defaultdict(Decimal))
    rejects: dict[str, dict[str, int]] = field(default_factory=lambda: defaultdict(lambda: defaultdict(int)))
    approved: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    realized_start: dict[str, Decimal] = field(default_factory=dict)
    realized: dict[str, Decimal] = field(default_factory=dict)
    recon: dict[str, str] = field(default_factory=dict)
    breaks: dict[str, int] = field(default_factory=dict)
    alerts: dict[str, int] = field(default_factory=lambda: defaultdict(int))


class Reports:
    def __init__(self) -> None:
        self.status: execution_pb2.TradingStatus | None = None
        self.day: Day | None = None

    def start(self, date: str) -> Day:
        if self.day is None or self.day.date != date:
            prev = self.day
            self.day = Day(date=date)
            if prev is not None:
                self.day.realized_start = dict(prev.realized)
                self.day.realized = dict(prev.realized)
        return self.day

    def on_status(self, st: execution_pb2.TradingStatus) -> None:
        self.status = st

    def on_recon(self, date: str, r: ledger_pb2.ReconciliationResult) -> None:
        d = self.start(date)
        d.recon[r.broker_account_id] = ledger_pb2.ReconciliationStatus.Name(r.status).removeprefix(
            "RECONCILIATION_STATUS_"
        )
        d.breaks[r.broker_account_id] = len(r.breaks)

    def on_decision(self, date: str, dec: orders_pb2.RiskDecision) -> None:
        d = self.start(date)
        sid = dec.strategy_id or "manual"
        if dec.approved:
            d.approved[sid] += 1
        else:
            d.rejects[sid][dec.first_failure or "unknown"] += 1

    def on_fill(self, date: str, x: orders_pb2.ExecutionReport) -> None:
        d = self.start(date)
        d.fills[x.broker_account_id] += 1
        if x.fees.value:
            d.fees[x.broker_account_id] += Decimal(x.fees.value)

    def on_ledger_update(self, date: str, upd: ledger_pb2.LedgerUpdate) -> None:
        d = self.start(date)
        for p in upd.positions:
            d.realized[f"{p.broker_account_id}/{p.owner}|{p.instrument_id}"] = Decimal(p.realized_pnl.value or "0")

    def on_alert(self, date: str, severity: str) -> None:
        self.start(date).alerts[severity] += 1

    def premarket(self, date: str) -> tuple[bool, str]:
        """The pre-market checklist: brokers ready, reconciliation clean, no latched kill switch or
        halt, clock OK, strategies loaded. Returns (all_ok, text)."""
        st = self.status
        lines = [f"Pre-market checklist {date}"]
        if st is None:
            return False, "\n".join([*lines, "FAIL execution heartbeat not seen"])
        ok = True
        for a in st.accounts:
            ready = a.session_state == "SESSION_STATE_READY"
            clean = a.reconciliation_status == "RECONCILIATION_STATUS_OK"
            ok = ok and ready and clean
            mark = "OK  " if ready and clean else "FAIL"
            session = a.session_state.removeprefix("SESSION_STATE_")
            recon = a.reconciliation_status.removeprefix("RECONCILIATION_STATUS_")
            lines.append(
                f"{mark} {a.broker_account_id} ({a.environment}): session {session}, recon {recon}, "
                f"PDT guard {a.pdt_guard}"
            )
        if not st.accounts:
            ok = False
            lines.append("FAIL no broker accounts")
        lines.append(f"{'OK  ' if st.clock_ok else 'FAIL'} clock skew within threshold")
        ok = ok and st.clock_ok
        if st.active_kill_switches:
            ok = False
            lines.append(f"FAIL {len(st.active_kill_switches)} kill switch(es) latched")
        if st.active_halts:
            ok = False
            lines.append(f"FAIL {len(st.active_halts)} halt(s) active")
        active = [
            s for s in st.strategies if s.mode in (execution_pb2.STRATEGY_MODE_PAPER, execution_pb2.STRATEGY_MODE_LIVE)
        ]
        lines.append(f"{'OK  ' if active else 'WARN'} {len(active)} strategy(ies) in paper/live; limits loaded")
        return ok, "\n".join(lines)

    def eod(self, date: str) -> str:
        d = self.start(date)
        lines = [f"End-of-day summary {date}"]
        for acct in sorted(set(d.fills) | set(d.recon)):
            lines.append(
                f"{acct}: {d.fills.get(acct, 0)} fills, fees {d.fees.get(acct, Decimal(0))}, "
                f"recon {d.recon.get(acct, 'n/a')}, open breaks {d.breaks.get(acct, 0)}"
            )
        owners: dict[str, Decimal] = defaultdict(Decimal)
        start: dict[str, Decimal] = defaultdict(Decimal)
        for k, v in d.realized.items():
            if "|" in k:
                owners[k.split("|")[0]] += v
        for k, v in d.realized_start.items():
            if "|" in k:
                start[k.split("|")[0]] += v
        for owner in sorted(owners):
            lines.append(f"{owner}: realized P&L today {owners[owner] - start.get(owner, Decimal(0))}")
        for sid in sorted(set(d.approved) | set(d.rejects)):
            rej = ", ".join(f"{k} {v}" for k, v in sorted(d.rejects.get(sid, {}).items())) or "none"
            lines.append(f"{sid}: {d.approved.get(sid, 0)} approved intents, rejects: {rej}")
        lines.append("alerts: " + (", ".join(f"{k} {v}" for k, v in sorted(d.alerts.items())) or "none"))
        return "\n".join(lines)
