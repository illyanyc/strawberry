"""Validation splitters and overfitting statistics (BT-030..BT-047, SM-002)."""

from __future__ import annotations

import math
from collections import Counter

import numpy as np
import pytest

from tbcore.validation import (
    CombinatorialPurgedCV,
    PurgedKFold,
    WalkForward,
    deflated_sharpe,
    effective_trials,
    expected_max_sharpe,
    min_backtest_length,
    min_track_record_length,
    pbo_cscv,
    probabilistic_sharpe,
    romano_wolf,
    sharpe,
    spa_test,
    stitch_walk_forward,
)
from tbcore.validation.stats import bootstrap_ci

H = 5  # label horizon in samples


def _overlapping(n: int = 120) -> tuple[np.ndarray, np.ndarray]:
    t0 = np.arange(n, dtype=np.int64) * 10
    return t0, t0 + H * 10


def _assert_purged(train: np.ndarray, test: np.ndarray, t0: np.ndarray, t1: np.ndarray, embargo: int) -> None:
    cuts = np.flatnonzero(np.diff(test) != 1) + 1
    for blk in np.split(test, cuts):
        start, end = t0[blk].min(), t1[blk].max()
        overlap = (t0[train] <= end) & (t1[train] >= start)
        assert not overlap.any(), "a training label window overlaps the test block"
        assert not ((t0[train] > end) & (t0[train] <= end + embargo)).any(), "embargo violated"


def test_bt031_bt033_purged_kfold_removes_overlapping_labels() -> None:
    t0, t1 = _overlapping()
    cv = PurgedKFold(5, embargo=30, label_end_times=t1)
    splits = list(cv.split(t0))
    assert len(splits) == 5
    tested = np.concatenate([s.test for s in splits])
    assert sorted(tested.tolist()) == list(range(len(t0)))
    for s in splits:
        assert np.intersect1d(s.train, s.test).size == 0
        _assert_purged(s.train, s.test, t0, t1, 30)
    # Without purging, a naive split of the middle fold would overlap on both sides.
    mid = splits[2]
    naive = np.setdiff1d(np.arange(len(t0)), mid.test)
    assert ((t0[naive] <= t1[mid.test].max()) & (t1[naive] >= t0[mid.test].min())).any()
    assert naive.size - mid.train.size >= 2 * H


def test_bt033_cpcv_splits_and_paths() -> None:
    t0, t1 = _overlapping(240)
    cv = CombinatorialPurgedCV(n_groups=6, n_test_groups=2, embargo=20, label_end_times=t1)
    splits = list(cv.split(t0))
    assert len(splits) == cv.n_splits == 15
    assert cv.n_paths == 5
    counts: Counter[int] = Counter()
    for s in splits:
        _assert_purged(s.train, s.test, t0, t1, 20)
        counts.update(s.test.tolist())
    assert set(counts.values()) == {5}
    paths = cv.paths()
    assert len(paths) == 5
    for p in paths:
        assert sorted(g for g, _ in p) == list(range(6))
    values = [np.full(s.test.size, float(s.fold)) for s in splits]
    oos = cv.oos_paths(t0, values)
    assert oos.shape == (5, 240)
    assert np.isfinite(oos).all()
    # Every (group, split) test result is used by exactly one path.
    pairs = [pair for p in paths for pair in p]
    assert len(pairs) == len(set(pairs)) == 15 * 2


def test_bt034_walk_forward_gap_anchoring_and_stitching() -> None:
    t0, t1 = _overlapping(100)
    wf = WalkForward(train_len=40, test_len=20, embargo=20, label_end_times=t1)
    splits = list(wf.split(t0))
    assert [s.test[0] for s in splits] == [40, 60, 80]
    for s in splits:
        assert (t1[s.train] < t0[s.test].min() - 20).all()
        assert s.train.size <= 40
    anchored = list(WalkForward(40, 20, anchored=True, label_end_times=t1).split(t0))
    assert all(s.train[0] == 0 for s in anchored)
    assert anchored[-1].train.size > anchored[0].train.size
    stitched = stitch_walk_forward(splits, [np.ones(s.test.size) * k for k, s in enumerate(splits)], 100)
    assert np.isnan(stitched[:40]).all() and np.isfinite(stitched[40:]).all()
    with pytest.raises(ValueError, match="overlapping"):
        stitch_walk_forward(list(WalkForward(40, 20, step=10).split(t0)), [np.ones(20)] * 5, 100)


def test_bt030_era_wise_splits_never_split_an_era() -> None:
    eras = np.repeat(np.arange(30), 7)
    t0 = np.repeat(np.arange(30, dtype=np.int64) * 100, 7)
    t1 = t0 + 250  # labels span ~3 eras
    for s in PurgedKFold(5, label_end_times=t1).split(t0, groups=eras):
        assert not set(eras[s.train]) & set(eras[s.test])
        _assert_purged(s.train, s.test, t0, t1, 0)
    for s in CombinatorialPurgedCV(5, 2, label_end_times=t1).split(t0, groups=eras):
        assert not set(eras[s.train]) & set(eras[s.test])


def test_bt030_rejects_bad_inputs() -> None:
    with pytest.raises(ValueError, match="label end"):
        list(PurgedKFold(3, label_end_times=[0, 1, 0]).split([0, 1, 2]))
    with pytest.raises(ValueError, match="sorted"):
        list(PurgedKFold(2).split([3, 1, 2, 4]))


def test_bt041_deflated_sharpe_matches_the_published_example() -> None:
    # Bailey & López de Prado (2014): SR 2.5 (annual) over 1,250 daily obs, skew -3, kurtosis 10,
    # 100 trials with annualized Sharpe variance 0.5 -> E[max SR] = 0.1132, DSR = 0.9004.
    sr = 2.5 / math.sqrt(250)
    v = 0.5 / 250
    assert expected_max_sharpe(100, v) == pytest.approx(0.1132, abs=5e-4)
    assert deflated_sharpe(sr, 100, v, -3.0, 10.0, 1250) == pytest.approx(0.9004, abs=2e-3)
    # More trials deflate further; one trial is plain PSR against 0.
    assert deflated_sharpe(sr, 1000, v, -3.0, 10.0, 1250) < deflated_sharpe(sr, 100, v, -3.0, 10.0, 1250)
    assert deflated_sharpe(sr, 1, v, 0.0, 3.0, 1250) == pytest.approx(probabilistic_sharpe(sr, 0.0, 1250))


def test_bt042_bt043_min_track_record_and_backtest_length() -> None:
    sr, target = 0.08, 0.02
    n = min_track_record_length(sr, target, -0.5, 5.0, 0.95)
    assert probabilistic_sharpe(sr, target, n, -0.5, 5.0) == pytest.approx(0.95, abs=1e-9)  # type: ignore[arg-type]
    assert math.isinf(min_track_record_length(0.01, 0.02))
    # "45 independent trials in 5 years can produce an in-sample Sharpe of 1 from noise alone".
    assert min_backtest_length(45, 1.0) == pytest.approx(5.0, abs=0.05)


def test_bt044_effective_trials_counts_clusters() -> None:
    rng = np.random.default_rng(3)
    base = rng.normal(0, 0.01, (750, 3))
    trials = np.concatenate([base[:, [k]] + rng.normal(0, 0.002, (750, 10)) for k in range(3)], axis=1)
    et = effective_trials(trials)
    assert et.n_effective == 3
    assert len(set(et.labels[:10])) == 1 and len(set(et.labels[10:20])) == 1
    assert et.sr_variance >= 0


def test_bt045_pbo_noise_vs_real_edge() -> None:
    # On pure noise the IS-best trial is a coin flip OOS: PBO ~ 0.5 on average over datasets (a single
    # dataset's PBO is widely dispersed, so the check averages independent draws).
    draws = [pbo_cscv(np.random.default_rng(s).normal(0, 0.01, (800, 20)), n_partitions=10).pbo for s in range(12)]
    assert 0.35 <= float(np.mean(draws)) <= 0.65
    rng = np.random.default_rng(11)
    noise = rng.normal(0, 0.01, (1600, 40))
    edge = noise.copy()
    edge[:, 0] += 0.004
    res = pbo_cscv(edge)
    assert res.pbo < 0.05
    assert res.n_combinations == 12870
    custom = pbo_cscv(edge[:, :5], n_partitions=8, metric=lambda r: float(np.mean(r)))
    assert custom.pbo < 0.1


def test_bt046_spa_detects_superior_model_only() -> None:
    rng = np.random.default_rng(5)
    bench = rng.normal(0, 0.01, 1000)
    noise = bench[:, None] + rng.normal(0, 0.01, (1000, 20))
    assert spa_test(bench, noise, n_boot=500).p_consistent > 0.1
    good = noise.copy()
    good[:, 3] += 0.003
    res = spa_test(bench, good, n_boot=500)
    assert res.p_consistent < 0.05
    assert res.p_lower <= res.p_consistent <= res.p_upper


def test_bt047_romano_wolf_and_bootstrap_ci() -> None:
    rng = np.random.default_rng(9)
    bench = rng.normal(0, 0.01, 1000)
    models = bench[:, None] + rng.normal(0, 0.01, (1000, 6))
    models[:, 0] += 0.003
    p = romano_wolf(bench, models, n_boot=500)
    assert p[0] < 0.05
    assert (p[1:] > 0.05).all()
    r = rng.normal(0.001, 0.01, 1000)
    lo, hi = bootstrap_ci(r, n_boot=400)
    assert lo < sharpe(r) < hi


def test_bt040_sharpe_annualization() -> None:
    r = np.array([0.01, -0.005, 0.007, 0.002, -0.001])
    assert sharpe(r, 252) == pytest.approx(sharpe(r) * math.sqrt(252))
    assert math.isnan(sharpe([0.01]))
