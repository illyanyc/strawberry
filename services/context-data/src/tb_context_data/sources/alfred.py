"""ALFRED vintages of FRED series (DF-010, CX-006).

``series/observations`` with ``realtime_start=1776-07-04``, ``realtime_end=9999-12-31`` and
``output_type=1`` returns one row per (period, value) and the real-time period it was current, so
every vintage of every period arrives in one request. Each row is a version available at the
series' release time on its ``realtime_start`` (official vintage date); vintages are authoritative,
so a new vintage keeps its official time even when fetched later.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Any

from tb_context_data.http import HttpSource
from tb_context_data.records import Record, Version
from tb_context_data.sources import utc_midnight_ns
from tbcore.context import availability as av


class Alfred:
    def __init__(self, http: HttpSource, key: str, cfg: av.AvailabilityConfig) -> None:
        self.http = http
        self.key = key
        self.cfg = cfg

    async def series(self, series_id: str, observation_start: date | None = None) -> list[Record]:
        params: dict[str, Any] = {
            "series_id": series_id,
            "api_key": self.key,
            "file_type": "json",
            "realtime_start": "1776-07-04",
            "realtime_end": "9999-12-31",
            "output_type": 1,
        }
        if observation_start is not None:
            params["observation_start"] = observation_start.isoformat()
        body = await self.http.get("/fred/series/observations", params)
        return self.records(series_id, body.get("observations", []) if isinstance(body, dict) else [])

    def records(self, series_id: str, observations: list[dict[str, Any]]) -> list[Record]:
        sid = series_id.upper()
        by_period: dict[str, list[Version]] = defaultdict(list)
        for o in observations:
            period, rs = str(o.get("date", "")), str(o.get("realtime_start", ""))
            if not period or not rs:
                continue
            value = str(o.get("value", ""))
            vintage = date.fromisoformat(rs)
            payload = {"series_id": sid, "period": period, "value": "" if value == "." else value, "realtime_start": rs}
            by_period[period].append(Version(payload, av.macro_available_at(sid, vintage, self.cfg), vintage=rs))
        return [
            Record(
                "macro",
                "alfred",
                f"{sid}|{p}",
                utc_midnight_ns(date.fromisoformat(p)),
                tuple(sorted(vs, key=lambda v: v.available_at)),
                authoritative=True,
            )
            for p, vs in sorted(by_period.items())
        ]
