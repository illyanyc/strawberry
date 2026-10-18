"""Client for the section 10/11 research services (`MLService`, `OptimizerService`; ADR-0035).

One generic call path: ``await api.call("tb.optimizer.v1.OptimizerService", "GetLineage", req)``.
The response type comes from the proto descriptor, so no Python gRPC codegen is needed, and test
doubles implement the same single method. Every call carries a service token for the target
audience (SE-040); the services resolve it to actor ``analyst`` whatever the request says (EX-030).
The assistant has no client for ``HoldoutService``: the holdout is unreachable by construction
(EX-012, AN-030).
"""

from __future__ import annotations

from typing import Any, Protocol

import grpc
from google.protobuf import descriptor_pool, message_factory
from google.protobuf.message import Message

# Import for descriptor registration.
from tb.ml.v1 import ml_service_pb2  # noqa: F401
from tb.optimizer.v1 import optimizer_pb2  # noqa: F401
from tbcore.svcauth import Signer

ML = "tb.ml.v1.MLService"
OPT = "tb.optimizer.v1.OptimizerService"
# Services the analyst may call; HoldoutService is deliberately absent (AN-030, EX-012).
ALLOWED_SERVICES = frozenset({ML, OPT})
AUDIENCE = {ML: "ml", OPT: "optimizer"}


class ResearchCallError(RuntimeError):
    def __init__(self, service: str, method: str, code: str, detail: str) -> None:
        super().__init__(f"{service.rsplit('.', 1)[-1]}.{method}: {code}: {detail}")
        self.code = code
        self.detail = detail


class ResearchAPI(Protocol):
    async def call(self, service: str, method: str, request: Message) -> Message: ...


def response_type(service: str, method: str) -> type[Message]:
    svc = descriptor_pool.Default().FindServiceByName(service)
    cls: type[Message] = message_factory.GetMessageClass(svc.FindMethodByName(method).output_type)
    return cls


class GrpcResearch:
    """gRPC client for MLService and OptimizerService with signed service tokens."""

    def __init__(self, endpoints: dict[str, str], signer: Signer, timeout: float = 30.0) -> None:
        unknown = set(endpoints) - ALLOWED_SERVICES
        if unknown:
            raise ValueError(f"analyst may not call {sorted(unknown)}")
        self._signer = signer
        self._timeout = timeout
        self._channels = {svc: grpc.aio.insecure_channel(addr) for svc, addr in endpoints.items()}

    async def call(self, service: str, method: str, request: Message) -> Message:
        if service not in self._channels:
            raise ResearchCallError(service, method, "PERMISSION_DENIED", "service not allowed for the analyst")
        resp_cls = response_type(service, method)
        rpc: Any = self._channels[service].unary_unary(
            f"/{service}/{method}",
            request_serializer=lambda m: m.SerializeToString(),
            response_deserializer=resp_cls.FromString,
        )
        try:
            result: Message = await rpc(
                request, timeout=self._timeout, metadata=self._signer.metadata(AUDIENCE[service])
            )
        except grpc.aio.AioRpcError as exc:
            raise ResearchCallError(service, method, exc.code().name, exc.details() or "") from None
        return result

    async def close(self) -> None:
        for ch in self._channels.values():
            await ch.close()
