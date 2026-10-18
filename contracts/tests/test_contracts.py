"""Proves the generated Python contracts import and round-trip with the pinned protobuf runtime."""

from google.protobuf import struct_pb2

from tb.audit.v1 import audit_pb2
from tb.common.v1 import common_pb2
from tb.ml.v1 import training_pb2
from tb.optimizer.v1 import optimizer_pb2
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


def test_diagnostics_report_and_trial_round_trip() -> None:
    rep = training_pb2.DiagnosticsReport(
        report_id="01J00000000000000000000000",
        run_id="run-1",
        holdout_excluded=True,
        findings=[
            training_pb2.Finding(
                code="overfitting",
                severity=training_pb2.SEVERITY_WARNING,
                evidence=[training_pb2.Evidence(name="ic_gap", value=0.21, threshold=0.1)],
            )
        ],
    )
    back = training_pb2.DiagnosticsReport.FromString(rep.SerializeToString())
    assert back.findings[0].evidence[0].value == 0.21 and back.holdout_excluded
    params = struct_pb2.Struct()
    params.update({"num_leaves": 15, "learning_rate": 0.05})
    trial = optimizer_pb2.Trial(trial_id="t1", lineage_id="l1", params=params, status=optimizer_pb2.TRIAL_STATUS_PRUNED)
    t2 = optimizer_pb2.Trial.FromString(trial.SerializeToString())
    assert t2.params["num_leaves"] == 15 and t2.status == optimizer_pb2.TRIAL_STATUS_PRUNED
