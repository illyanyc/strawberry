"""Pure core of the strategy runtime: registry, bar dispatch, own-portfolio tracking, and the
conversion between tbcore.strategy objects and contract messages. No I/O (unit-testable).

Rules: SM-011 (same strategy code as the backtester), SM-014 (a strategy sees only its own
virtual portfolio and data available at its decision time), N5/OE-014 (opens carry a stop).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from tb.common.v1 import common_pb2
from tb.ledger.v1 import ledger_pb2
from tb.marketdata.v1 import marketdata_pb2
from tb.orders.v1 import orders_pb2
from tb.signals.v1 import signals_pb2
from tbcore.data.calendar import Calendar, xnys
from tbcore.strategy import Bar, Intent, OpeningRangeBreakout, ORBParams, OwnPosition, Strategy, StrategyContext

TIMEFRAMES = {
    marketdata_pb2.TIMEFRAME_1M: "1m",
    marketdata_pb2.TIMEFRAME_5M: "5m",
    marketdata_pb2.TIMEFRAME_15M: "15m",
    marketdata_pb2.TIMEFRAME_30M: "30m",
    marketdata_pb2.TIMEFRAME_1H: "1h",
    marketdata_pb2.TIMEFRAME_1D: "1d",
}
SIDES = {"buy": common_pb2.SIDE_BUY, "sell": common_pb2.SIDE_SELL, "sell_short": common_pb2.SIDE_SELL_SHORT}
HORIZONS = {"intraday": common_pb2.HORIZON_INTRADAY, "swing": common_pb2.HORIZON_SWING}
TIFS = {"day": orders_pb2.TIME_IN_FORCE_DAY, "gtc": orders_pb2.TIME_IN_FORCE_GTC}
CLASSES = {"orb": lambda sid, params: OpeningRangeBreakout(sid, ORBParams.from_dict(params))}


def dec(d: common_pb2.Decimal) -> Decimal:
    return Decimal(d.value) if d.value else Decimal(0)


@dataclass(slots=True)
class Hosted:
    """One runtime-hosted strategy from the registry seed."""

    strategy_id: str
    account: str
    horizon: str
    mode: str
    timeframe: str
    instruments: list[str]
    allocation: Decimal
    impl: Strategy
    ctx: StrategyContext


@dataclass(slots=True)
class Runner:
    calendar: Calendar
    hosted: list[Hosted] = field(default_factory=list)
    bars_processed: int = 0
    intents_published: int = 0
    last_bar_available_at: int = 0

    @classmethod
    def from_registry(cls, path: str | Path, calendar: Calendar | None = None) -> Runner:
        cal = calendar or xnys()
        data: dict[str, Any] = json.loads(Path(path).read_text(encoding="utf-8"))
        r = cls(calendar=cal)
        for e in data.get("strategies", []):
            rt = e.get("runtime")
            if not rt or e.get("mode") not in ("paper", "shadow", "live"):
                continue
            make = CLASSES.get(rt.get("class", ""))
            if make is None:
                raise ValueError(f"strategy {e['strategy_id']}: unknown runtime class {rt.get('class')!r}")
            sid = e["strategy_id"]
            impl = make(sid, rt.get("params", {}))
            ctx = StrategyContext(
                strategy_id=sid,
                now=0,
                calendar=cal,
                allocation=Decimal(str(e.get("allocation", "0"))),
                params=rt.get("params", {}),
            )
            impl.on_start(ctx)
            r.hosted.append(
                Hosted(
                    strategy_id=sid,
                    account=e["broker_account_id"],
                    horizon=e.get("horizon", "intraday"),
                    mode=e.get("mode", "paper"),
                    timeframe=rt.get("timeframe", "15m"),
                    instruments=list(rt.get("instruments", [])),
                    allocation=ctx.allocation,
                    impl=impl,
                    ctx=ctx,
                )
            )
        return r

    def subjects(self) -> list[str]:
        out = sorted({f"md.bar.{h.timeframe}.{i}" for h in self.hosted for i in h.instruments})
        return out

    def accounts(self) -> list[str]:
        return sorted({h.account for h in self.hosted})

    def on_ledger_update(self, upd: ledger_pb2.LedgerUpdate) -> None:
        """Track each hosted strategy's own positions only (SM-014): other owners are ignored."""
        for p in upd.positions:
            for h in self.hosted:
                if p.owner == h.strategy_id and p.broker_account_id == h.account:
                    h.ctx.positions[p.instrument_id] = OwnPosition(
                        instrument_id=p.instrument_id,
                        qty=dec(p.qty),
                        cost_basis=dec(p.cost_basis),
                        realized_pnl=dec(p.realized_pnl),
                    )

    def on_bar(self, b: marketdata_pb2.Bar) -> list[orders_pb2.OrderIntent]:
        """Dispatch one final bar; the decision time is the bar's available_at (DF-002)."""
        if not b.is_final:
            return []
        tf = TIMEFRAMES.get(b.timeframe, "")
        bar = Bar(
            instrument_id=b.instrument_id,
            timeframe=tf,
            ts_open=b.ts_open,
            ts_close=b.ts_close,
            available_at=b.available_at,
            open=dec(b.open),
            high=dec(b.high),
            low=dec(b.low),
            close=dec(b.close),
            volume=dec(b.volume),
        )
        out: list[orders_pb2.OrderIntent] = []
        for h in self.hosted:
            if tf != h.timeframe or b.instrument_id not in h.instruments:
                continue
            h.ctx.now = b.available_at
            for it in h.impl.on_bar(h.ctx, bar):
                out.append(self.to_proto(h, it, b.available_at))
        self.bars_processed += 1
        self.last_bar_available_at = max(self.last_bar_available_at, b.available_at)
        self.intents_published += len(out)
        return out

    def to_proto(self, h: Hosted, it: Intent, decision_time: int) -> orders_pb2.OrderIntent:
        if it.kind == "open" and it.stop_loss is None:
            raise ValueError("open intents must carry a stop (N5)")
        mode = common_pb2.MODE_LIVE if h.mode == "live" else common_pb2.MODE_PAPER
        p = orders_pb2.OrderIntent(
            intent_id=it.intent_id,
            strategy_id=h.strategy_id,
            broker_account_id=h.account,
            instrument_id=it.instrument_id,
            side=SIDES[it.side],
            quantity=common_pb2.Decimal(value=str(it.qty)),
            order_type=orders_pb2.ORDER_TYPE_MARKET,
            tif=orders_pb2.TIME_IN_FORCE_DAY,
            reason=it.reason,
            mode=mode,
            correlation_id=it.correlation_id or it.intent_id,
            created_at=decision_time,
            horizon=HORIZONS[h.horizon],
            kind=orders_pb2.INTENT_KIND_OPEN if it.kind == "open" else orders_pb2.INTENT_KIND_CLOSE,
            source=orders_pb2.INTENT_SOURCE_STRATEGY,
            decision_time=decision_time,
            reference_price=common_pb2.Decimal(value=str(it.reference_price)),
        )
        if it.stop_loss is not None:
            p.protective.stop_loss_price.value = str(it.stop_loss)
            p.protective.tif = TIFS["gtc" if h.horizon == "swing" else it.protective_tif]
            if it.take_profit is not None:
                p.protective.take_profit_price.value = str(it.take_profit)
        return p

    def heartbeat(self, runtime_id: str, now_ns: int, version: str = "") -> signals_pb2.RuntimeHeartbeat:
        return signals_pb2.RuntimeHeartbeat(
            runtime_id=runtime_id,
            strategy_ids=[h.strategy_id for h in self.hosted],
            last_bar_available_at=self.last_bar_available_at,
            bars_processed=self.bars_processed,
            intents_published=self.intents_published,
            sent_at=now_ns,
            version=version,
        )
