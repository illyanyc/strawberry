"""``instrument_id`` <-> IBKR ``conId`` resolution.

Orders are routed by ``conId``, never by ticker (section 04 pitfalls, VP-010). The platform
instrument master (section 03) is the source of truth; until it serves IBKR IDs, the adapter reads
a static map (``TB_IBKR_INSTRUMENTS_PATH``, JSON list of :class:`Instrument`) and, for symbols outside
it, IBKR's own contract details (:class:`IbkrContractResolver`).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

from tb_broker_ibkr.models import Instrument
from tb_broker_ibkr.port import IbPort


class UnknownInstrumentError(LookupError):
    pass


class InstrumentResolver(Protocol):
    async def resolve(self, instrument_id: str) -> Instrument: ...

    async def resolve_symbol(self, symbol: str) -> Instrument: ...

    def instrument_id_for(self, con_id: int) -> str:
        """Reverse lookup for broker-originated records; empty string when unknown."""
        ...


class StaticInstrumentResolver:
    def __init__(self, instruments: list[Instrument]) -> None:
        self._by_id = {i.instrument_id: i for i in instruments}
        self._by_con = {i.con_id: i.instrument_id for i in instruments}
        self._by_symbol = {i.symbol: i for i in instruments if i.symbol}

    @classmethod
    def from_file(cls, path: str | Path) -> StaticInstrumentResolver:
        if not path:
            return cls([])
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls([Instrument.model_validate(item) for item in raw])

    async def resolve(self, instrument_id: str) -> Instrument:
        try:
            return self._by_id[instrument_id]
        except KeyError:
            raise UnknownInstrumentError(instrument_id) from None

    async def resolve_symbol(self, symbol: str) -> Instrument:
        try:
            return self._by_symbol[symbol]
        except KeyError:
            raise UnknownInstrumentError(symbol) from None

    def instrument_id_for(self, con_id: int) -> str:
        return self._by_con.get(con_id, "")


class IbkrContractResolver:
    """IB-027: the static map first, then IBKR contract details (SMART/USD stocks), cached.

    A symbol must resolve to exactly one US stock/ETF contract; ambiguity is an unknown instrument,
    never a guess. ``instrument_id`` stays empty for contracts outside the instrument map.
    """

    def __init__(self, static: StaticInstrumentResolver, port: IbPort) -> None:
        self._static = static
        self._port = port
        self._cache: dict[str, Instrument] = {}

    async def resolve(self, instrument_id: str) -> Instrument:
        return await self._static.resolve(instrument_id)

    async def resolve_symbol(self, symbol: str) -> Instrument:
        try:
            return await self._static.resolve_symbol(symbol)
        except UnknownInstrumentError:
            pass
        if symbol in self._cache:
            return self._cache[symbol]
        if not symbol or not self._port.is_connected():
            raise UnknownInstrumentError(symbol)
        matches = await self._port.contract_details(symbol=symbol)
        if len(matches) != 1:
            raise UnknownInstrumentError(f"{symbol}: {len(matches)} IBKR contracts")
        c = matches[0]
        instrument = Instrument(
            instrument_id=self._static.instrument_id_for(c.con_id),
            con_id=c.con_id,
            symbol=c.symbol,
            primary_exchange=c.primary_exchange,
            currency=c.currency,
            min_tick=c.min_tick,
        )
        self._cache[symbol] = instrument
        return instrument

    def instrument_id_for(self, con_id: int) -> str:
        return self._static.instrument_id_for(con_id)
