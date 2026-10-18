"""Strategy runtime core (SM-014: own portfolio only; N5: stops on opens; RM-011 horizon)."""

from __future__ import annotations

import asyncio
import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest
import tb_strategy_runtime.__main__ as entry
from tb_strategy_runtime.runner import Runner

from tb.common.v1 import common_pb2
from tb.context.v1 import context_pb2
from tb.ledger.v1 import ledger_pb2
from tb.marketdata.v1 import marketdata_pb2
from tb.orders.v1 import orders_pb2
from tbcore.data.calendar import xnys

NS = 1_000_000_000


def registry(tmp_path: Path) -> Path:
    p = tmp_path / "strategies.json"
    p.write_text(
        json.dumps(
            {
                "strategies": [
                    {
                        "strategy_id": "orb",
                        "short_id": "orbaaaa",
                        "horizon": "intraday",
                        "broker_account_id": "sim-paper",
                        "mode": "paper",
                        "allocation": "10000",
                        "runtime": {"class": "orb", "timeframe": "15m", "instruments": ["I1"], "params": {}},
                    },
                    {
                        "strategy_id": "off",
                        "short_id": "offaaaa",
                        "horizon": "intraday",
                        "broker_account_id": "sim-paper",
                        "mode": "disabled",
                        "allocation": "1",
                        "runtime": {"class": "orb", "timeframe": "15m", "instruments": ["I1"], "params": {}},
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    return p


def bar(i: int, o: str, h: str, lo: str, c: str) -> marketdata_pb2.Bar:
    s = xnys().session(date(2026, 10, 14))
    assert s is not None
    start = s.open + timedelta(minutes=15 * i)
    end = start + timedelta(minutes=15)
    d = common_pb2.Decimal
    return marketdata_pb2.Bar(
        instrument_id="I1",
        timeframe=marketdata_pb2.TIMEFRAME_15M,
        ts_open=int(start.timestamp()) * NS,
        ts_close=int(end.timestamp()) * NS,
        available_at=int(end.timestamp() + 60) * NS,
        open=d(value=o),
        high=d(value=h),
        low=d(value=lo),
        close=d(value=c),
        volume=d(value="1"),
        is_final=True,
    )


def test_runner_hosts_active_strategies_and_publishes_protected_intents(tmp_path: Path) -> None:
    r = Runner.from_registry(registry(tmp_path))
    assert [h.strategy_id for h in r.hosted] == ["orb"]
    assert r.subjects() == ["md.bar.15m.I1"] and r.accounts() == ["sim-paper"]
    assert r.on_bar(bar(0, "100", "101", "99", "100.5")) == []
    out = r.on_bar(bar(1, "100.5", "101.5", "100.4", "101.4"))
    assert len(out) == 1
    it = out[0]
    assert (
        it.kind == orders_pb2.INTENT_KIND_OPEN
        and it.side == common_pb2.SIDE_BUY
        and it.horizon == common_pb2.HORIZON_INTRADAY
    )
    assert it.protective.stop_loss_price.value and it.protective.take_profit_price.value
    assert it.decision_time == bar(1, "0", "0", "0", "0").available_at
    hb = r.heartbeat("rt", 1)
    assert hb.bars_processed == 2 and hb.intents_published == 1 and hb.last_bar_available_at == it.decision_time


def test_runner_sees_only_its_own_positions_sm014(tmp_path: Path) -> None:
    r = Runner.from_registry(registry(tmp_path))
    d = common_pb2.Decimal
    upd = ledger_pb2.LedgerUpdate(
        broker_account_id="sim-paper",
        positions=[
            ledger_pb2.StrategyPosition(
                broker_account_id="sim-paper", owner="manual", instrument_id="I1", qty=d(value="100")
            ),
            ledger_pb2.StrategyPosition(
                broker_account_id="sim-paper", owner="other", instrument_id="I1", qty=d(value="7")
            ),
        ],
    )
    r.on_ledger_update(upd)
    assert r.hosted[0].ctx.position("I1") == 0
    upd.positions.append(
        ledger_pb2.StrategyPosition(broker_account_id="sim-paper", owner="orb", instrument_id="I1", qty=d(value="5"))
    )
    r.on_ledger_update(upd)
    assert r.hosted[0].ctx.position("I1") == 5


class _FakeJS:
    def __init__(self) -> None:
        self.subs: list[tuple[str, Any]] = []

    async def subscribe(self, subject: str, **kw: Any) -> None:
        self.subs.append((subject, kw.get("stream")))


class _FakeNC:
    def __init__(self) -> None:
        self.js = _FakeJS()

    def jetstream(self) -> _FakeJS:
        return self.js

    async def publish(self, *_: Any, **__: Any) -> None:
        return None

    async def drain(self) -> None:
        return None


def test_every_jetstream_subscription_names_its_stream(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The runtime's NATS user may not look streams up ($JS.API.STREAM.NAMES); unnamed subscribes time out."""
    nc = _FakeNC()

    async def connect(**_: Any) -> _FakeNC:
        return nc

    monkeypatch.setattr(entry.nats, "connect", connect)
    stop = asyncio.Event()
    stop.set()
    asyncio.run(entry.run(Runner.from_registry(registry(tmp_path)), "nats://x", "u", "p", "rt", 1.0, stop))
    assert nc.js.subs == [("ledger.update.sim-paper", "LEDGER"), ("md.bar.15m.I1", "MARKETDATA")]


def blackout_registry(tmp_path: Path) -> Path:
    p = tmp_path / "strategies.json"
    entry_ = json.loads(registry(tmp_path).read_text())["strategies"][0]
    entry_["runtime"]["blackout"] = {"kinds": ["CPI"], "before_minutes": 30, "after_minutes": 120}
    p.write_text(json.dumps({"strategies": [entry_]}), encoding="utf-8")
    return p


def cpi_event(at_ns: int, available_ns: int) -> context_pb2.ContextEvent:
    ev = context_pb2.ContextEvent(kind=context_pb2.CONTEXT_KIND_CALENDAR, record_key="US|CPI YoY|x", source="fmp")
    ev.provenance.event_time, ev.provenance.available_at = at_ns, available_ns
    ev.payload.update({"event": "CPI YoY", "category": "CPI", "impact": "High", "country": "US"})
    return ev


def test_cx016_event_blackout_blocks_cpi_entries_in_paper(tmp_path: Path) -> None:
    """Acceptance (paper): the runtime drops the ORB entry inside a CPI blackout delivered on
    ctx.calendar, and opens normally without it. Exits are never blocked."""
    s = xnys().session(date(2026, 10, 14))
    assert s is not None
    release = int((s.open - timedelta(hours=1)).timestamp()) * NS  # 08:30 New York
    plain = Runner.from_registry(blackout_registry(tmp_path))
    plain.on_bar(bar(0, "100", "101", "99", "100.5"))
    assert len(plain.on_bar(bar(1, "100.5", "101.5", "100.4", "101.4"))) == 1
    r = Runner.from_registry(blackout_registry(tmp_path))
    assert r.uses_events()
    r.on_context_event(cpi_event(release, release - 7 * 86_400 * NS))
    r.on_bar(bar(0, "100", "101", "99", "100.5"))
    assert r.on_bar(bar(1, "100.5", "101.5", "100.4", "101.4")) == []
    assert r.hosted[0].impl.blocked  # type: ignore[attr-defined]


def test_cx016_blackout_unknown_at_decision_time_does_not_block(tmp_path: Path) -> None:
    s = xnys().session(date(2026, 10, 14))
    assert s is not None
    release = int((s.open - timedelta(hours=1)).timestamp()) * NS
    r = Runner.from_registry(blackout_registry(tmp_path))
    r.on_context_event(cpi_event(release, int(s.close.timestamp()) * NS))  # published after the decision
    r.on_bar(bar(0, "100", "101", "99", "100.5"))
    assert len(r.on_bar(bar(1, "100.5", "101.5", "100.4", "101.4"))) == 1
