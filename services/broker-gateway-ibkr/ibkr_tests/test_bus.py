"""Bus encoding: subjects and the ExecutionReport contract (OE-006 dedupe key, DF/OE decimals)."""

from __future__ import annotations

from decimal import Decimal

from tb_broker_ibkr.codec import execution_proto, subject_token
from tb_broker_ibkr.models import Environment, ExecutionReport, OwnerKind, Side

from tb.common.v1 import common_pb2
from tb.orders.v1 import orders_pb2


def test_subject_token_is_nats_safe() -> None:
    assert subject_token("ibkr-paper-bot") == "ibkr-paper-bot"
    assert subject_token("a.b*c>d e") == "a_b_c_d_e"


def test_execution_report_proto_round_trip() -> None:
    report = ExecutionReport(
        broker_account_id="ibkr-paper-bot",
        broker_exec_id="0000e0d5.1.01.01",
        client_order_id="tb1-swing01-01J9ZK3M4N5P6Q7R8S9T0V1W2X-0",
        broker_order_id="1849274651",
        owner=OwnerKind.STRATEGY,
        strategy_short_id="swing01",
        instrument_id="inst-aapl",
        con_id=265598,
        symbol="AAPL",
        side=Side.BUY,
        qty=Decimal(40),
        price=Decimal("180.25"),
        cum_qty=Decimal(40),
        fees=Decimal("0.20"),
        event_time=1,
        ingested_at=2,
    )
    msg = orders_pb2.ExecutionReport.FromString(execution_proto(report, Environment.PAPER).SerializeToString())
    assert (msg.qty.value, msg.price.value, msg.fees.value) == ("40", "180.25", "0.20")
    assert msg.mode == common_pb2.MODE_PAPER and msg.side == common_pb2.SIDE_BUY
    unfeed = execution_proto(report.model_copy(update={"fees": None}), Environment.LIVE)
    assert not unfeed.HasField("fees") and unfeed.mode == common_pb2.MODE_LIVE
