"""``instrument_id`` <-> IBKR ``conId`` resolution.

Orders are routed by ``conId``, never by ticker (section 04 pitfalls, VP-010). The platform
instrument master (section 03) is the source of truth; until it serves IBKR IDs, the adapter reads
a static map (``TB_IBKR_INSTRUMENTS_PATH``, JSON list of :class:`Instrument`).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

from tb_broker_ibkr.models import Instrument


class UnknownInstrumentError(LookupError):
    pass


class InstrumentResolver(Protocol):
    async def resolve(self, instrument_id: str) -> Instrument: ...

    def instrument_id_for(self, con_id: int) -> str:
        """Reverse lookup for broker-originated records; empty string when unknown."""
        ...


class StaticInstrumentResolver:
    def __init__(self, instruments: list[Instrument]) -> None:
        self._by_id = {i.instrument_id: i for i in instruments}
        self._by_con = {i.con_id: i.instrument_id for i in instruments}

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

    def instrument_id_for(self, con_id: int) -> str:
        return self._by_con.get(con_id, "")
