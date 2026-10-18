"""Test kit: a Python stand-in for marketdata's context sink (same row shape as
``lake.ContextRowsFromEvent``: one row per instrument, canonical payload JSON) and ref tables for
symbol resolution. Production writes go only through marketdata (D4)."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import polars as pl
from tb_context_data.records import Out

from tbcore.context import availability as av
from tbcore.context.canonical import canonical_json, payload_sha256
from tbcore.data.calendar import xnys

ROOT = Path(__file__).resolve().parents[3]
FIX = ROOT / "tests" / "fixtures"
DT = pl.Datetime("ns", "UTC")
IDS = {
    "AAPL": "01AAPL000000000000000000AA",
    "MSFT": "01MSFT000000000000000000MS",
    "NVDA": "01NVDA000000000000000000NV",
    "SPY": "01SPY0000000000000000000SP",
}


def write_refs(root: Path, symbols: dict[str, str] | None = None, universe: str = "dev") -> None:
    symbols = symbols or IDS
    (root / "ref").mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        [
            {"instrument_id": i, "symbol": s, "exchange": "NASDAQ", "valid_from": 0, "valid_to": 0, "source": "test"}
            for s, i in symbols.items()
        ],
        schema={
            "instrument_id": pl.Utf8,
            "symbol": pl.Utf8,
            "exchange": pl.Utf8,
            "valid_from": pl.Int64,
            "valid_to": pl.Int64,
            "source": pl.Utf8,
        },
    ).write_parquet(root / "ref" / "symbols.parquet")
    ids = sorted(symbols.values())
    pl.DataFrame({"instrument_id": ids, "delisted_at": [0] * len(ids)}).write_parquet(
        root / "ref" / "instruments.parquet"
    )
    members = [i for s, i in symbols.items() if s != "SPY"]
    pl.DataFrame(
        {
            "universe": [universe] * len(members),
            "instrument_id": members,
            "valid_from": [0] * len(members),
            "valid_to": [0] * len(members),
        }
    ).write_parquet(root / "ref" / "universe_members.parquet")


_seq = [0]


def sink(root: Path, outs: Sequence[Out], ingested_at: int = 0) -> int:
    """Write published versions to the lake as marketdata's sink would (staging files)."""
    rows: list[dict[str, Any]] = []
    for o in outs:
        raw = canonical_json(o.payload)
        base = {
            "kind": o.kind,
            "event_time": o.event_time,
            "available_at": o.available_at,
            "ingested_at": ingested_at or o.available_at,
            "source": o.source,
            "vintage": o.vintage,
            "payload_json": raw,
            "payload_sha256": payload_sha256(o.payload),
            "record_key": o.record_key,
            "event_id": o.msg_id,
        }
        if not o.instrument_ids:
            rows.append({**base, "instrument_id": "", "symbol": ",".join(o.symbols)})
        for i, inst in enumerate(o.instrument_ids):
            sym = o.symbols[i] if len(o.symbols) == len(o.instrument_ids) else ""
            rows.append({**base, "instrument_id": inst, "symbol": sym})
    if not rows:
        return 0
    df = pl.DataFrame(rows).with_columns(
        [pl.col(c).cast(pl.Int64).cast(DT) for c in ("event_time", "available_at", "ingested_at")]
    )
    df = df.with_columns(pl.col("event_time").dt.year().alias("_y"), pl.col("event_time").dt.month().alias("_m"))
    for (kind, y, m), part in df.group_by(["kind", "_y", "_m"]):
        d = root / "context" / f"kind={kind}" / f"year={y:04d}" / f"month={m:02d}"
        d.mkdir(parents=True, exist_ok=True)
        _seq[0] += 1
        part.drop(["_y", "_m"]).write_parquet(d / f"staging-{_seq[0]:026d}.parquet")
    return len(rows)


def fixture(*parts: str) -> Any:
    return json.loads((FIX.joinpath(*parts)).read_text())


def fixture_transport(routes: dict[str, Any]) -> httpx.MockTransport:
    """Serve ``routes[path]`` (a JSON value or callable(request) -> JSON) and record requests."""
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        fn = routes.get(req.url.path)
        if fn is None:
            return httpx.Response(404, json={"error": "no fixture for " + req.url.path})
        body = fn(req) if callable(fn) else fn
        return httpx.Response(200, json=body)

    t = httpx.MockTransport(handler)
    t.seen = seen  # type: ignore[attr-defined]
    return t


def write_spy_daily(root: Path, spy_id: str, sessions: int = 420) -> None:
    """SPY daily bars (raw, final) in the production bars layout, plus VIXCLS vintages as context."""
    from_ = datetime(2025, 3, 1, tzinfo=UTC)
    sess = xnys().sessions(from_, datetime(2026, 10, 17, tzinfo=UTC))[-sessions:]
    px = 450.0
    rows: list[dict[str, Any]] = []
    vix: list[dict[str, Any]] = []
    for i, s in enumerate(sess):
        px *= 1.0 + (0.003 if i % 5 else -0.008)
        rows.append(
            {
                "instrument_id": spy_id,
                "symbol": "SPY",
                "timeframe": "1d",
                "ts_open": s.open,
                "ts_close": s.close,
                "open": px,
                "high": px * 1.01,
                "low": px * 0.99,
                "close": px,
                "volume": 1e8,
                "vwap": px,
                "trade_count": 1000,
                "feed": "alpaca.sip",
                "source": "alpaca",
                "revision": 0,
                "is_final": True,
                "session": "regular",
                "repaired": False,
                "adjustment_basis": "raw",
                "available_at": s.close + timedelta(seconds=60),
                "ingested_at": s.close + timedelta(seconds=61),
            }
        )
        nxt = s.date + timedelta(days=1)
        vix.append(
            {
                "series_id": "VIXCLS",
                "period": s.date.isoformat(),
                "value": f"{14 + (i % 20) * 0.5:.2f}",
                "realtime_start": nxt.isoformat(),
                "_at": av.macro_available_at("VIXCLS", nxt),
                "_ev": int(s.close.timestamp()) * 1_000_000_000,
            }
        )
    df = pl.DataFrame(rows).with_columns(
        *[pl.col(c).cast(DT) for c in ("ts_open", "ts_close", "available_at", "ingested_at")],
        pl.col("revision").cast(pl.Int32),
    )
    df = df.with_columns(pl.col("ts_open").dt.year().alias("_y"), pl.col("ts_open").dt.month().alias("_m"))
    for (y, m), part in df.group_by(["_y", "_m"]):
        d = root / "bars" / "feed=alpaca.sip" / "timeframe=1d" / f"year={y:04d}" / f"month={m:02d}"
        d.mkdir(parents=True, exist_ok=True)
        part.drop(["_y", "_m"]).write_parquet(d / "part-00000000000000000000000001.parquet")
    outs = [
        Out(
            "macro",
            "alfred",
            f"VIXCLS|{v['period']}",
            v["_ev"],
            v["_at"],
            {k: v[k] for k in ("series_id", "period", "value", "realtime_start")},
            (),
            (),
            v["realtime_start"],
        )
        for v in vix
    ]
    sink(root, outs)
