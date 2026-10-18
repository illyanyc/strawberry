"""Section 09 context data in tbcore: availability rules, point-in-time reads, news dedupe,
sentiment aggregates, regimes, event blackouts, and context features (rules CX-*)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import polars as pl
import pytest

from tbcore.context import (
    BlackoutStrategy,
    ContextFeatureSpec,
    ContextStore,
    EventBlackout,
    InMemoryEventSource,
    ScheduledEvent,
    Timing,
    aggregate,
    canonical_json,
    categorize,
    compute_regimes,
    context_features,
    find_duplicates,
    parse_timing,
    payload_sha256,
    stamp,
)
from tbcore.context import availability as av
from tbcore.context.news import Article
from tbcore.data.calendar import xnys
from tbcore.data.lake import Lake
from tbcore.features import materialize, mvp_feature_set
from tbcore.strategy import Bar, OpeningRangeBreakout, ORBParams, StrategyContext

from .lakekit import context_row, et, ns, ref_tables, synth_bars, write_bars, write_context

FIXTURE_LAKE = Path(__file__).resolve().parents[4] / "tests" / "fixtures" / "lake_context"
NS = 1_000_000_000


def t(dt: datetime) -> int:
    return ns(dt)


# ---------------------------------------------------------------- availability rules


def test_cx003_canonical_json_matches_go() -> None:
    p = {"b": 100.0, "a": "x<y & z>", "c": [1.5, True, None], "d": {"z": "\u2028"}}
    assert canonical_json(p) == '{"a":"x\\u003cy \\u0026 z\\u003e","b":100,"c":[1.5,true,null],"d":{"z":"\\u2028"}}'
    # Same digest as TestCX003CanonicalPayloadDigest in services/marketdata/internal/lake.
    assert payload_sha256(p) == "8b51f256dc7659d481e166d1d6d15eb8ed8fa90a084aa81c8a50da2113e395b1"


def test_cx004_stamp_first_seen_rule_changed_fetch_time_unchanged_none() -> None:
    assert stamp(rule_ns=100, fetched_ns=500, previous_sha=None, sha="a") == 100
    assert stamp(rule_ns=100, fetched_ns=500, previous_sha="a", sha="a") is None
    assert stamp(rule_ns=100, fetched_ns=500, previous_sha="a", sha="b") == 500
    assert stamp(rule_ns=900, fetched_ns=500, previous_sha="a", sha="b") == 900  # never before the rule


@pytest.mark.parametrize(
    ("raw", "want"),
    [
        ("bmo", Timing.BMO),
        ("AMC", Timing.AMC),
        ("pre-market", Timing.BMO),
        ("post market", Timing.AMC),
        ("08:00", Timing.BMO),
        ("16:05", Timing.AMC),
        ("12:00", Timing.DMH),
        ("", Timing.UNKNOWN),
        ("--", Timing.UNKNOWN),
    ],
)
def test_cx007_parse_timing(raw: str, want: Timing) -> None:
    assert parse_timing(raw) == want


def test_cx007_amc_result_available_after_session_bmo_at_open() -> None:
    d = date(2026, 7, 30)
    amc = av.earnings_actual_available_at(d, Timing.AMC)
    unknown = av.earnings_actual_available_at(d, Timing.UNKNOWN)
    s = xnys().session(d)
    assert s is not None
    assert amc > int(s.post_close.timestamp()) * NS - 1  # after the extended session
    assert unknown == amc  # unknown timing is treated like AMC (conservative)
    assert av.earnings_actual_available_at(d, Timing.BMO) == int(s.open.timestamp()) * NS


def test_cx005_finra_publication_date_from_schedule_and_fallback() -> None:
    assert av.finra_publication_date(date(2026, 4, 15)) == date(2026, 4, 24)
    # Outside the seeded schedule: 8 XNYS sessions after settlement (schedule uses 7).
    pub = av.finra_publication_date(date(2024, 6, 14))
    sessions = xnys().sessions(datetime(2024, 6, 15, tzinfo=UTC), datetime(2024, 7, 15, tzinfo=UTC))
    assert pub == sessions[7].date
    at = av.finra_available_at(date(2026, 4, 15))
    assert at == t(et(2026, 4, 24, 23, 59))
    assert at > t(et(2026, 4, 15, 16, 0))  # never the settlement date


def test_cx009_edgar_acceptance_is_never_before_the_true_acceptance() -> None:
    # Recorded 2026-10-18: AAPL Form 4 0001140361-26-039785. Submissions API says
    # "2026-10-16T02:30:17.000Z"; the filing header says ACCEPTANCE-DATETIME 20261015183017 (New York).
    true_acceptance = t(et(2026, 10, 15, 18, 30)) + 17 * NS
    got = av.edgar_available_at("2026-10-16T02:30:17.000Z")
    assert got >= true_acceptance
    assert got - true_acceptance == (4 * 3600 + 300) * NS  # +4 h label offset (EDT) + 5 min delay


def test_cx006_macro_release_time_per_series() -> None:
    assert av.macro_available_at("PAYEMS", date(2026, 6, 5)) == t(et(2026, 6, 5, 8, 30))
    assert av.macro_available_at("UNKNOWNSERIES", date(2026, 6, 5)) == t(et(2026, 6, 5, 23, 59))


def test_cx008_fundamentals_accepted_date_is_new_york_time() -> None:
    assert av.fundamentals_available_at("2026-07-31 06:01:36") == t(et(2026, 7, 31, 6, 1)) + 36 * NS + 300 * NS


# ---------------------------------------------------------------- point-in-time reads


def test_cx002_go_written_context_fixture_vintages_as_of() -> None:
    """Go-written context files (one compacted part, one staging file) read point-in-time."""
    store = ContextStore(Lake(FIXTURE_LAKE))
    before = store.macro("PAYEMS", et(2026, 6, 30))
    after = store.macro("PAYEMS", et(2026, 7, 10))
    assert before["value"].to_list() == ["159100"] and before["vintage"].to_list() == ["2026-06-05"]
    assert after["value"].to_list() == ["159020"] and after["vintage"].to_list() == ["2026-07-03"]
    assert store.macro("PAYEMS", et(2026, 6, 5, 8, 0)).is_empty()  # before the first release


def _payems_lake(root: Path) -> None:
    """Nonfarm payrolls March 2026 with three vintages (first release, two revisions)."""
    rows = []
    for vintage, value in (("2026-04-03", "158900"), ("2026-05-08", "158760"), ("2026-06-05", "158810")):
        d = date.fromisoformat(vintage)
        rows.append(
            context_row(
                "macro",
                "PAYEMS|2026-03-01",
                t(datetime(2026, 3, 1, tzinfo=UTC)),
                av.macro_available_at("PAYEMS", d),
                {"series_id": "PAYEMS", "period": "2026-03-01", "value": value, "realtime_start": vintage},
                source="alfred",
                vintage=vintage,
            )
        )
    rows.append(
        context_row(
            "macro",
            "PAYEMS|2026-04-01",
            t(datetime(2026, 4, 1, tzinfo=UTC)),
            av.macro_available_at("PAYEMS", date(2026, 5, 8)),
            {"series_id": "PAYEMS", "period": "2026-04-01", "value": "159000", "realtime_start": "2026-05-08"},
            source="alfred",
            vintage="2026-05-08",
        )
    )
    write_context(root, rows)


def test_cx006_macro_as_of_returns_vintage_known_then(tmp_path: Path) -> None:
    """Acceptance: macro(series, as_of) returns the vintage known at as_of for a revised series."""
    _payems_lake(tmp_path)
    s = ContextStore(Lake(tmp_path))
    march = date(2026, 3, 1)
    assert s.macro_value("PAYEMS", march, et(2026, 4, 3, 8, 29)) is None  # one minute before release
    assert s.macro_value("PAYEMS", march, et(2026, 4, 3, 8, 30)) == Decimal("158900")
    assert s.macro_value("PAYEMS", march, et(2026, 5, 8, 8, 29)) == Decimal("158900")  # not the revision
    assert s.macro_value("PAYEMS", march, et(2026, 5, 8, 9, 0)) == Decimal("158760")
    assert s.macro_value("PAYEMS", march, et(2026, 7, 1)) == Decimal("158810")
    assert s.macro_latest("PAYEMS", et(2026, 4, 30)) == (march, Decimal("158900"))
    assert s.macro_latest("PAYEMS", et(2026, 5, 9)) == (date(2026, 4, 1), Decimal("159000"))


def _earnings_lake(root: Path, timing: str = "amc") -> None:
    d = date(2026, 7, 30)
    key = "AAPL|2026Q3"
    base = {
        "symbol": "AAPL",
        "report_date": "2026-07-30",
        "timing": timing,
        "eps_estimate": "1.48",
        "revenue_estimate": "95000000000",
    }
    rows = [
        context_row(
            "earnings",
            key,
            t(et(2026, 7, 30)),
            av.earnings_schedule_available_at(d),
            {**base, "phase": "scheduled", "basis": "assumed_lead"},
            instrument_id="AAPLID",
            source="fmp",
        ),
        context_row(
            "earnings",
            key,
            t(et(2026, 7, 30)),
            av.earnings_actual_available_at(d, parse_timing(timing)),
            {**base, "phase": "reported", "eps_actual": "1.62", "eps_surprise": "0.14", "basis": "rule"},
            instrument_id="AAPLID",
            source="fmp",
        ),
    ]
    write_context(root, rows)


def test_cx007_amc_surprise_not_visible_during_report_day_session(tmp_path: Path) -> None:
    """Acceptance: an AMC earnings surprise is not visible to a decision during that day's session."""
    _earnings_lake(tmp_path, "amc")
    s = ContextStore(Lake(tmp_path))
    during = s.earnings_window("AAPLID", et(2026, 7, 30, 15, 45))
    assert during.last_eps_surprise is None and during.next_report_date == date(2026, 7, 30)
    assert during.days_to_next is not None and 0 < during.days_to_next < 1
    nxt_day = s.earnings_window("AAPLID", et(2026, 7, 31, 9, 45))
    assert nxt_day.last_eps_surprise == Decimal("0.14") and nxt_day.next_report_date is None
    feats = context_features(
        pl.DataFrame(
            {
                "instrument_id": ["AAPLID"] * 3,
                "decision_time": [t(et(2026, 7, 30, 10)), t(et(2026, 7, 30, 15, 45)), t(et(2026, 7, 31, 9, 45))],
            }
        ),
        s,
    )
    assert feats["ctx_last_eps_surprise_pct"].to_list()[:2] == [None, None]
    assert feats["ctx_last_eps_surprise_pct"][2] == pytest.approx(0.14 / 1.48)
    assert feats["ctx_days_to_earnings"][0] is not None and feats["ctx_days_to_earnings"][2] is None


def test_cx007_bmo_surprise_visible_at_open(tmp_path: Path) -> None:
    _earnings_lake(tmp_path, "bmo")
    s = ContextStore(Lake(tmp_path))
    assert s.earnings_window("AAPLID", et(2026, 7, 30, 9, 29)).last_eps_surprise is None
    assert s.earnings_window("AAPLID", et(2026, 7, 30, 9, 30)).last_eps_surprise == Decimal("0.14")


def test_cx007_rescheduled_report_is_a_new_version(tmp_path: Path) -> None:
    key = "MSFT|2026Q3"
    base = {"symbol": "MSFT", "timing": "amc", "phase": "scheduled"}
    write_context(
        tmp_path,
        [
            context_row(
                "earnings",
                key,
                t(et(2026, 7, 28)),
                t(et(2026, 7, 1)),
                {**base, "report_date": "2026-07-28"},
                instrument_id="M",
            ),
            context_row(
                "earnings",
                key,
                t(et(2026, 7, 29)),
                t(et(2026, 7, 20, 18)),
                {**base, "report_date": "2026-07-29"},
                instrument_id="M",
            ),
        ],
    )
    s = ContextStore(Lake(tmp_path))
    assert s.earnings_window("M", et(2026, 7, 15)).next_report_date == date(2026, 7, 28)
    assert s.earnings_window("M", et(2026, 7, 21)).next_report_date == date(2026, 7, 29)


def test_cx005_short_interest_visible_only_from_publication(tmp_path: Path) -> None:
    rows = []
    for settle, si, dtc in ((date(2026, 3, 31), "1000000", "1.2"), (date(2026, 4, 15), "1500000", "1.9")):
        rows.append(
            context_row(
                "short_interest",
                f"AAPL|{settle}",
                t(datetime.combine(settle, datetime.min.time(), UTC)),
                av.finra_available_at(settle),
                {
                    "symbol": "AAPL",
                    "settlement_date": settle.isoformat(),
                    "short_interest": si,
                    "days_to_cover": dtc,
                    "change_pct": "50.0",
                },
                instrument_id="AAPLID",
                source="finra",
            )
        )
    write_context(tmp_path, rows)
    s = ContextStore(Lake(tmp_path))
    mid = s.short_interest("AAPLID", et(2026, 4, 20))  # after the 04-15 settlement, before its publication
    assert mid is not None and mid["settlement_date"] == "2026-03-31"
    later = s.short_interest("AAPLID", et(2026, 4, 25, 9, 30))
    assert later is not None and later["settlement_date"] == "2026-04-15"
    f = context_features(
        pl.DataFrame(
            {"instrument_id": ["AAPLID", "AAPLID"], "decision_time": [t(et(2026, 4, 20)), t(et(2026, 4, 25, 9, 30))]}
        ),
        s,
    )
    assert f["ctx_si_days_to_cover"].to_list() == [1.2, 1.9]


# ---------------------------------------------------------------- news and sentiment


def test_cx013_syndicated_duplicates_detected() -> None:
    arts = [
        Article("1", "AAPL", 0, "Apple Beats Q3 Estimates As Services Revenue Hits Record"),
        Article("2", "AAPL", 150 * NS, "Apple beats Q3 estimates as services revenue hits record!"),
        Article("3", "AAPL", 300 * NS, "Apple announces new iPhone lineup"),
        Article("4", "MSFT", 200 * NS, "Apple beats Q3 estimates as services revenue hits record"),
        Article("5", "AAPL", 2 * 86_400 * NS, "Apple beats Q3 estimates as services revenue hits record"),
    ]
    assert find_duplicates(arts) == {("2", "AAPL"): "1"}  # other instrument and outside window are originals


def _scores() -> pl.DataFrame:
    base = t(et(2026, 7, 30, 12))
    rows = [
        ("A", base - 3600 * NS, 0.8, False, False),
        ("A", base - 1800 * NS, 0.6, True, False),  # syndicated copy
        ("A", base - 600 * NS, -0.2, False, False),
        ("A", base - 300 * NS, 0.9, False, True),  # contaminated (published before model cutoff)
        ("A", base + 60 * NS, -1.0, False, False),  # future: not available yet
    ]
    return pl.DataFrame(
        [
            {
                "instrument_id": i,
                "event_time": e,
                "available_at": e + 2 * NS,
                "score": s,
                "duplicate": d,
                "contaminated": c,
            }
            for i, e, s, d, c in rows
        ]
    )


def test_cx012_sentiment_aggregate_point_in_time_and_contamination_excluded() -> None:
    base = t(et(2026, 7, 30, 12))
    agg = aggregate(_scores(), base, 24 * 3600 * NS, 12 * 3600 * NS)
    assert agg.count == 2 and agg.mean == pytest.approx(0.3)
    assert agg.novelty == pytest.approx(2 / 3)
    assert agg.decay is not None and -0.2 < agg.decay < 0.8
    with_contaminated = aggregate(_scores(), base, 24 * 3600 * NS, 12 * 3600 * NS, include_contaminated=True)
    assert with_contaminated.count == 3
    assert aggregate(_scores(), base, 24 * 3600 * NS, 12 * 3600 * NS) == agg  # deterministic


def test_cx014_store_excludes_contaminated_sentiment(tmp_path: Path) -> None:
    pub = t(et(2026, 7, 30, 10))
    rows = [
        context_row(
            "news_sentiment",
            f"n{i}|m@1",
            pub,
            pub + 62 * NS,
            {
                "news_id": f"n{i}",
                "model": "m",
                "model_version": "1",
                "score": sc,
                "contaminated": c,
                "duplicate": False,
            },
            instrument_id="A",
        )
        for i, (sc, c) in enumerate(((0.5, False), (0.9, True)))
    ]
    write_context(tmp_path, rows)
    s = ContextStore(Lake(tmp_path))
    assert s.sentiment(["A"], et(2026, 7, 30, 11))["score"].to_list() == [0.5]
    assert s.sentiment_agg("A", timedelta(hours=24), et(2026, 7, 30, 11)).count == 1
    assert s.sentiment_agg("A", timedelta(hours=24), et(2026, 7, 30, 10, 0)).count == 0  # not yet scored


# ---------------------------------------------------------------- regimes


def _spy_daily(n: int = 320) -> pl.DataFrame:
    sess = xnys().sessions(datetime(2025, 1, 1, tzinfo=UTC), datetime(2026, 9, 1, tzinfo=UTC))[:n]
    px, rows = 500.0, []
    for i, s in enumerate(sess):
        px *= 1.0 + (0.004 if i % 7 else -0.01) * (2.5 if i > n - 40 else 1.0)
        rows.append({"ts_close": s.close, "close": px, "available_at": s.close + timedelta(seconds=60)})
    return pl.DataFrame(rows).with_columns(
        pl.col("ts_close").cast(pl.Datetime("ns", "UTC")), pl.col("available_at").cast(pl.Datetime("ns", "UTC"))
    )


def test_cx017_regimes_use_only_past_data() -> None:
    spy = _spy_daily()
    vix = spy.select(
        (pl.col("available_at") + pl.duration(hours=12)).alias("available_at"),
        (pl.int_range(pl.len()) % 30 + 12).cast(pl.Float64).alias("value"),
    )
    full = compute_regimes(spy, vix)
    cut = compute_regimes(spy.head(280), vix)
    assert full.head(280).drop("vix_pct_252").equals(cut.drop("vix_pct_252"))  # truncating the future changes nothing
    assert full["vol_regime"][0] == "unknown" and full["version"][0] == "regime_v1"
    assert set(full["trend_regime"].tail(50).to_list()) <= {"uptrend", "downtrend", "chop"}
    # The VIX joined to a row was available before that row (12 h later than the bar, so the prior day's).
    assert full["vix"][1] == vix["value"][0]


# ---------------------------------------------------------------- event blackout


def test_cx015_categorize() -> None:
    assert categorize("CPI YoY") == "CPI"
    assert categorize("Core Consumer Price Index MoM") == "CPI"
    assert categorize("Fed Interest Rate Decision") == "FOMC"
    assert categorize("Non Farm Payrolls") == "NFP"
    assert categorize("Baker Hughes Oil Rig Count") == ""


def _orb_day(d: date, inst: str = "SPYID") -> list[Bar]:
    s = xnys().session(d)
    assert s is not None
    o = int(s.open.timestamp()) * NS
    step = 15 * 60 * NS
    px = [
        (Decimal(100), Decimal(101), Decimal("99.5"), Decimal("100.5")),
        (Decimal("100.5"), Decimal(102), Decimal("100.4"), Decimal("101.8")),
    ]
    out = []
    for i, (op, hi, lo, cl) in enumerate(px):
        out.append(
            Bar(
                inst,
                "15m",
                o + i * step,
                o + (i + 1) * step,
                o + (i + 1) * step + 60 * NS,
                op,
                hi,
                lo,
                cl,
                Decimal(1000),
            )
        )
    return out


def _run(strategy: object, bars: list[Bar]) -> list[str]:
    ctx = StrategyContext(strategy_id="orb", now=0, calendar=xnys(), allocation=Decimal(10000))
    strategy.on_start(ctx)  # type: ignore[attr-defined]
    kinds = []
    for b in bars:
        ctx.now = b.available_at
        kinds += [i.kind for i in strategy.on_bar(ctx, b)]  # type: ignore[attr-defined]
    return kinds


def test_cx016_event_blackout_blocks_entries_around_cpi_in_backtest(tmp_path: Path) -> None:
    """Acceptance (backtest): with a CPI release at 08:30 and a 120-minute after-window, the ORB
    breakout decided at 10:01 is not opened; the same bars on a non-CPI day open normally."""
    cpi_day, plain_day = date(2026, 9, 11), date(2026, 9, 14)
    release = t(et(2026, 9, 11, 8, 30))
    write_context(
        tmp_path,
        [
            context_row(
                "calendar",
                "US|CPI YoY|2026-09-11T12:30",
                release,
                av.econ_schedule_available_at(release),
                {"event": "CPI YoY", "country": "US", "category": "CPI", "impact": "High", "phase": "scheduled"},
                source="fmp",
            ),
        ],
    )
    store = ContextStore(Lake(tmp_path))
    bo = EventBlackout.from_dict({"kinds": ["CPI"], "before_minutes": 30, "after_minutes": 120})
    bars = _orb_day(cpi_day) + _orb_day(plain_day)
    assert _run(OpeningRangeBreakout("orb", ORBParams()), bars) == ["open", "open"]
    wrapped = BlackoutStrategy(OpeningRangeBreakout("orb", ORBParams()), bo, store)
    assert _run(wrapped, bars) == ["open"]
    assert wrapped.blocked and wrapped.blocked[0][2] == "CPI YoY"


def test_cx016_blackout_is_point_in_time_and_never_blocks_closes() -> None:
    src = InMemoryEventSource()
    at = t(et(2026, 9, 11, 10, 0))
    src.add(
        ScheduledEvent(
            "calendar", "CPI YoY", at=at, available_at=at - 3600 * NS, category="CPI", country="US", record_key="k"
        )
    )
    bo = EventBlackout(kinds=("CPI",), before_ns=30 * 60 * NS, after_ns=30 * 60 * NS)
    assert bo.active(src, at - 10 * 60 * NS, "X") is not None
    assert bo.active(src, at - 2 * 3600 * NS, "X") is None  # outside the window
    late = InMemoryEventSource()
    late.add(ScheduledEvent("calendar", "CPI YoY", at=at, available_at=at + 60 * NS, category="CPI", record_key="k"))
    assert bo.active(late, at - 10 * 60 * NS, "X") is None  # not yet known: cannot block (no look-ahead)
    earn = EventBlackout(kinds=("earnings",), before_ns=3600 * NS, after_ns=3600 * NS)
    src.add(ScheduledEvent("earnings", "earnings X", at=at, available_at=0, instrument_id="X", record_key="e"))
    assert earn.active(src, at, "X") is not None and earn.active(src, at, "Y") is None


# ---------------------------------------------------------------- feature store


def test_cx018_context_features_in_materialize_and_future_rows_ignored(tmp_path: Path) -> None:
    insts = ["AAA", "BBB"]
    start = datetime(2026, 9, 14, tzinfo=UTC)
    bars = synth_bars([*insts, "SPY"], start - timedelta(days=10), 12)
    write_bars(tmp_path, bars)
    ref_tables(tmp_path, {i: (0, 0) for i in insts})
    rel = t(et(2026, 9, 16, 8, 30))
    rows = [
        context_row(
            "calendar",
            "US|CPI|2026-09-16",
            rel,
            av.econ_schedule_available_at(rel),
            {"event": "CPI YoY", "country": "US", "category": "CPI", "impact": "High"},
            source="fmp",
        ),
        context_row(
            "short_interest",
            "AAA|2026-08-31",
            t(datetime(2026, 8, 31, tzinfo=UTC)),
            av.finra_available_at(date(2026, 8, 31)),
            {"symbol": "AAA", "settlement_date": "2026-08-31", "days_to_cover": "2.5", "change_pct": "10"},
            instrument_id="AAA",
        ),
    ]
    write_context(tmp_path, rows)
    lake = Lake(tmp_path)
    fs = mvp_feature_set("SPY")
    spec = ContextFeatureSpec()
    end = datetime(2026, 9, 18, tzinfo=UTC)
    snap = materialize(fs, lake, "dev", start, end, out_root=tmp_path / "snaps", context=spec)
    df = snap.load()
    assert set(spec.names) <= set(df.columns)
    assert df.filter(pl.col("instrument_id") == "AAA")["ctx_si_days_to_cover"].drop_nulls().unique().to_list() == [2.5]
    assert (
        df.filter(pl.col("instrument_id") == "BBB")["ctx_si_days_to_cover"].null_count()
        == df.filter(pl.col("instrument_id") == "BBB").height
    )
    hrs = df.filter(pl.col("ts_open") < pl.lit(et(2026, 9, 16, 8, 30)).dt.cast_time_unit("ns"))[
        "ctx_hours_to_macro_event"
    ].drop_nulls()
    assert hrs.len() > 0 and hrs.min() > 0  # type: ignore[operator]
    plain = materialize(fs, lake, "dev", start, end, out_root=tmp_path / "snaps")
    assert plain.snapshot_id != snap.snapshot_id
    # Future-shift: a context row that becomes available after every decision changes nothing.
    write_context(
        tmp_path,
        [
            context_row(
                "short_interest",
                "AAA|2026-09-15",
                t(datetime(2026, 9, 15, tzinfo=UTC)),
                t(et(2026, 12, 1)),
                {"symbol": "AAA", "settlement_date": "2026-09-15", "days_to_cover": "99", "change_pct": "0"},
                instrument_id="AAA",
            ),
        ],
    )
    again = materialize(fs, lake, "dev", start, end, out_root=tmp_path / "snaps2", context=spec).load()
    assert again.select(spec.names).equals(df.select(spec.names))
