"""tbcore.ml: labels and weights, datasets, folds, pipeline, model zoo, training, evaluation, and the
DiagnosticsReport (section 10; ML-001..ML-016, ML-020..ML-023)."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from tb.ml.v1 import training_pb2 as tp
from tbcore.backtest.costs import CostModelConfig
from tbcore.backtest.signals import SignalEvalConfig, evaluate_decisions
from tbcore.backtest.vectorized import VectorData
from tbcore.labels import rank_relevance, sample_weights
from tbcore.ml import config, dataset, diagnostics, models, runs, schema, synthetic, train
from tbcore.ml.bundle import FeatureMismatchError, ModelBundle
from tbcore.ml.preprocess import Pipeline
from tbcore.ml.rules import residual_reversal_signals, residual_z


def _cfg(family: str, src: str, tf: str, hp: Mapping[str, object] | None = None) -> tp.TrainingConfig:
    c = tp.TrainingConfig(family=family, data_source=src, timeframe=tf)
    if hp:
        c.hyperparams.update(dict(hp))
    return config.resolve(c, schema.defaults(models.FAMILIES[family].schema))


@lru_cache(maxsize=16)
def _ds(family: str, src: str, tf: str) -> dataset.Dataset:
    cfg = _cfg(family, src, tf)
    bars, uni, bench = dataset.load_bars(cfg)
    spec = synthetic.preset(src.split(":")[1])
    return dataset.build(
        cfg, bars, uni, bench, boundary=synthetic.holdout_boundary(spec), task=models.model_task(family)
    )


def test_ml003_uniqueness_weights_hand_example() -> None:
    # Two labels on one instrument: spans (0, 2] and (1, 3]: bars 1..3; bar 2 is shared.
    w = sample_weights(np.array(["a", "a"]), np.array([0, 1]), np.array([2, 3]), "uniqueness", normalize=False)
    assert w == pytest.approx([0.75, 0.75])
    # A third, disjoint label is fully unique.
    inst3, s3, e3 = np.array(["a", "a", "a"]), np.array([0, 1, 5]), np.array([2, 3, 7])
    w3 = sample_weights(inst3, s3, e3, "uniqueness", normalize=False)
    assert w3 == pytest.approx([0.75, 0.75, 1.0])
    td = sample_weights(inst3, s3, e3, "time_decay", decay=0.5)
    assert td[0] < td[1] < td[2] and td.mean() == pytest.approx(1.0)
    assert list(rank_relevance(np.array([0.0, 0.5, 0.99, 1.0]), 5)) == [0, 2, 4, 4]


def test_ml001_ml020_dataset_rows_end_before_the_boundary() -> None:
    ds = _ds("lgbm_regression", "synthetic:intraday_small", "15m")
    boundary = synthetic.holdout_boundary(synthetic.preset("intraday_small"))
    f = ds.frame
    assert f.height > 1000
    assert f["label_available_at"].max() < boundary  # type: ignore[operator]
    assert (f["t1"] > f["ts_open"]).all() and (f["label_available_at"] >= f["decision_time"]).all()
    assert ds.bars["ts_open"].max() < boundary  # type: ignore[operator]  # nothing at or after the boundary was read
    assert ds.manifest.rows_dropped["warmup"] > 0


def test_ml004_snapshot_id_is_deterministic_and_content_addressed() -> None:
    cfg = _cfg("lgbm_regression", "synthetic:intraday_small", "15m")
    bars, uni, bench = dataset.load_bars(cfg)
    b = synthetic.holdout_boundary(synthetic.preset("intraday_small"))
    a1 = dataset.build(cfg, bars, uni, bench, boundary=b)
    a2 = dataset.build(cfg, bars, uni, bench, boundary=b)
    assert a1.snapshot_id == a2.snapshot_id
    other = dataset.build(cfg, bars.with_columns(pl.col("close") * 1.0001), uni, bench, boundary=b)
    assert other.snapshot_id != a1.snapshot_id
    fewer = dataset.build(cfg, bars, uni[:-1], bench, boundary=b)
    assert fewer.snapshot_id != a1.snapshot_id


def test_ml004_features_do_not_change_when_the_future_changes() -> None:
    cfg = _cfg("ridge", "synthetic:intraday_small", "15m")
    bars, uni, bench = dataset.load_bars(cfg)
    b = synthetic.holdout_boundary(synthetic.preset("intraday_small"))
    base = dataset.build(cfg, bars, uni, bench, boundary=b)
    cut = int(base.frame["decision_time"].quantile(0.5) or 0)
    shocked = bars.with_columns(
        pl.when(pl.col("available_at") > cut).then(pl.col("close") * 1.5).otherwise(pl.col("close")).alias("close")
    )
    alt = dataset.build(cfg, shocked, uni, bench, boundary=b)
    key = ["instrument_id", "ts_open"]
    j = base.frame.filter(pl.col("decision_time") <= cut).join(alt.frame, on=key, suffix="_alt")
    for f in base.features:
        a, c = j[f].to_numpy(), j[f + "_alt"].to_numpy()
        ok = np.isfinite(a) & np.isfinite(c)
        assert np.allclose(a[ok], c[ok]), f


def test_ml005_folds_purge_and_embargo_at_least_the_horizon() -> None:
    cfg = _cfg("ridge", "synthetic:intraday_small", "15m")
    assert cfg.splitter.embargo_bars >= cfg.label.horizon
    ds = _ds("ridge", "synthetic:intraday_small", "15m")
    splits = train.make_splits(cfg, ds)
    assert splits
    t0 = ds.frame["decision_time"].to_numpy()
    t1 = ds.frame["label_available_at"].to_numpy()
    eras = ds.frame["era"].to_numpy()
    for s in splits:
        assert not set(eras[s.train]) & set(eras[s.test])
        assert t1[s.train].max() < t0[s.test].min()  # walk-forward: training labels end before the test block


def test_ml006_pipeline_uses_training_rows_only() -> None:
    rng = np.random.default_rng(0)
    xtr = rng.normal(0, 1, (500, 3))
    xte = rng.normal(5, 1, (100, 3))
    p = Pipeline.fit(["a", "b", "c"], xtr)
    z = p.transform(xte)
    # Winsorized at the training fold's 99th percentile and standardized with training moments.
    assert np.allclose(z.max(axis=0), (p.hi - p.mean) / p.std)
    p2 = Pipeline.from_dict(p.to_dict())
    assert np.allclose(p2.transform(xte), z)


def test_ml007_early_stopping_tail_is_inside_the_training_fold() -> None:
    ds = _ds("lgbm_regression", "synthetic:intraday_small", "15m")
    cfg = _cfg("lgbm_regression", "synthetic:intraday_small", "15m")
    s = train.make_splits(cfg, ds)[0]
    core, valid = train._valid_tail(s.train, ds, cfg.splitter.embargo_bars * config.bar_ns("15m"))
    assert valid.size > 0 and not set(valid) & set(s.test) and not set(core) & set(valid)
    assert ds.frame["label_available_at"].to_numpy()[core].max() < ds.frame["decision_time"].to_numpy()[valid].min()


def test_ml011_hyperparameter_schema_validation() -> None:
    sch = models.FAMILIES["lgbm_regression"].schema
    assert schema.validate(sch, {"num_leaves": 31, "learning_rate": 0.1}) == []
    errs = schema.validate(sch, {"num_leaves": 1, "learning_rate": "x", "bogus": 1})
    assert any("num_leaves" in e for e in errs) and any("learning_rate" in e for e in errs)
    assert any("bogus" in e for e in errs)
    assert schema.coerce(sch, {"num_leaves": 31.0})["num_leaves"] == 31


@pytest.mark.parametrize(
    ("family", "src", "tf"),
    [
        ("no_skill", "synthetic:intraday_small", "15m"),
        ("ridge", "synthetic:intraday_small", "15m"),
        ("lgbm_regression", "synthetic:intraday_small", "15m"),
        ("lgbm_lambdarank", "synthetic:daily_small", "1d"),
        ("orb_meta", "synthetic:intraday_small", "15m"),
        ("residual_reversal_meta", "synthetic:daily_small", "1d"),
    ],
)
def test_ml014_every_family_trains_and_round_trips(family: str, src: str, tf: str, tmp_path: Path) -> None:
    cfg = _cfg(family, src, tf, {"n_estimators": 60} if "lgbm" in family or "meta" in family else None)
    ds = _ds(family, src, tf)
    res = train.train(cfg, ds, size_curve=False)
    assert res.folds and np.isfinite(res.oof).any()
    b = res.final
    assert b is not None
    b.save(tmp_path / "m")
    back = ModelBundle.load(tmp_path / "m")
    x = ds.frame.select(ds.features).to_numpy()[:200]
    assert np.allclose(back.score_matrix(x), b.score_matrix(x))
    with pytest.raises(FeatureMismatchError):
        back.check_features("other-hash", ds.features, tf)


def test_ml015_rebuild_reproduces_metrics_exactly() -> None:
    cfg = _cfg("lgbm_regression", "synthetic:intraday_small", "15m", {"n_estimators": 80})
    ds = _ds("lgbm_regression", "synthetic:intraday_small", "15m")
    a = train.train(cfg, ds, size_curve=False)
    b = train.train(cfg, ds, size_curve=False)
    assert [f.metrics for f in a.folds] == [f.metrics for f in b.folds]
    assert np.array_equal(np.nan_to_num(a.oof), np.nan_to_num(b.oof))


def test_ml016_meta_labels_and_filter_threshold() -> None:
    ds = _ds("orb_meta", "synthetic:intraday_small", "15m")
    assert set(np.unique(ds.frame["y"].to_numpy())) <= {0.0, 1.0}
    cfg = _cfg("orb_meta", "synthetic:intraday_small", "15m", {"n_estimators": 40})
    res = train.train(cfg, ds, size_curve=False)
    # Default threshold = training base rate (ML-016), so some signals pass the filter.
    assert 0.0 < res.folds[0].threshold < 1.0
    assert res.oos_metrics["primary_trades"] >= res.oos_metrics["bt_trades"] > 0


def test_ml016_residual_z_is_shared_by_vectorized_signals() -> None:
    bars = synthetic.generate(synthetic.preset("daily_small"))
    sig = residual_reversal_signals(bars, synthetic.MARKET_ID)
    inst = synthetic.instrument_id(0)
    c = bars.filter(pl.col("instrument_id") == inst)["close"].to_numpy()
    m = bars.filter(pl.col("instrument_id") == synthetic.MARKET_ID)["close"].to_numpy()
    z = sig.filter(pl.col("instrument_id") == inst)["z"].to_numpy()
    assert math.isnan(z[5]) and z[100] == pytest.approx(residual_z(c[:101], m[:101], 5, 20))


def test_ml012_signal_evaluator_enters_at_the_next_bar_open() -> None:
    bars = synthetic.generate(synthetic.preset("intraday_small")).filter(pl.col("instrument_id") != synthetic.MARKET_ID)
    vd = VectorData.from_bars(bars)
    inst = vd.instruments[0]
    t = 3
    r = evaluate_decisions(
        vd, [inst], np.array([vd.ts_open[t]]), np.array([1]), SignalEvalConfig(horizon=2), CostModelConfig()
    )
    tr = r.trades.row(0, named=True)
    assert tr["entry_time"] == vd.ts_open[t + 1] and tr["exit_time"] == vd.ts_open[t + 3]
    raw = vd.open[t + 1, 0]
    assert tr["entry_price"] > raw  # buy pays the spread
    # A decision on the session's last bar cannot enter in the same session (capped intraday).
    last = int(np.flatnonzero(vd.session == 0)[-1])
    r2 = evaluate_decisions(
        vd, [inst], np.array([vd.ts_open[last]]), np.array([1]), SignalEvalConfig(horizon=2), CostModelConfig()
    )
    assert r2.trades.height == 0


def test_ml023_overfit_fixture_is_flagged() -> None:
    hp = {
        "num_leaves": 128,
        "min_data_in_leaf": 5,
        "n_estimators": 300,
        "early_stopping_rounds": 0,
        "learning_rate": 0.2,
        "lambda_l2": 0.0,
    }
    cfg = _cfg("lgbm_regression", "synthetic:intraday_noise", "15m", hp)
    bars, uni, bench = dataset.load_bars(cfg)
    ds = dataset.build(
        cfg, bars, uni[:10], bench, boundary=synthetic.holdout_boundary(synthetic.preset("intraday_noise"))
    )
    rep = diagnostics.build_report(train.train(cfg, ds, size_curve=False), "overfit")
    codes = {f.code for f in rep.findings}
    assert "overfitting" in codes and "no_skill" in codes
    assert rep.holdout_excluded


def test_ml023_leaky_feature_is_flagged() -> None:
    base = _ds("ridge", "synthetic:intraday_small", "15m")
    rng = np.random.default_rng(0)
    leaky = dataset.Dataset(
        base.frame.with_columns((pl.col("y") + pl.Series(rng.normal(0, 0.3, base.frame.height))).alias("leak")),
        [*base.features, "leak"],
        base.task,
        base.manifest,
        base.bars,
        base.benchmark,
        base.feature_set,
    )
    cfg = _cfg("ridge", "synthetic:intraday_small", "15m")
    rep = diagnostics.build_report(train.train(cfg, leaky, size_curve=False), "leaky")
    flags = {(f.feature, f.reason) for f in rep.feature_diagnostics.leakage_flags}
    assert ("leak", "single_feature_skill") in flags
    assert any(f.code == "leakage_suspicion" and f.severity == tp.SEVERITY_CRITICAL for f in rep.findings)


def test_ml021_diagnostics_are_size_limited() -> None:
    ds = _ds("ridge", "synthetic:intraday_small", "15m")
    cfg = _cfg("ridge", "synthetic:intraday_small", "15m")
    rep = diagnostics.build_report(
        train.train(cfg, ds, size_curve=False), "r", limits=diagnostics.Limits(top_features=3, slices=4)
    )
    assert len(rep.data_quality.features) == 3 and len(rep.error_slices) <= 4 and rep.truncated
    assert rep.ByteSize() < 64_000


def test_bt014_vectorized_swing_shorts_pay_borrow_for_calendar_days() -> None:
    bars = synthetic.generate(synthetic.preset("daily_small")).filter(pl.col("instrument_id") != synthetic.MARKET_ID)
    vd = VectorData.from_bars(bars)
    inst, t = vd.instruments[0], 10
    cfg = SignalEvalConfig(horizon=5, cap_at_session_close=False)
    args = (vd, [inst], np.array([vd.ts_open[t]]), np.array([-1]), cfg)
    paid = evaluate_decisions(*args, CostModelConfig(borrow_fee_bps_annual=360.0))
    free = evaluate_decisions(*args, CostModelConfig(borrow_fee_bps_annual=0.0))
    tr = paid.trades.row(0, named=True)
    days = (tr["exit_time"] - tr["entry_time"]) // (86_400 * 10**9)
    assert days >= 5
    expect = tr["qty"] * vd.open[t + 1, 0] * 0.036 * days / 360
    assert paid.metrics["borrow_cost"] == pytest.approx(expect, rel=1e-9)
    assert free.trades["pnl"][0] - tr["pnl"] == pytest.approx(expect, rel=1e-9)
    longs = evaluate_decisions(
        vd, [inst], np.array([vd.ts_open[t]]), np.array([1]), cfg, CostModelConfig(borrow_fee_bps_annual=360.0)
    )
    assert longs.metrics["borrow_cost"] == 0.0


def test_bt008_vectorized_delisted_name_exits_at_last_close_less_haircut() -> None:
    bars = synthetic.generate(synthetic.preset("daily_small")).filter(pl.col("instrument_id") != synthetic.MARKET_ID)
    vd0 = VectorData.from_bars(bars)
    inst = vd0.instruments[0]
    cut = int(vd0.ts_open[14])
    gone = bars.filter((pl.col("instrument_id") != inst) | (pl.col("ts_open") <= cut))
    vd = VectorData.from_bars(gone)
    k = int(np.flatnonzero(vd.ts_open == cut)[0])
    cfg = SignalEvalConfig(horizon=10, cap_at_session_close=False, delisting_haircut=0.5)
    r = evaluate_decisions(vd, [inst], np.array([vd.ts_open[10]]), np.array([1]), cfg, CostModelConfig())
    assert r.trades.height == 1 and r.metrics["delisted_trades"] == 1.0  # never silently dropped (survivorship)
    assert r.trades["exit_price"][0] == pytest.approx(vd.close[k, 0] * 0.5)


def test_ml044_training_reference_for_drift_monitoring(tmp_path: Path) -> None:
    """ML-044, MN-038: a run stores a deterministic training-window feature sample and importance ranks."""
    cfg = _cfg("lgbm_regression", "synthetic:intraday_small", "15m")
    ds = _ds("lgbm_regression", "synthetic:intraday_small", "15m")
    res = train.train(cfg, ds, size_curve=False, explain=False)
    runs.write_monitoring_reference(res, diagnostics.build_report(res, "r1"), tmp_path)
    ref = pl.read_parquet(tmp_path / "reference.parquet")
    assert 0 < ref.height <= runs.REFERENCE_ROWS
    assert ref.columns == ["instrument_id", "decision_time", *ds.features]
    assert ref["decision_time"].to_numpy().max() <= ds.frame["decision_time"].to_numpy().max()  # training window only
    ranks = json.loads((tmp_path / "importance.json").read_text())
    assert sorted(ranks) == sorted(ds.features) and sorted(ranks.values()) == list(range(1, len(ds.features) + 1))
    runs.write_monitoring_reference(res, diagnostics.build_report(res, "r1"), tmp_path / "again")
    assert pl.read_parquet(tmp_path / "again" / "reference.parquet").equals(ref)  # deterministic
