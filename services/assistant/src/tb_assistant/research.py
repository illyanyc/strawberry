"""Client for the section 10/11 research services (`MLService`, `OptimizerService`; ADR-0035).

One generic call path: ``await api.call("tb.optimizer.v1.OptimizerService", "GetLineage", req)``,
built on ``tbcore.rpc.AsyncClient`` (descriptor-built, a fresh service token per call, SE-040). Test
doubles implement the same single method. The services resolve every call from this client to actor
``analyst`` whatever the request says (EX-030). There is no client for ``HoldoutService``: the
holdout is unreachable by construction (EX-012, AN-030).
"""

from __future__ import annotations

from typing import Protocol

import grpc
from google.protobuf.message import Message

from tb.ml.v1 import ml_service_pb2
from tb.optimizer.v1 import optimizer_pb2
from tbcore.rpc import AsyncClient
from tbcore.svcauth import Signer

ML = "tb.ml.v1.MLService"
OPT = "tb.optimizer.v1.OptimizerService"
# Services the analyst may call; HoldoutService is deliberately absent (AN-030, EX-012).
ALLOWED_SERVICES = frozenset({ML, OPT})
AUDIENCE = {ML: "ml", OPT: "optimizer"}
DESCRIPTORS = {
    ML: ml_service_pb2.DESCRIPTOR.services_by_name["MLService"],
    OPT: optimizer_pb2.DESCRIPTOR.services_by_name["OptimizerService"],
}


class ResearchCallError(RuntimeError):
    def __init__(self, service: str, method: str, code: str, detail: str) -> None:
        super().__init__(f"{service.rsplit('.', 1)[-1]}.{method}: {code}: {detail}")
        self.code = code
        self.detail = detail


class ResearchAPI(Protocol):
    async def call(self, service: str, method: str, request: Message) -> Message: ...


class GrpcResearch:
    """gRPC client for MLService and OptimizerService with signed service tokens."""

    def __init__(self, endpoints: dict[str, str], signer: Signer, timeout: float = 30.0) -> None:
        unknown = set(endpoints) - ALLOWED_SERVICES
        if unknown:
            raise ValueError(f"analyst may not call {sorted(unknown)}")
        self._channels = {svc: grpc.aio.insecure_channel(addr) for svc, addr in endpoints.items()}
        self._clients = {
            svc: AsyncClient(ch, DESCRIPTORS[svc], signer, AUDIENCE[svc], timeout) for svc, ch in self._channels.items()
        }

    async def call(self, service: str, method: str, request: Message) -> Message:
        client = self._clients.get(service)
        if client is None:
            raise ResearchCallError(service, method, "PERMISSION_DENIED", "service not allowed for the analyst")
        try:
            result: Message = await client.call(method, request)
        except grpc.aio.AioRpcError as exc:
            raise ResearchCallError(service, method, exc.code().name, exc.details() or "") from None
        return result

    async def close(self) -> None:
        for ch in self._channels.values():
            await ch.close()
