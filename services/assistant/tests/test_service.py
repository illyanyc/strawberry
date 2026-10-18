# mypy: disable-error-code="no-untyped-call,attr-defined"
"""AnalystService over real gRPC with service tokens, chat, bus wiring, and the egress proxy
(SE-040, AN-053, AN-060, AN-080)."""

from __future__ import annotations

import asyncio
import os
import threading
from collections.abc import Iterator
from typing import Any

import grpc
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from tb_assistant.bus import (
    FeedbackCache,
    JetStreamPublisher,
    subscribe_all,
    trigger_from_feedback,
    trigger_from_run,
    trigger_from_study,
)
from tb_assistant.chat import Chat
from tb_assistant.coder import Coder
from tb_assistant.egress_proxy import EgressProxy, parse_allow, target_of
from tb_assistant.eval.fake_research import World
from tb_assistant.eval.harness import PassingSandbox, build
from tb_assistant.loop import StaticSymbols
from tb_assistant.service import ALLOW, AnalystServicer

from tb.analyst.v1 import analyst_pb2 as A
from tb.ml.v1 import training_pb2
from tb.monitor.v1 import feedback_pb2
from tb.optimizer.v1 import optimizer_pb2
from tbcore.rpc import Client, serve
from tbcore.svcauth import Signer, Verifier

SVC = A.DESCRIPTOR.services_by_name["AnalystService"]


@pytest.fixture
def stack() -> Iterator[tuple[Any, Client, Client, asyncio.AbstractEventLoop]]:
    """The analyst on fakes behind a real gRPC server; clients signed as api-gateway and as ml."""
    h = build(lineages={"L1": {"level": 0, "world": World(true_sharpe=0.15)}})
    aloop = asyncio.new_event_loop()
    t = threading.Thread(target=aloop.run_forever, daemon=True)
    t.start()
    gw_key, ml_key = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
    verifier = Verifier("assistant", {"api-gateway": gw_key.public_key(), "ml": ml_key.public_key()})
    chat = Chat(h.llm, h.tools, h.journal, StaticSymbols({"AAPL"}), h.loop.ids, h.clock)
    coder = Coder(h.llm, PassingSandbox(), h.journal, h.audit, ids=h.loop.ids, clock=h.clock)
    servicer = AnalystServicer(h.loop, h.journal, h.tools, chat, coder, aloop)
    server, port = serve("127.0.0.1:0", [(SVC, servicer, ALLOW)], verifier)
    server.start()
    ch = grpc.insecure_channel(f"127.0.0.1:{port}")
    seed = lambda k: k.private_bytes_raw()  # noqa: E731
    gw = Client(ch, SVC, Signer("api-gateway", seed(gw_key)), "assistant")
    other = Client(ch, SVC, Signer("ml", seed(ml_key)), "assistant")
    yield h, gw, other, aloop
    server.stop(0)
    aloop.call_soon_threadsafe(aloop.stop)


def _owner_round(h: Any, gw: Client) -> str:
    run = h.research.run_owner_training("L1")
    h.research.completions.clear()
    resp = gw.call(
        "RequestAnalysis", A.RequestAnalysisRequest(lineage_id="L1", ref_id=run.run_id, ref_kind="run", question="why?")
    )
    assert resp.round_id
    return str(resp.round_id)


def test_se040_only_api_gateway_may_call(stack: tuple[Any, Client, Client, asyncio.AbstractEventLoop]) -> None:
    _, gw, other, _ = stack
    with pytest.raises(grpc.RpcError) as err:
        other.call("ListLineages", A.ListLineagesRequest())
    assert err.value.code() == grpc.StatusCode.PERMISSION_DENIED
    assert gw.call("ListLineages", A.ListLineagesRequest()).lineages[0].lineage_id == "L1"


def test_an060_journal_review_record_launches_and_status_over_grpc(
    stack: tuple[Any, Client, Client, asyncio.AbstractEventLoop],
) -> None:
    h, gw, _, _aloop = stack
    rid = _owner_round(h, gw)
    rnd = gw.call("GetRound", A.GetRoundRequest(round_id=rid)).round
    assert rnd.trigger == A.ROUND_TRIGGER_OWNER_REQUEST and rnd.decision == A.ROUND_DECISION_AWAITING_APPROVAL
    assert [r.round_id for r in gw.call("GetJournal", A.GetJournalRequest(lineage_id="L1")).rounds] == [rid]
    first = rnd.plan.actions[0].action_id
    resp = gw.call(
        "ReviewActions", A.ReviewActionsRequest(round_id=rid, action_ids=[first], approve=True, actor="owner")
    )
    assert len(resp.launches) == 1 and resp.launches[0].action_id == first
    plan = gw.call(
        "RecordLaunches",
        A.RecordLaunchesRequest(round_id=rid, launches=[A.LaunchRecord(action_id=first, job_ids=["job-x"])]),
    ).plan
    assert plan.actions[0].status == A.ACTION_STATUS_LAUNCHED
    st = gw.call("GetLoopStatus", A.GetLoopStatusRequest(lineage_id="L1")).status
    assert st.autonomy.level == 0 and st.state == A.LOOP_STATE_AWAITING_RESULTS
    paused = gw.call("SetLoopPaused", A.SetLoopPausedRequest(lineage_id="L1", paused=True, actor="owner")).lineages
    assert paused[0].autonomy.paused and paused[0].state == A.LOOP_STATE_PAUSED
    with pytest.raises(grpc.RpcError) as err:
        gw.call("GetRound", A.GetRoundRequest(round_id="nope"))
    assert err.value.code() == grpc.StatusCode.NOT_FOUND


def test_trial_ledger_view_has_no_holdout(stack: tuple[Any, Client, Client, asyncio.AbstractEventLoop]) -> None:
    h, gw, _, _aloop = stack
    for _ in range(3):
        h.research.run_owner_training("L1")
    led = gw.call("GetTrialLedger", A.GetTrialLedgerRequest(lineage_id="L1"))
    assert len(led.trials) == 3 and {t.source for t in led.trials} == {"owner"}
    assert led.outcome.lineage_trials == 3 and led.cycle_budget == 30
    assert "holdout" not in str(led).lower()


def test_an080_chat_cites_only_known_ids_and_masks(
    stack: tuple[Any, Client, Client, asyncio.AbstractEventLoop],
) -> None:
    h, gw, _, _aloop = stack
    run = h.research.run_owner_training("L1")
    conv = gw.call("CreateConversation", A.CreateConversationRequest(title="t", lineage_id="L1")).conversation
    reply = gw.call(
        "PostMessage",
        A.PostMessageRequest(conversation_id=conv.conversation_id, content=f"How did {run.run_id} do on AAPL?"),
    ).reply
    assert reply.role == "assistant" and reply.cited_ids and run.diagnostics_report_id in reply.cited_ids
    got = gw.call("GetConversation", A.GetConversationRequest(conversation_id=conv.conversation_id)).conversation
    assert [m.role for m in got.messages] == ["owner", "assistant"]
    chat_prompts = [c.prompt for c in h.journal.llm_calls if c.role == "chat"]
    assert chat_prompts and all("AAPL" not in p for p in chat_prompts)
    assert len(gw.call("ListConversations", A.ListConversationsRequest()).conversations) == 1


def test_code_proposal_review_over_grpc(stack: tuple[Any, Client, Client, asyncio.AbstractEventLoop]) -> None:
    h, gw, _, aloop = stack
    rnd = A.AnalystRound(round_id="r1", lineage_id="L1")
    act = A.PlanAction(action_id="a1", tool="write_code_proposal", description="z-score", config_diff_json="{}")
    coder = Coder(h.llm, PassingSandbox(), h.journal, h.audit, ids=h.loop.ids, clock=h.clock)
    pid = asyncio.run_coroutine_threadsafe(coder.propose(rnd, act), aloop).result(10)
    props = gw.call("ListCodeProposals", A.ListCodeProposalsRequest(lineage_id="L1")).proposals
    assert [p.proposal_id for p in props] == [pid]
    p = gw.call(
        "ReviewCodeProposal", A.ReviewCodeProposalRequest(proposal_id=pid, approve=True, note="ok", actor="owner")
    ).proposal
    assert p.status == A.CODE_PROPOSAL_STATUS_APPROVED_FOR_MERGE


# ---- bus wiring


def test_bus_event_conversion() -> None:
    t = trigger_from_run(
        training_pb2.MLRunCompleted(
            run_id="r", job_id="j", lineage_id="L", analyst_round_id="R", status=training_pb2.JOB_STATUS_FAILED
        )
    )
    assert (t.trigger_id, t.job_id, t.analyst_round_id, t.failed) == ("ml.run.completed:r", "j", "R", True)
    s = trigger_from_study(
        optimizer_pb2.StudyCompleted(
            study_id="s", lineage_id="L", candidate_id="c", status=optimizer_pb2.STUDY_STATUS_COMPLETED
        )
    )
    assert s.study_id == "s" and s.candidate_id == "c" and not s.failed
    f = trigger_from_feedback(
        feedback_pb2.RetrainSuggested(trigger_id="rt-1", strategy_id="orb", lineage_id="L", report_id="fb-1")
    )
    assert f.kind == A.ROUND_TRIGGER_LIVE_FEEDBACK and f.strategy_id == "orb" and f.report_id == "fb-1"


class _Msg:
    def __init__(self, subject: str, data: bytes) -> None:
        self.subject, self.data, self.acked, self.naked = subject, data, False, False

    async def ack(self) -> None:
        self.acked = True

    async def nak(self, delay: float = 0) -> None:
        self.naked = True


class _JS:
    def __init__(self) -> None:
        self.subs: dict[str, tuple[str, Any]] = {}
        self.published: list[tuple[str, bytes, dict[str, str]]] = []

    async def subscribe(self, subject: str, **kw: Any) -> None:
        self.subs[subject] = (kw["stream"], kw["cb"])
        assert kw["manual_ack"] and kw["durable"]

    async def publish(self, subject: str, data: bytes, headers: dict[str, str]) -> None:
        self.published.append((subject, data, headers))


async def test_bus_subscriptions_name_streams_ack_and_nak() -> None:
    js, cache, seen = _JS(), FeedbackCache(), []

    async def on_trigger(t: Any) -> None:
        if t.lineage_id == "boom":
            raise RuntimeError("boom")
        seen.append(t)

    async def noop(_: Any) -> None:
        return None

    await subscribe_all(js, on_trigger=on_trigger, on_backtest=noop, feedback=cache, on_lower=noop)
    assert {s: st for s, (st, _) in js.subs.items()} == {
        "ml.run.completed": "RESEARCH",
        "optimizer.study.completed": "RESEARCH",
        "backtest.completed": "RESEARCH",
        "monitor.feedback.>": "MONITOR",
        "monitor.retrain_suggested.>": "MONITOR",
        "analyst.autonomy.lower": "ANALYST",
    }
    ok = _Msg("ml.run.completed", training_pb2.MLRunCompleted(run_id="r", lineage_id="L").SerializeToString())
    await js.subs["ml.run.completed"][1](ok)
    bad = _Msg("ml.run.completed", training_pb2.MLRunCompleted(run_id="r2", lineage_id="boom").SerializeToString())
    await js.subs["ml.run.completed"][1](bad)
    assert ok.acked and bad.naked and len(seen) == 1
    rep = feedback_pb2.LiveFeedbackReport(report_id="fb-1", strategy_id="orb")
    await js.subs["monitor.feedback.>"][1](_Msg("monitor.feedback.orb", rep.SerializeToString()))
    assert cache.latest_feedback("orb") == rep
    pub = JetStreamPublisher(js)
    await pub.publish("analyst.plan.ready", A.PlanReady(round_id="x"))
    await pub.publish("analyst.plan.ready", A.PlanReady(round_id="x"))
    await pub.alert("warning", "s", "m", "k")
    ids = [h["Nats-Msg-Id"] for _, _, h in js.published]
    assert ids[0] == ids[1] and js.published[2][0] == "alert.warning"


# ---- egress proxy (AN-053)


def test_an053_proxy_target_parsing() -> None:
    assert target_of("CONNECT api.anthropic.com:443 HTTP/1.1") == ("CONNECT", "api.anthropic.com", 443, "")
    assert target_of("POST http://host.docker.internal:11434/v1/chat/completions HTTP/1.1") == (
        "POST",
        "host.docker.internal",
        11434,
        "/v1/chat/completions",
    )
    assert target_of("GET /relative HTTP/1.1") is None and target_of("garbage") is None
    assert parse_allow(" API.anthropic.com:443 , x:1 ") == frozenset({"api.anthropic.com:443", "x:1"})


async def test_an053_proxy_refuses_unlisted_and_tunnels_listed() -> None:
    async def upstream(r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
        data = await r.read(100)
        w.write(b"echo:" + data)
        await w.drain()
        w.close()

    up = await asyncio.start_server(upstream, "127.0.0.1", 0)
    up_port = up.sockets[0].getsockname()[1]
    proxy = EgressProxy(frozenset({f"127.0.0.1:{up_port}"}))
    srv = await asyncio.start_server(proxy.handle, "127.0.0.1", 0)
    port = srv.sockets[0].getsockname()[1]

    async def ask(line: str) -> bytes:
        r, w = await asyncio.open_connection("127.0.0.1", port)
        w.write(line.encode() + b"\r\nHost: x\r\n\r\n")
        await w.drain()
        head = await asyncio.wait_for(r.read(200), 5)
        if b" 200 " in head:
            w.write(b"ping")
            await w.drain()
            head += await asyncio.wait_for(r.read(200), 5)
        w.close()
        return head

    assert b"403" in await ask("CONNECT broker-gateway:9090 HTTP/1.1")
    assert b"403" in await ask("CONNECT 127.0.0.1:1 HTTP/1.1")
    ok = await ask(f"CONNECT 127.0.0.1:{up_port} HTTP/1.1")
    assert b"200 Connection established" in ok and b"echo:ping" in ok
    assert proxy.refused == ["broker-gateway:9090", "127.0.0.1:1"]
    srv.close()
    up.close()


def test_no_secrets_in_environment_reads() -> None:
    # The assistant reads keys only from mounted files (SE-010): no LLM key variable is consulted.
    assert not [k for k in os.environ if k.startswith("TB_") and "API_KEY" in k]
