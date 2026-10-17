"""Order path against the fake gateway.

OE-002 (attribution required), OE-003 / OE-I1 (no duplicate orders on timeout or retry), OE-005,
IB-001/IB-002 (orderRef limit and echo), IB-005 (explicit account), IB-021..IB-026.
"""

from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import tb_broker_ibkr
from tb_broker_ibkr.adapter import build_place_order
from tb_broker_ibkr.fakes.gateway import GatewayState
from tb_broker_ibkr.models import (
    AccountNotFoundError,
    BrokerUnavailableError,
    InvalidOrderError,
    OrderChanges,
    OrderRejectedError,
    OrderState,
    OrderType,
    OwnerKind,
    SessionState,
    Side,
    SubmitOutcomeUnknownError,
    TimeInForce,
)

from .conftest import AAPL, ACCT, PAPER_CODE, Harness, cid, make_harness, order, run


async def ready_harness(**kwargs: Any) -> Harness:
    h = make_harness(**kwargs)
    await h.start()
    await h.until_ready()
    return h


def test_submit_sets_attribution_account_and_contract() -> None:
    async def body() -> None:
        h = await ready_harness()
        ack = await h.drive(h.adapter.submit_order(order()))
        placed = h.gateway.placements[0]
        assert placed.order_ref == cid()  # IB-001
        assert placed.account == PAPER_CODE  # IB-005: never the login's default account
        assert placed.con_id == AAPL.con_id  # routed by conId, not ticker
        assert (placed.order_type, placed.tif, placed.lmt_price) == ("LMT", "DAY", Decimal("180.25"))
        assert ack.broker_order_id == str(h.gateway.order_by_ref(cid()).perm_id)  # IB-003
        assert not ack.recovered
        update = h.sink.orders[-1]
        assert (update.state, update.owner, update.strategy_short_id) == (
            OrderState.ACCEPTED,
            OwnerKind.STRATEGY,
            "swing01",
        )
        await h.stop()

    run(body)


@pytest.mark.parametrize(
    "client_order_id",
    ["", "manual-order", "tb1-x-01J9ZK3M4N5P6Q7R8S9T0V1W2X-0", "tb1-swing01-notaulid-0"],
)
def test_oe002_malformed_attribution_never_reaches_ibkr(client_order_id: str) -> None:
    async def body() -> None:
        h = await ready_harness()
        with pytest.raises(InvalidOrderError):
            await h.drive(h.adapter.submit_order(order(client_order_id)))
        assert h.gateway.placements == []
        await h.stop()

    run(body)


def test_ib001_attribution_longer_than_configured_limit_rejected() -> None:
    async def body() -> None:
        h = await ready_harness(order_ref_max_len=40)
        with pytest.raises(InvalidOrderError, match="limit is 40"):
            await h.drive(h.adapter.submit_order(order(cid("abcd1234", attempt=12))))  # 42 chars
        assert h.gateway.placements == []
        await h.stop()

    run(body)


def test_oe_i1_resubmitting_the_same_client_order_id_returns_the_existing_order() -> None:
    async def body() -> None:
        h = await ready_harness()
        first = await h.drive(h.adapter.submit_order(order()))
        second = await h.drive(h.adapter.submit_order(order()))
        assert second.broker_order_id == first.broker_order_id and second.recovered
        assert len(h.gateway.placements) == 1
        await h.stop()

    run(body)


def test_oe003_submit_timeout_recovers_by_lookup_without_resubmitting() -> None:
    async def body() -> None:
        h = await ready_harness()
        h.gateway.ack_delay = 30  # IBKR accepted it but the acknowledgement is late
        ack = await h.drive(h.adapter.submit_order(order()))
        assert ack.recovered
        assert ack.broker_order_id == str(h.gateway.order_by_ref(cid()).perm_id)
        assert len(h.gateway.placements) == 1
        assert len(h.gateway.orders) == 1
        await h.stop()

    run(body)


def test_oe003_lost_submit_is_unknown_and_never_resubmitted() -> None:
    async def body() -> None:
        h = await ready_harness()
        h.gateway.drop_next_order = True  # the order never reached IBKR
        with pytest.raises(SubmitOutcomeUnknownError):
            await h.drive(h.adapter.submit_order(order()))
        assert len(h.gateway.placements) == 1 and h.gateway.orders == {}
        assert h.sink.orders[-1].state is OrderState.UNKNOWN
        # The OMS retrying the same attempt does not place a second order.
        with pytest.raises(SubmitOutcomeUnknownError):
            await h.drive(h.adapter.submit_order(order()))
        assert len(h.gateway.placements) == 1
        await h.stop()

    run(body)


def test_reject_is_typed_and_published() -> None:
    async def body() -> None:
        h = await ready_harness()
        h.gateway.reject_next = (201, "Order rejected - reason: insufficient buying power")
        with pytest.raises(OrderRejectedError) as err:
            await h.drive(h.adapter.submit_order(order()))
        assert err.value.broker_code == 201
        await h.clock.settle()
        assert h.sink.orders[-1].state is OrderState.REJECTED
        with pytest.raises(OrderRejectedError):
            await h.drive(h.adapter.submit_order(order()))  # same attempt: same answer, no new order
        assert len(h.gateway.placements) == 1
        await h.stop()

    run(body)


def test_ib002_truncated_order_ref_fails_closed() -> None:
    async def body() -> None:
        h = await ready_harness()
        h.gateway.order_ref_limit = 32  # pretend IBKR truncates refs (what spike V1 checks)
        await h.drive(h.adapter.submit_order(order()))
        await h.clock.settle()
        assert h.sink.orders[-1].state is OrderState.UNKNOWN
        assert h.state is SessionState.DEGRADED
        assert h.adapter.session_status(ACCT).degraded_reasons == ("order_ref_mismatch",)
        assert any(a.subject.endswith(".session") and "spike V1" in a.message for a in h.sink.alerts)
        with pytest.raises(BrokerUnavailableError):
            await h.drive(h.adapter.submit_order(order(cid(attempt=1))))
        # Sticky: a reconnect does not clear a configuration fault.
        h.gateway.go_down()
        h.gateway.come_up()
        await h.clock.advance(120)
        assert h.state is SessionState.DEGRADED
        await h.stop()

    run(body)


def test_ib023_cancel_platform_order() -> None:
    async def body() -> None:
        h = await ready_harness()
        ack = await h.drive(h.adapter.submit_order(order()))
        cancel = await h.adapter.cancel_order(ACCT, client_order_id=cid())
        assert cancel.broker_order_id == ack.broker_order_id
        await h.clock.settle()
        assert h.sink.orders[-1].state is OrderState.CANCELED  # requested cancel: not EXPIRED
        with pytest.raises(InvalidOrderError):
            await h.adapter.cancel_order(ACCT, broker_order_id=ack.broker_order_id)
        await h.stop()

    run(body)


def test_ib023_vp006_external_orders_cannot_be_cancelled_by_the_platform() -> None:
    async def body() -> None:
        h = await ready_harness()
        manual = h.gateway.place_external_order(PAPER_CODE, AAPL.con_id, "AAPL", "SELL", Decimal(5))
        await h.clock.settle()
        with pytest.raises(InvalidOrderError, match="outside the platform"):
            await h.adapter.cancel_order(ACCT, broker_order_id=str(manual.perm_id))
        assert h.gateway.cancels == []
        await h.stop()

    run(body)


def test_ib022_replace_bumps_attempt_and_keeps_perm_id() -> None:
    async def body() -> None:
        h = await ready_harness()
        ack = await h.drive(h.adapter.submit_order(order()))
        rep = await h.adapter.replace_order(ACCT, cid(), OrderChanges(limit_price=Decimal("181.00")))
        assert rep.new_client_order_id == cid(attempt=1)
        assert rep.broker_order_id == ack.broker_order_id
        await h.clock.settle()
        _, placed = h.gateway.modifications[0]
        assert placed.order_ref == cid(attempt=1) and placed.lmt_price == Decimal("181.00")
        replaced = [o for o in h.sink.orders if o.client_order_id == cid() and o.state is OrderState.REPLACED]
        assert replaced, "the old attempt is reported replaced"
        pending = [o for o in h.sink.orders if o.state is OrderState.PENDING_REPLACE]
        assert pending and pending[0].client_order_id == cid(attempt=1), "unconfirmed modify is pending_replace"
        live = h.sink.orders[-1]
        assert live.client_order_id == cid(attempt=1) and live.broker_order_id == ack.broker_order_id
        assert live.state is OrderState.ACCEPTED, "IBKR echoed the new orderRef: modify confirmed"
        assert h.state is SessionState.READY  # echo of the new ref matched (IB-002)
        await h.stop()

    run(body)


def test_ib022_rejected_modify_reverts_to_the_previous_attempt() -> None:
    async def body() -> None:
        h = await ready_harness()
        await h.drive(h.adapter.submit_order(order()))
        h.gateway.reject_next_modify = (105, "Order modification does not match original order")
        await h.adapter.replace_order(ACCT, cid(), OrderChanges(qty=Decimal(20)))
        await h.clock.settle()
        assert h.sink.orders[-1].client_order_id == cid()
        assert h.state is SessionState.READY
        # The previous attempt can be replaced again.
        rep = await h.adapter.replace_order(ACCT, cid(), OrderChanges(limit_price=Decimal("181.00")))
        assert rep.new_client_order_id == cid(attempt=1)
        await h.stop()

    run(body)


def test_ib022_replace_cannot_change_strategy_or_intent() -> None:
    async def body() -> None:
        h = await ready_harness()
        await h.drive(h.adapter.submit_order(order()))
        with pytest.raises(InvalidOrderError, match="keeps the strategy"):
            await h.adapter.replace_order(ACCT, cid(), OrderChanges(qty=Decimal(5)), cid("other01", attempt=1))
        await h.stop()

    run(body)


def test_ib005_unknown_account_rejected() -> None:
    async def body() -> None:
        h = await ready_harness()
        with pytest.raises(AccountNotFoundError, match="disallowed"):
            await h.drive(h.adapter.submit_order(order(broker_account_id="ibkr-paper-main")))
        await h.stop()

    run(body)


def test_cancel_refused_while_disconnected() -> None:
    async def body() -> None:
        h = await ready_harness()
        await h.drive(h.adapter.submit_order(order()))
        h.gateway.go_down(GatewayState.DOWN)
        await h.clock.settle()
        with pytest.raises(BrokerUnavailableError):
            await h.adapter.cancel_order(ACCT, client_order_id=cid())
        await h.stop()

    run(body)


# ---- IB-025 / IB-026: order translation ------------------------------------------------------


@pytest.mark.parametrize(
    ("changes", "ib_type", "ib_tif", "lmt", "aux", "outside_rth"),
    [
        ({"order_type": OrderType.MARKET, "limit_price": None}, "MKT", "DAY", None, None, False),
        (
            {"order_type": OrderType.MARKET, "limit_price": None, "tif": TimeInForce.CLS},
            "MOC",
            "DAY",
            None,
            None,
            False,
        ),
        (
            {"order_type": OrderType.MARKET, "limit_price": None, "tif": TimeInForce.OPG},
            "MKT",
            "OPG",
            None,
            None,
            False,
        ),
        ({"tif": TimeInForce.CLS}, "LOC", "DAY", Decimal("180.25"), None, False),
        ({"tif": TimeInForce.GTC, "extended_hours": True}, "LMT", "GTC", Decimal("180.25"), None, True),
        ({"tif": TimeInForce.IOC}, "LMT", "IOC", Decimal("180.25"), None, False),
        (
            {"order_type": OrderType.STOP, "limit_price": None, "stop_price": Decimal("170"), "tif": TimeInForce.GTC},
            "STP",
            "GTC",
            None,
            Decimal("170"),
            False,
        ),
        (
            {"order_type": OrderType.STOP_LIMIT, "stop_price": Decimal("181"), "limit_price": Decimal("181.5")},
            "STP LMT",
            "DAY",
            Decimal("181.5"),
            Decimal("181"),
            False,
        ),
    ],
)
def test_ib025_order_translation(
    changes: dict[str, Any], ib_type: str, ib_tif: str, lmt: Decimal | None, aux: Decimal | None, outside_rth: bool
) -> None:
    placed = build_place_order(order(**changes), AAPL, PAPER_CODE, cid())
    assert (placed.order_type, placed.tif, placed.lmt_price, placed.aux_price, placed.outside_rth) == (
        ib_type,
        ib_tif,
        lmt,
        aux,
        outside_rth,
    )


@pytest.mark.parametrize(
    ("side", "order_type", "limit", "stop", "want_lmt", "want_aux"),
    [
        (Side.BUY, OrderType.LIMIT, Decimal("180.257"), None, Decimal("180.25"), None),
        (Side.SELL, OrderType.LIMIT, Decimal("180.251"), None, Decimal("180.26"), None),
        (Side.SELL_SHORT, OrderType.LIMIT, Decimal("180.251"), None, Decimal("180.26"), None),
        (Side.BUY, OrderType.STOP, None, Decimal("181.001"), None, Decimal("181.01")),
        (Side.SELL, OrderType.STOP, None, Decimal("169.999"), None, Decimal("169.99")),
        (Side.BUY, OrderType.STOP_LIMIT, Decimal("181.509"), Decimal("181.001"), Decimal("181.50"), Decimal("181.01")),
        (Side.BUY, OrderType.LIMIT, Decimal("180.25"), None, Decimal("180.25"), None),  # on tick: unchanged
    ],
)
def test_br017_tick_rounding_direction(
    side: Side,
    order_type: OrderType,
    limit: Decimal | None,
    stop: Decimal | None,
    want_lmt: Decimal | None,
    want_aux: Decimal | None,
) -> None:
    placed = build_place_order(
        order(side=side, order_type=order_type, limit_price=limit, stop_price=stop), AAPL, PAPER_CODE, cid()
    )
    assert (placed.lmt_price, placed.aux_price) == (want_lmt, want_aux)


@pytest.mark.parametrize(
    ("changes", "match"),
    [
        ({"qty": Decimal("1.5")}, "whole shares"),
        ({"limit_price": Decimal("-1")}, "positive"),
        ({"order_type": OrderType.MARKET}, "no prices"),
        ({"order_type": OrderType.MARKET, "limit_price": None, "extended_hours": True}, "outside regular hours"),
        ({"order_type": OrderType.LIMIT, "limit_price": None}, "limit_price only"),
        ({"order_type": OrderType.STOP, "limit_price": None}, "stop_price only"),
        ({"order_type": OrderType.STOP_LIMIT}, "limit_price and stop_price"),
        (
            {"order_type": OrderType.STOP, "limit_price": None, "stop_price": Decimal(170), "tif": TimeInForce.OPG},
            "market and limit",
        ),
        ({"side": Side.SELL_SHORT}, "not shortable"),
    ],
)
def test_ib026_invalid_orders_rejected_locally(changes: dict[str, Any], match: str) -> None:
    instrument = AAPL.model_copy(update={"shortable": False})
    with pytest.raises(InvalidOrderError, match=match):
        build_place_order(order(**changes), instrument, PAPER_CODE, cid())


def test_br020_no_account_wide_cancel_or_liquidation_in_adapter_code() -> None:
    package = Path(tb_broker_ibkr.__file__).parent
    offenders = [
        p.name
        for p in package.rglob("*.py")
        if re.search(r"reqGlobalCancel|globalCancel", p.read_text(encoding="utf-8"))
    ]
    assert offenders == []


def test_br013_stopped_adapter_refuses_orders() -> None:
    async def body() -> None:
        h = make_harness()
        await h.adapter.supervisor.mark_stopped("TB_TRADING_LIVE_ENABLED is not true")
        status = h.adapter.session_status(ACCT)
        assert status.state is SessionState.STOPPED
        with pytest.raises(BrokerUnavailableError):
            await h.adapter.submit_order(order())
        assert h.gateway.connect_attempts == 0

    run(body)
