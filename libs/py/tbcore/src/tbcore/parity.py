"""Live/paper versus backtest parity (section 08, ADR-0031, BT-070, BT-071; Gate 3).

A recorded bus stream (``tbcore.backtest.recording``) is replayed through the event-driven
backtester with the same strategy version and registry, then compared with what paper or live did:

* ``recorded`` fills: the backtest takes its executions from the recording, so intents (IDs,
  quantities, prices), risk decisions, and realized P&L must match exactly (BT-070);
* ``model`` fills: the backtest uses the fill and cost models; trades must match intent for intent,
  and the P&L difference is attributed to price slippage, size, and fees with an unexplained
  residual at most ``$0.01`` per trade (BT-071). The per-leg slippage feeds model calibration.

Run as ``python -m tbcore.parity RECORDING --config CONFIG.json --mode recorded|model``.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from tb.common.v1 import common_pb2
from tb.orders.v1 import orders_pb2
from tbcore.backtest.config import BacktestConfig
from tbcore.backtest.event import run_event_backtest
from tbcore.backtest.recording import Recording
from tbcore.backtest.result import BacktestResult

Mode = Literal["recorded", "model"]
ROLES = {
    "ORDER_ROLE_ENTRY": "entry",
    "ORDER_ROLE_EXIT": "exit",
    "ORDER_ROLE_PROTECTIVE_STOP": "stop",
    "ORDER_ROLE_TAKE_PROFIT": "take_profit",
}
CENT = Decimal("0.01")


@dataclass
class Leg:
    intent_id: str
    role: str
    side: int
    paper_qty: Decimal = Decimal(0)
    paper_px: Decimal = Decimal(0)
    paper_fees: Decimal = Decimal(0)
    bt_qty: Decimal = Decimal(0)
    bt_px: Decimal = Decimal(0)
    bt_fees: Decimal = Decimal(0)

    @property
    def slippage_bps(self) -> float | None:
        """Backtest fill versus paper fill, positive = the backtest paid more (buy) or got less (sell)."""
        if not self.paper_qty or not self.bt_qty or not self.paper_px:
            return None
        return float((self.bt_px - self.paper_px) / self.paper_px * self.side * 10_000)


@dataclass
class ParityReport:
    strategy_id: str
    mode: str
    passed: bool
    intents_paper: int
    intents_backtest: int
    matched_intents: list[str] = field(default_factory=list)
    missed_intents: list[str] = field(default_factory=list)  # in paper, not in the backtest
    extra_intents: list[str] = field(default_factory=list)  # in the backtest, not in paper
    intent_mismatches: list[str] = field(default_factory=list)
    decision_mismatches: list[str] = field(default_factory=list)
    missed_legs: list[str] = field(default_factory=list)
    extra_legs: list[str] = field(default_factory=list)
    legs: list[dict[str, Any]] = field(default_factory=list)
    paper_realized: str = "0"
    backtest_realized: str = "0"
    pnl_difference: str = "0"
    price_effect: str = "0"
    size_effect: str = "0"
    fee_effect: str = "0"
    unexplained: str = "0"
    tolerance: str = "0.01"
    notes: list[str] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)


def _dec(x: common_pb2.Decimal) -> Decimal:
    return Decimal(x.value) if x.value else Decimal(0)


def _side(s: str | int) -> int:
    return 1 if s in ("buy", common_pb2.SIDE_BUY) else -1


def _intent_view(it: orders_pb2.OrderIntent) -> dict[str, str]:
    return {
        "instrument_id": it.instrument_id,
        "side": common_pb2.Side.Name(it.side).removeprefix("SIDE_").lower(),
        "kind": orders_pb2.IntentKind.Name(it.kind).removeprefix("INTENT_KIND_").lower(),
        "qty": it.quantity.value,
        "reference_price": it.reference_price.value,
        "stop_loss": it.protective.stop_loss_price.value,
        "take_profit": it.protective.take_profit_price.value,
    }


def compare_recording(
    recording: Recording, config: BacktestConfig, result: BacktestResult | None = None
) -> ParityReport:
    """Replay ``recording`` with ``config`` (its ``fill_model.mode`` picks BT-070 or BT-071) and compare."""
    if len(config.strategies) != 1:
        raise ValueError("parity compares one strategy at a time")
    spec = config.strategies[0]
    mode: Mode = config.fill_model.mode
    res = result or run_event_backtest(config, recording=recording)
    rep = ParityReport(spec.strategy_id, mode, False, 0, res.intents.height)
    paper_intents = {it.intent_id: it for it in recording.intents if it.strategy_id == spec.strategy_id}
    rep.intents_paper = len(paper_intents)
    bt = {r["intent_id"]: r for r in res.intents.iter_rows(named=True)}
    exact = ("instrument_id", "side", "kind", "reference_price", "stop_loss", "take_profit") + (
        ("qty",) if mode == "recorded" else ()
    )
    for iid, it in paper_intents.items():
        if iid not in bt:
            rep.missed_intents.append(iid)
            continue
        rep.matched_intents.append(iid)
        pv, bv = _intent_view(it), bt[iid]
        diffs = [f"{k}: paper {pv[k]} backtest {bv[k]}" for k in exact if not _same(pv[k], str(bv[k]))]
        if diffs:
            rep.intent_mismatches.append(f"{iid}: " + "; ".join(diffs))
    rep.extra_intents = [i for i in bt if i not in paper_intents]
    for d in recording.decisions:
        b = bt.get(d.intent_id)
        if b is not None and (
            d.approved != b["approved"] or (not d.approved and d.first_failure != b["first_failure"])
        ):
            rep.decision_mismatches.append(
                f"{d.intent_id}: paper {d.approved}/{d.first_failure} backtest {b['approved']}/{b['first_failure']}"
            )
    # Legs: executions aggregated per (intent, role).
    roles = recording.order_roles()
    prefix = f"tb1-{spec.short_id}-"
    legs: dict[tuple[str, str], Leg] = {}
    for x in recording.executions:
        if not x.client_order_id.startswith(prefix):
            continue
        intent = x.client_order_id.split("-")[2]
        role = ROLES.get(roles.get(x.broker_order_id, ("", ""))[1], "unknown")
        leg = legs.setdefault((intent, role), Leg(intent, role, _side(x.side)))
        q, p = _dec(x.qty), _dec(x.price)
        leg.paper_px = (leg.paper_px * leg.paper_qty + q * p) / (leg.paper_qty + q)
        leg.paper_qty += q
        leg.paper_fees += _dec(x.fees)
    for f in res.fills.iter_rows(named=True):
        if f["strategy_id"] != spec.strategy_id:
            continue
        leg = legs.setdefault((f["intent_id"], f["role"]), Leg(f["intent_id"], f["role"], _side(f["side"])))
        q, p = Decimal(str(f["qty"])), Decimal(str(f["price"]))
        leg.bt_px = (leg.bt_px * leg.bt_qty + q * p) / (leg.bt_qty + q)
        leg.bt_qty += q
        leg.bt_fees += Decimal(str(f["fees"]))
    price = size = fee = Decimal(0)
    for (intent, role), leg in sorted(legs.items()):
        if leg.paper_qty and not leg.bt_qty:
            rep.missed_legs.append(f"{intent}:{role}")
        elif leg.bt_qty and not leg.paper_qty:
            rep.extra_legs.append(f"{intent}:{role}")
        # Cash flow of a leg: -side x qty x price - fees; for flat-to-flat books the realized P&L is the
        # sum of cash flows, so paper - backtest decomposes exactly into these three effects.
        price += -leg.side * leg.bt_qty * (leg.paper_px - leg.bt_px) if leg.paper_qty else Decimal(0)
        size += -leg.side * (leg.paper_qty - leg.bt_qty) * (leg.paper_px if leg.paper_qty else leg.bt_px)
        fee += -(leg.paper_fees - leg.bt_fees)
        rep.legs.append(
            {
                "intent_id": intent,
                "role": role,
                "side": "buy" if leg.side > 0 else "sell",
                "paper_qty": str(leg.paper_qty),
                "paper_price": str(leg.paper_px.quantize(Decimal("0.0001"))),
                "backtest_qty": str(leg.bt_qty),
                "backtest_price": str(leg.bt_px.quantize(Decimal("0.0001"))),
                "paper_fees": str(leg.paper_fees),
                "backtest_fees": str(leg.bt_fees),
                "slippage_bps": leg.slippage_bps,
            }
        )
    paper_realized = _paper_realized(recording, spec.strategy_id)
    bt_realized = (
        Decimal(str(res.daily.filter(res.daily["strategy_id"] == spec.strategy_id)["realized_pnl"][-1]))
        if res.daily.height
        else Decimal(0)
    )
    diff = paper_realized - bt_realized
    unexplained = diff - price - size - fee
    trades = max(len([leg for leg in legs.values() if leg.role == "entry"]), 1)
    rep.paper_realized, rep.backtest_realized, rep.pnl_difference = str(paper_realized), str(bt_realized), str(diff)
    rep.price_effect, rep.size_effect, rep.fee_effect = (
        str(v.quantize(Decimal("0.000001"))) for v in (price, size, fee)
    )
    rep.unexplained = str(unexplained.quantize(Decimal("0.000001")))
    structural = not (
        rep.missed_intents or rep.extra_intents or rep.decision_mismatches or rep.missed_legs or rep.extra_legs
    )
    if mode == "recorded":
        rep.tolerance = str(CENT)
        rep.passed = structural and not rep.intent_mismatches and abs(diff) <= CENT
    else:
        tol = CENT * trades
        rep.tolerance = str(tol)
        rep.passed = structural and not rep.intent_mismatches and abs(unexplained) <= tol
        rep.notes.append(
            "model mode: quantities may differ (sizing compounds realized P&L); the size effect explains it"
        )
    if res.stats.get("open_episodes"):
        rep.notes.append("open positions at the end: the P&L decomposition assumes flat-to-flat books")
    return rep


def _same(a: str, b: str) -> bool:
    """Equal as decimals when both are numbers (``406`` equals ``406.0``), else as strings."""
    try:
        return Decimal(a or "0") == Decimal(b or "0")
    except ArithmeticError:
        return a == b


def _paper_realized(recording: Recording, strategy_id: str) -> Decimal:
    last: dict[str, Decimal] = {}
    for u in recording.ledger_updates:
        for p in u.positions:
            if p.owner == strategy_id:
                last[p.instrument_id] = _dec(p.realized_pnl)
    return sum(last.values(), Decimal(0))


def compare_live_vs_backtest(strategy_id: str, recording: str | Path, config: BacktestConfig) -> ParityReport:
    """Section 08 entry point: parity of one strategy over a recorded period (a recording file; pulling
    the period from JetStream is Planned)."""
    if config.strategies[0].strategy_id != strategy_id:
        raise ValueError(f"config is for {config.strategies[0].strategy_id}, not {strategy_id}")
    return compare_recording(Recording.load(recording), config)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m tbcore.parity", description=__doc__.splitlines()[0])
    ap.add_argument("recording")
    ap.add_argument("--config", required=True)
    ap.add_argument("--mode", choices=["recorded", "model"], default="recorded")
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    cfg = BacktestConfig.model_validate_json(Path(a.config).read_text(encoding="utf-8"))
    cfg = cfg.model_copy(update={"fill_model": cfg.fill_model.model_copy(update={"mode": a.mode})})
    rep = compare_recording(Recording.load(a.recording), cfg)
    out = rep.to_json()
    if a.out:
        Path(a.out).write_text(out, encoding="utf-8")
    verdict = "PASS" if rep.passed else "FAIL"
    print(
        f"parity {a.mode}: {verdict} intents {len(rep.matched_intents)}/{rep.intents_paper} matched, "
        f"P&L paper {rep.paper_realized} backtest {rep.backtest_realized} unexplained {rep.unexplained}"
    )
    return 0 if rep.passed else 1


if __name__ == "__main__":
    sys.exit(main())
