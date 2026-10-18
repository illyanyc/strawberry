"""Context features for the feature store (section 09 reference features, CX-018).

``context_features`` appends point-in-time context columns to a frame of decisions
(``instrument_id``, ``decision_time``). Every value uses only context versions with
``available_at <= decision_time`` (DF-002) and is computed by this one function for offline
materialization and online inference (DF-004). Contaminated sentiment scores are excluded (CX-014).

Features (version 1):

* ``ctx_news_count_24h``, ``ctx_sent_mean_24h``, ``ctx_sent_decay_24h``, ``ctx_news_novelty_24h``
* ``ctx_days_to_earnings``, ``ctx_days_since_earnings``, ``ctx_last_eps_surprise_pct``
* ``ctx_si_days_to_cover``, ``ctx_si_change_pct``
* ``ctx_hours_to_macro_event`` (next event in ``macro_categories``, capped at 7 days), ``ctx_macro_event_today``
* ``ctx_vix``, ``ctx_vix_pct_252``, ``ctx_regime_vol_high``, ``ctx_regime_trend`` (-1, 0, 1), ``ctx_regime_stress``
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from datetime import time as dtime

import numpy as np
import polars as pl

from tbcore.context.availability import ET, earnings_actual_available_at, et_ns, parse_timing
from tbcore.context.store import ContextStore
from tbcore.data.lake import asof_join

NS = 1_000_000_000
HOUR_NS = 3600 * NS
DAY_NS = 24 * HOUR_NS


@dataclass(frozen=True)
class ContextFeatureSpec:
    version: int = 1
    sentiment_window_hours: float = 24.0
    sentiment_half_life_hours: float = 12.0
    sentiment_model: str = ""
    macro_categories: tuple[str, ...] = ("CPI", "FOMC", "NFP")
    macro_horizon_days: float = 7.0

    @property
    def names(self) -> list[str]:
        return list(CONTEXT_FEATURES)

    @property
    def hash(self) -> str:
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True, default=list).encode()).hexdigest()[:16]


CONTEXT_FEATURES = (
    "ctx_news_count_24h",
    "ctx_sent_mean_24h",
    "ctx_sent_decay_24h",
    "ctx_news_novelty_24h",
    "ctx_days_to_earnings",
    "ctx_days_since_earnings",
    "ctx_last_eps_surprise_pct",
    "ctx_si_days_to_cover",
    "ctx_si_change_pct",
    "ctx_hours_to_macro_event",
    "ctx_macro_event_today",
    "ctx_vix",
    "ctx_vix_pct_252",
    "ctx_regime_vol_high",
    "ctx_regime_trend",
    "ctx_regime_stress",
)


def _ns(df: pl.DataFrame, col: str) -> pl.Expr:
    return pl.col(col) if df.schema[col] == pl.Int64 else pl.col(col).dt.epoch("ns")


def _running_latest(versions: pl.DataFrame, by: str, order: str) -> pl.DataFrame:
    """Versions sorted by availability, keeping only those that advance ``order`` (or revise the
    current newest) per ``by``, so a backward as-of join returns the newest record known then."""
    v = versions.sort("_at")
    return v.filter(pl.col(order) >= pl.col(order).cum_max().over(by))


def context_features(
    decisions: pl.DataFrame,
    store: ContextStore,
    spec: ContextFeatureSpec | None = None,
    time_col: str = "decision_time",
) -> pl.DataFrame:
    """Append the context features to ``decisions`` (row order kept)."""
    spec = spec or ContextFeatureSpec()
    d = decisions.with_columns(_ns(decisions, time_col).alias("_t")).with_row_index("_row")
    if d.is_empty():
        return decisions.with_columns([pl.lit(None, dtype=pl.Float64).alias(n) for n in CONTEXT_FEATURES])
    as_of = int(d["_t"].max())  # type: ignore[arg-type]
    insts = sorted(d["instrument_id"].unique().to_list())
    keys = d.select("_row", "instrument_id", "_t")
    parts = [
        _sentiment(keys, store, spec, insts, as_of),
        _earnings(keys, store, insts, as_of),
        _short_interest(keys, store, insts, as_of),
        _macro_events(keys, store, spec, as_of),
        _regime(keys, store, spec, as_of),
    ]
    out = d
    for p in parts:
        out = out.join(p, on="_row", how="left")
    out = out.with_columns([pl.col(n).cast(pl.Float64) for n in CONTEXT_FEATURES])
    return out.sort("_row").drop(["_row", "_t"])


def _sentiment(
    keys: pl.DataFrame, store: ContextStore, spec: ContextFeatureSpec, insts: list[str], as_of: int
) -> pl.DataFrame:
    s = store.sentiment(insts, as_of, spec.sentiment_model or None, include_contaminated=False)
    empty = keys.select("_row").with_columns(
        pl.lit(0.0).alias("ctx_news_count_24h"),
        pl.lit(None, dtype=pl.Float64).alias("ctx_sent_mean_24h"),
        pl.lit(None, dtype=pl.Float64).alias("ctx_sent_decay_24h"),
        pl.lit(None, dtype=pl.Float64).alias("ctx_news_novelty_24h"),
    )
    if s.is_empty():
        return empty
    win = int(spec.sentiment_window_hours * HOUR_NS)
    hl = spec.sentiment_half_life_hours * HOUR_NS
    s = s.select(
        "instrument_id",
        pl.col("available_at").dt.epoch("ns").alias("_at"),
        pl.col("event_time").dt.epoch("ns").alias("_pub"),
        "score",
        pl.col("duplicate").fill_null(False).alias("_dup"),
    )
    j = keys.join(s, on="instrument_id", how="inner").filter(
        (pl.col("_at") <= pl.col("_t")) & (pl.col("_pub") > pl.col("_t") - win) & (pl.col("_pub") <= pl.col("_t"))
    )
    w = (0.5 ** ((pl.col("_t") - pl.col("_pub")).cast(pl.Float64) / hl)).alias("_w")
    agg = (
        j.with_columns(w)
        .group_by("_row")
        .agg(
            (~pl.col("_dup")).sum().cast(pl.Float64).alias("ctx_news_count_24h"),
            pl.col("score").filter(~pl.col("_dup")).mean().alias("ctx_sent_mean_24h"),
            (
                (pl.col("score") * pl.col("_w")).filter(~pl.col("_dup")).sum()
                / pl.col("_w").filter(~pl.col("_dup")).sum()
            ).alias("ctx_sent_decay_24h"),
            ((~pl.col("_dup")).sum() / pl.len()).cast(pl.Float64).alias("ctx_news_novelty_24h"),
        )
    )
    return (
        empty.select("_row").join(agg, on="_row", how="left").with_columns(pl.col("ctx_news_count_24h").fill_null(0.0))
    )


def _earnings(keys: pl.DataFrame, store: ContextStore, insts: list[str], as_of: int) -> pl.DataFrame:
    cols = ["ctx_days_to_earnings", "ctx_days_since_earnings", "ctx_last_eps_surprise_pct"]
    empty = keys.select("_row").with_columns([pl.lit(None, dtype=pl.Float64).alias(c) for c in cols])
    v = store.versions("earnings", as_of, insts)
    if v.is_empty():
        return empty
    rows = []
    for r in v.iter_rows(named=True):
        when = earnings_actual_available_at(date.fromisoformat(r["report_date"]), parse_timing(r["timing"]), store.cfg)
        surprise = _f(r["eps_surprise"])
        est = _f(r["eps_estimate"])
        pct = surprise / abs(est) if surprise is not None and est not in (None, 0.0) else None
        rows.append(
            (
                r["instrument_id"],
                r["record_key"],
                int(r["available_at"].timestamp() * 1e6) * 1000,
                when,
                r["phase"] == "reported",
                pct,
            )
        )
    ver = pl.DataFrame(
        rows,
        schema={
            "instrument_id": pl.Utf8,
            "record_key": pl.Utf8,
            "_at": pl.Int64,
            "_when": pl.Int64,
            "_rep": pl.Boolean,
            "_pct": pl.Float64,
        },
        orient="row",
    ).sort("_at")
    # Each version is valid from its availability until the next version of the same record.
    ver = ver.with_columns(pl.col("_at").shift(-1).over("record_key", "instrument_id").fill_null(2**62).alias("_until"))
    j = keys.join(ver, on="instrument_id", how="inner").filter(
        (pl.col("_at") <= pl.col("_t")) & (pl.col("_t") < pl.col("_until"))
    )
    nxt = (
        j.filter(pl.col("_when") > pl.col("_t"))
        .group_by("_row")
        .agg(((pl.col("_when").min() - pl.col("_t").first()) / DAY_NS).alias("ctx_days_to_earnings"))
    )
    last = (
        j.filter(pl.col("_rep") & (pl.col("_when") <= pl.col("_t")))
        .sort("_when")
        .group_by("_row")
        .agg(
            ((pl.col("_t").last() - pl.col("_when").last()) / DAY_NS).alias("ctx_days_since_earnings"),
            pl.col("_pct").last().alias("ctx_last_eps_surprise_pct"),
        )
    )
    return empty.select("_row").join(nxt, on="_row", how="left").join(last, on="_row", how="left")


def _f(s: object) -> float | None:
    if s is None or s == "":
        return None
    try:
        return float(str(s))
    except ValueError:
        return None


def _short_interest(keys: pl.DataFrame, store: ContextStore, insts: list[str], as_of: int) -> pl.DataFrame:
    cols = ["ctx_si_days_to_cover", "ctx_si_change_pct"]
    empty = keys.select("_row").with_columns([pl.lit(None, dtype=pl.Float64).alias(c) for c in cols])
    v = store.versions("short_interest", as_of, insts)
    if v.is_empty():
        return empty
    v = v.select(
        "instrument_id",
        pl.col("available_at").dt.epoch("ns").alias("_at"),
        pl.col("settlement_date").str.to_date().alias("_settle"),
        pl.col("days_to_cover").cast(pl.Float64, strict=False).alias("ctx_si_days_to_cover"),
        pl.col("change_pct").cast(pl.Float64, strict=False).alias("ctx_si_change_pct"),
    )
    v = _running_latest(v, "instrument_id", "_settle").drop("_settle")
    j = asof_join(keys, v, on="instrument_id", left_time="_t", right_time="_at")
    return j.select("_row", *cols)


def _macro_events(keys: pl.DataFrame, store: ContextStore, spec: ContextFeatureSpec, as_of: int) -> pl.DataFrame:
    cols = ["ctx_hours_to_macro_event", "ctx_macro_event_today"]
    times = np.array(sorted(set(keys["_t"].to_list())), dtype=np.int64)
    v = store.versions("calendar", as_of)
    hz = int(spec.macro_horizon_days * DAY_NS)
    if v.is_empty():
        res = pl.DataFrame(
            {"_t": times, cols[0]: [None] * len(times), cols[1]: [0.0] * len(times)},
            schema={"_t": pl.Int64, cols[0]: pl.Float64, cols[1]: pl.Float64},
        )
        return keys.join(res, on="_t", how="left").select("_row", *cols)
    v = (
        v.filter(pl.col("category").is_in(list(spec.macro_categories)))
        .select(
            "record_key",
            pl.col("available_at").dt.epoch("ns").alias("_at"),
            pl.col("event_time").dt.epoch("ns").alias("_ev"),
        )
        .sort("_at")
    )
    v = v.with_columns(pl.col("_at").shift(-1).over("record_key").fill_null(2**62).alias("_until"))
    at, ev, until = v["_at"].to_numpy(), v["_ev"].to_numpy(), v["_until"].to_numpy()
    hours: list[float | None] = []
    today: list[float] = []
    for t in times:
        known = (at <= t) & (t < until)
        fut = ev[known & (ev > t) & (ev <= t + hz)]
        hours.append(float((fut.min() - t) / HOUR_NS) if fut.size else None)
        d0 = datetime.fromtimestamp(int(t) / 1e9, tz=ET).date()
        day0, day1 = et_ns(d0, dtime(0, 0)), et_ns(d0 + timedelta(days=1), dtime(0, 0))
        today.append(1.0 if np.any(known & (ev >= day0) & (ev < day1)) else 0.0)
    res = pl.DataFrame(
        {"_t": times, cols[0]: hours, cols[1]: today}, schema={"_t": pl.Int64, cols[0]: pl.Float64, cols[1]: pl.Float64}
    )
    return keys.join(res, on="_t", how="left").select("_row", *cols)


def _regime(keys: pl.DataFrame, store: ContextStore, spec: ContextFeatureSpec, as_of: int) -> pl.DataFrame:
    cols = ["ctx_vix", "ctx_vix_pct_252", "ctx_regime_vol_high", "ctx_regime_trend", "ctx_regime_stress"]
    empty = keys.select("_row").with_columns([pl.lit(None, dtype=pl.Float64).alias(c) for c in cols])
    v = store.versions("regime", as_of)
    if v.is_empty():
        return empty
    v = v.select(
        pl.lit("R").alias("_k"),
        pl.col("available_at").dt.epoch("ns").alias("_at"),
        pl.col("event_time").dt.epoch("ns").alias("_ev"),
        pl.col("vix").alias("ctx_vix"),
        pl.col("vix_pct_252").alias("ctx_vix_pct_252"),
        pl.when(pl.col("vol_regime") == "unknown")
        .then(None)
        .otherwise((pl.col("vol_regime") == "high").cast(pl.Float64))
        .alias("ctx_regime_vol_high"),
        pl.col("trend_regime")
        .replace_strict({"uptrend": 1.0, "downtrend": -1.0, "chop": 0.0}, default=None, return_dtype=pl.Float64)
        .alias("ctx_regime_trend"),
        pl.col("stress").cast(pl.Float64).alias("ctx_regime_stress"),
    )
    v = _running_latest(v, "_k", "_ev").drop("_ev")
    j = asof_join(keys.with_columns(pl.lit("R").alias("_k")), v, on="_k", left_time="_t", right_time="_at")
    return j.select("_row", *cols)
