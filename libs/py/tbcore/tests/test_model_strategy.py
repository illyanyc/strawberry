"""Model strategies on the shared host: online/offline parity, fail-closed feature checks, event-driven
evaluation with protective stops, the ML filter, next-open swing decisions, and a shadow challenger next
to a paper champion (ML-040, ML-041, ML-042, ML-016, SM-011, SM-013, BT-026, EX-056)."""

from __future__ import annotations

import json
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from tb.ml.v1 import training_pb2 as tp
from tbcore.backtest.model_eval import evaluate_event, next_open_decisions, strategy_spec
from tbcore.data.calendar import xnys
from tbcore.ml import config, dataset, models, schema, synthetic, train
from tbcore.ml.bundle import FeatureMismatchError, ModelBundle
from tbcore.strategy.api import Bar
from tbcore.strategy.host import Runner
from tbcore.strategy.model_signal import OnlineModelScores

PRESET = "intraday_small"


@lru_cache(maxsize=4)
def _trained(family: str = "ridge") -> tuple[train.TrainingResult, dataset.Dataset]:
    cfg = config.resolve(
        tp.TrainingConfig(family=family, data_source=f"synthetic:{PRESET}", timeframe="15m"),
        schema.defaults(models.FAMILIES[family].schema),
    )
    bars, uni, bench = dataset.load_bars(cfg)
    ds = dataset.build(cfg, bars, uni, bench, boundary=synthetic.holdout_boundary(synthetic.preset(PRESET)))
    return train.train(cfg, ds, size_curve=False, explain=False), ds


def _bar(row: dict[str, object]) -> Bar:
    return Bar(
        instrument_id=str(row["instrument_id"]),
        timeframe="15m",
        ts_open=int(row["ts_open"]),  # type: ignore[call-overload]
        ts_close=int(row["ts_close"]),  # type: ignore[call-overload]
        available_at=int(row["available_at"]),  # type: ignore[call-overload]
        open=Decimal(str(row["open"])),
        high=Decimal(str(row["high"])),
        low=Decimal(str(row["low"])),
        close=Decimal(str(row["close"])),
        volume=Decimal(str(row["volume"])),
    )


def test_ml041_ml042_online_predictions_equal_offline_for_the_same_bars() -> None:
    res, ds = _trained()
    bundle = res.final
    assert bundle is not None
    uni = sorted(set(ds.frame["instrument_id"].to_list()))
    src = OnlineModelScores(bundle, uni)
    bars = ds.bars.sort(["ts_open", "instrument_id"])
    times = np.sort(bars["ts_open"].unique().to_numpy())
    feed = bars.filter(pl.col("ts_open") >= int(times[-60]))
    online: dict[tuple[str, int], tuple[float, str]] = {}
    for row in feed.iter_rows(named=True):
        for d in src.decisions(_bar(row)):
            online[(d.instrument_id, d.bar.ts_open)] = (d.score, d.feature_snapshot_id)
    offline = ds.frame.filter(pl.col("ts_open") >= int(times[-60 + ds.feature_set.max_lookback + 1]))
    scores = bundle.score_frame(offline)
    checked = 0
    for row, s in zip(offline.iter_rows(named=True), scores, strict=True):
        key = (row["instrument_id"], row["ts_open"])
        if key in online:
            assert online[key][0] == pytest.approx(float(s), abs=1e-9)
            assert online[key][1].startswith("fv_")
            checked += 1
    assert checked > 100


def test_ml040_online_scoring_fails_closed_on_a_feature_set_mismatch() -> None:
    res, ds = _trained()
    assert res.final is not None
    j = res.final.to_json()
    j["feature_set_hash"] = "0" * 64
    with pytest.raises(FeatureMismatchError):
        OnlineModelScores(ModelBundle.from_json(j), sorted(set(ds.frame["instrument_id"].to_list())))


def _decisions(res: train.TrainingResult) -> pl.DataFrame:
    thr = {fr.fold: fr.threshold for fr in res.folds}
    return res.oof_frame().with_columns(
        pl.col("pred").alias("score"),
        (pl.col("pred") >= pl.col("fold").replace_strict(thr, return_dtype=pl.Float64)).alias("take"),
    )


def test_sm011_model_signal_runs_event_driven_with_protective_stops() -> None:
    res, ds = _trained()
    dec = _decisions(res)
    uni = sorted(set(ds.frame["instrument_id"].to_list()))
    spec = strategy_spec("regression", "15m", uni, ds.benchmark, {"horizon": 4}, Decimal(100_000))
    ev = evaluate_event(dec, ds.bars, spec, int(dec["decision_time"].min()), int(ds.bars["available_at"].max() or 0))  # type: ignore[arg-type]
    assert ev.trades > 10 and ev.rets.size > 0
    opens = ev.result.intents.filter(pl.col("kind") == "open")
    assert opens.height and opens["stop_loss"].null_count() == 0  # N5: every entry carries a stop
    assert (opens["decision_time"] > 0).all()


def test_ml016_meta_filter_drops_entries_the_filter_does_not_take() -> None:
    _, ds = _trained()
    uni = sorted(set(ds.frame["instrument_id"].to_list()))
    params = {"horizon": 4, "primary_class": "orb", "primary_params": {}}
    spec = strategy_spec("rule_filter", "15m", uni, ds.benchmark, params, Decimal(100_000))
    keys = ds.frame.select("instrument_id", "ts_open").unique()
    none = keys.with_columns(pl.lit(0.0).alias("score"), pl.lit(False).alias("take"))
    start = int(ds.frame["decision_time"].quantile(0.6) or 0)
    end = int(ds.bars["available_at"].max() or 0)  # type: ignore[arg-type]
    blocked = evaluate_event(none, ds.bars, spec, start, end)
    allowed = evaluate_event(none.with_columns(pl.lit(True).alias("take")), ds.bars, spec, start, end)
    assert blocked.trades == 0 and allowed.trades > 0


def test_bt026_daily_decisions_execute_at_the_next_open() -> None:
    spec = synthetic.preset("daily_small")
    bars = synthetic.generate(spec)
    shifted = next_open_decisions(bars)
    cal = xnys()
    row = shifted.sort("ts_open").row(0, named=True)
    nxt = next(
        s
        for s in cal.sessions(synthetic.SyntheticSpec().start, synthetic.SyntheticSpec().start.replace(year=2026))
        if int(s.open.timestamp()) * 10**9 > row["ts_open"]
    )
    assert row["available_at"] == int(nxt.open.timestamp()) * 10**9 + 60 * 10**9
    assert row["available_at"] > row["ts_close"]  # later than the bar's own finality: never look-ahead
    uni = synthetic.universe(spec)[:8]
    params = {"horizon": 5, "primary_class": "residual_reversal", "primary_params": {"benchmark": synthetic.MARKET_ID}}
    s = strategy_spec("rule_filter", "1d", uni, synthetic.MARKET_ID, params, Decimal(100_000))
    keys = bars.select("instrument_id", "ts_open").with_columns(pl.lit(1.0).alias("score"), pl.lit(True).alias("take"))
    ev = evaluate_event(keys, bars, s, int(bars["ts_open"].quantile(0.3) or 0), int(bars["available_at"].max() or 0))  # type: ignore[arg-type]
    assert ev.trades > 0 and ev.result.intents["approved"].any()


def test_ex056_shadow_challenger_runs_next_to_the_paper_champion(tmp_path: Path) -> None:
    res, ds = _trained()
    assert res.final is not None
    res.final.save(tmp_path / "champ")
    res.final.save(tmp_path / "chall")
    uni = sorted(set(ds.frame["instrument_id"].to_list()))

    def entry(sid: str, mode: str, bundle: str) -> dict[str, object]:
        return {
            "strategy_id": sid,
            "short_id": sid[:7],
            "broker_account_id": "sim-paper",
            "mode": mode,
            "allocation": "10000",
            "runtime": {
                "class": "model_signal",
                "timeframe": "15m",
                "instruments": [*uni, ds.benchmark],
                "params": {"bundle": bundle, "universe": uni, "horizon": 4, "benchmark": ds.benchmark},
            },
        }

    reg = {
        "strategies": [
            entry("champion", "paper", str(tmp_path / "champ")),
            entry("challenger", "shadow", str(tmp_path / "chall")),
        ]
    }
    runner = Runner.from_registry_data(json.loads(json.dumps(reg)))
    assert {h.strategy_id: h.mode for h in runner.hosted} == {"champion": "paper", "challenger": "shadow"}
    bars = ds.bars.sort(["ts_open", "instrument_id"])
    times = np.sort(bars["ts_open"].unique().to_numpy())
    seen = {"champion": 0, "challenger": 0}
    for row in bars.filter(pl.col("ts_open") >= int(times[-200])).iter_rows(named=True):
        for it in runner.dispatch(_bar(row)):
            seen[it.strategy_id] += 1
    assert seen["champion"] > 0 and seen["challenger"] == seen["champion"]  # same model, same bars, same intents
