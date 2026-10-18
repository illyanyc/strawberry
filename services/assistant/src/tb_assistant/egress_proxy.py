"""``llm-egress``: the allowlisting forward proxy for LLM providers (AN-053, ADR-0042).

The assistant container sits only on internal networks; this proxy is the only analyst component on
an egress network. It accepts ``CONNECT host:port`` (HTTPS) and absolute-form HTTP requests (local
OpenAI-compatible servers on the Mac host) and only for ``host:port`` pairs in
``TB_LLM_EGRESS_ALLOW``; everything else gets 403 and a log line with the refused target (never the
request body or headers, which may carry API keys).

Run: ``python -m tb_assistant.egress_proxy`` (listens on ``TB_LLM_EGRESS_LISTEN``, default ``0.0.0.0:3128``).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from urllib.parse import urlsplit

from tbcore.logging import configure

log = logging.getLogger("llm-egress")
MAX_HEADER = 16384


def parse_allow(raw: str) -> frozenset[str]:
    return frozenset(e.strip().lower() for e in raw.split(",") if e.strip())


def target_of(request_line: str) -> tuple[str, str, int, str] | None:
    """(method, host, port, origin-form path) or None for a malformed request line."""
    parts = request_line.split()
    if len(parts) != 3:
        return None
    method, target, _ = parts
    if method.upper() == "CONNECT":
        host, _, port = target.rpartition(":")
        if not host or not port.isdigit():
            return None
        return "CONNECT", host.strip("[]").lower(), int(port), ""
    u = urlsplit(target)
    if u.scheme != "http" or not u.hostname:
        return None
    path = (u.path or "/") + (f"?{u.query}" if u.query else "")
    return method.upper(), u.hostname.lower(), u.port or 80, path


async def _pipe(r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
    with contextlib.suppress(ConnectionError, asyncio.IncompleteReadError):
        while data := await r.read(65536):
            w.write(data)
            await w.drain()
    with contextlib.suppress(Exception):
        w.close()


class EgressProxy:
    def __init__(self, allow: frozenset[str]) -> None:
        self.allow = allow
        self.refused: list[str] = []

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            head = await reader.readuntil(b"\r\n\r\n")
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            writer.close()
            return
        if len(head) > MAX_HEADER:
            await self._reply(writer, 431, "request header too large")
            return
        lines = head.decode("latin-1").split("\r\n")
        t = target_of(lines[0])
        if t is None:
            await self._reply(writer, 400, "bad request")
            return
        method, host, port, path = t
        key = f"{host}:{port}"
        if key not in self.allow:
            self.refused.append(key)
            log.warning("egress refused", extra={"target": key})
            await self._reply(writer, 403, "destination not allowed")
            return
        try:
            up_r, up_w = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=10)
        except (OSError, TimeoutError):
            await self._reply(writer, 502, "upstream unreachable")
            return
        if method == "CONNECT":
            writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
            await writer.drain()
        else:
            version = lines[0].split()[-1]
            headers = [h for h in lines[1:] if h and not h.lower().startswith(("proxy-", "connection:"))]
            out = f"{method} {path} {version}\r\n" + "\r\n".join([*headers, "Connection: close"]) + "\r\n\r\n"
            up_w.write(out.encode("latin-1"))
            await up_w.drain()
        await asyncio.gather(_pipe(reader, up_w), _pipe(up_r, writer))

    @staticmethod
    async def _reply(writer: asyncio.StreamWriter, code: int, reason: str) -> None:
        body = reason.encode()
        writer.write(
            f"HTTP/1.1 {code} {reason}\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode() + body
        )
        with contextlib.suppress(ConnectionError):
            await writer.drain()
        writer.close()


async def serve(allow: frozenset[str], host: str, port: int) -> asyncio.Server:
    proxy = EgressProxy(allow)
    return await asyncio.start_server(proxy.handle, host, port, limit=MAX_HEADER * 2)


def main() -> None:
    configure("llm-egress", os.environ.get("TB_LOG_LEVEL", "info"))
    allow = parse_allow(os.environ.get("TB_LLM_EGRESS_ALLOW", ""))
    host, _, port = os.environ.get("TB_LLM_EGRESS_LISTEN", "0.0.0.0:3128").rpartition(":")
    log.info("llm egress proxy starting", extra={"allow": sorted(allow)})

    async def run() -> None:
        srv = await serve(allow, host, int(port))
        async with srv:
            await srv.serve_forever()

    asyncio.run(run())


if __name__ == "__main__":
    main()
