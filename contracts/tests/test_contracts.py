"""Proves the generated Python contracts import and round-trip with the pinned protobuf runtime."""

from tb.audit.v1 import audit_pb2
from tb.common.v1 import common_pb2
from tb.orders.v1 import orders_pb2


def test_order_intent_round_trip() -> None:
    intent = orders_pb2.OrderIntent(
        intent_id="01JABCDEFGHJKMNPQRSTVWXYZ0",
        strategy_id="manual",
        broker_account_id="alpaca-paper-1",
        side=common_pb2.SIDE_BUY,
        quantity=common_pb2.Decimal(value="10"),
        mode=common_pb2.MODE_PAPER,
    )
    decoded = orders_pb2.OrderIntent.FromString(intent.SerializeToString())
    assert decoded == intent
    assert decoded.WhichOneof("sizing") == "quantity"


def test_audit_event_fields() -> None:
    event = audit_pb2.AuditEvent(seq=1, action="auth.login.success", prev_hash="0" * 64)
    assert audit_pb2.AuditEvent.FromString(event.SerializeToString()).action == "auth.login.success"
