"""Test helpers: write a lake in the production layout (as the Go writer does) from Python frames.

Only tests write lakes from Python; production writes go through marketdata (D4 single writer).
"""

from __future__ import annotations

import math
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import polars as pl

from tbcore.data.calendar import xnys

DT = pl.Datetime("ns", "UTC")
SCHEMA: dict[str, Any] = {
    "instrument_id": pl.Utf8,
    "symbol": pl.Utf8,
    "timeframe": pl.Utf8,
    "ts_open": DT,
    "ts_close": DT,
    "open": pl.Float64,
    "high": pl.Float64,
    "low": pl.Float64,
    "close": pl.Float64,
    "volume": pl.Float64,
    "vwap": pl.Float64,
    "trade_count": pl.Int64,
    "feed": pl.Utf8,
    "source": pl.Utf8,
    "revision": pl.Int32,
    "is_final": pl.Boolean,
    "session": pl.Utf8,
    "repaired": pl.Boolean,
    "adjustment_basis": pl.Utf8,
    "available_at": DT,
    "ingested_at": DT,
}


def ns(dt: datetime) -> int:
    return int(dt.timestamp()) * 1_000_000_000 + dt.microsecond * 1000


def write_bars(
    root: Path, df: pl.DataFrame, dataset: str = "bars", name: str = "part-00000000000000000000000001"
) -> None:
    """Write bars partitioned like the Go writer (feed/timeframe/year/month)."""
    df = df.with_columns(pl.col("ts_open").dt.year().alias("_y"), pl.col("ts_open").dt.month().alias("_m"))
    for (feed, tf, y, m), part in df.group_by(["feed", "timeframe", "_y", "_m"]):
        d = root / dataset / f"feed={feed}" / f"timeframe={tf}" / f"year={y:04d}" / f"month={m:02d}"
        d.mkdir(parents=True, exist_ok=True)
        part.drop(["_y", "_m"]).sort(["instrument_id", "ts_open"]).write_parquet(d / f"{name}.parquet")


def write_ref(root: Path, table: str, df: pl.DataFrame) -> None:
    (root / "ref").mkdir(parents=True, exist_ok=True)
    df.write_parquet(root / "ref" / f"{table}.parquet")


def synth_bars(
    instruments: list[str],
    start: datetime,
    sessions: int,
    timeframe: str = "15m",
    seed: int = 7,
    feed: str = "alpaca.sip",
    delay: timedelta = timedelta(seconds=60),
    extended: bool = True,
    base: dict[str, float] | None = None,
) -> pl.DataFrame:
    """Random-walk bars over the next ``sessions`` XNYS sessions from ``start``."""
    rng = random.Random(seed)
    cal = xnys()
    sess = cal.sessions(start, start + timedelta(days=sessions * 2 + 10))[:sessions]
    rows = []
    for inst in instruments:
        px = (base or {}).get(inst, 50 + rng.random() * 100)
        for s in sess:
            for op in s.bar_opens(timeframe, extended):
                cl = s.bar_close(op, timeframe)
                o = px
                c = max(0.5, o * math.exp(rng.gauss(0, 0.004)))
                h = max(o, c) * (1 + abs(rng.gauss(0, 0.001)))
                lo = min(o, c) * (1 - abs(rng.gauss(0, 0.001)))
                v = float(int(1000 + rng.random() * 50_000))
                part = "regular" if s.open <= op < s.close else ("pre" if op < s.open else "post")
                rows.append(
                    {
                        "instrument_id": inst,
                        "symbol": inst,
                        "timeframe": timeframe,
                        "ts_open": op,
                        "ts_close": cl,
                        "open": round(o, 4),
                        "high": round(h, 4),
                        "low": round(lo, 4),
                        "close": round(c, 4),
                        "volume": v,
                        "vwap": round((o + c + h + lo) / 4, 4),
                        "trade_count": int(v // 50),
                        "feed": feed,
                        "source": "test",
                        "revision": 0,
                        "is_final": True,
                        "session": part,
                        "repaired": False,
                        "adjustment_basis": "raw",
                        "available_at": cl + delay,
                        "ingested_at": cl + delay + timedelta(seconds=1),
                    }
                )
                px = c
    return pl.DataFrame(rows, schema=SCHEMA, orient="row")


def ref_tables(
    root: Path,
    members: dict[str, tuple[int, int]],
    delisted: dict[str, int] | None = None,
    sector: dict[str, str] | None = None,
    symbols: dict[str, str] | None = None,
    universe: str = "dev",
    actions: list[dict[str, Any]] | None = None,
) -> None:
    """Write ref/ tables: instruments, symbols, universe_members, corporate_actions."""
    delisted = delisted or {}
    sector = sector or {}
    symbols = symbols or {}
    ids = sorted(set(members) | set(symbols.values()))
    write_ref(
        root,
        "instruments",
        pl.DataFrame(
            {
                "instrument_id": ids,
                "asset_class": ["us_equity"] * len(ids),
                "currency": ["USD"] * len(ids),
                "name": ids,
                "primary_exchange": ["NASDAQ"] * len(ids),
                "sector_etf": [sector.get(i, "") for i in ids],
                "listed_at": [0] * len(ids),
                "delisted_at": [delisted.get(i, 0) for i in ids],
            }
        ),
    )
    sym_rows = [
        {"instrument_id": i, "symbol": s, "exchange": "NASDAQ", "valid_from": 0, "valid_to": 0, "source": "test"}
        for s, i in symbols.items()
    ]
    write_ref(
        root,
        "symbols",
        pl.DataFrame(
            sym_rows,
            schema={
                "instrument_id": pl.Utf8,
                "symbol": pl.Utf8,
                "exchange": pl.Utf8,
                "valid_from": pl.Int64,
                "valid_to": pl.Int64,
                "source": pl.Utf8,
            },
        ),
    )
    write_ref(
        root,
        "universe_members",
        pl.DataFrame(
            {
                "universe": [universe] * len(members),
                "instrument_id": list(members),
                "valid_from": [v[0] for v in members.values()],
                "valid_to": [v[1] for v in members.values()],
            },
            schema={"universe": pl.Utf8, "instrument_id": pl.Utf8, "valid_from": pl.Int64, "valid_to": pl.Int64},
        ),
    )
    write_actions(root, actions or [])


def write_actions(root: Path, actions: list[dict[str, Any]]) -> None:
    schema = {
        "source": pl.Utf8,
        "source_id": pl.Utf8,
        "instrument_id": pl.Utf8,
        "symbol": pl.Utf8,
        "type": pl.Utf8,
        "ex_date": pl.Int64,
        "ratio": pl.Utf8,
        "amount": pl.Utf8,
        "new_symbol": pl.Utf8,
        "successor_instrument_id": pl.Utf8,
        "record_date": pl.Int64,
        "payable_date": pl.Int64,
        "declared_at": pl.Int64,
        "available_at": pl.Int64,
        "ingested_at": pl.Int64,
        "agreement": pl.Utf8,
    }
    rows = []
    for i, a in enumerate(actions):
        r: dict[str, Any] = dict.fromkeys(schema)
        r.update(
            {
                "source": "alpaca",
                "source_id": f"a{i}",
                "symbol": "",
                "ratio": "",
                "amount": "",
                "new_symbol": "",
                "successor_instrument_id": "",
                "record_date": 0,
                "payable_date": 0,
                "declared_at": 0,
                "ingested_at": 0,
                "agreement": "single_source",
            }
        )
        r.update(a)
        rows.append(r)
    write_ref(root, "corporate_actions", pl.DataFrame(rows, schema=schema, orient="row"))


def et(y: int, mo: int, d: int, h: int = 0, mi: int = 0) -> datetime:
    from zoneinfo import ZoneInfo

    return datetime(y, mo, d, h, mi, tzinfo=ZoneInfo("America/New_York")).astimezone(UTC)
