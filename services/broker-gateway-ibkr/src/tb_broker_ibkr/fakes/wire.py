"""A TCP server that speaks enough of the TWS API socket protocol for the real ``ib_async`` client.

Covers the session layer, which is where IB Gateway behaviour matters most for supervision:
the ``API\\0`` handshake and version negotiation, ``startApi``, ``managedAccounts`` and
``nextValidId``, the end-markers of every startup/resync request (positions, open and completed
orders, executions, account summary), ``reqCurrentTime`` heartbeats, error/status codes (1100,
1102, 2104, ...), a gateway that accepts TCP but has no API session (IBC waiting for 2FA), and a
gateway restart (all sockets dropped). It does not model orders on the wire; order behaviour is
covered by :mod:`tb_broker_ibkr.fakes.gateway` behind the port seam.

Message framing: 4-byte big-endian length, then NUL-terminated ASCII fields.
"""

from __future__ import annotations

import asyncio
import contextlib
import struct
import time
from dataclasses import dataclass, field

SERVER_VERSION = 176  # inside ib_async 2.1's supported range (157..178); >= 166 adds the error JSON field


def frame(*fields: object) -> bytes:
    payload = b"".join(str(f).encode() + b"\0" for f in fields)
    return struct.pack(">I", len(payload)) + payload


@dataclass
class WireGateway:
    managed_accounts: list[str] = field(default_factory=lambda: ["DU0000001"])
    accepting_api: bool = True
    answer_heartbeats: bool = True
    host: str = "127.0.0.1"
    port: int = 0
    requests: list[tuple[int, list[str]]] = field(default_factory=list)
    handshakes: int = 0
    _server: asyncio.Server | None = None
    _writers: set[asyncio.StreamWriter] = field(default_factory=set)
    _next_id: int = 1

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, self.host, self.port)
        sock = self._server.sockets[0]
        self.port = int(sock.getsockname()[1])

    async def stop(self) -> None:
        self.drop_all()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    def drop_all(self) -> None:
        """Gateway restart: every API socket closes."""
        for w in list(self._writers):
            w.close()
        self._writers.clear()

    def send_error(self, code: int, message: str, req_id: int = -1) -> None:
        for w in list(self._writers):
            w.write(frame(4, 2, req_id, code, message, ""))

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        if not self.accepting_api:
            # socat forwards the TCP connection but nothing listens behind it yet.
            writer.close()
            return
        try:
            prefix = await reader.readexactly(4)
            if prefix != b"API\0":
                writer.close()
                return
            await self._read_msg(reader)  # "v157..178[ options]"
            self.handshakes += 1
            writer.write(frame(SERVER_VERSION, time.strftime("%Y%m%d %H:%M:%S UTC", time.gmtime())))
            self._writers.add(writer)
            while True:
                fields = await self._read_msg(reader)
                if not fields:
                    continue
                self._respond(writer, int(fields[0]), fields)
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            self._writers.discard(writer)
            with contextlib.suppress(Exception):
                writer.close()

    @staticmethod
    async def _read_msg(reader: asyncio.StreamReader) -> list[str]:
        (size,) = struct.unpack(">I", await reader.readexactly(4))
        payload = (await reader.readexactly(size)).decode(errors="replace")
        parts = payload.split("\0")
        return parts[:-1] if parts and parts[-1] == "" else parts

    def _respond(self, w: asyncio.StreamWriter, msg_id: int, fields: list[str]) -> None:
        self.requests.append((msg_id, fields))
        accounts = ",".join(self.managed_accounts)
        if msg_id == 71:  # startApi
            w.write(frame(15, 1, accounts))
            w.write(frame(9, 1, self._next_id))
        elif msg_id == 17:  # reqManagedAccts
            w.write(frame(15, 1, accounts))
        elif msg_id == 8:  # reqIds
            w.write(frame(9, 1, self._next_id))
        elif msg_id == 61:  # reqPositions
            w.write(frame(62, 1))
        elif msg_id in (5, 16):  # reqOpenOrders / reqAllOpenOrders
            w.write(frame(53, 1))
        elif msg_id == 99:  # reqCompletedOrders
            w.write(frame(102))
        elif msg_id == 7:  # reqExecutions(reqId, filter)
            w.write(frame(55, 1, fields[2]))
        elif msg_id == 62:  # reqAccountSummary(reqId, group, tags)
            w.write(frame(64, 1, fields[2]))
        elif msg_id == 6 and len(fields) > 2 and fields[2] in ("1", "True"):  # reqAccountUpdates
            w.write(frame(54, 1, fields[3] if len(fields) > 3 else ""))
        elif msg_id == 76:  # reqAccountUpdatesMulti(reqId, ...)
            w.write(frame(74, 1, fields[2]))
        elif msg_id == 49 and self.answer_heartbeats:  # reqCurrentTime
            w.write(frame(49, 1, int(time.time())))
