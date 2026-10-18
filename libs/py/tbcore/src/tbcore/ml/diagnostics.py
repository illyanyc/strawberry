"""DiagnosticsReport for every training run (ML-020..ML-023, section 10).

Deterministic and computed only from the run's training and validation folds (the dataset ends at or
before the holdout boundary, so ``holdout_excluded`` is always true). Lists are cut to ``Limits``
(ML-021) and findings use ``Thresholds`` (config, ML-023; defaults in docs/business-logic/ml.md).
"""

from __future__ import annotations

import math
import time
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import polars as pl
from numpy.typing import NDArray

from tb.ml.v1 import training_pb2 as tp
from tbcore.labels import sample_weights
from tbcore.ml import metrics
from tbcore.ml.dataset import regimes
from tbcore.ml.train import TrainingResult, skill

F64 = NDArray[np.float64]


@dataclass(frozen=True)
class Thresholds:
    overfit_gap: float = 0.10
    underfit_skill: float = 0.01
    underfit_auc_skill: float = 0.02
    instability_rank_std: float = 5.0
    instability_sign_flip: float = 0.3
    regime_share: float = 0.70
    starved_rows: float = 2000.0
    size_curve_gain: float = 0.01
    leak_importance_share: float = 0.50
    leak_single_ic: float = 0.30
    leak_future_corr: float = 0.20
    calibration_ece: float = 0.10
    shift_psi: float = 0.25
    no_skill_margin: float = 0.005


@dataclass(frozen=True)
class Limits:
    top_features: int = 30
    shift_rows: int = 200
    slices: int = 50
    worst_samples: int = 20
    curve_points: int = 50


def _finite(d: dict[str, float]) -> dict[str, float]:
    return {k: float(v) for k, v in d.items() if v is not None and math.isfinite(float(v))}


def _downsample(points: list[tuple[int, float, float]], n: int) -> list[tuple[int, float, float]]:
    if len(points) <= n:
        return points
    idx = np.unique(np.linspace(0, len(points) - 1, n).round().astype(int))
    return [points[i] for i in idx]


def _period(ts: NDArray[Any]) -> tp.Period:
    return tp.Period(start=int(ts.min()), end=int(ts.max()) + 1) if ts.size else tp.Period()


def _components(corr: F64, names: list[str], cut: float) -> list[tuple[list[str], float]]:
    n = len(names)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(n):
        for j in range(i + 1, n):
            if abs(corr[i, j]) > cut:
                parent[find(i)] = find(j)
    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    out = []
    for members in groups.values():
        if len(members) > 1:
            sub = np.abs(corr[np.ix_(members, members)])
            np.fill_diagonal(sub, 0.0)
            out.append(([names[i] for i in members], float(sub.max())))
    return out


def build_report(
    res: TrainingResult, run_id: str, th: Thresholds | None = None, limits: Limits | None = None
) -> tp.DiagnosticsReport:
    th = th or Thresholds()
    lim = limits or Limits()
    ds = res.dataset
    f = ds.frame
    task = res.task
    feats = ds.features
    x_all = f.select(feats).to_numpy().astype(np.float64)
    y_all = f["y"].to_numpy().astype(np.float64)
    groups = f["ts_open"].to_numpy()
    rep = tp.DiagnosticsReport(
        report_id=f"diag_{run_id}",
        run_id=run_id,
        created_at=time.time_ns(),
        holdout_excluded=True,
        family=res.config.family,
        task=task,
        thresholds={k: float(v) for k, v in asdict(th).items()},
    )
    truncated = False
    ts = f["decision_time"].to_numpy()
    # ---- data quality ----
    dq = rep.data_quality
    for fr in res.folds:
        dq.rows_per_fold.append(
            tp.FoldMetrics(
                fold=fr.fold,
                train_rows=int(fr.train.size),
                test_rows=int(fr.test.size),
                train=_period(ts[fr.train]),
                test=_period(ts[fr.test]),
            )
        )
        yt = y_all[fr.test]
        dq.labels.append(
            tp.LabelFoldStat(
                fold=fr.fold,
                mean=float(np.nanmean(yt)) if yt.size else 0.0,
                std=float(np.nanstd(yt)) if yt.size else 0.0,
                skew=float(np.nanmean(((yt - np.nanmean(yt)) / (np.nanstd(yt) or 1.0)) ** 3)) if yt.size > 2 else 0.0,
                positive_rate=float(np.mean(yt > (0.5 if task == "classification" else 0.0))) if yt.size else 0.0,
                n=int(yt.size),
            )
        )
    gain_mean = np.mean([fr.gain for fr in res.folds], axis=0) if res.folds else np.zeros(len(feats))
    order = list(np.argsort(-gain_mean))
    top = order[: lim.top_features]
    truncated |= len(feats) > lim.top_features
    for j in top:
        col = x_all[:, j]
        fin = col[np.isfinite(col)]
        z = (fin - fin.mean()) / (fin.std() or 1.0) if fin.size else fin
        dq.features.append(
            tp.FeatureStat(
                feature=feats[j],
                missing_rate=float(1.0 - fin.size / max(col.size, 1)),
                mean=float(fin.mean()) if fin.size else 0.0,
                std=float(fin.std()) if fin.size else 0.0,
                p01=float(np.percentile(fin, 1)) if fin.size else 0.0,
                p99=float(np.percentile(fin, 99)) if fin.size else 0.0,
                outlier_rate=float(np.mean(np.abs(z) > 5)) if fin.size else 0.0,
            )
        )
    uniq = sample_weights(
        f["instrument_id"].to_numpy(), f["bar_idx"].to_numpy(), f["end_bar"].to_numpy(), "uniqueness", normalize=False
    )
    dq.avg_uniqueness = float(np.mean(uniq)) if uniq.size else 0.0
    rep.data_quality.rows_dropped.update(dict(ds.manifest.rows_dropped))
    # ---- feature diagnostics ----
    fd = rep.feature_diagnostics
    shift_top = top[:10]
    max_psi = 0.0
    psi_feature = ""
    for fr in res.folds:
        for j in shift_top:
            if len(fd.shift) >= lim.shift_rows:
                truncated = True
                break
            p = metrics.psi(x_all[fr.train, j], x_all[fr.test, j])
            k = metrics.ks(x_all[fr.train, j], x_all[fr.test, j])
            fd.shift.append(tp.ShiftStat(feature=feats[j], fold=fr.fold, psi=p if math.isfinite(p) else 0.0, ks=k))
            if math.isfinite(p) and p > max_psi:
                max_psi, psi_feature = p, feats[j]
    sub = x_all[:: max(1, x_all.shape[0] // 5000)]
    with np.errstate(all="ignore"):
        cmat = np.corrcoef(np.nan_to_num(sub, nan=0.0), rowvar=False) if sub.shape[0] > 2 else np.eye(len(feats))
    cmat = np.nan_to_num(np.atleast_2d(cmat))
    for members, mx in _components(cmat, feats, 0.7):
        fd.clusters.append(tp.FeatureCluster(features=members, max_abs_corr=mx))
    xs_mean = pl.DataFrame({"t": groups, **{feats[j]: x_all[:, j] for j in shift_top}}).group_by("t").mean().sort("t")
    for j in shift_top:
        stat = metrics.adf_stat(xs_mean[feats[j]].to_numpy())
        fd.stationarity.append(
            tp.StationarityResult(
                feature=feats[j],
                adf_stat=stat if math.isfinite(stat) else 0.0,
                critical_5pct=-2.86,
                stationary=bool(math.isfinite(stat) and stat < -2.86),
            )
        )
    tested = np.flatnonzero(res.oof_fold >= 0)
    rb = f["ret_beyond"].to_numpy()
    for j in order:
        share = float(gain_mean[j])
        if share > th.leak_importance_share:
            fd.leakage_flags.append(
                tp.LeakageFlag(
                    feature=feats[j], reason="dominant_importance", value=share, threshold=th.leak_importance_share
                )
            )
        single = metrics.ic(x_all[tested, j], y_all[tested]) if tested.size > 10 else math.nan
        if math.isfinite(single) and abs(single) > th.leak_single_ic:
            fd.leakage_flags.append(
                tp.LeakageFlag(
                    feature=feats[j], reason="single_feature_skill", value=abs(single), threshold=th.leak_single_ic
                )
            )
        fut = metrics.ic(x_all[:, j], rb)
        if math.isfinite(fut) and abs(fut) > th.leak_future_corr:
            fd.leakage_flags.append(
                tp.LeakageFlag(
                    feature=feats[j], reason="future_correlation", value=abs(fut), threshold=th.leak_future_corr
                )
            )
    # ---- learning curves ----
    lc = rep.learning_curves
    gaps = []
    for fr in res.folds:
        pts = _downsample(fr.curve, lim.curve_points)
        lc.per_fold.append(
            tp.FoldCurve(
                fold=fr.fold,
                metric=fr.curve_metric,
                best_step=fr.best_iteration,
                points=[
                    tp.CurvePoint(
                        step=s,
                        train_metric=a if math.isfinite(a) else 0.0,
                        val_metric=b if math.isfinite(b) else 0.0,
                    )
                    for s, a, b in pts
                ],
            )
        )
        ts_, vs_ = skill(task, fr.metrics, "train_"), skill(task, fr.metrics, "val_")
        if math.isfinite(ts_) and math.isfinite(vs_):
            gaps.append(ts_ - vs_)
    for rows, a, b in res.size_curve:
        lc.size_curve.append(
            tp.SizePoint(
                train_rows=rows, train_metric=a if math.isfinite(a) else 0.0, val_metric=b if math.isfinite(b) else 0.0
            )
        )
    lc.overfitting_gap = float(np.mean(gaps)) if gaps else 0.0
    # ---- fold metrics ----
    for fr in res.folds:
        rep.fold_metrics.append(
            tp.FoldMetrics(
                fold=fr.fold,
                train_rows=int(fr.train.size),
                test_rows=int(fr.test.size),
                train=_period(ts[fr.train]),
                test=_period(ts[fr.test]),
                metrics=_finite(fr.metrics),
                backtest=_finite(fr.backtest),
                best_iteration=fr.best_iteration,
            )
        )
    # ---- importance ----
    imp = rep.importance
    imp.method = "gain"
    ranks = np.array([np.argsort(np.argsort(-fr.gain)) for fr in res.folds]) if res.folds else np.zeros((0, len(feats)))
    signs = (
        np.array([np.sign(np.where(np.abs(fr.shap_dir) > 0.1, fr.shap_dir, 0.0)) for fr in res.folds])
        if res.folds
        else np.zeros((0, len(feats)))
    )
    for fr in res.folds:
        imp.per_fold.append(tp.FoldImportance(fold=fr.fold, values={feats[j]: float(fr.gain[j]) for j in top}))
    unstable: list[tuple[str, float, float]] = []
    for j in top:
        g = np.array([fr.gain[j] for fr in res.folds])
        flips = 0.0
        if signs.shape[0] > 1:
            s = signs[:, j]
            nz = s[s != 0]
            flips = float(min(np.mean(nz > 0), np.mean(nz < 0))) if nz.size else 0.0
        rstd = float(np.std(ranks[:, j])) if ranks.shape[0] > 1 else 0.0
        imp.stability.append(
            tp.ImportanceStability(
                feature=feats[j], mean=float(g.mean()), std=float(g.std()), sign_flip_rate=flips, rank_std=rstd
            )
        )
        top10 = j in order[:10] and len(res.folds) > 1
        if top10 and (rstd > th.instability_rank_std or flips > th.instability_sign_flip):
            unstable.append((feats[j], rstd, flips))
    if res.folds:
        for fa, fb, val in res.folds[-1].interactions[:10]:
            imp.top_interactions.append(tp.Interaction(feature_a=fa, feature_b=fb, value=val))
    # ---- error slices ----
    pred = res.oof[tested]
    yt = y_all[tested]
    overall = _skill_of(task, pred, yt, groups[tested])
    slice_cols: dict[str, NDArray[Any]] = {
        "regime": f["regime"].to_numpy()[tested],
        "symbol": f["instrument_id"].to_numpy()[tested],
        "label_class": _label_class(task, yt),
    }
    if res.config.timeframe != "1d":
        slice_cols["time_of_day"] = f["tod"].to_numpy()[tested]
    vol = f["vol"].to_numpy()[tested]
    if np.isfinite(vol).sum() > 10:
        q = np.nanquantile(vol, [0.2, 0.4, 0.6, 0.8])
        slice_cols["vol_bucket"] = np.array(
            [f"q{int(np.searchsorted(q, v)) + 1}" if math.isfinite(v) else "na" for v in vol]
        )
    for by, col in slice_cols.items():
        keys, counts = np.unique(col, return_counts=True)
        for key in keys[np.argsort(-counts)]:
            if len(rep.error_slices) >= lim.slices:
                truncated = True
                break
            m = col == key
            if m.sum() < 5:
                continue
            sk = _skill_of(task, pred[m], yt[m], groups[tested][m])
            ce = (
                metrics.calibration_error(pred[m], yt[m])
                if task == "classification"
                else abs(float(np.mean(pred[m]) - np.mean(yt[m])))
            )
            rep.error_slices.append(
                tp.ErrorSlice(
                    slice_by=by,
                    slice=str(key),
                    n=int(m.sum()),
                    metric=sk if math.isfinite(sk) else 0.0,
                    calibration_error=ce if math.isfinite(ce) else 0.0,
                    vs_overall=(sk - overall) if math.isfinite(sk) and math.isfinite(overall) else 0.0,
                    metric_name="auc_skill" if task == "classification" else "ic",
                )
            )
    err = np.abs(pred - yt)
    regimes = f["regime"].to_numpy()[tested]
    for i in np.argsort(-np.nan_to_num(err, nan=-1.0))[: lim.worst_samples]:
        rep.worst_samples.append(
            tp.WorstSample(
                instrument_id=str(f["instrument_id"][int(tested[i])]),
                decision_time=int(ts[tested[i]]),
                label=float(yt[i]),
                prediction=float(pred[i]),
                regime=str(regimes[i]),
            )
        )
    # ---- findings (ML-023) ----
    findings: list[tp.Finding] = []

    def add(code: str, sev: int, msg: str, ev: list[tuple[str, float, float]]) -> None:
        findings.append(
            tp.Finding(
                code=code,
                severity=sev,  # type: ignore[arg-type]
                message=msg,
                evidence=[tp.Evidence(name=n, value=v, threshold=t, ref=f"{rep.report_id}") for n, v, t in ev],
            )
        )

    W, C, INFO = tp.SEVERITY_WARNING, tp.SEVERITY_CRITICAL, tp.SEVERITY_INFO
    if gaps and lc.overfitting_gap > th.overfit_gap:
        add(
            "overfitting",
            W,
            "training skill far exceeds validation skill",
            [("mean_train_minus_val_skill", lc.overfitting_gap, th.overfit_gap)],
        )
    tr_sk = [skill(task, fr.metrics, "train_") for fr in res.folds]
    va_sk = [skill(task, fr.metrics, "val_") for fr in res.folds]
    under = th.underfit_auc_skill if task == "classification" else th.underfit_skill
    if tr_sk and np.nanmean(tr_sk) < under and np.nanmean(va_sk) < under:
        add(
            "underfitting",
            W,
            "the model learns little even on training data",
            [
                ("mean_train_skill", float(np.nanmean(tr_sk)), under),
                ("mean_val_skill", float(np.nanmean(va_sk)), under),
            ],
        )
    for name, rstd, flips in unstable:
        add(
            "instability",
            W,
            f"importance of {name} is unstable across folds",
            [("rank_std", rstd, th.instability_rank_std), ("sign_flip_rate", flips, th.instability_sign_flip)],
        )
    share, reg = _regime_share(res)
    if share > th.regime_share:
        add(
            "regime_dependence",
            W,
            f"most positive P&L comes from regime {reg}",
            [("pnl_share", share, th.regime_share)],
        )
    min_rows = min((fr.train.size for fr in res.folds), default=0)
    sc_gain = (res.size_curve[-1][2] - res.size_curve[0][2]) if len(res.size_curve) > 1 else 0.0
    if min_rows < th.starved_rows or (math.isfinite(sc_gain) and sc_gain > th.size_curve_gain):
        add(
            "data_starved",
            INFO if min_rows >= th.starved_rows else W,
            "more data would likely help",
            [
                ("min_train_rows", float(min_rows), th.starved_rows),
                ("size_curve_gain", float(sc_gain), th.size_curve_gain),
            ],
        )
    for lf in fd.leakage_flags:
        add(
            "leakage_suspicion",
            C,
            f"{lf.feature}: {lf.reason}",
            [(lf.reason, lf.value, lf.threshold)],
        )
    if task == "classification":
        ece = metrics.calibration_error(pred, yt)
        if math.isfinite(ece) and ece > th.calibration_ece:
            add("calibration", W, "filter probabilities are poorly calibrated", [("ece", ece, th.calibration_ece)])
    if max_psi > th.shift_psi:
        add(
            "distribution_shift",
            W,
            f"{psi_feature} shifts between training and validation",
            [("max_psi", max_psi, th.shift_psi)],
        )
    oos_skill = overall
    if not math.isfinite(oos_skill) or oos_skill <= th.no_skill_margin:
        add(
            "no_skill",
            W,
            "out-of-fold skill does not beat the no-skill baseline",
            [("oos_skill", oos_skill if math.isfinite(oos_skill) else 0.0, th.no_skill_margin)],
        )
    n1 = res.oos_metrics.get("bt_net_return", 0.0)
    n2 = res.oos_metrics.get("bt_net_return_2x", 0.0)
    if n1 > 0 and n2 < 0:
        add("cost_sensitivity", W, "positive at 1x costs, negative at 2x", [("net_1x", n1, 0.0), ("net_2x", n2, 0.0)])
    rep.findings.extend(findings)
    rep.truncated = truncated
    return rep


def _skill_of(task: str, pred: F64, y: F64, groups: NDArray[Any]) -> float:
    if task == "classification":
        a = metrics.auc(pred, y)
        return a - 0.5 if math.isfinite(a) else math.nan
    return metrics.ic(pred, y)


def _label_class(task: str, y: F64) -> NDArray[Any]:
    if task == "classification":
        return np.where(y > 0.5, "positive", "negative")
    q = np.nanquantile(y, [0.2, 0.4, 0.6, 0.8]) if y.size > 5 else np.array([0.0])
    return np.array([f"q{int(np.searchsorted(q, v)) + 1}" for v in y])


def _regime_share(res: TrainingResult) -> tuple[float, str]:
    tr = res.trades
    if tr.is_empty():
        return 0.0, ""
    col = "decision_time" if "decision_time" in tr.columns else "entry_time"
    rmap = regimes(res.dataset.bars, res.dataset.benchmark)
    j = tr.select(pl.col(col).alias("ts_open"), "pnl").join(rmap, on="ts_open", how="left")
    pos = j.filter(pl.col("pnl") > 0).group_by("regime").agg(pl.col("pnl").sum()).sort("pnl", descending=True)
    total = float(pos["pnl"].sum()) if pos.height else 0.0
    if total <= 0 or pos.height < 1:
        return 0.0, ""
    return float(pos["pnl"][0]) / total, str(pos["regime"][0])
