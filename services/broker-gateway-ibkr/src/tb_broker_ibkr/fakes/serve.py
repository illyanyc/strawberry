"""Serve the IBKR adapter's gRPC interface over the in-process fake gateway (no IBKR needed).

Used by ``make test-ibkr-contract`` to run the shared Go contract suite (BR-001,
``services/broker-gateway/internal/contract``) against this adapter: it writes a fresh Ed25519 seed for
the ``execution`` caller (the suite signs with it) and trusts only that key.

    python -m tb_broker_ibkr.fakes.serve --addr 127.0.0.1:19090 --seed-out /tmp/exec.seed
"""

from __future__ import annotations

import argparse
import asyncio
import base64
from decimal import Decimal
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat

from tb_broker_ibkr.adapter import AdapterConfig, IbkrAdapter
from tb_broker_ibkr.audit import MemoryAuditLog
from tb_broker_ibkr.clock import SystemClock
from tb_broker_ibkr.cursor_store import CursorStore
from tb_broker_ibkr.events import FanoutSink, LogSink
from tb_broker_ibkr.fakes.gateway import FakeGateway, FakeOrder
from tb_broker_ibkr.grpc_service import AUDIENCE, BrokerGatewayServicer, StreamHub, build_server
from tb_broker_ibkr.instruments import IbkrContractResolver, StaticInstrumentResolver
from tb_broker_ibkr.models import Environment
from tb_broker_ibkr.svcauth import Verifier

FAKE_ACCOUNT = "ibkr-paper-fake"
FAKE_CODE = "DU0000001"


def enable_auto_fill(gateway: FakeGateway) -> None:
    """Paper-simulation mode for cross-service tests (tests/paper, D30 swing routing): the account
    reports buying power, and market and limit orders fill completely at their limit price (or
    100) right after the acknowledgement; stop orders rest until canceled."""
    gateway.account_values[FAKE_CODE] = {
        "NetLiquidation": "100000",
        "TotalCashValue": "100000",
        "BuyingPower": "200000",
        "ExcessLiquidity": "50000",
    }
    deliver_ack = gateway._deliver_ack

    async def ack_then_fill(order: FakeOrder) -> None:
        await deliver_ack(order)
        if order.order_type in ("MKT", "LMT") and order.filled < order.total_qty:
            gateway.fill(order.perm_id, order.total_qty - order.filled, order.lmt_price or Decimal(100))

    gateway._deliver_ack = ack_then_fill  # type: ignore[method-assign]


async def serve(addr: str, seed_out: Path, auto_fill: bool = False, instruments: str = "") -> None:
    key = Ed25519PrivateKey.generate()
    seed = key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    seed_out.write_text(base64.b64encode(seed).decode() + "\n", encoding="utf-8")
    seed_out.chmod(0o600)

    clock = SystemClock()
    gateway = FakeGateway(clock, [FAKE_CODE])
    if auto_fill:
        enable_auto_fill(gateway)
    port = gateway.port(1)
    hub = StreamHub(Environment.PAPER)
    adapter = IbkrAdapter(
        config=AdapterConfig(environment=Environment.PAPER, accounts={FAKE_ACCOUNT: FAKE_CODE}),
        port=port,
        clock=clock,
        sink=FanoutSink(LogSink(), hub),
        resolver=IbkrContractResolver(StaticInstrumentResolver.from_file(instruments), port),
        cursor=CursorStore(),
        audit=MemoryAuditLog(),
    )
    hub.state = lambda: adapter.supervisor.state
    server, _ = build_server(
        BrokerGatewayServicer(adapter, hub), Verifier(AUDIENCE, {"execution": key.public_key()}), addr
    )
    await server.start()
    task = asyncio.ensure_future(adapter.run())
    try:
        await server.wait_for_termination()
    finally:
        adapter.stop()
        task.cancel()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--addr", default="127.0.0.1:19090")
    parser.add_argument("--seed-out", type=Path, required=True)
    parser.add_argument(
        "--auto-fill", action="store_true", help="fill market/limit orders at once (cross-service tests)"
    )
    parser.add_argument(
        "--instruments", default="", help="instrument map JSON (same format as TB_IBKR_INSTRUMENTS_PATH)"
    )
    args = parser.parse_args()
    asyncio.run(serve(args.addr, args.seed_out, args.auto_fill, args.instruments))


if __name__ == "__main__":
    main()
