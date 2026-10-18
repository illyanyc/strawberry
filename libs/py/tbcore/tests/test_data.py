"""Tests for the lake query layer, calendar, adjustments, universes, as-of joins, and DQ (section 03)."""

from __future__ import annotations

import json
import random
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import polars as pl
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from tbcore.data import AdjustmentBlockedError, Lake, apply_adjustment, asof_join, to_ns, xnys
from tbcore.dq import dq_check

from .lakekit import SCHEMA, et, ns, ref_tables, synth_bars, write_actions, write_bars, write_ref

REPO = Path(__file__).resolve().parents[4]


def test_calendar_matches_go_copy_and_handles_dst_half_days() -> None:
    go = (REPO / "libs/go/calendar/xnys_sessions.csv").read_bytes()
    py = (REPO / "libs/py/tbcore/src/tbcore/data/xnys_sessions.csv").read_bytes()
    assert go == py, "Go and Python calendar files diverged; run scripts/gen_calendar.py (MD-005)"
    cal = xnys()
    assert cal.session(date(2026, 3, 6)).open == datetime(2026, 3, 6, 14, 30, tzinfo=UTC)  # type: ignore[union-attr]
    assert cal.session(date(2026, 3, 9)).open == datetime(2026, 3, 9, 13, 30, tzinfo=UTC)  # type: ignore[union-attr]
    half = cal.session(date(2025, 11, 28))
    assert half is not None and half.early_close and len(half.bar_opens("15m")) == 14
    assert cal.session(date(2025, 12, 25)) is None
    assert cal.next_open(datetime(2026, 3, 6, 21, tzinfo=UTC)) == datetime(2026, 3, 9, 13, 30, tzinfo=UTC)
    assert not cal.is_open(datetime(2026, 3, 7, 15, tzinfo=UTC))
    assert cal.bar_close_times("1h", date(2025, 11, 28))[-1] == datetime(2025, 11, 28, 18, 0, tzinfo=UTC)


def test_reads_go_written_lake_with_revisions() -> None:
    lake = Lake(REPO / "tests/fixtures/lake")
    s, e = datetime(2026, 3, 2, tzinfo=UTC), datetime(2026, 3, 3, tzinfo=UTC)
    latest = lake.bars(None, "1m", s, e, adjust="raw")
    assert latest.height == 31  # 30 compacted + 1 staged bar, each key once
    corrected = latest.filter(pl.col("ts_open") == datetime(2026, 3, 2, 14, 33, tzinfo=UTC))
    assert corrected["close"][0] == 99.99 and corrected["revision"][0] == 1
    asof = lake.bars(None, "1m", s, e, as_of=datetime(2026, 3, 2, 15, tzinfo=UTC), adjust="raw")
    orig = asof.filter(pl.col("ts_open") == datetime(2026, 3, 2, 14, 33, tzinfo=UTC))
    assert orig["revision"][0] == 0 and orig["close"][0] == pytest.approx(50.4)
    first = lake.bars(None, "1m", s, e, adjust="raw", versions="first")
    assert first.filter(pl.col("ts_open") == datetime(2026, 3, 2, 14, 33, tzinfo=UTC))["revision"][0] == 0


def test_dq_flags_injected_defects_same_as_go() -> None:
    fx = REPO / "tests/fixtures/dq"
    df = pl.read_csv(fx / "bars_1m_injected.csv").with_columns(
        pl.col("ts_open").str.to_datetime(time_zone="UTC"),
        pl.col("ts_close").str.to_datetime(time_zone="UTC"),
        pl.col("volume").cast(pl.Float64),
    )
    exp = json.loads((fx / "expected.json").read_text())
    rep = dq_check(
        df, datetime(2026, 3, 6, tzinfo=UTC), datetime(2026, 3, 7, tzinfo=UTC), "1m", instruments=["INSTA", "INSTB"]
    )
    assert dict(rep.counts) == exp["counts"]
    g = rep.gaps[0]
    assert (g.instrument_id, g.missing) == ("INSTA", 10)
    assert g.start == datetime(2026, 3, 6, 15, 0, tzinfo=UTC) and g.end == datetime(2026, 3, 6, 15, 10, tzinfo=UTC)
    assert all(f.instrument_id != "INSTB" for f in rep.findings)


def _daily(inst: str, days: list[date], closes: list[float]) -> pl.DataFrame:
    cal = xnys()
    rows = []
    for d, c in zip(days, closes, strict=True):
        s = cal.session(d)
        assert s is not None
        rows.append(
            {
                "instrument_id": inst,
                "symbol": inst,
                "timeframe": "1d",
                "ts_open": s.open,
                "ts_close": s.close,
                "open": c,
                "high": c,
                "low": c,
                "close": c,
                "volume": 1000.0,
                "vwap": c,
                "trade_count": 10,
                "feed": "alpaca.sip",
                "source": "t",
                "revision": 0,
                "is_final": True,
                "session": "regular",
                "repaired": False,
                "adjustment_basis": "raw",
                "available_at": s.close + timedelta(minutes=1),
                "ingested_at": s.close + timedelta(minutes=2),
            }
        )
    return pl.DataFrame(rows, schema=SCHEMA, orient="row")


def test_df005_adjustment_golden_splits_dividends_and_announcement_timing(tmp_path: Path) -> None:
    # AAPL 4:1 split, ex 2020-08-31 (announced 2020-07-30); NVDA 10:1 split, ex 2024-06-10 (announced 2024-05-22).
    aapl = _daily(
        "AAPL",
        [date(2020, 8, 27), date(2020, 8, 28), date(2020, 8, 31), date(2020, 9, 1)],
        [500.0, 499.23, 129.04, 134.18],
    )
    nvda = _daily("NVDA", [date(2024, 6, 6), date(2024, 6, 7), date(2024, 6, 10)], [1209.98, 1208.88, 121.79])
    div = _daily("KO", [date(2025, 3, 13), date(2025, 3, 14)], [70.0, 69.49])
    write_bars(tmp_path, pl.concat([aapl, nvda, div]))
    ref_tables(
        tmp_path,
        {"AAPL": (0, 0), "NVDA": (0, 0), "KO": (0, 0)},
        actions=[
            {
                "instrument_id": "AAPL",
                "type": "split",
                "ratio": "4",
                "ex_date": ns(et(2020, 8, 31)),
                "available_at": ns(et(2020, 7, 30, 16, 30)),
            },
            {
                "instrument_id": "NVDA",
                "type": "split",
                "ratio": "10",
                "ex_date": ns(et(2024, 6, 10)),
                "available_at": ns(et(2024, 5, 22, 16, 30)),
            },
            {
                "instrument_id": "KO",
                "type": "dividend",
                "amount": "0.51",
                "ex_date": ns(et(2025, 3, 14)),
                "available_at": ns(et(2025, 2, 13, 8)),
            },
        ],
    )
    lake = Lake(tmp_path)
    s, e = datetime(2020, 1, 1, tzinfo=UTC), datetime(2026, 1, 1, tzinfo=UTC)
    adj = lake.bars(["AAPL"], "1d", s, e, adjust="split")
    assert adj["close"].to_list() == pytest.approx([125.0, 124.8075, 129.04, 134.18])
    assert adj["volume"].to_list() == [4000.0, 4000.0, 1000.0, 1000.0]
    assert set(adj["adjustment_basis"]) == {"split"}
    nv = lake.bars(["NVDA"], "1d", s, e, adjust="split")
    assert nv["close"].to_list() == pytest.approx([120.998, 120.888, 121.79])
    # As of 2024-06-07 the split is announced but not yet effective: no adjustment (price at as_of is pre-split).
    pre = lake.bars(["NVDA"], "1d", s, e, as_of=et(2024, 6, 7, 17), adjust="split")
    assert pre["close"].to_list() == pytest.approx([1209.98, 1208.88])
    # As of a date before the announcement nothing is applied either.
    assert lake.bars(["AAPL"], "1d", s, e, as_of=et(2020, 8, 28, 17), adjust="split")[
        "close"
    ].to_list() == pytest.approx([500.0, 499.23])
    ko = lake.bars(["KO"], "1d", s, e, adjust="split_div")
    assert ko["close"].to_list() == pytest.approx([70.0 * (1 - 0.51 / 70.0), 69.49])
    assert lake.bars(["KO"], "1d", s, e, adjust="split")["close"].to_list() == pytest.approx([70.0, 69.49])
    assert lake.bars(["KO"], "1d", s, e, adjust="raw")["close"].to_list() == pytest.approx([70.0, 69.49])


def test_md012_disagreement_blocks_adjustment(tmp_path: Path) -> None:
    write_bars(tmp_path, _daily("X", [date(2024, 6, 6), date(2024, 6, 7)], [10.0, 5.0]))
    ref_tables(
        tmp_path,
        {"X": (0, 0)},
        actions=[
            {
                "instrument_id": "X",
                "type": "split",
                "ratio": "2",
                "ex_date": ns(et(2024, 6, 7)),
                "available_at": ns(et(2024, 6, 1)),
                "agreement": "disagreed",
            }
        ],
    )
    lake = Lake(tmp_path)
    with pytest.raises(AdjustmentBlockedError):
        lake.bars(["X"], "1d", datetime(2024, 1, 1, tzinfo=UTC), datetime(2025, 1, 1, tzinfo=UTC))
    assert (
        lake.bars(["X"], "1d", datetime(2024, 1, 1, tzinfo=UTC), datetime(2025, 1, 1, tzinfo=UTC), adjust="raw").height
        == 2
    )


def test_md012_owner_rejected_action_is_ignored(tmp_path: Path) -> None:
    write_bars(tmp_path, _daily("X", [date(2024, 6, 6), date(2024, 6, 7)], [10.0, 5.0]))
    base = {"instrument_id": "X", "type": "split", "ex_date": ns(et(2024, 6, 7)), "available_at": ns(et(2024, 6, 1))}
    ref_tables(
        tmp_path,
        {"X": (0, 0)},
        actions=[
            {**base, "source": "alpaca", "ratio": "3", "agreement": "rejected"},
            {**base, "source": "fmp", "ratio": "2", "agreement": "confirmed"},
        ],
    )
    adj = Lake(tmp_path).bars(["X"], "1d", datetime(2024, 1, 1, tzinfo=UTC), datetime(2025, 1, 1, tzinfo=UTC))
    assert adj["close"].to_list() == [5.0, 5.0]


def test_df006_universe_includes_delisted_while_listed(tmp_path: Path) -> None:
    ref_tables(
        tmp_path,
        {"LIVE": (ns(et(2015, 1, 2)), 0), "GONE": (ns(et(2015, 1, 2)), 0)},
        delisted={"GONE": ns(et(2022, 3, 1))},
        symbols={"LIVE": "LIVE", "GONE": "GONE"},
    )
    lake = Lake(tmp_path)
    assert lake.universe("dev", et(2021, 6, 1)) == ["GONE", "LIVE"]
    assert lake.universe("dev", et(2023, 6, 1)) == ["LIVE"]
    assert lake.universe("dev", et(2014, 6, 1)) == []
    assert lake.resolve("gone", et(2020, 1, 2)) == "GONE"


def _versions(rng: random.Random, n_keys: int) -> pl.DataFrame:
    base = datetime(2026, 1, 5, 15, tzinfo=UTC)
    rows = []
    for k in range(n_keys):
        t = base + timedelta(minutes=k)
        avail = t + timedelta(minutes=1, seconds=rng.randint(0, 120))
        for rev in range(rng.randint(1, 3)):
            rows.append(
                {
                    "instrument_id": f"I{k % 3}",
                    "symbol": "S",
                    "timeframe": "1m",
                    "ts_open": t,
                    "ts_close": t + timedelta(minutes=1),
                    "open": 1.0,
                    "high": 1.0,
                    "low": 1.0,
                    "close": float(rev + 1),
                    "volume": 1.0,
                    "vwap": 1.0,
                    "trade_count": 1,
                    "feed": "alpaca.sip",
                    "source": "t",
                    "revision": rev,
                    "is_final": True,
                    "session": "regular",
                    "repaired": False,
                    "adjustment_basis": "raw",
                    "available_at": avail,
                    "ingested_at": avail,
                }
            )
            avail += timedelta(seconds=rng.randint(1, 3600))
    return pl.DataFrame(rows, schema=SCHEMA, orient="row")


def test_dfi1_property_as_of_never_returns_future_rows(tmp_path: Path) -> None:
    """DF-I1: 10,000 random (dataset, as_of) cases; bars(as_of=t) never returns available_at > t and
    always returns, per key, the latest revision known at t."""
    rng = random.Random(20261017)
    cases = 0
    for ds in range(10):
        root = tmp_path / f"lake{ds}"
        df = _versions(rng, 40)
        latest = df.sort("revision").group_by(["instrument_id", "ts_open"]).last()
        older = (
            df.join(
                latest.select("instrument_id", "ts_open", pl.col("revision").alias("_max")),
                on=["instrument_id", "ts_open"],
            )
            .filter(pl.col("revision") < pl.col("_max"))
            .drop("_max")
        )
        write_bars(root, latest.select(list(SCHEMA)))
        if older.height:
            write_bars(root, older.select(list(SCHEMA)), dataset="bar_revisions")
        lake = Lake(root)
        lo, hi = to_ns(df["available_at"].min()), to_ns(df["available_at"].max()) + 10**9  # type: ignore[arg-type]
        s, e = datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 2, 1, tzinfo=UTC)
        full = lake.bars(None, "1m", s, e, as_of=hi, adjust="raw")  # every version visible
        assert full.height == latest.height
        for _ in range(1000):
            a = rng.randint(lo - 10**9, hi)
            got = lake._select(df.lazy(), a)  # same selection the reader applies, on the in-memory frame
            assert got.filter(pl.col("available_at").dt.epoch("ns") > a).height == 0
            exp = (
                df.filter(pl.col("available_at").dt.epoch("ns") <= a)
                .sort("revision")
                .group_by(["instrument_id", "ts_open"])
                .last()
            )
            assert got.height == exp.height
            if got.height:
                j = got.join(exp, on=["instrument_id", "ts_open"], suffix="_e")
                assert (j["revision"] == j["revision_e"]).all()
            cases += 1
        for a in rng.sample(range(lo, hi), 20):  # and through the on-disk reader
            disk = lake.bars(None, "1m", s, e, as_of=a, adjust="raw")
            assert disk.filter(pl.col("available_at").dt.epoch("ns") > a).height == 0
    assert cases == 10_000


@settings(max_examples=300, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    st.lists(
        st.tuples(st.integers(0, 9), st.integers(0, 9), st.sampled_from([2.0, 4.0, 0.5, 10.0])), min_size=0, max_size=4
    ),
    st.integers(0, 9),
)
def test_df005_property_adjustment_uses_only_known_actions(acts: list[tuple[int, int, float]], asof_day: int) -> None:
    """Random splits (ex day, announcement day, ratio): the adjusted series as of day d equals the raw
    series times the product of 1/ratio over actions with ex in (bar, d] and announced by d."""
    base = datetime(2025, 1, 6, 14, 30, tzinfo=UTC)
    days = [base + timedelta(days=i) for i in range(10)]
    bars = pl.DataFrame(
        {
            "instrument_id": ["X"] * 10,
            "ts_open": days,
            "open": [100.0] * 10,
            "high": [100.0] * 10,
            "low": [100.0] * 10,
            "close": [100.0] * 10,
            "vwap": [100.0] * 10,
            "volume": [1.0] * 10,
        },
        schema={
            "instrument_id": pl.Utf8,
            "ts_open": pl.Datetime("ns", "UTC"),
            "open": pl.Float64,
            "high": pl.Float64,
            "low": pl.Float64,
            "close": pl.Float64,
            "vwap": pl.Float64,
            "volume": pl.Float64,
        },
    )
    asof = ns(days[asof_day] + timedelta(hours=12))
    ex_seen: set[int] = set()
    rows = []
    for ex_d, ann_d, ratio in acts:
        if ex_d in ex_seen:
            continue
        ex_seen.add(ex_d)
        rows.append(
            {
                "instrument_id": "X",
                "type": "split",
                "ratio": str(ratio),
                "amount": "",
                "ex_date": ns(days[ex_d] - timedelta(hours=10)),
                "available_at": ns(days[min(ann_d, ex_d)] - timedelta(hours=11)),
                "agreement": "single_source",
                "source": "t",
            }
        )
    ca = pl.DataFrame(
        rows,
        schema={
            "instrument_id": pl.Utf8,
            "type": pl.Utf8,
            "ratio": pl.Utf8,
            "amount": pl.Utf8,
            "ex_date": pl.Int64,
            "available_at": pl.Int64,
            "agreement": pl.Utf8,
            "source": pl.Utf8,
        },
    )
    known = ca.filter((pl.col("available_at") <= asof) & (pl.col("ex_date") <= asof))
    out = apply_adjustment(bars, known, "split")
    for i, t in enumerate(days):
        f = 1.0
        for r in known.iter_rows(named=True):
            if r["ex_date"] > ns(t):
                f /= float(r["ratio"])
        assert out["close"][i] == pytest.approx(100.0 * f)


def test_asof_join_fundamentals_snapshots_point_in_time() -> None:
    """FMP restates fundamentals in place; each fetch is a snapshot with its own available_at. A
    decision can only see snapshots fetched at or before it (DF-002, DF-010)."""
    snaps = pl.DataFrame(
        {
            "instrument_id": ["A", "A", "A", "B"],
            "available_at": [et(2026, 1, 29, 16, 5), et(2026, 2, 3, 9), et(2026, 2, 20, 9), et(2026, 1, 15)],
            "eps": [1.10, 1.12, 1.15, 0.5],
        }
    )
    decisions = pl.DataFrame(
        {
            "instrument_id": ["A", "A", "A", "A", "B", "C"],
            "decision_time": [
                et(2026, 1, 29, 16),
                et(2026, 1, 30, 10),
                et(2026, 2, 3, 9),
                et(2026, 3, 1),
                et(2026, 2, 1),
                et(2026, 2, 1),
            ],
        }
    )
    out = asof_join(decisions, snaps)
    assert out["eps"].to_list() == [None, 1.10, 1.12, 1.15, 0.5, None]
    tol = asof_join(decisions, snaps, tolerance_ns=7 * 86400 * 10**9)
    assert tol["eps"].to_list() == [None, 1.10, 1.12, None, None, None]


def test_context_snapshots_as_of(tmp_path: Path) -> None:
    d = tmp_path / "context" / "kind=fundamentals" / "year=2025" / "month=12"
    d.mkdir(parents=True)
    ev = datetime(2025, 12, 31, tzinfo=UTC)
    pl.DataFrame(
        {
            "kind": ["fundamentals"] * 2,
            "instrument_id": ["A", "A"],
            "symbol": ["A", "A"],
            "event_time": [ev, ev],
            "available_at": [et(2026, 2, 1), et(2026, 2, 10)],
            "ingested_at": [et(2026, 2, 1), et(2026, 2, 10)],
            "source": ["fmp", "fmp"],
            "vintage": ["", ""],
            "payload_json": ['{"eps":1.10}', '{"eps":1.12}'],
            "payload_sha256": ["a", "b"],
        },
        schema_overrides={
            "event_time": pl.Datetime("ns", "UTC"),
            "available_at": pl.Datetime("ns", "UTC"),
            "ingested_at": pl.Datetime("ns", "UTC"),
        },
    ).write_parquet(d / "part-1.parquet")
    lake = Lake(tmp_path)
    assert lake.context("fundamentals", ["A"], as_of=et(2026, 2, 5))["payload_json"].to_list() == ['{"eps":1.10}']
    assert lake.context("fundamentals", ["A"], as_of=et(2026, 3, 1))["payload_json"].to_list() == ['{"eps":1.12}']
    assert lake.context("fundamentals", ["A"], as_of=et(2026, 1, 1)).height == 0


def test_feed_preference_and_explicit_feed(tmp_path: Path) -> None:
    s = datetime(2026, 3, 2, tzinfo=UTC)
    sip = synth_bars(["A"], s, 1, "15m", extended=False)
    iex = sip.with_columns(pl.lit("alpaca.iex").alias("feed"), (pl.col("volume") * 0.03).alias("volume"))
    write_bars(tmp_path, pl.concat([sip, iex]))
    write_actions(tmp_path, [])
    lake = Lake(tmp_path)
    both = lake.bars(None, "15m", s, s + timedelta(days=2), adjust="raw")
    assert set(both["feed"]) == {"alpaca.sip"} and both.height == sip.height
    only = lake.bars(None, "15m", s, s + timedelta(days=2), adjust="raw", feed="alpaca.iex")
    assert set(only["feed"]) == {"alpaca.iex"}


def test_naive_datetimes_rejected() -> None:
    with pytest.raises(ValueError, match="naive"):
        to_ns(datetime(2026, 1, 1))


def test_calendar_is_fresh() -> None:
    """MD-005: the committed calendar equals a fresh generation from the pinned exchange_calendars."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("gen_calendar", REPO / "scripts/gen_calendar.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    fresh = mod.generate()
    for target in mod.TARGETS:
        assert target.read_text() == fresh, f"{target} is stale; run make gen-calendar"


def test_md010_quarantined_symbol_days_excluded(tmp_path: Path) -> None:
    s = datetime(2026, 3, 2, tzinfo=UTC)
    write_bars(tmp_path, synth_bars(["A", "B"], s, 2, "15m", extended=False))
    write_actions(tmp_path, [])
    write_ref(
        tmp_path,
        "quarantine",
        pl.DataFrame(
            {"instrument_id": ["A"], "session_date": ["2026-03-03"], "reason": ["recon_breach"], "detail": [""]}
        ),
    )
    lake = Lake(tmp_path)
    allb = lake.bars(None, "15m", s, s + timedelta(days=3), adjust="raw")
    clean = lake.bars(None, "15m", s, s + timedelta(days=3), adjust="raw", exclude_quarantined=True)
    assert allb.height - clean.height == 26
    dropped = clean.filter(
        (pl.col("instrument_id") == "A")
        & (pl.col("ts_open").dt.convert_time_zone("America/New_York").dt.date() == date(2026, 3, 3))
    )
    assert dropped.height == 0
