"""Strategy runtime core (SM-014: own portfolio only; N5: stops on opens; RM-011 horizon)."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from tb_strategy_runtime.runner import Runner

from tb.common.v1 import common_pb2
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
