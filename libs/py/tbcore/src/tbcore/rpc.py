"""gRPC for Python services without generated stubs (ADR-0035): handlers and clients built from the
proto service descriptors, plus the service-token interceptor (SE-040).

``serve`` registers an implementation object whose methods are named like the RPCs; the interceptor
verifies the caller's token and the per-method allowlist and exposes the caller as ``current_caller()``
(the services derive the actor from it, EX-030). ``Client`` and ``AsyncClient`` call any RPC by name
with a fresh token per call.
"""

from __future__ import annotations

import contextvars
from collections.abc import AsyncIterator, Callable, Iterator
from concurrent import futures
from typing import Any

import grpc
from google.protobuf import message_factory
from google.protobuf.descriptor import MethodDescriptor, ServiceDescriptor
from google.protobuf.message import Message

from tbcore.svcauth import SCHEME, Signer, TokenError, Verifier

_CALLER: contextvars.ContextVar[str] = contextvars.ContextVar("tb_rpc_caller", default="")


def current_caller() -> str:
    """Issuer of the verified service token of the call being handled ('' outside a call)."""
    return _CALLER.get()


def _classes(m: MethodDescriptor) -> tuple[type[Message], type[Message]]:
    return message_factory.GetMessageClass(m.input_type), message_factory.GetMessageClass(m.output_type)


def full_method(svc: ServiceDescriptor, method: str) -> str:
    return f"/{svc.full_name}/{method}"


class _Generic(grpc.GenericRpcHandler):
    def __init__(self, svc: ServiceDescriptor, impl: object) -> None:
        self._handlers: dict[str, grpc.RpcMethodHandler] = {}
        for m in svc.methods:
            fn = getattr(impl, m.name, None)
            if fn is None:
                continue
            req, resp = _classes(m)
            make = grpc.unary_stream_rpc_method_handler if m.server_streaming else grpc.unary_unary_rpc_method_handler
            self._handlers[full_method(svc, m.name)] = make(
                fn, request_deserializer=req.FromString, response_serializer=resp.SerializeToString
            )

    def service(self, details: grpc.HandlerCallDetails) -> grpc.RpcMethodHandler | None:
        return self._handlers.get(details.method)


def _deny(code: grpc.StatusCode, message: str) -> grpc.RpcMethodHandler:
    def abort(request: Any, context: grpc.ServicerContext) -> Any:
        context.abort(code, message)

    return grpc.unary_unary_rpc_method_handler(abort)


class TokenInterceptor(grpc.ServerInterceptor):
    """SE-040: a valid token whose issuer is allowed for the method (``allow[method]`` or ``allow["*"]``)."""

    def __init__(self, verifier: Verifier, allow: dict[str, list[str]]) -> None:
        self._verifier = verifier
        self._allow = allow

    def intercept_service(
        self,
        continuation: Callable[[grpc.HandlerCallDetails], grpc.RpcMethodHandler | None],
        details: grpc.HandlerCallDetails,
    ) -> grpc.RpcMethodHandler | None:
        method = details.method.rsplit("/", 1)[-1]
        values = [v for k, v in (details.invocation_metadata or ()) if k == "authorization"]
        if len(values) != 1 or not str(values[0]).startswith(SCHEME):
            return _deny(grpc.StatusCode.UNAUTHENTICATED, "service token required")
        try:
            claims = self._verifier.verify(str(values[0])[len(SCHEME) :])
        except TokenError:
            return _deny(grpc.StatusCode.UNAUTHENTICATED, "invalid service token")
        allowed = self._allow.get(method, self._allow.get("*", []))
        if claims.issuer not in allowed:
            return _deny(grpc.StatusCode.PERMISSION_DENIED, f"service {claims.issuer!r} may not call {method}")
        h = continuation(details)
        if h is None:
            return None
        caller = claims.issuer

        def bind(fn: Callable[..., Any]) -> Callable[..., Any]:
            def run(request: Any, context: grpc.ServicerContext) -> Any:
                tok = _CALLER.set(caller)
                try:
                    return fn(request, context)
                finally:
                    _CALLER.reset(tok)

            return run

        def bind_stream(fn: Callable[..., Iterator[Any]]) -> Callable[..., Iterator[Any]]:
            def run(request: Any, context: grpc.ServicerContext) -> Iterator[Any]:
                tok = _CALLER.set(caller)
                try:
                    yield from fn(request, context)
                finally:
                    _CALLER.reset(tok)

            return run

        if h.unary_stream is not None:
            return grpc.unary_stream_rpc_method_handler(
                bind_stream(h.unary_stream),
                request_deserializer=h.request_deserializer,
                response_serializer=h.response_serializer,
            )
        assert h.unary_unary is not None
        return grpc.unary_unary_rpc_method_handler(
            bind(h.unary_unary), request_deserializer=h.request_deserializer, response_serializer=h.response_serializer
        )


def serve(
    address: str,
    services: list[tuple[ServiceDescriptor, object, dict[str, list[str]]]],
    verifier: Verifier,
    workers: int = 16,
) -> tuple[grpc.Server, int]:
    """A started-later sync gRPC server for ``(service, impl, allowlist)`` triples; returns (server, port)."""
    allow: dict[str, list[str]] = {}
    for _, _, a in services:
        for k, v in a.items():
            allow.setdefault(k, [])
            allow[k] = sorted({*allow[k], *v})
    server = grpc.server(
        futures.ThreadPoolExecutor(max_workers=workers), interceptors=[TokenInterceptor(verifier, allow)]
    )
    server.add_generic_rpc_handlers([_Generic(svc, impl) for svc, impl, _ in services])
    port = server.add_insecure_port(address)
    return server, port


class Client:
    """Synchronous client for one service: ``client.call("GetRun", req)`` / ``client.stream(...)``."""

    def __init__(
        self, channel: grpc.Channel, svc: ServiceDescriptor, signer: Signer | None, audience: str, timeout: float = 30.0
    ) -> None:
        self._svc, self._signer, self._aud, self._timeout = svc, signer, audience, timeout
        self._unary: dict[str, Any] = {}
        self._streams: dict[str, Any] = {}
        for m in svc.methods:
            req, resp = _classes(m)
            path = full_method(svc, m.name)
            if m.server_streaming:
                self._streams[m.name] = channel.unary_stream(
                    path, request_serializer=req.SerializeToString, response_deserializer=resp.FromString
                )
            else:
                self._unary[m.name] = channel.unary_unary(
                    path, request_serializer=req.SerializeToString, response_deserializer=resp.FromString
                )

    def _md(self) -> tuple[tuple[str, str], ...]:
        return self._signer.metadata(self._aud) if self._signer else ()

    def call(self, method: str, request: Message, timeout: float | None = None) -> Any:
        return self._unary[method](request, metadata=self._md(), timeout=timeout or self._timeout)

    def stream(self, method: str, request: Message, timeout: float | None = None) -> Iterator[Any]:
        it: Iterator[Any] = self._streams[method](request, metadata=self._md(), timeout=timeout)
        return it


class AsyncClient:
    """``grpc.aio`` counterpart of ``Client`` (for asyncio services such as the assistant)."""

    def __init__(
        self,
        channel: grpc.aio.Channel,
        svc: ServiceDescriptor,
        signer: Signer | None,
        audience: str,
        timeout: float = 30.0,
    ) -> None:
        self._signer, self._aud, self._timeout = signer, audience, timeout
        self._unary: dict[str, Any] = {}
        self._streams: dict[str, Any] = {}
        for m in svc.methods:
            req, resp = _classes(m)
            path = full_method(svc, m.name)
            if m.server_streaming:
                self._streams[m.name] = channel.unary_stream(
                    path, request_serializer=req.SerializeToString, response_deserializer=resp.FromString
                )
            else:
                self._unary[m.name] = channel.unary_unary(
                    path, request_serializer=req.SerializeToString, response_deserializer=resp.FromString
                )

    def _md(self) -> tuple[tuple[str, str], ...]:
        return self._signer.metadata(self._aud) if self._signer else ()

    async def call(self, method: str, request: Message, timeout: float | None = None) -> Any:
        return await self._unary[method](request, metadata=self._md(), timeout=timeout or self._timeout)

    async def stream(self, method: str, request: Message) -> AsyncIterator[Any]:
        async for item in self._streams[method](request, metadata=self._md()):
            yield item
