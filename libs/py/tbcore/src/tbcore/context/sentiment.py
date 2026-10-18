"""Point-in-time news-sentiment aggregates (CX-012, CX-014).

Input: scored articles (one row per article and instrument) with ``available_at`` (the score's
availability), ``event_time`` (publication), ``score`` in [-1, 1], ``duplicate``, and
``contaminated``. Aggregates for a decision time ``t`` use only rows with ``available_at <= t`` and
publication in ``(t - window, t]``. Contaminated scores (article published before the scoring
model's training cutoff) are excluded by default (N7 rule 10).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import polars as pl

NS = 1_000_000_000


@dataclass(frozen=True, slots=True)
class SentimentAgg:
    count: int
    mean: float | None
    decay: float | None
    novelty: float | None


def aggregate(
    scores: pl.DataFrame,
    decision_ns: int,
    window_ns: int,
    half_life_ns: int,
    include_contaminated: bool = False,
) -> SentimentAgg:
    """Aggregate one instrument's scores as of ``decision_ns``.

    * ``count``: original (non-duplicate) articles in the window;
    * ``mean``: their mean score;
    * ``decay``: exponentially decay-weighted mean by age since publication (``half_life_ns``);
    * ``novelty``: share of articles in the window that are originals (1.0 = no syndicated repeats).
    """
    if scores.is_empty():
        return SentimentAgg(0, None, None, None)
    t = pl.col("available_at").dt.epoch("ns") if scores.schema["available_at"] != pl.Int64 else pl.col("available_at")
    e = pl.col("event_time").dt.epoch("ns") if scores.schema["event_time"] != pl.Int64 else pl.col("event_time")
    f = scores.filter((t <= decision_ns) & (e > decision_ns - window_ns) & (e <= decision_ns))
    if not include_contaminated:
        f = f.filter(~pl.col("contaminated").fill_null(True))
    if f.is_empty():
        return SentimentAgg(0, None, None, None)
    total = f.height
    orig = f.filter(~pl.col("duplicate").fill_null(False))
    if orig.is_empty():
        return SentimentAgg(0, None, None, 0.0)
    ages = orig.select((decision_ns - e).alias("age"))["age"].to_list()
    vals = orig["score"].to_list()
    w = [math.pow(0.5, a / half_life_ns) for a in ages]
    decay = sum(wi * v for wi, v in zip(w, vals, strict=True)) / sum(w)
    return SentimentAgg(orig.height, sum(vals) / len(vals), decay, orig.height / total)
