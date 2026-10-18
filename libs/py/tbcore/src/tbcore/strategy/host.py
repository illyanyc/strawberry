"""Strategy host: the pure core of the strategy runtime, shared with the event-driven backtester.

Registry, bar dispatch, own-portfolio tracking, and the conversion between tbcore.strategy objects
and contract messages. No I/O. The live runtime (``services/strategy-runtime``) and the backtester
(``tbcore.backtest.event``) both dispatch through ``Runner``, so a backtest hosts strategies exactly
as production does (BT-002).

Rules: SM-011 (same strategy code as the backtester), SM-014 (a strategy sees only its own
virtual portfolio and data available at its decision time), N5/OE-014 (opens carry a stop).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from google.protobuf.json_format import MessageToDict

from tb.common.v1 import common_pb2
from tb.context.v1 import context_pb2
from tb.ledger.v1 import ledger_pb2
from tb.marketdata.v1 import marketdata_pb2
from tb.orders.v1 import orders_pb2
from tb.signals.v1 import signals_pb2
from tbcore.context.availability import earnings_actual_available_at, parse_timing
from tbcore.context.events import BlackoutStrategy, EventBlackout, InMemoryEventSource, ScheduledEvent
from tbcore.context.store import ContextStore
from tbcore.data.calendar import Calendar, xnys
from tbcore.strategy import Bar, Intent, OpeningRangeBreakout, ORBParams, OwnPosition, Strategy, StrategyContext
from tbcore.strategy.model_signal import (
    MetaFilterStrategy,
    ModelSignalParams,
    ModelSignalStrategy,
    ResidualReversal,
    ResidualReversalParams,
    score_source,
)

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
MODES = {
    "live": common_pb2.MODE_LIVE,
    "paper": common_pb2.MODE_PAPER,
    "shadow": common_pb2.MODE_PAPER,
    "backtest": common_pb2.MODE_BACKTEST,
}
StrategyFactory = Callable[[str, dict[str, object]], Strategy]


def _primary(name: str, sid: str, params: dict[str, object]) -> Strategy:
    if name == "orb":
        return OpeningRangeBreakout(sid, ORBParams.from_dict(params))
    if name == "residual_reversal":
        return ResidualReversal(sid, ResidualReversalParams.from_dict(dict(params)))
    raise ValueError(f"unknown primary rule {name!r}")


def _model_signal(sid: str, params: dict[str, object]) -> Strategy:
    return ModelSignalStrategy(
        sid,
        ModelSignalParams.from_dict(dict(params)),
        score_source(dict(params), list(params.get("universe", []))),  # type: ignore[call-overload]
    )


def _meta_filter(sid: str, params: dict[str, object]) -> Strategy:
    prim = _primary(str(params.get("primary_class", "orb")), sid, dict(params.get("primary_params", {})))  # type: ignore[call-overload]
    return MetaFilterStrategy(sid, prim, score_source(dict(params), list(params.get("universe", []))))  # type: ignore[call-overload]


CLASSES: dict[str, StrategyFactory] = {
    "orb": lambda sid, params: OpeningRangeBreakout(sid, ORBParams.from_dict(params)),
    # Section 10 model strategies (ML-040..ML-042) and the swing primary rule (ML-016).
    "model_signal": _model_signal,
    "meta_filter": _meta_filter,
    "residual_reversal": lambda sid, params: ResidualReversal(sid, ResidualReversalParams.from_dict(dict(params))),
}


def register_class(name: str, factory: StrategyFactory) -> None:
    """Register a runtime strategy class (the registry's ``runtime.class``)."""
    if name in CLASSES and CLASSES[name] is not factory:
        raise ValueError(f"runtime class {name!r} already registered")
    CLASSES[name] = factory


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


def scheduled_events(ev: context_pb2.ContextEvent) -> list[ScheduledEvent]:
    """Calendar and earnings context events as blackout events (CX-016); others yield nothing."""
    p: dict[str, Any] = MessageToDict(ev.payload) if ev.HasField("payload") else {}
    pv = ev.provenance
    if ev.kind == context_pb2.CONTEXT_KIND_CALENDAR:
        return [
            ScheduledEvent(
                "calendar",
                str(p.get("event", "")),
                at=pv.event_time,
                available_at=pv.available_at,
                category=str(p.get("category", "")),
                impact=str(p.get("impact", "")),
                country=str(p.get("country", "")),
                record_key=ev.record_key,
            )
        ]
    if ev.kind == context_pb2.CONTEXT_KIND_EARNINGS and p.get("report_date"):
        at = earnings_actual_available_at(
            date.fromisoformat(str(p["report_date"])), parse_timing(str(p.get("timing", "")))
        )
        return [
            ScheduledEvent(
                "earnings",
                f"earnings {ev.record_key}",
                at=at,
                available_at=pv.available_at,
                instrument_id=i,
                record_key=f"{ev.record_key}|{i}",
            )
            for i in ev.instrument_ids
        ]
    return []


@dataclass(slots=True)
class Runner:
    calendar: Calendar
    hosted: list[Hosted] = field(default_factory=list)
    events: InMemoryEventSource = field(default_factory=InMemoryEventSource)
    bars_processed: int = 0
    intents_published: int = 0
    last_bar_available_at: int = 0
    # Mode stamped on intents; None = the strategy's registry mode (live -> LIVE, otherwise PAPER).
    intent_mode: str | None = None

    @classmethod
    def from_registry(cls, path: str | Path, calendar: Calendar | None = None) -> Runner:
        data: dict[str, Any] = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.from_registry_data(data, calendar)

    @classmethod
    def from_registry_data(
        cls, data: dict[str, Any], calendar: Calendar | None = None, intent_mode: str | None = None
    ) -> Runner:
        """Host the registry's runtime strategies in mode paper, shadow, or live (the seed format of
        ``infra/compose/strategies.json``)."""
        cal = calendar or xnys()
        r = cls(calendar=cal, intent_mode=intent_mode)
        for e in data.get("strategies", []):
            rt = e.get("runtime")
            if not rt or e.get("mode") not in ("paper", "shadow", "live"):
                continue
            make = CLASSES.get(rt.get("class", ""))
            if make is None:
                raise ValueError(f"strategy {e['strategy_id']}: unknown runtime class {rt.get('class')!r}")
            sid = e["strategy_id"]
            impl: Strategy = make(sid, rt.get("params", {}))
            if rt.get("blackout"):
                # CX-016: no new positions around scheduled events; the same wrapper runs in backtests.
                impl = BlackoutStrategy(impl, EventBlackout.from_dict(rt["blackout"]), r.events)
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

    def uses_events(self) -> bool:
        return any(isinstance(h.impl, BlackoutStrategy) for h in self.hosted)

    def on_context_event(self, ev: context_pb2.ContextEvent) -> None:
        self.events.extend(scheduled_events(ev))

    def load_events(self, store: ContextStore, now_ns: int, horizon_days: int = 45) -> int:
        """Seed the event source from the lake: every stored calendar/earnings version around now
        (the bus keeps only 7 days, so schedules published weeks ahead come from here)."""
        n = 0
        lo, hi = now_ns - 2 * 86_400 * 10**9, now_ns + horizon_days * 86_400 * 10**9
        insts = sorted({i for h in self.hosted for i in h.instruments})
        for kind in ("calendar", "earnings"):
            df = store.versions(kind, None, insts if kind == "earnings" else None)
            for r in df.iter_rows(named=True):
                ev = context_pb2.ContextEvent(
                    kind=context_pb2.CONTEXT_KIND_CALENDAR if kind == "calendar" else context_pb2.CONTEXT_KIND_EARNINGS,
                    record_key=r["record_key"],
                    instrument_ids=[r["instrument_id"]] if r["instrument_id"] else [],
                )
                ev.provenance.event_time = int(r["event_time"].timestamp() * 1e6) * 1000
                ev.provenance.available_at = int(r["available_at"].timestamp() * 1e6) * 1000
                ev.payload.update(
                    {
                        k: r[k]
                        for k in ("event", "category", "impact", "country", "report_date", "timing")
                        if r.get(k) is not None
                    }
                )
                for se in scheduled_events(ev):
                    if lo <= se.at <= hi:
                        self.events.add(se)
                        n += 1
        return n

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
        """Dispatch one final bar from the bus; the decision time is the bar's available_at (DF-002)."""
        if not b.is_final:
            return []
        return self.dispatch(bar_from_proto(b))

    def dispatch(self, bar: Bar) -> list[orders_pb2.OrderIntent]:
        """Run every hosted strategy on one final bar at its ``available_at`` (BT-002, SM-014)."""
        out: list[orders_pb2.OrderIntent] = []
        for h in self.hosted:
            if bar.timeframe != h.timeframe or bar.instrument_id not in h.instruments:
                continue
            h.ctx.now = bar.available_at
            for it in h.impl.on_bar(h.ctx, bar):
                out.append(self.to_proto(h, it, bar.available_at))
        self.bars_processed += 1
        self.last_bar_available_at = max(self.last_bar_available_at, bar.available_at)
        self.intents_published += len(out)
        return out

    def to_proto(self, h: Hosted, it: Intent, decision_time: int) -> orders_pb2.OrderIntent:
        if it.kind == "open" and it.stop_loss is None:
            raise ValueError("open intents must carry a stop (N5)")
        mode = (
            MODES[self.intent_mode]
            if self.intent_mode
            else (common_pb2.MODE_LIVE if h.mode == "live" else common_pb2.MODE_PAPER)
        )
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


def bar_from_proto(b: marketdata_pb2.Bar) -> Bar:
    """Convert a bus bar into the strategy API bar (prices and volume as decimals)."""
    return Bar(
        instrument_id=b.instrument_id,
        timeframe=TIMEFRAMES.get(b.timeframe, ""),
        ts_open=b.ts_open,
        ts_close=b.ts_close,
        available_at=b.available_at,
        open=dec(b.open),
        high=dec(b.high),
        low=dec(b.low),
        close=dec(b.close),
        volume=dec(b.volume),
    )
