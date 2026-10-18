"""MonitorService over gRPC with service tokens (ADR-0045, SE-040, MN-036, MN-042)."""

from __future__ import annotations

import base64
from pathlib import Path

import grpc
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat
from tb_monitor import containment as ct
from tb_monitor.alertbox import Inbox
from tb_monitor.core import Monitor
from tb_monitor.service import ALLOW, SERVICE, MonitorServicer
from test_monitoring import FakeExec, monitored

from tb.monitor.v1 import feedback_pb2
from tb.monitor.v1 import monitor_pb2 as mp
from tb.orders.v1 import execution_pb2 as ex
from tbcore.audit import MemoryAudit
from tbcore.docstore import MemoryDocStore
from tbcore.rpc import Client, serve
from tbcore.svcauth import Signer, Verifier


def _key(tmp: Path, service: str) -> Signer:
    k = Ed25519PrivateKey.generate()
    (tmp / f"svc_{service.replace('-', '_')}_ed25519.pub").write_text(
        base64.b64encode(k.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()
    )
    return Signer(service, k.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption()))


def test_monitor_service_boundaries_actor_and_step_up(tmp_path: Path) -> None:
    store, audit = MemoryDocStore(), MemoryAudit()
    mon = Monitor(
        {"ml-a": monitored()}, store, ct.Containment(store, audit, FakeExec(), lambda *a: None), lambda *a: None
    )
    store.put("feedback", "ml-a", feedback_pb2.LiveFeedbackReport(report_id="fb-1", strategy_id="ml-a"), "ml-a")
    gw, assistant = _key(tmp_path, "api-gateway"), _key(tmp_path, "assistant")
    server, port = serve(
        "127.0.0.1:0",
        [(SERVICE, MonitorServicer(mon, Inbox(store, audit)), ALLOW)],
        Verifier.from_secrets("monitor", ["api-gateway", "assistant"], tmp_path),
    )
    server.start()
    try:
        g = Client(grpc.insecure_channel(f"127.0.0.1:{port}"), SERVICE, gw, "monitor")
        a = Client(grpc.insecure_channel(f"127.0.0.1:{port}"), SERVICE, assistant, "monitor")
        assert a.call("GetLiveFeedback", mp.GetLiveFeedbackRequest(strategy_id="ml-a")).report.report_id == "fb-1"
        with pytest.raises(grpc.RpcError) as e:
            a.call("ListStrategyHealth", mp.ListStrategyHealthRequest())
        assert e.value.code() == grpc.StatusCode.PERMISSION_DENIED
        hs = g.call("ListStrategyHealth", mp.ListStrategyHealthRequest()).strategies
        assert [h.strategy_id for h in hs] == ["ml-a"]
        assert g.call("GetStrategyHealth", mp.GetStrategyHealthRequest(strategy_id="ml-a")).health.strategy_id == "ml-a"
        with pytest.raises(grpc.RpcError) as e2:
            g.call("GetDriftReport", mp.GetDriftReportRequest(strategy_id="nope"))
        assert e2.value.code() == grpc.StatusCode.NOT_FOUND
        pol = g.call("GetContainmentPolicy", mp.GetContainmentPolicyRequest(strategy_id="ml-a"))
        assert pol.inherited and pol.policy.breach_action == mp.CONTAINMENT_ACTION_KIND_DEMOTE
        loose = mp.ContainmentPolicy()
        loose.CopyFrom(pol.policy)
        loose.drawdown_breach_multiple = 3.0
        with pytest.raises(grpc.RpcError) as e3:
            g.call(
                "SetContainmentPolicy",
                mp.SetContainmentPolicyRequest(policy=loose, actor=ex.Actor(name="owner"), reason="r"),
            )
        assert e3.value.code() == grpc.StatusCode.PERMISSION_DENIED
        ok = g.call(
            "SetContainmentPolicy",
            mp.SetContainmentPolicyRequest(
                policy=loose, actor=ex.Actor(name="owner", step_up_method="passkey"), reason="wider drawdown band"
            ),
        )
        assert ok.loosened and ok.policy.updated_by == "owner"
        assert g.call("GetGraduationEvidence", mp.GetGraduationEvidenceRequest(strategy_id="ml-a")).evidence.criteria
    finally:
        server.stop(0)
