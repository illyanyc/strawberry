"""Point-in-time query layer over the Parquet lake (section 03, ADR-0020).

Rules enforced here: DF-002 (filter on ``available_at <= as_of``), DF-005 (raw storage, read-time
adjustment using only corporate actions known as of ``as_of``), DF-006 (point-in-time universes with
delisted instruments), DF-007 (latest revision wins; superseded revisions are used for as-of reads),
MD-012 (cross-source disagreement blocks adjustment).

The lake is written only by ``marketdata`` (Go). Readers use Polars so nanosecond timestamps are
preserved (DuckDB maps UTC timestamps to microseconds); DuckDB remains available for ad-hoc SQL over
the same files.
"""

from __future__ import annotations

import glob as _glob
import os
from collections import defaultdict
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import polars as pl

Adjust = Literal["raw", "split", "split_div"]
TimeLike = datetime | int

KEY = ["instrument_id", "timeframe", "ts_open", "feed"]
PRICE_COLS = ["open", "high", "low", "close", "vwap"]
DEFAULT_FEED_PREFERENCE = ("alpaca.sip", "alpaca.iex", "fmp")


class AdjustmentBlockedError(RuntimeError):
    """Raised when sources disagree on a corporate action needed for adjustment (MD-012)."""


def to_ns(t: TimeLike) -> int:
    """UTC nanoseconds from a timezone-aware datetime or an int (already ns)."""
    if isinstance(t, int):
        return t
    if t.tzinfo is None:
        raise ValueError("naive datetimes are not allowed; use UTC (DF-011)")
    return int(t.timestamp()) * 1_000_000_000 + t.microsecond * 1_000


def to_dt(ns: int) -> datetime:
    return datetime.fromtimestamp(ns / 1e9, tz=UTC)


_DT = pl.Datetime("ns", "UTC")


def _lit_ts(ns: int) -> pl.Expr:
    return pl.lit(ns, dtype=pl.Int64).cast(_DT)


class Lake:
    """Read-only view of a lake root."""

    def __init__(self, root: str | os.PathLike[str], feed_preference: Sequence[str] = DEFAULT_FEED_PREFERENCE) -> None:
        self.root = Path(root)
        self.feed_preference = tuple(feed_preference)

    # ------------------------------------------------------------------ files

    def files(self, dataset: str, timeframe: str | None = None, feed: str | None = None) -> list[str]:
        """Visible Parquet files of a dataset (temporary dot-files are never matched)."""
        f = feed or "*"
        tf = timeframe or "*"
        if dataset in ("bars", "bar_revisions"):
            pattern = self.root / dataset / f"feed={f}" / f"timeframe={tf}" / "year=*" / "month=*" / "*.parquet"
        elif dataset == "context":
            pattern = self.root / "context" / "kind=*" / "year=*" / "month=*" / "*.parquet"
        else:
            pattern = self.root / dataset / "**" / "*.parquet"
        return sorted(p for p in _glob.glob(str(pattern), recursive=True) if not os.path.basename(p).startswith("."))

    def _scan(self, paths: list[str]) -> pl.LazyFrame | None:
        if not paths:
            return None
        return pl.scan_parquet(paths, hive_partitioning=False, missing_columns="insert", extra_columns="ignore")

    def ref(self, table: str) -> pl.DataFrame:
        """A reference table mirrored by marketdata (ref/<table>.parquet)."""
        p = self.root / "ref" / f"{table}.parquet"
        if not p.exists():
            raise FileNotFoundError(f"reference table {table} not exported yet ({p})")
        return pl.read_parquet(p)

    # ------------------------------------------------------------------ bars

    def _bars_lazy(
        self, dataset: str, instruments: Sequence[str] | None, timeframe: str, start: int, end: int, feed: str | None
    ) -> tuple[pl.LazyFrame | None, pl.LazyFrame | None]:
        """(unique, needs_dedupe) lazy frames. Partitions with exactly one compacted part file are unique
        per natural key already (ST-004), so only staging files, multi-file partitions, and revisions
        go through version selection."""
        paths = self.files(dataset, timeframe, feed)
        by_dir: dict[str, list[str]] = defaultdict(list)
        for p in paths:
            by_dir[os.path.dirname(p)].append(p)
        unique_paths, dup_paths = [], []
        for files in by_dir.values():
            if dataset == "bars" and len(files) == 1 and os.path.basename(files[0]).startswith("part-"):
                unique_paths += files
            else:
                dup_paths += files

        def scan(group: list[str]) -> pl.LazyFrame | None:
            lf = self._scan(group)
            if lf is None:
                return None
            lf = lf.filter((pl.col("ts_open") >= _lit_ts(start)) & (pl.col("ts_open") < _lit_ts(end)))
            if instruments is not None:
                lf = lf.filter(pl.col("instrument_id").is_in(list(instruments)))
            return lf

        return scan(unique_paths), scan(dup_paths)

    def bars(
        self,
        instruments: Sequence[str] | None,
        timeframe: str,
        start: TimeLike,
        end: TimeLike,
        as_of: TimeLike | None = None,
        adjust: Adjust = "split_div",
        feed: str | None = None,
        versions: Literal["latest", "first"] = "latest",
    ) -> pl.DataFrame:
        """Bars with ``ts_open`` in ``[start, end)``.

        With ``as_of``: only versions with ``available_at <= as_of`` are considered (superseded
        revisions included, so the result is what was knowable then), and adjustments use only
        corporate actions with ``available_at <= as_of`` and ``ex_date <= as_of`` (DF-002, DF-005).
        Without ``feed``, one feed per bar is chosen by ``feed_preference``. ``versions="first"``
        returns each bar as first published (what live decisions saw), ignoring later corrections.
        """
        s, e = to_ns(start), to_ns(end)
        uniq, dup = self._bars_lazy("bars", instruments, timeframe, s, e, feed)
        need_versions = as_of is not None or versions == "first"
        if need_versions:
            ru, rd = self._bars_lazy("bar_revisions", instruments, timeframe, s, e, feed)
            revs = [x for x in (ru, rd) if x is not None]
            if revs:
                # Keys with revisions must be resolved across both datasets.
                parts = [x for x in (uniq, dup, *revs) if x is not None]
                uniq, dup = None, pl.concat(parts, how="vertical_relaxed")
        # Unique and needs-dedupe partitions are disjoint directories, hence disjoint keys.
        frames = []
        if uniq is not None:
            frames.append(
                (uniq if as_of is None else uniq.filter(pl.col("available_at") <= _lit_ts(to_ns(as_of)))).collect()
            )
        if dup is not None:
            frames.append(self._select(dup, as_of, versions))
        if not frames:
            return _empty_bars()
        df = pl.concat(frames, how="vertical_relaxed") if len(frames) > 1 else frames[0]
        if feed is None and df.height and df["feed"].n_unique() > 1:
            rank = {f: i for i, f in enumerate(self.feed_preference)}
            df = (
                df.with_columns(pl.col("feed").replace_strict(rank, default=len(rank)).alias("_feed_rank"))
                .sort("_feed_rank")
                .unique(subset=["instrument_id", "timeframe", "ts_open"], keep="first")
                .drop("_feed_rank")
            )
        df = df.sort(["instrument_id", "ts_open"])
        if adjust != "raw":
            df = self.adjust(df, adjust, as_of)
        return df

    @staticmethod
    def _select(
        lf: pl.LazyFrame, as_of: TimeLike | None, versions: Literal["latest", "first"] = "latest"
    ) -> pl.DataFrame:
        """Version selection (DF-002, DF-007): drop versions unknown at ``as_of``, then keep one row per
        natural key: the highest revision (``latest``) or the first published (``first``)."""
        if as_of is not None:
            lf = lf.filter(pl.col("available_at") <= _lit_ts(to_ns(as_of)))
        newest_first = versions == "latest"
        return (
            lf.sort(["revision", "available_at", "ingested_at"], descending=[newest_first, False, False])
            .unique(subset=KEY, keep="first", maintain_order=True)
            .collect()
        )

    # ------------------------------------------------------------------ corporate actions

    def corporate_actions(
        self, instruments: Iterable[str] | None = None, as_of: TimeLike | None = None
    ) -> pl.DataFrame:
        """Corporate actions known as of ``as_of`` (all when None), one row per source report."""
        try:
            ca = self.ref("corporate_actions")
        except FileNotFoundError:
            return pl.DataFrame(
                schema={
                    "instrument_id": pl.Utf8,
                    "type": pl.Utf8,
                    "ex_date": pl.Int64,
                    "ratio": pl.Utf8,
                    "amount": pl.Utf8,
                    "available_at": pl.Int64,
                    "agreement": pl.Utf8,
                    "source": pl.Utf8,
                }
            )
        if instruments is not None:
            ca = ca.filter(pl.col("instrument_id").is_in(list(instruments)))
        if as_of is not None:
            a = to_ns(as_of)
            ca = ca.filter((pl.col("available_at") <= a) & (pl.col("ex_date") <= a))
        return ca

    def adjust(self, df: pl.DataFrame, basis: Adjust, as_of: TimeLike | None = None) -> pl.DataFrame:
        """Backward-adjust raw bars using corporate actions known as of ``as_of`` (DF-005, D27);
        see ``apply_adjustment``."""
        if df.is_empty():
            return df.with_columns(pl.lit(basis).alias("adjustment_basis"))
        ca = self.corporate_actions(df["instrument_id"].unique().to_list(), as_of)
        return apply_adjustment(df, ca, basis)

    # ------------------------------------------------------------------ universes and instruments

    def universe(self, name: str, as_of: TimeLike) -> list[str]:
        """Members of ``name`` at ``as_of``, including instruments later delisted (DF-006)."""
        a = to_ns(as_of)
        m = self.ref("universe_members").filter(
            (pl.col("universe") == name)
            & (pl.col("valid_from") <= a)
            & ((pl.col("valid_to") == 0) | (pl.col("valid_to") > a))
        )
        inst = self.ref("instruments").filter((pl.col("delisted_at") == 0) | (pl.col("delisted_at") > a))
        return sorted(m.join(inst.select("instrument_id"), on="instrument_id")["instrument_id"].unique().to_list())

    def resolve(self, symbol: str, as_of: TimeLike) -> str | None:
        """instrument_id whose ticker was ``symbol`` at ``as_of``."""
        a = to_ns(as_of)
        hit = self.ref("symbols").filter(
            (pl.col("symbol") == symbol.upper())
            & (pl.col("valid_from") <= a)
            & ((pl.col("valid_to") == 0) | (pl.col("valid_to") > a))
        )
        return None if hit.is_empty() else str(hit["instrument_id"][0])

    def history(self, instrument_id: str) -> pl.DataFrame:
        """Ticker history of an instrument."""
        return self.ref("symbols").filter(pl.col("instrument_id") == instrument_id).sort("valid_from")

    # ------------------------------------------------------------------ context data

    def context(
        self, kind: str, instruments: Sequence[str] | None = None, as_of: TimeLike | None = None
    ) -> pl.DataFrame:
        """Context events (e.g. FMP fundamentals snapshots). With ``as_of``, the latest snapshot per
        (instrument, event_time) with ``available_at <= as_of`` (DF-002, DF-010)."""
        lf = self._scan(self.files("context"))
        if lf is None:
            return pl.DataFrame()
        lf = lf.filter(pl.col("kind") == kind)
        if instruments is not None:
            lf = lf.filter(pl.col("instrument_id").is_in(list(instruments)))
        if as_of is not None:
            lf = lf.filter(pl.col("available_at") <= _lit_ts(to_ns(as_of)))
        return (
            lf.sort("available_at", descending=True)
            .unique(subset=["instrument_id", "event_time"], keep="first")
            .sort(["instrument_id", "event_time"])
            .collect()
        )


def _empty_bars() -> pl.DataFrame:
    return pl.DataFrame(
        schema={
            "instrument_id": pl.Utf8,
            "symbol": pl.Utf8,
            "timeframe": pl.Utf8,
            "ts_open": _DT,
            "ts_close": _DT,
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
            "available_at": _DT,
            "ingested_at": _DT,
        }
    )


def asof_join(
    left: pl.DataFrame,
    right: pl.DataFrame,
    on: str = "instrument_id",
    left_time: str = "decision_time",
    right_time: str = "available_at",
    tolerance_ns: int | None = None,
) -> pl.DataFrame:
    """Point-in-time join: each left row gets the latest right row with
    ``right[right_time] <= left[left_time]`` for the same ``on`` key (DF-002). Left row order is kept.

    Times are compared as int64 nanoseconds so nothing is truncated. Right columns that collide with
    left names are suffixed ``_right``. With ``tolerance_ns``, matches older than the tolerance are
    nulled. Implemented with Polars ``join_asof`` (ADR-0020: DuckDB's ASOF JOIN took minutes on
    10M rows on the reference VM; Polars takes under a second).
    """

    def as_ns(df: pl.DataFrame, col: str, alias: str) -> pl.DataFrame:
        expr = pl.col(col) if df.schema[col] == pl.Int64 else pl.col(col).dt.epoch("ns")
        return df.with_columns(expr.alias(alias))

    lt, rt = "__tb_lt", "__tb_rt"
    lf = as_ns(left, left_time, lt).with_row_index("__tb_row")
    rf = as_ns(right, right_time, rt)
    rename = {c: f"{c}_right" for c in rf.columns if c in lf.columns and c != on}
    rf = rf.rename(rename)
    out = (
        lf.sort(lt)
        .join_asof(rf.sort(rt), left_on=lt, right_on=rt, by=on, strategy="backward", check_sortedness=False)
        .sort("__tb_row")
    )
    if tolerance_ns is not None:
        stale = (pl.col(lt) - pl.col(rt)) > tolerance_ns
        rcols = [c for c in rf.columns if c not in (on, rt)]
        out = out.with_columns([pl.when(stale).then(None).otherwise(pl.col(c)).alias(c) for c in rcols])
    return out.drop(["__tb_row", lt, rt], strict=False)


def apply_adjustment(df: pl.DataFrame, actions: pl.DataFrame, basis: Adjust) -> pl.DataFrame:
    """Backward-adjust raw bars with the given corporate actions (already filtered to what is known as
    of the decision time). Pure function shared by ``Lake.bars`` and the online feature engine.

    For a bar opened at τ the price factor is the product of ``f_a`` over actions with ``τ < ex_a``:
    splits ``f = 1/ratio`` (volume * ratio), dividends ``f = 1 - amount / previous close`` (CRSP
    convention; previous close = last raw close before the ex-date in the frame). Actions with
    ``agreement == "disagreed"`` block adjustment (MD-012).
    """
    if df.is_empty() or basis == "raw":
        return df.with_columns(pl.lit(basis).alias("adjustment_basis"))
    types = ["split"] if basis == "split" else ["split", "dividend"]
    ca = actions.filter(pl.col("type").is_in(types)) if not actions.is_empty() else actions
    if ca.is_empty():
        return df.with_columns(pl.lit(basis).alias("adjustment_basis"))
    blocked = ca.filter(pl.col("agreement") == "disagreed")["instrument_id"].unique().to_list()
    if blocked:
        raise AdjustmentBlockedError(
            f"corporate-action sources disagree for {sorted(blocked)}; adjustment blocked (MD-012)"
        )
    # One action per (instrument, type, ex_date): sources that agree report the same action.
    ca = ca.sort("source").unique(subset=["instrument_id", "type", "ex_date"], keep="first")
    first_ts = df.group_by("instrument_id").agg(pl.col("ts_open").min().dt.epoch("ns").alias("_first"))
    ca = ca.join(first_ts, on="instrument_id").filter(pl.col("ex_date") > pl.col("_first"))
    if ca.is_empty():
        return df.with_columns(pl.lit(basis).alias("adjustment_basis"))
    closes = df.select("instrument_id", pl.col("ts_open").dt.epoch("ns").alias("_t"), "close").sort("_t")
    rows = []
    for a in ca.sort(["instrument_id", "ex_date"]).iter_rows(named=True):
        if a["type"] == "split":
            ratio = float(a["ratio"]) if a["ratio"] else 0.0
            if ratio <= 0:
                continue
            rows.append((a["instrument_id"], a["ex_date"], 1.0 / ratio, ratio))
        else:
            prev = closes.filter((pl.col("instrument_id") == a["instrument_id"]) & (pl.col("_t") < a["ex_date"]))
            if prev.is_empty() or not a["amount"]:
                continue
            pc = float(prev["close"][-1])
            f = 1.0 - float(a["amount"]) / pc if pc > 0 else 1.0
            rows.append((a["instrument_id"], a["ex_date"], f, 1.0))
    if not rows:
        return df.with_columns(pl.lit(basis).alias("adjustment_basis"))
    fac = pl.DataFrame(
        rows, schema={"instrument_id": pl.Utf8, "ex_date": pl.Int64, "f": pl.Float64, "vf": pl.Float64}, orient="row"
    )
    # cum_after(ex_i) = product of factors with ex >= ex_i, per instrument.
    fac = (
        fac.sort(["instrument_id", "ex_date"], descending=[False, True])
        .with_columns(
            pl.col("f").cum_prod().over("instrument_id").alias("pf"),
            pl.col("vf").cum_prod().over("instrument_id").alias("vff"),
        )
        .sort(["instrument_id", "ex_date"])
    )
    out = (
        df.with_columns(pl.col("ts_open").dt.epoch("ns").alias("_t"))
        .sort("_t")
        .join_asof(
            fac.select("instrument_id", pl.col("ex_date").alias("_t_ex"), "pf", "vff"),
            left_on="_t",
            right_on="_t_ex",
            by="instrument_id",
            strategy="forward",
            allow_exact_matches=False,
            check_sortedness=False,
        )
        .with_columns(pl.col("pf").fill_null(1.0), pl.col("vff").fill_null(1.0))
    )
    out = out.with_columns([(pl.col(c) * pl.col("pf")).alias(c) for c in PRICE_COLS if c in out.columns])
    out = out.with_columns((pl.col("volume") * pl.col("vff")).alias("volume"), pl.lit(basis).alias("adjustment_basis"))
    return out.drop(["_t", "_t_ex", "pf", "vff"], strict=False).sort(["instrument_id", "ts_open"])
