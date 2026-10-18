"""Event-driven backtester (section 08, ADR-0031, BT-001..BT-008, BT-020..BT-024).

The engine owns the clock, the data, and the fill decisions. Strategies run through the live
strategy host (``tbcore.strategy.host.Runner``); every intent goes to ``tb-simcore``, which runs the
production OMS engine, pre-trade gate, and ledger accounting core. Fills come from the shared fill
and cost models, or, in ``recorded`` mode, from the executions of a bus recording (parity).

Event order at one instant: fill bars that closed, session closes, recorded and manual executions,
and corporate actions are applied first (oldest first); then the OMS time-driven work runs; then
the decision bars available at that instant are dispatched to strategies.
"""

from __future__ import annotations

import math
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import numpy as np
import polars as pl

from tb.backtest.v1 import simcore_pb2
from tb.broker.v1 import gateway_pb2
from tb.common.v1 import common_pb2
from tb.ledger.v1 import ledger_pb2
from tb.orders.v1 import execution_pb2, orders_pb2
from tbcore.backtest.config import BacktestConfig
from tbcore.backtest.costs import borrow_cost, fees, to_fee, to_price
from tbcore.backtest.fills import FillBar, WorkingOrder, match_bar
from tbcore.backtest.recording import Recording
from tbcore.backtest.result import BacktestResult
from tbcore.backtest.simcore import SimCore
from tbcore.context.events import BlackoutStrategy, ScheduledEvent
from tbcore.context.store import ContextStore
from tbcore.data.calendar import TIMEFRAMES, Session, xnys
from tbcore.strategy.api import Bar
from tbcore.strategy.host import TIMEFRAMES as TIMEFRAME_NAMES
from tbcore.strategy.host import Runner

NS = 1_000_000_000
MINUTE = 60 * NS
FINALITY_NS = 60 * NS
ROLE_NAMES = {
    "ORDER_ROLE_ENTRY": "entry",
    "ORDER_ROLE_EXIT": "exit",
    "ORDER_ROLE_PROTECTIVE_STOP": "stop",
    "ORDER_ROLE_TAKE_PROFIT": "take_profit",
}
KINDS = {
    orders_pb2.ORDER_TYPE_MARKET: "market",
    orders_pb2.ORDER_TYPE_LIMIT: "limit",
    orders_pb2.ORDER_TYPE_STOP: "stop",
}
FINAL = {
    orders_pb2.ORDER_STATE_FILLED,
    orders_pb2.ORDER_STATE_CANCELED,
    orders_pb2.ORDER_STATE_EXPIRED,
    orders_pb2.ORDER_STATE_REJECTED,
    orders_pb2.ORDER_STATE_REPLACED,
}
HORIZONS = {"intraday": common_pb2.HORIZON_INTRADAY, "swing": common_pb2.HORIZON_SWING}
BROKERS = {"alpaca": common_pb2.BROKER_ALPACA, "ibkr": common_pb2.BROKER_IBKR, "simulator": common_pb2.BROKER_SIMULATOR}


class LeakageError(RuntimeError):
    """A datum would reach a strategy before its ``available_at`` (BT-004, DF-002)."""


def _d(v: Decimal | str | float) -> common_pb2.Decimal:
    return common_pb2.Decimal(value=str(v))


def _dec(x: common_pb2.Decimal) -> Decimal:
    return Decimal(x.value) if x.value else Decimal(0)


_PX: dict[float, Decimal] = {}


def _px(x: float) -> Decimal:
    """Float price/volume from the lake as the decimal its shortest repr denotes (memoized)."""
    d = _PX.get(x)
    if d is None:
        if len(_PX) > 1_000_000:
            _PX.clear()
        d = _PX[x] = Decimal(repr(float(x)))
    return d


def _decimals(x: np.ndarray) -> list[Decimal]:
    """Per-row decimals of a float column, converting each distinct value once."""
    uniq, inv = np.unique(x, return_inverse=True)
    vals = [Decimal(repr(float(u))) for u in uniq]
    return [vals[i] for i in inv.tolist()]


def _rows_by_instrument(f: pl.DataFrame) -> dict[str, np.ndarray]:
    """Row positions of each instrument in a frame sorted by close time."""
    g = f.with_row_index("_r").group_by("instrument_id").agg(pl.col("_r"))
    return {
        str(i): np.asarray(r, dtype=np.int64)
        for i, r in zip(g["instrument_id"].to_list(), g["_r"].to_list(), strict=True)
    }


def _ns_col(df: pl.DataFrame, col: str) -> pl.Expr:
    if df.schema[col] == pl.Int64:
        return pl.col(col)
    return pl.col(col).dt.cast_time_unit("ns").dt.replace_time_zone("UTC").cast(pl.Int64)


def normalize_bars(df: pl.DataFrame) -> pl.DataFrame:
    """Bars with int64 ns times and float prices, sorted by (available_at, instrument_id)."""
    cols = ["instrument_id", "ts_open", "ts_close", "available_at", "open", "high", "low", "close", "volume"]
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise ValueError(f"bars are missing columns {missing}")
    out = df.select(
        pl.col("instrument_id").cast(pl.Utf8),
        *[_ns_col(df, c).alias(c) for c in ("ts_open", "ts_close", "available_at")],
        *[pl.col(c).cast(pl.Float64) for c in ("open", "high", "low", "close", "volume")],
    )
    bad = out.filter(pl.col("available_at") < pl.col("ts_close"))
    if bad.height:
        raise LeakageError(f"{bad.height} bars are available before they close (BT-004)")
    return out.sort(["available_at", "instrument_id", "ts_open"])


def fill_frame(df: pl.DataFrame, lookback: int) -> pl.DataFrame:
    """Fill bars with minute-of-session (open/close regime aware of early closes) and the impact
    inputs known before each session: ADV and daily volatility over ``lookback`` prior sessions."""
    cal = xnys()
    local = pl.col("ts_open").cast(pl.Datetime("ns", "UTC")).dt.convert_time_zone("America/New_York")
    df = df.sort(["instrument_id", "ts_open"]).with_columns(local.dt.date().alias("session_date"))
    dates = sorted(set(df["session_date"].to_list()))
    closes = {}
    opens = {}
    for d in dates:
        s = cal.session(d)
        if s is not None:
            opens[d] = int(s.open.timestamp()) * NS
            closes[d] = int(s.close.timestamp()) * NS
    sess = pl.DataFrame(
        {"session_date": list(opens), "_open": list(opens.values()), "_close": [closes[d] for d in opens]},
        schema={"session_date": pl.Date, "_open": pl.Int64, "_close": pl.Int64},
    )
    df = df.join(sess, on="session_date", how="left")
    since = (pl.col("ts_open") - pl.col("_open")) // MINUTE
    to_close = (pl.col("_close") - pl.col("ts_open")) // MINUTE
    df = df.with_columns(
        pl.when(
            pl.col("_open").is_null() | (pl.col("ts_open") < pl.col("_open")) | (pl.col("ts_open") >= pl.col("_close"))
        )
        .then(-1)
        .when(to_close < since)
        .then(390 - to_close)
        .otherwise(since)
        .cast(pl.Int32)
        .alias("minute_of_session")
    )
    daily = (
        df.group_by(["instrument_id", "session_date"])
        .agg(pl.col("volume").sum().alias("_vol"), pl.col("close").last().alias("_close_px"))
        .sort(["instrument_id", "session_date"])
        .with_columns((pl.col("_close_px") / pl.col("_close_px").shift(1).over("instrument_id")).log().alias("_ret"))
        .with_columns(
            pl.col("_vol").shift(1).rolling_mean(lookback, min_samples=2).over("instrument_id").alias("adv"),
            pl.col("_ret").shift(1).rolling_std(lookback, min_samples=2).over("instrument_id").alias("daily_vol"),
        )
        .select("instrument_id", "session_date", "adv", "daily_vol")
    )
    return (
        df.join(daily, on=["instrument_id", "session_date"], how="left")
        .with_columns(pl.col("adv").fill_null(float("nan")), pl.col("daily_vol").fill_null(float("nan")))
        .sort(["ts_close", "instrument_id"])
    )


@dataclass(frozen=True)
class CorporateActionEvent:
    instrument_id: str
    kind: str
    value: str
    source_id: str


SideEvent = Session | orders_pb2.ExecutionReport | CorporateActionEvent


@dataclass
class _Position:
    qty: Decimal = Decimal(0)
    cost: Decimal = Decimal(0)
    realized: Decimal = Decimal(0)


@dataclass
class _Episode:
    strategy_id: str
    instrument_id: str
    side: int
    entry_time: int
    realized_at_open: Decimal
    fills: list[dict[str, object]] = field(default_factory=list)
    borrow: Decimal = Decimal(0)


class EventEngine:
    def __init__(
        self,
        config: BacktestConfig,
        bars: pl.DataFrame,
        fill_bars: pl.DataFrame | None = None,
        corporate_actions: pl.DataFrame | None = None,
        recording: Recording | None = None,
        scheduled_events: Iterable[ScheduledEvent] | None = None,
        context_store: ContextStore | None = None,
    ) -> None:
        self.cfg = config
        self.cal = xnys()
        start = (
            int(config.start.timestamp() * NS)
            if config.start.tzinfo
            else int(config.start.replace(tzinfo=UTC).timestamp() * NS)
        )
        end = (
            int(config.end.timestamp() * NS)
            if config.end.tzinfo
            else int(config.end.replace(tzinfo=UTC).timestamp() * NS)
        )
        self.start_ns, self.end_ns = start, end
        hosted = {i for s in config.strategies for i in s.instruments}
        universe = set(config.universe.instruments) | hosted
        b = normalize_bars(bars).filter(
            (pl.col("ts_open") >= start) & (pl.col("ts_open") < end) & pl.col("instrument_id").is_in(list(universe))
        )
        self.bars = b
        fb = normalize_bars(fill_bars) if fill_bars is not None else b
        fb = fb.filter(
            (pl.col("ts_open") >= start) & (pl.col("ts_open") < end) & pl.col("instrument_id").is_in(list(universe))
        )
        self.fill = fill_frame(fb, config.fill_model.impact_lookback_sessions)
        self.recording = recording
        self.ca = corporate_actions
        self.runner = Runner.from_registry_data(config.registry(), self.cal, intent_mode="backtest")
        # Event blackouts (CX-016): the source is point-in-time (versions apply from their
        # available_at), so it can be seeded with the whole window up front.
        n_events = 0
        if scheduled_events is not None:
            evs = list(scheduled_events)
            self.runner.events.extend(evs)
            n_events += len(evs)
        if context_store is not None:
            n_events += self.runner.load_events(context_store, start, horizon_days=(end - start) // (86_400 * NS) + 2)
        self.warnings: list[str] = []
        self.clock = 0
        self.working: dict[str, WorkingOrder] = {}
        self.by_instr: dict[str, list[WorkingOrder]] = {}
        self.order_rows: dict[str, dict[str, object]] = {}
        self.intent_rows: list[dict[str, object]] = []
        self.intent_kind: dict[str, str] = {}
        self.fill_rows: list[dict[str, object]] = []
        self.trade_rows: list[dict[str, object]] = []
        self.equity_rows: list[dict[str, object]] = []
        self.daily_rows: list[dict[str, object]] = []
        self.positions: dict[tuple[str, str], _Position] = {}
        self.open_pos: dict[str, set[str]] = {}
        self.realized_sum: dict[str, Decimal] = {}
        self.episodes: dict[tuple[str, str], _Episode] = {}
        self.last_bar: dict[str, tuple[float, int, int]] = {}  # instrument -> close, ts_close, interval
        self.alloc = {s.strategy_id: s.allocation for s in config.strategies}
        self.halts: dict[str, list[tuple[int, int]]] = {h.instrument_id: [] for h in config.account.halts}
        for h in config.account.halts:
            self.halts[h.instrument_id].append((h.start, h.end))
        self._sess_cache = (0, 0, 0)
        self.fallback_impacts = 0
        self.unmatched_recorded: list[str] = []
        self.valuation_mismatch = 0
        self.borrow_total: dict[str, Decimal] = {}
        self.delisted: list[str] = []
        tf_width = int(TIMEFRAMES[config.fill_model.fill_timeframe or config.timeframe].total_seconds() * NS)
        if tf_width > FINALITY_NS + config.fill_model.latency_ms * 1_000_000 and config.fill_model.mode == "model":
            self.warnings.append(
                f"coarse_fills: {config.fill_model.fill_timeframe or config.timeframe} fill bars fill at the open "
                "of the bar containing the arrival, up to one bar before it (BT-020)"
            )
        if self.runner.uses_events() and n_events == 0:
            self.warnings.append(
                "blackout_without_events: a strategy has an event blackout but no scheduled events (CX-016)"
            )
        if config.universe.survivorship != "complete":
            self.warnings.append(
                f"survivorship: universe {config.universe.name} is {config.universe.survivorship} (U5, O3, BT-005)"
            )

    # ---- simcore plumbing

    def _setup(self, sc: SimCore) -> None:
        a = self.cfg.account
        acct = simcore_pb2.SimAccount(
            broker_account_id=a.broker_account_id,
            broker=BROKERS[a.broker],
            cash=_d(a.cash),
            supports_bracket=a.supports_bracket,
            margin_multiple=_d(a.margin_multiple),
            margin_util_cap=_d(a.margin_util_cap),
            pdt_guard=a.pdt_guard,
            pdt_max=3,
            segregation=a.segregation,
            dedicated=a.dedicated,
            opening_positions=[
                ledger_pb2.Position(instrument_id=p.instrument_id, qty=_d(p.qty), avg_cost=_d(p.avg_cost))
                for p in a.manual_positions
            ],
        )
        routes: dict[str, set[str]] = {}
        for s in self.cfg.strategies:
            routes.setdefault(s.horizon, set()).add(s.broker_account_id)
        instruments = sorted(
            set(self.cfg.universe.instruments) | {i for s in self.cfg.strategies for i in s.instruments}
        )
        setup = simcore_pb2.SimSetup(
            accounts=[acct],
            registry_json=self.cfg.registry_json(),
            routing=[
                simcore_pb2.SimRoute(horizon=HORIZONS[h], broker_account_ids=sorted(v))
                for h, v in sorted(routes.items())
            ],
            instruments=[
                simcore_pb2.SimInstrument(
                    instrument_id=i,
                    symbol=self.cfg.universe.symbols.get(i, i[-6:]),
                    tradable=True,
                    shortable=i in a.shortable,
                )
                for i in instruments
            ],
            start=self.clock,
            flatten_before_close_ns=a.flatten_before_close_s * NS,
        )
        if self.cfg.risk_limits:
            setup.global_limits.CopyFrom(_limits(self.cfg.risk_limits))
        sc.call(simcore_pb2.SimRequest(setup=setup))

    def _now(self, t: int) -> int:
        self.clock = max(self.clock, t)
        return self.clock

    def _sim_bars(self, instruments: Iterable[str]) -> list[simcore_pb2.SimBar]:
        out = []
        for i in sorted(set(instruments)):
            lb = self.last_bar.get(i)
            if lb is not None:
                out.append(simcore_pb2.SimBar(instrument_id=i, close=_d(_px(lb[0])), ts_close=lb[1], interval_ns=lb[2]))
        return out

    def _active_instruments(self) -> set[str]:
        held = {k[1] for k, p in self.positions.items() if p.qty != 0}
        return held | set(self.by_instr)

    def _handle(self, resp: simcore_pb2.SimResponse, arrival: int) -> None:
        for upd in resp.ledger_updates:
            self._on_ledger(upd)
        for bo in resp.orders:
            self._on_order(bo, arrival)
            for leg in bo.legs:
                self._on_order(leg, arrival)

    def _role(self, b: gateway_pb2.BrokerOrder) -> str:
        if b.parent_broker_order_id:
            return "stop" if b.type == orders_pb2.ORDER_TYPE_STOP else "take_profit"
        parts = b.client_order_id.split("-")
        attempt = int(parts[-1]) if len(parts) == 4 and parts[-1].isdigit() else 1
        if attempt >= 50 and b.type == orders_pb2.ORDER_TYPE_STOP:
            return "stop"
        kind = self.intent_kind.get(parts[2] if len(parts) == 4 else "", "INTENT_KIND_CLOSE")
        return "entry" if kind == "INTENT_KIND_OPEN" else "exit"

    def _on_order(self, b: gateway_pb2.BrokerOrder, arrival: int) -> None:
        oid = b.broker_order_id
        row = self.order_rows.get(oid)
        parts = b.client_order_id.split("-")
        if row is None:
            row = {
                "broker_order_id": oid,
                "client_order_id": b.client_order_id,
                "intent_id": parts[2] if len(parts) == 4 else "",
                "role": self._role(b),
                "instrument_id": b.instrument.instrument_id,
                "side": "buy" if b.side == common_pb2.SIDE_BUY else "sell",
                "kind": KINDS.get(b.type, "other"),
                "qty": b.qty.value,
                "limit": float(b.limit_price.value) if b.limit_price.value else None,
                "stop": float(b.stop_price.value) if b.stop_price.value else None,
                "tif": orders_pb2.TimeInForce.Name(b.tif).removeprefix("TIME_IN_FORCE_").lower(),
                "parent": b.parent_broker_order_id,
                "created_at": self.clock,
                "state": "",
                "filled": "0",
                "ended_at": 0,
            }
            self.order_rows[oid] = row
        row["state"] = orders_pb2.OrderState.Name(b.state).removeprefix("ORDER_STATE_").lower()
        row["filled"] = b.filled_qty.value or "0"
        if b.state in FINAL:
            row["ended_at"] = self.clock
            w = self.working.pop(oid, None)
            if w is not None:
                lst = self.by_instr.get(w.instrument_id, [])
                if w in lst:
                    lst.remove(w)
                if not lst:
                    self.by_instr.pop(w.instrument_id, None)
            return
        held = b.broker_status == "held"
        w = self.working.get(oid)
        if w is None:
            w = WorkingOrder(
                broker_order_id=oid,
                client_order_id=b.client_order_id,
                account=b.broker_account_id,
                instrument_id=b.instrument.instrument_id,
                side=1 if b.side == common_pb2.SIDE_BUY else -1,
                qty=_dec(b.qty),
                kind=KINDS.get(b.type, "market"),
                tif="gtc" if b.tif == orders_pb2.TIME_IN_FORCE_GTC else "day",
                arrival=arrival,
                role=str(row["role"]),
                limit=float(b.limit_price.value) if b.limit_price.value else None,
                stop=float(b.stop_price.value) if b.stop_price.value else None,
                filled=_dec(b.filled_qty),
                held=held,
                parent=b.parent_broker_order_id,
                intent_id=str(row["intent_id"]),
                extended=b.extended_hours,
            )
            self.working[oid] = w
            self.by_instr.setdefault(w.instrument_id, []).append(w)
            if w.parent:
                sibs = [x for x in self.working.values() if x.parent == w.parent and x is not w]
                for s in sibs:
                    s.siblings.append(oid)
                    w.siblings.append(s.broker_order_id)
        else:
            if w.held and not held:
                w.arrival = max(w.arrival, self.clock)
            w.held = held
            w.qty = _dec(b.qty)
            w.filled = _dec(b.filled_qty)

    def _on_ledger(self, upd: ledger_pb2.LedgerUpdate) -> None:
        self.runner.on_ledger_update(upd)
        for p in upd.positions:
            if p.owner not in self.alloc:
                continue
            key = (p.owner, p.instrument_id)
            pos = self.positions.setdefault(key, _Position())
            before = pos.qty
            realized = _dec(p.realized_pnl)
            self.realized_sum[p.owner] = self.realized_sum.get(p.owner, Decimal(0)) + realized - pos.realized
            pos.qty, pos.cost, pos.realized = _dec(p.qty), _dec(p.cost_basis), realized
            if pos.qty != 0:
                self.open_pos.setdefault(p.owner, set()).add(p.instrument_id)
            else:
                self.open_pos.get(p.owner, set()).discard(p.instrument_id)
            # A trade is a flat-to-flat episode; an opening fill realizes nothing, so the realized P&L
            # after it is the baseline the episode's P&L is measured from (the ledger core's FIFO).
            if before == 0 and pos.qty != 0:
                self.episodes[key] = _Episode(
                    p.owner, p.instrument_id, 1 if pos.qty > 0 else -1, self.clock, pos.realized
                )
            elif before != 0 and pos.qty == 0 and key in self.episodes:
                self._close_episode(key, pos.realized)

    def _close_episode(self, key: tuple[str, str], realized: Decimal) -> None:
        ep = self.episodes.pop(key)
        entries = [f for f in ep.fills if f["role"] == "entry"]
        exits = [f for f in ep.fills if f["role"] != "entry"]
        qty = sum((Decimal(str(f["qty"])) for f in entries), Decimal(0))
        exit_qty = sum((Decimal(str(f["qty"])) for f in exits), Decimal(0))

        def avg(fs: list[dict[str, object]], q: Decimal) -> float:
            return float(sum(Decimal(str(f["qty"])) * Decimal(str(f["price"])) for f in fs) / q) if q else math.nan

        fee = sum((Decimal(str(f["fees"])) for f in ep.fills), Decimal(0)) + ep.borrow
        slip = sum((float(str(f["slippage_cost"])) for f in ep.fills), 0.0)
        pnl = realized - ep.realized_at_open
        self.trade_rows.append(
            {
                "strategy_id": ep.strategy_id,
                "instrument_id": ep.instrument_id,
                "side": "long" if ep.side > 0 else "short",
                "entry_time": ep.entry_time,
                "exit_time": self.clock,
                "qty": float(qty),
                "entry_price": avg(entries, qty),
                "exit_price": avg(exits, exit_qty),
                "pnl": float(pnl),
                "gross_pnl": float(pnl + fee),
                "fees": float(fee),
                "slippage_cost": slip,
                "exit_reason": str(exits[-1]["role"]) if exits else "",
                "entry_intent": str(entries[0]["intent_id"]) if entries else "",
            }
        )

    # ---- fills

    def _fill(
        self,
        sc: SimCore,
        w: WorkingOrder,
        qty: Decimal,
        price: Decimal,
        fee: Decimal,
        at: int,
        reference: float,
        slip_bps: float,
        reason: str,
    ) -> None:
        now = self._now(max(at, w.arrival))
        cum = w.filled + qty
        resp = sc.call(
            simcore_pb2.SimRequest(
                fill=simcore_pb2.SimFill(
                    now=now,
                    broker_account_id=w.account,
                    broker_order_id=w.broker_order_id,
                    qty=_d(qty),
                    price=_d(price),
                    fees=_d(fee),
                    exec_id=f"{w.broker_order_id}:{cum}",
                )
            )
        )
        owner = next(
            (s.strategy_id for s in self.cfg.strategies if f"tb1-{s.short_id}-" in w.client_order_id), "manual"
        )
        row: dict[str, object] = {
            "exec_id": f"{w.broker_order_id}:{cum}",
            "broker_order_id": w.broker_order_id,
            "client_order_id": w.client_order_id,
            "intent_id": w.intent_id,
            "strategy_id": owner,
            "role": w.role,
            "instrument_id": w.instrument_id,
            "side": "buy" if w.side > 0 else "sell",
            "qty": float(qty),
            "price": float(price),
            "reference": reference,
            "slippage_bps": slip_bps,
            "slippage_cost": float(abs(price - _px(reference)) * qty),
            "fees": float(fee),
            "time": now,
            "reason": reason,
        }
        self.fill_rows.append(row)
        key = (owner, w.instrument_id)
        existed = key in self.episodes
        if existed:
            self.episodes[key].fills.append(row)
        self._handle(resp, now)
        if not existed and key in self.episodes:
            self.episodes[key].fills.append(row)

    def _match_fill_bar(self, sc: SimCore, i: int) -> None:
        inst = self._fi[i]
        orders = self.by_instr.get(inst)
        if not orders:
            return
        halted = any(s < self._fclose[i] and e > self._fopen[i] for s, e in self.halts.get(inst, []))
        bar = FillBar(
            inst,
            int(self._fopen[i]),
            int(self._fclose[i]),
            float(self._fo[i]),
            float(self._fh[i]),
            float(self._fl[i]),
            float(self._fc[i]),
            float(self._fv[i]),
            int(self._fm[i]),
            float(self._fadv[i]),
            float(self._fdv[i]),
            halted=halted,
        )
        after_open = False
        for _ in range(4):
            active = [w for w in self.by_instr.get(inst, []) if not w.held]
            decisions = match_bar(active, bar, self.cfg.fill_model, self.cfg.cost_model, after_open=after_open)
            if not decisions:
                return
            for d in decisions:
                if d.order.broker_order_id not in self.working or d.qty <= 0 or d.qty > d.order.remaining:
                    continue
                px = to_price(d.price)
                fee = to_fee(float(fees(self.cfg.cost_model, d.order.side, float(d.qty), float(px), bar.ts_open)))
                self.fallback_impacts += int(d.impact_fallback)
                self._fill(sc, d.order, d.qty, px, fee, bar.ts_open, d.reference, d.slippage_bps, d.reason)
            after_open = True

    def _recorded_fill(self, sc: SimCore, x: orders_pb2.ExecutionReport, roles: dict[str, tuple[str, str]]) -> None:
        coid, role = roles.get(x.broker_order_id, (x.client_order_id, ""))
        want = ROLE_NAMES.get(role, "")
        cands = [
            w
            for w in self.working.values()
            if w.client_order_id == coid and not w.held and (not want or w.role == want)
        ]
        if not cands:
            self.unmatched_recorded.append(f"{x.broker_exec_id}:{coid}:{want}")
            return
        w = cands[0]
        qty = min(_dec(x.qty), w.remaining)
        self._fill(sc, w, qty, _dec(x.price), _dec(x.fees), x.event_time, float(_dec(x.price)), 0.0, "recorded")

    # ---- loop

    def run(self, simcore: SimCore) -> BacktestResult:
        t0 = time.perf_counter()
        b = self.bars
        self.clock = (int(b["available_at"].to_numpy().min()) if b.height else self.start_ns) - MINUTE
        self._setup(simcore)
        f = self.fill
        self._fi = f["instrument_id"].to_list()
        self._fopen, self._fclose = f["ts_open"].to_numpy(), f["ts_close"].to_numpy()
        self._fo, self._fh, self._fl, self._fc, self._fv = (
            f[c].to_numpy() for c in ("open", "high", "low", "close", "volume")
        )
        self._fm, self._fadv, self._fdv = (
            f["minute_of_session"].to_numpy(),
            f["adv"].to_numpy(),
            f["daily_vol"].to_numpy(),
        )
        inst = b["instrument_id"].to_list()
        tso, tsc, av = b["ts_open"].to_numpy(), b["ts_close"].to_numpy(), b["available_at"].to_numpy()
        o, h, lo, c, v = (b[col].to_numpy() for col in ("open", "high", "low", "close", "volume"))
        do_, dh, dl, dc, dv = (_decimals(x) for x in (o, h, lo, c, v))
        recorded = (
            {
                (x.instrument_id, x.ts_open): x
                for x in self.recording.bars
                if TIMEFRAME_NAMES.get(x.timeframe) == self.cfg.timeframe
            }
            if self.recording is not None
            else {}
        )
        tf = self.cfg.timeframe
        interval = int(TIMEFRAMES[tf].total_seconds() * NS)
        events = self._side_events()
        roles = self.recording.order_roles() if self.recording is not None else {}
        latency = self.cfg.fill_model.latency_ms * 1_000_000
        n, i, fi, ei = b.height, 0, 0, 0
        dispatched = 0

        model = self.cfg.fill_model.mode == "model"
        fclose = self._fclose
        rows_by_inst = _rows_by_instrument(f)

        def next_working_row(start: int) -> int | None:
            best: int | None = None
            for instr in self.by_instr:
                rows = rows_by_inst.get(instr)
                if rows is None:
                    continue
                k = int(np.searchsorted(rows, start))
                if k < len(rows) and (best is None or int(rows[k]) < best):
                    best = int(rows[k])
            return best

        def process_until(t: int) -> None:
            # Fill bars of instruments without working orders cannot change anything, so only bars
            # of instruments with working orders are visited, merged in time order with the side
            # events; a fill bar closing at the same instant as a side event is matched first.
            nonlocal fi, ei
            while True:
                ne = events[ei][0] if ei < len(events) else None
                row = next_working_row(fi) if model and self.by_instr else None
                nf = int(fclose[row]) if row is not None else None
                if row is not None and nf is not None and nf <= t and (ne is None or nf <= ne):
                    self._match_fill_bar(simcore, row)
                    fi = row + 1
                    continue
                if ne is not None and ne <= t:
                    if nf is None or nf > ne:
                        fi = max(fi, int(np.searchsorted(fclose, ne, side="right")))
                    _, _, kind, payload = events[ei]
                    ei += 1
                    self._side_event(simcore, kind, payload, roles)
                    continue
                fi = max(fi, int(np.searchsorted(fclose, t, side="right")))
                return

        while i < n:
            t = int(av[i])
            process_until(t)
            j = i
            while j < n and int(av[j]) == t:
                j += 1
            now = self._now(t)
            for k in range(i, j):
                self.last_bar[inst[k]] = (float(c[k]), int(tsc[k]), interval)
            if self._tick_due(now):
                resp = simcore.call(
                    simcore_pb2.SimRequest(
                        advance=simcore_pb2.SimAdvance(now=now, bars=self._sim_bars(self._active_instruments()))
                    )
                )
                self._handle(resp, now + latency)
            for k in range(i, j):
                if int(av[k]) < int(tsc[k]) or int(av[k]) > now:
                    raise LeakageError(f"bar {inst[k]}@{int(tso[k])} dispatched at {now} (BT-004)")
                pb = recorded.get((inst[k], int(tso[k])))
                if pb is not None:
                    # A recorded bus bar goes through the runtime's own path (on_bar), unchanged.
                    intents = self.runner.on_bar(pb)
                else:
                    intents = self.runner.dispatch(
                        Bar(inst[k], tf, int(tso[k]), int(tsc[k]), int(av[k]), do_[k], dh[k], dl[k], dc[k], dv[k])
                    )
                for it in intents:
                    self._submit(simcore, it, now, latency)
                dispatched += 1
            self._record_equity(now)
            i = j
        process_until(self.end_ns)
        manual = simcore.call(
            simcore_pb2.SimRequest(
                query=simcore_pb2.SimQuery(owner="manual", broker_account_id=self.cfg.account.broker_account_id)
            )
        ).portfolio
        stats = {
            "manual_positions": {p.instrument_id: p.qty.value for p in manual.positions},
            "bars_dispatched": dispatched,
            "fill_bars": len(self._fi),
            "simcore_requests": simcore.requests,
            "elapsed_s": round(time.perf_counter() - t0, 3),
        }
        return self._result(stats)

    def _submit(self, sc: SimCore, it: orders_pb2.OrderIntent, now: int, latency: int) -> None:
        self.intent_kind[it.intent_id] = orders_pb2.IntentKind.Name(it.kind)
        resp = sc.call(
            simcore_pb2.SimRequest(
                submit=simcore_pb2.SimSubmit(now=now, intent=it, bars=self._sim_bars([it.instrument_id]))
            )
        )
        d = resp.decision
        self.intent_rows.append(
            {
                "intent_id": it.intent_id,
                "strategy_id": it.strategy_id,
                "instrument_id": it.instrument_id,
                "side": common_pb2.Side.Name(it.side).removeprefix("SIDE_").lower(),
                "kind": orders_pb2.IntentKind.Name(it.kind).removeprefix("INTENT_KIND_").lower(),
                "qty": it.quantity.value,
                "reference_price": it.reference_price.value,
                "stop_loss": it.protective.stop_loss_price.value,
                "take_profit": it.protective.take_profit_price.value,
                "decision_time": it.decision_time,
                "approved": d.approved,
                "first_failure": d.first_failure,
                "checks": "; ".join(f"{c.name}: {c.value} vs {c.limit}" for c in d.checks if not c.passed),
            }
        )
        self._handle(resp, now + latency)

    def _side_events(self) -> list[tuple[int, int, str, SideEvent]]:
        """Session closes, recorded/manual executions, and corporate actions as (time, seq, kind, payload)."""
        ev: list[tuple[int, int, str, SideEvent]] = []
        start = datetime.fromtimestamp(self.start_ns / NS, tz=UTC)
        end = datetime.fromtimestamp(self.end_ns / NS, tz=UTC)
        for s in self.cal.sessions(start, end):
            ev.append((int(s.close.timestamp()) * NS, 0, "close", s))
        if self.recording is not None:
            for x in self.recording.executions:
                platform = x.client_order_id.startswith("tb1-")
                if platform and self.cfg.fill_model.mode == "recorded":
                    ev.append((x.event_time, 2, "recorded_fill", x))
                elif not platform:
                    ev.append((x.event_time, 1, "manual_fill", x))
        if self.ca is not None and self.ca.height:
            for r in self.ca.iter_rows(named=True):
                kind = str(r["type"])
                if kind not in ("split", "dividend", "delisting"):
                    continue
                # BT-008: the delisting price is the last traded price when the event is processed.
                val = r["ratio"] if kind == "split" else (r["amount"] if kind == "dividend" else "")
                ex = int(str(r["ex_date"]))
                src = str(r.get("source_id") or f"ca:{r['instrument_id']}:{ex}")
                ev.append(
                    (ex, 1, "corporate_action", CorporateActionEvent(str(r["instrument_id"]), kind, str(val), src))
                )
        ev.sort(key=lambda e: (e[0], e[1]))
        return ev

    def _side_event(self, sc: SimCore, kind: str, payload: SideEvent, roles: dict[str, tuple[str, str]]) -> None:
        if isinstance(payload, orders_pb2.ExecutionReport):
            if kind == "recorded_fill":
                self._recorded_fill(sc, payload, roles)
                return
            now = self._now(payload.event_time)
            resp = sc.call(
                simcore_pb2.SimRequest(
                    manual_fill=simcore_pb2.SimManualFill(
                        now=now,
                        broker_account_id=self.cfg.account.broker_account_id,
                        instrument_id=payload.instrument_id,
                        side=payload.side,
                        qty=payload.qty,
                        price=payload.price,
                        fees=payload.fees,
                        exec_id=payload.broker_exec_id,
                    )
                )
            )
            self._handle(resp, now)
        elif isinstance(payload, CorporateActionEvent):
            now = self._now(self._session_open_after(self.clock))
            if payload.kind == "delisting":
                self._delist(sc, payload, now)
                return
            resp = sc.call(
                simcore_pb2.SimRequest(
                    corporate_action=simcore_pb2.SimCorporateAction(
                        now=now,
                        instrument_id=payload.instrument_id,
                        kind=payload.kind,
                        value=_d(payload.value),
                        source_id=payload.source_id,
                    )
                )
            )
            self._handle(resp, now)
        else:
            self._session_close(sc, payload)

    def _delist(self, sc: SimCore, ca: CorporateActionEvent, now: int) -> None:
        """BT-008: a delisted instrument's working orders end (the broker cancels them) and every open
        position in it is closed at the last traded price times (1 - delisting_haircut), with the
        ledger core's cash flow; the trade's exit reason is ``delisting``."""
        last = self.last_bar.get(ca.instrument_id)
        if last is None:
            self.warnings.append(
                f"delisting_without_price: {ca.instrument_id} had no bar before {ca.source_id} (BT-008)"
            )
            return
        price = to_price(last[0] * (1.0 - self.cfg.fill_model.delisting_haircut))
        for w in sorted(self.by_instr.get(ca.instrument_id, []), key=lambda x: x.broker_order_id):
            if w.broker_order_id in self.working:
                resp = sc.call(
                    simcore_pb2.SimRequest(
                        order_end=simcore_pb2.SimOrderEnd(
                            now=now,
                            broker_account_id=w.account,
                            broker_order_id=w.broker_order_id,
                            state=orders_pb2.ORDER_STATE_CANCELED,
                        )
                    )
                )
                self._handle(resp, now)
        for (sid, inst), pos in self.positions.items():
            ep = self.episodes.get((sid, inst))
            if inst == ca.instrument_id and pos.qty != 0 and ep is not None:
                ep.fills.append(
                    {
                        "role": "delisting",
                        "qty": float(abs(pos.qty)),
                        "price": float(price),
                        "fees": 0.0,
                        "slippage_cost": 0.0,
                        "intent_id": "",
                    }
                )
        self.delisted.append(ca.instrument_id)
        resp = sc.call(
            simcore_pb2.SimRequest(
                corporate_action=simcore_pb2.SimCorporateAction(
                    now=now, instrument_id=ca.instrument_id, kind="delisting", value=_d(price), source_id=ca.source_id
                )
            )
        )
        self._handle(resp, now)

    def _charge_borrow(self, sc: SimCore, s: Session, now: int) -> None:
        """BT-014: a short held over a session close pays borrow for the calendar days until the next
        session (Friday → Monday is 3) on its notional at the close, at the account's per-instrument
        rate or the cost model's default, scaled by ``cost_multiplier``. Intraday shorts pay nothing."""
        nxt = self.cal.next_open(s.close)
        days = (nxt.astimezone(UTC).date() - s.date).days if nxt else 1
        for st in self.cfg.strategies:
            for inst in sorted(self.open_pos.get(st.strategy_id, ())):
                pos = self.positions[(st.strategy_id, inst)]
                if pos.qty >= 0 or inst not in self.last_bar:
                    continue
                notional = float(abs(pos.qty)) * self.last_bar[inst][0]
                rate = self.cfg.account.borrow_fee_bps.get(inst)
                fee = to_fee(float(borrow_cost(self.cfg.cost_model, notional, max(days, 1), rate)))
                if fee <= 0:
                    continue
                resp = sc.call(
                    simcore_pb2.SimRequest(
                        fee=simcore_pb2.SimFee(
                            now=now,
                            broker_account_id=st.broker_account_id,
                            owner=st.strategy_id,
                            instrument_id=inst,
                            amount=_d(fee),
                            source_id=f"borrow:{st.strategy_id}:{inst}:{s.date.isoformat()}",
                        )
                    )
                )
                self.borrow_total[st.strategy_id] = self.borrow_total.get(st.strategy_id, Decimal(0)) + fee
                ep = self.episodes.get((st.strategy_id, inst))
                if ep is not None:
                    ep.borrow += fee
                self._handle(resp, now)

    def _session_close(self, sc: SimCore, s: Session) -> None:
        now = self._now(int(s.close.timestamp()) * NS)
        for w in sorted(self.working.values(), key=lambda x: x.broker_order_id):
            if w.tif == "day" and not w.parent and w.broker_order_id in self.working:
                resp = sc.call(
                    simcore_pb2.SimRequest(
                        order_end=simcore_pb2.SimOrderEnd(
                            now=now,
                            broker_account_id=w.account,
                            broker_order_id=w.broker_order_id,
                            state=orders_pb2.ORDER_STATE_EXPIRED,
                        )
                    )
                )
                self._handle(resp, now)
        resp = sc.call(
            simcore_pb2.SimRequest(
                advance=simcore_pb2.SimAdvance(now=now, bars=self._sim_bars(self._active_instruments()))
            )
        )
        self._handle(resp, now)
        self._charge_borrow(sc, s, now)
        self._record_daily(sc, s.date.isoformat(), now)

    def _tick_due(self, t: int) -> bool:
        """Whether the OMS time-driven work can act at ``t``: the intraday flatten window before the
        close (OE-016); every submit carries the clock and marks, so other instants need no tick."""
        lo, hi, close = self._sess_cache
        if not lo <= t < hi:
            s, _ = self.cal.session_at(datetime.fromtimestamp(t / NS, tz=UTC))
            if s is None:
                return False
            lo, hi, close = (
                int(s.pre_open.timestamp()) * NS,
                int(s.post_close.timestamp()) * NS,
                int(s.close.timestamp()) * NS,
            )
            self._sess_cache = (lo, hi, close)
        return t >= close - self.cfg.account.flatten_before_close_s * NS - MINUTE

    def _session_open_after(self, t: int) -> int:
        nxt = self.cal.next_open(datetime.fromtimestamp(t / NS, tz=UTC))
        return int(nxt.timestamp()) * NS if nxt else t

    # ---- valuation and results

    def _equity(self, sid: str) -> tuple[Decimal, Decimal]:
        """Allocation + realized + unrealized at the latest closes: the ledger core's owner equity."""
        eq = self.alloc[sid] + self.realized_sum.get(sid, Decimal(0))
        gross = Decimal(0)
        for inst in self.open_pos.get(sid, ()):
            p = self.positions[(sid, inst)]
            mark = _px(self.last_bar[inst][0]) if inst in self.last_bar else p.cost / p.qty
            eq += p.qty * mark - p.cost
            gross += abs(p.qty * mark)
        return eq, gross

    def _record_equity(self, t: int) -> None:
        for s in self.cfg.strategies:
            eq, gross = self._equity(s.strategy_id)
            self.equity_rows.append(
                {"time": t, "strategy_id": s.strategy_id, "equity": float(eq), "gross_exposure": float(gross)}
            )

    def _record_daily(self, sc: SimCore, date: str, t: int) -> None:
        for s in self.cfg.strategies:
            pv = sc.call(
                simcore_pb2.SimRequest(
                    query=simcore_pb2.SimQuery(owner=s.strategy_id, broker_account_id=s.broker_account_id)
                )
            ).portfolio
            eq = _dec(pv.equity)
            mine, _ = self._equity(s.strategy_id)
            if abs(eq - mine) > Decimal("0.01"):
                self.valuation_mismatch += 1
            self.daily_rows.append(
                {
                    "date": date,
                    "time": t,
                    "strategy_id": s.strategy_id,
                    "equity": float(eq),
                    "realized_pnl": float(_dec(pv.realized_pnl)),
                    "unrealized_pnl": float(_dec(pv.unrealized_pnl)),
                    "fees": float(_dec(pv.fees)),
                    "day_pnl": float(_dec(pv.day_pnl)),
                    "gross_exposure": float(_dec(pv.gross_exposure)),
                }
            )

    def _result(self, stats: dict[str, Any]) -> BacktestResult:
        trades = (
            pl.DataFrame(self.trade_rows, schema=TRADE_SCHEMA) if self.trade_rows else pl.DataFrame(schema=TRADE_SCHEMA)
        )
        if trades.height < self.cfg.min_trades:
            self.warnings.append(f"low_trade_count: {trades.height} trades < {self.cfg.min_trades} (BT-005)")
        if self.fallback_impacts:
            self.warnings.append(
                f"impact_fallback: {self.fallback_impacts} fills priced with the fallback impact (BT-012)"
            )
        if self.unmatched_recorded:
            self.warnings.append(f"unmatched_recorded_fills: {len(self.unmatched_recorded)} (BT-070)")
        if self.valuation_mismatch:
            self.warnings.append(f"valuation_mismatch: {self.valuation_mismatch} session closes (BT-I4)")
        blocked = sum(len(h.impl.blocked) for h in self.runner.hosted if isinstance(h.impl, BlackoutStrategy))
        stats = {
            **stats,
            "unmatched_recorded": list(self.unmatched_recorded),
            "open_episodes": len(self.episodes),
            "blackout_blocked": blocked,
            "borrow_cost": {k: float(v) for k, v in sorted(self.borrow_total.items())},
            "delisted": list(self.delisted),
        }
        return BacktestResult(
            config=self.cfg,
            engine="event",
            intents=_frame(self.intent_rows, INTENT_SCHEMA),
            orders=_frame(sorted(self.order_rows.values(), key=lambda r: str(r["broker_order_id"])), ORDER_SCHEMA),
            fills=_frame(self.fill_rows, FILL_SCHEMA),
            trades=trades,
            equity=_frame(
                self.equity_rows,
                {"time": pl.Int64, "strategy_id": pl.Utf8, "equity": pl.Float64, "gross_exposure": pl.Float64},
            ),
            daily=_frame(self.daily_rows, DAILY_SCHEMA),
            warnings=self.warnings,
            stats=stats,
        )


TRADE_SCHEMA = {
    "strategy_id": pl.Utf8,
    "instrument_id": pl.Utf8,
    "side": pl.Utf8,
    "entry_time": pl.Int64,
    "exit_time": pl.Int64,
    "qty": pl.Float64,
    "entry_price": pl.Float64,
    "exit_price": pl.Float64,
    "pnl": pl.Float64,
    "gross_pnl": pl.Float64,
    "fees": pl.Float64,
    "slippage_cost": pl.Float64,
    "exit_reason": pl.Utf8,
    "entry_intent": pl.Utf8,
}
INTENT_SCHEMA = {
    "intent_id": pl.Utf8,
    "strategy_id": pl.Utf8,
    "instrument_id": pl.Utf8,
    "side": pl.Utf8,
    "kind": pl.Utf8,
    "qty": pl.Utf8,
    "reference_price": pl.Utf8,
    "stop_loss": pl.Utf8,
    "take_profit": pl.Utf8,
    "decision_time": pl.Int64,
    "approved": pl.Boolean,
    "first_failure": pl.Utf8,
    "checks": pl.Utf8,
}
ORDER_SCHEMA = {
    "broker_order_id": pl.Utf8,
    "client_order_id": pl.Utf8,
    "intent_id": pl.Utf8,
    "role": pl.Utf8,
    "instrument_id": pl.Utf8,
    "side": pl.Utf8,
    "kind": pl.Utf8,
    "qty": pl.Utf8,
    "limit": pl.Float64,
    "stop": pl.Float64,
    "tif": pl.Utf8,
    "parent": pl.Utf8,
    "created_at": pl.Int64,
    "state": pl.Utf8,
    "filled": pl.Utf8,
    "ended_at": pl.Int64,
}
FILL_SCHEMA = {
    "exec_id": pl.Utf8,
    "broker_order_id": pl.Utf8,
    "client_order_id": pl.Utf8,
    "intent_id": pl.Utf8,
    "strategy_id": pl.Utf8,
    "role": pl.Utf8,
    "instrument_id": pl.Utf8,
    "side": pl.Utf8,
    "qty": pl.Float64,
    "price": pl.Float64,
    "reference": pl.Float64,
    "slippage_bps": pl.Float64,
    "slippage_cost": pl.Float64,
    "fees": pl.Float64,
    "time": pl.Int64,
    "reason": pl.Utf8,
}
DAILY_SCHEMA = {
    "date": pl.Utf8,
    "time": pl.Int64,
    "strategy_id": pl.Utf8,
    "equity": pl.Float64,
    "realized_pnl": pl.Float64,
    "unrealized_pnl": pl.Float64,
    "fees": pl.Float64,
    "day_pnl": pl.Float64,
    "gross_exposure": pl.Float64,
}


def _frame(
    rows: list[dict[str, object]], schema: dict[str, pl.DataType] | dict[str, type[pl.DataType]]
) -> pl.DataFrame:
    return pl.DataFrame(rows, schema=schema, orient="row") if rows else pl.DataFrame(schema=schema)


def _limits(d: dict[str, str]) -> execution_pb2.RiskLimits:
    lim = execution_pb2.RiskLimits()
    fields = execution_pb2.RiskLimits.DESCRIPTOR.fields_by_name
    for k, v in d.items():
        if k not in fields:
            raise ValueError(f"unknown risk limit {k!r}")
        if fields[k].message_type is not None:
            getattr(lim, k).value = str(v)
        else:
            setattr(lim, k, int(v))
    return lim


def run_event_backtest(
    config: BacktestConfig,
    bars: pl.DataFrame | None = None,
    *,
    fill_bars: pl.DataFrame | None = None,
    corporate_actions: pl.DataFrame | None = None,
    recording: Recording | None = None,
    simcore: SimCore | None = None,
    scheduled_events: Iterable[ScheduledEvent] | None = None,
    context_store: ContextStore | None = None,
) -> BacktestResult:
    """Run one event-driven backtest (section 08 interface). ``bars`` are decision-timeframe bars in
    the lake schema (raw prices, as the bus carries them); a ``recording`` supplies them otherwise."""
    if bars is None:
        if recording is None:
            raise ValueError("bars or a recording are required")
        bars = recording.bars_frame(config.timeframe)
    engine = EventEngine(config, bars, fill_bars, corporate_actions, recording, scheduled_events, context_store)
    if simcore is not None:
        return engine.run(simcore)
    with SimCore() as sc:
        return engine.run(sc)


__all__ = ["EventEngine", "LeakageError", "fill_frame", "normalize_bars", "run_event_backtest"]
