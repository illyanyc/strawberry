"""Feature store tests: offline/online parity, leakage (future-shift), snapshots, GC (section 03)."""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import polars as pl
import pytest

from tbcore.data import Lake, xnys
from tbcore.features import (
    PARITY_TOLERANCE,
    FeatureSet,
    OnlineFeatureEngine,
    compare,
    compute,
    feature,
    gc_snapshots,
    materialize,
    mvp_feature_set,
    parity_check,
)
from tbcore.features.core import BAR_COLUMNS

from .lakekit import et, ns, ref_tables, synth_bars, write_bars

UNIVERSE = [f"I{i:02d}" for i in range(12)]
START = datetime(2026, 3, 2, tzinfo=UTC)


@pytest.fixture(scope="module")
def recorded(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, pl.DataFrame]:
    """A 'recorded live week': 15-minute extended-hours bars for 12 instruments, SPY, and XLK, with a
    2:1 split on I03 mid-week (announced before its ex-date) and a dividend on I05."""
    root = tmp_path_factory.mktemp("lake")
    bars = synth_bars([*UNIVERSE, "SPY", "XLK"], START, 6, "15m", seed=11)
    split_ex = et(2026, 3, 5)
    # Raw prices halve from the ex-date on (the split), volume doubles.
    bars = bars.with_columns(
        [
            pl.when((pl.col("instrument_id") == "I03") & (pl.col("ts_open") >= split_ex))
            .then(pl.col(c) / 2)
            .otherwise(pl.col(c))
            .alias(c)
            for c in ["open", "high", "low", "close", "vwap"]
        ]
    ).with_columns(
        pl.when((pl.col("instrument_id") == "I03") & (pl.col("ts_open") >= split_ex))
        .then(pl.col("volume") * 2)
        .otherwise(pl.col("volume"))
        .alias("volume")
    )
    write_bars(root, bars)
    members = {i: (ns(START - timedelta(days=30)), 0) for i in UNIVERSE}
    ref_tables(
        root,
        members,
        sector={i: "XLK" for i in UNIVERSE[:6]},
        symbols={"SPY": "SPY", "XLK": "XLK", **{i: i for i in UNIVERSE}},
        actions=[
            {
                "instrument_id": "I03",
                "type": "split",
                "ratio": "2",
                "ex_date": ns(split_ex),
                "available_at": ns(et(2026, 3, 3, 16, 30)),
            },
            {
                "instrument_id": "I05",
                "type": "dividend",
                "amount": "0.40",
                "ex_date": ns(et(2026, 3, 4)),
                "available_at": ns(et(2026, 2, 20)),
            },
        ],
    )
    return root, bars


def test_df004_offline_online_parity_on_recorded_week(recorded: tuple[Path, pl.DataFrame], tmp_path: Path) -> None:
    root, bars = recorded
    lake = Lake(root)
    fs = mvp_feature_set("SPY")
    end = START + timedelta(days=9)
    snap = materialize(fs, lake, "dev", START + timedelta(days=1), end, timeframe="15m", out_root=tmp_path / "snaps")
    offline = snap.load()
    assert offline.height > 0 and set(offline["instrument_id"]) == set(UNIVERSE)
    # Replay bar by bar through the online engine, applying each corporate action once it is known.
    actions = lake.corporate_actions()
    eng = OnlineFeatureEngine(fs, sector_of={i: "XLK" for i in UNIVERSE[:6]})
    applied: set[str] = set()
    out = []
    for _ts, batch in bars.sort("ts_open").group_by(["ts_open"], maintain_order=True):
        now_ns = ns(batch["available_at"].max())  # type: ignore[arg-type]
        new = actions.filter((pl.col("available_at") <= now_ns) & ~pl.col("source_id").is_in(list(applied)))
        if new.height:
            eng.apply_corporate_action(new)
            applied |= set(new["source_id"].to_list())
        out.append(eng.update(batch))
    online = pl.concat(out).filter(pl.col("instrument_id").is_in(UNIVERSE))
    rep = compare(offline, online, fs.names)
    assert rep.rows == offline.height
    assert rep.ok, f"parity violations: {rep.violations}, max diffs {rep.max_abs_diff}"
    worst = max(rep.max_abs_diff.values())
    print(f"offline/online parity: {rep.rows} rows x {len(fs.names)} features, max |diff| = {worst:.3e}")
    print(f"documented tolerance {PARITY_TOLERANCE}")
    # parity_check recomputes offline features for the stored online vectors' window.
    assert parity_check(
        fs,
        lake,
        online.filter(pl.col("ts_open") >= START + timedelta(days=3)),
        sector_of={i: "XLK" for i in UNIVERSE[:6]},
    ).ok


def test_df012_future_shift_leakage(recorded: tuple[Path, pl.DataFrame]) -> None:
    """Changing every bar after time t must not change any feature at or before t."""
    _, bars = recorded
    fs = mvp_feature_set("SPY")
    cut = START + timedelta(days=3, hours=17)
    a = compute(fs, bars.select(BAR_COLUMNS), sector_of={i: "XLK" for i in UNIVERSE[:6]})
    shocked = bars.with_columns(
        [
            pl.when(pl.col("ts_open") > cut).then(pl.col(c) * 1.7).otherwise(pl.col(c)).alias(c)
            for c in ["open", "high", "low", "close", "vwap", "volume"]
        ]
    )
    b = compute(fs, shocked.select(BAR_COLUMNS), sector_of={i: "XLK" for i in UNIVERSE[:6]})
    past_a, past_b = a.filter(pl.col("ts_open") <= cut), b.filter(pl.col("ts_open") <= cut)
    rep = compare(past_a, past_b, fs.names, tol=0.0)
    assert rep.ok, f"features at <= t changed when only the future changed: {rep.max_abs_diff}"
    assert not compare(a.filter(pl.col("ts_open") > cut), b.filter(pl.col("ts_open") > cut), fs.names, tol=0.0).ok


def test_df012_shuffled_labels_carry_no_signal(recorded: tuple[Path, pl.DataFrame]) -> None:
    """Sanity check of the harness: features have no correlation with a shuffled forward return."""
    _, bars = recorded
    fs = mvp_feature_set("SPY")
    f = compute(fs, bars.select(BAR_COLUMNS)).filter(pl.col("instrument_id").is_in(UNIVERSE)).drop_nulls("ret_4")
    shuffled = f.select(pl.col("ret_1").shuffle(seed=1).alias("y"), "ret_4")
    assert abs(shuffled.select(pl.corr("y", "ret_4")).item()) < 0.1


def test_df003_features_cannot_see_rows_after_as_of(recorded: tuple[Path, pl.DataFrame], tmp_path: Path) -> None:
    root, _ = recorded
    lake = Lake(root)
    fs = mvp_feature_set("SPY")
    as_of = START + timedelta(days=4, hours=15)
    snap = materialize(
        fs, lake, "dev", START + timedelta(days=1), START + timedelta(days=9), as_of=as_of, out_root=tmp_path / "s2"
    )
    df = snap.load()
    assert df.height > 0
    assert df["available_at"].max() <= as_of  # type: ignore[operator]


def test_df009_snapshots_immutable_deterministic_and_gc(recorded: tuple[Path, pl.DataFrame], tmp_path: Path) -> None:
    root, _ = recorded
    lake = Lake(root)
    fs = mvp_feature_set("SPY")
    out = tmp_path / "snaps"
    a = materialize(fs, lake, "dev", START + timedelta(days=1), START + timedelta(days=5), out_root=out)
    mtime = (a.path / "features.parquet").stat().st_mtime_ns
    b = materialize(fs, lake, "dev", START + timedelta(days=1), START + timedelta(days=5), out_root=out)
    assert a.snapshot_id == b.snapshot_id and (b.path / "features.parquet").stat().st_mtime_ns == mtime
    c = materialize(fs, lake, "dev", START + timedelta(days=1), START + timedelta(days=6), out_root=out)
    assert c.snapshot_id != a.snapshot_id
    assert a.manifest["feature_set"] == fs.hash and a.manifest["rows"] == a.load().height
    old = time.time() - 30 * 86400
    for snap in (a, c):
        os.utime(snap.path, (old, old))
    assert gc_snapshots(out, referenced={a.snapshot_id}) == [c.snapshot_id]
    assert a.path.exists() and not c.path.exists()


def test_feature_set_hash_changes_with_code_and_version() -> None:
    fs1 = mvp_feature_set("SPY")
    fs2 = FeatureSet.of("mvp_intraday", 1, [k for k in fs1_keys(fs1) if k != "rsi_14@1"], context={"market": "SPY"})
    assert fs1.hash != fs2.hash and fs1.max_lookback == 128

    @feature("tmp_feat", 1, ["close"], lookback=1)
    def tmp_feat() -> pl.Expr:
        return pl.col("close")

    with pytest.raises(ValueError, match="bump the version"):

        @feature("tmp_feat", 1, ["close"], lookback=1)
        def tmp_feat_changed() -> pl.Expr:
            return pl.col("close") * 2


def fs1_keys(fs: FeatureSet) -> list[str]:
    return [f"{f.name}@{f.version}" for f in fs.features]


def test_online_update_latency(recorded: tuple[Path, pl.DataFrame]) -> None:
    """Section 03 target: online update < 5 ms per bar per instrument for the MVP set."""
    _, bars = recorded
    fs = mvp_feature_set("SPY")
    eng = OnlineFeatureEngine(fs)
    batches = [b for _, b in bars.sort("ts_open").group_by(["ts_open"], maintain_order=True)]
    for b in batches[:200]:
        eng.update(b)
    t = time.perf_counter()
    n = 0
    for b in batches[200:260]:
        eng.update(b)
        n += b.height
    per = (time.perf_counter() - t) / n * 1000
    print(
        f"online feature update: {per:.3f} ms per bar per instrument ({len(UNIVERSE) + 2} instruments, ring {eng.ring})"
    )
    assert per < 5.0


@pytest.mark.perf
@pytest.mark.skipif(os.environ.get("TB_PERF") != "1", reason="set TB_PERF=1 for performance targets")
def test_perf_targets(tmp_path: Path) -> None:
    """Section 03 targets: one year of 1m bars for 100 symbols into Polars < 3 s; as-of join of 10M
    rows < 10 s."""
    import numpy as np

    from tbcore.data import asof_join

    cal = xnys()
    sessions = cal.sessions(datetime(2025, 1, 1, tzinfo=UTC), datetime(2026, 1, 1, tzinfo=UTC))
    opens = np.array([ns(o) for s in sessions for o in s.bar_opens("1m")], dtype=np.int64)
    n_inst = 100
    rng = np.random.default_rng(1)
    for i in range(n_inst):
        px = 100 * np.exp(np.cumsum(rng.normal(0, 0.0005, len(opens))))
        df = (
            pl.DataFrame(
                {
                    "instrument_id": f"P{i:03d}",
                    "symbol": f"P{i:03d}",
                    "timeframe": "1m",
                    "ts_open": opens,
                    "ts_close": opens + 60 * 10**9,
                    "open": px,
                    "high": px * 1.001,
                    "low": px * 0.999,
                    "close": px,
                    "volume": rng.integers(100, 10_000, len(opens)).astype(float),
                    "vwap": px,
                    "trade_count": 10,
                    "feed": "alpaca.sip",
                    "source": "perf",
                    "revision": 0,
                    "is_final": True,
                    "session": "regular",
                    "repaired": False,
                    "adjustment_basis": "raw",
                    "available_at": opens + 120 * 10**9,
                    "ingested_at": opens + 121 * 10**9,
                }
            )
            .with_columns(
                pl.col(c).cast(pl.Datetime("ns", "UTC")) for c in ["ts_open", "ts_close", "available_at", "ingested_at"]
            )
            .with_columns(pl.col("revision").cast(pl.Int32))
        )
        write_bars(tmp_path, df, name=f"part-{i:026d}")
    # Compacted layout: one file per partition (the Go writer compacts per month).
    from .lakekit import write_actions

    write_actions(tmp_path, [])
    for part in (tmp_path / "bars").rglob("month=*"):
        files = sorted(part.glob("*.parquet"))
        pl.concat([pl.read_parquet(f) for f in files]).sort(["instrument_id", "ts_open"]).write_parquet(
            part / "part-00000000000000000000000000.parquet"
        )
        for f in files:
            if f.name != "part-00000000000000000000000000.parquet":
                f.unlink()
    lake = Lake(tmp_path)
    t = time.perf_counter()
    df = lake.bars(
        [f"P{i:03d}" for i in range(n_inst)],
        "1m",
        datetime(2025, 1, 1, tzinfo=UTC),
        datetime(2026, 1, 1, tzinfo=UTC),
        adjust="raw",
    )
    load = time.perf_counter() - t
    print(f"load 1y of 1m bars x {n_inst} symbols: {df.height:,} rows in {load:.2f} s")
    assert df.height == len(opens) * n_inst and load < 3.0
    left = df.select("instrument_id", pl.col("ts_open").alias("decision_time")).head(10_000_000)
    right = df.select("instrument_id", "available_at", "close").sample(fraction=0.2, seed=1)
    t = time.perf_counter()
    out = asof_join(left, right)
    aj = time.perf_counter() - t
    print(f"as-of join {left.height:,} x {right.height:,} rows: {aj:.2f} s")
    assert out.height == left.height and aj < 10.0
