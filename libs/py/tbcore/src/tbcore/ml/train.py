"""Training loop: splits → fold-fitted pipeline and model → out-of-fold predictions → evaluation (ML-005..ML-016).

``train`` is deterministic for a fixed config, dataset, seed, and thread count (ML-015). Folds come from
``tbcore.validation`` with eras = session dates and an embargo of at least the label horizon (ML-005).
Early stopping uses a purged tail of each training fold, never the test fold (ML-007). Every run is
evaluated through the shared cost model (ML-012): model families by ``tbcore.backtest.signals``, the ORB
filter by the vectorized ORB engine, the swing filter by the signal evaluator.
"""

from __future__ import annotations

import math
import platform
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from importlib import metadata
from typing import Any

import numpy as np
import polars as pl
from numpy.typing import NDArray

from tb.ml.v1 import training_pb2 as tp
from tbcore.backtest.costs import CostModelConfig
from tbcore.backtest.signals import SignalEvalConfig, evaluate_decisions, session_days
from tbcore.backtest.vectorized import VectorData
from tbcore.labels import rank_relevance, sample_weights
from tbcore.ml import metrics
from tbcore.ml.bundle import ModelBundle
from tbcore.ml.config import DAILY, bar_ns, config_hash, hyperparams, to_dict
from tbcore.ml.dataset import Dataset
from tbcore.ml.models import EvalSet, Model, NoSkill, info, make, model_task
from tbcore.ml.preprocess import Pipeline
from tbcore.ml.rules import filtered_orb_result
from tbcore.validation.splitters import CombinatorialPurgedCV, PurgedKFold, Split, WalkForward, purge

F64 = NDArray[np.float64]
I64 = NDArray[np.int64]
Progress = Callable[[dict[str, Any]], None]
ALLOCATION = 100_000.0


class Cancelled(RuntimeError):
    pass


@dataclass
class FoldResult:
    fold: int
    train: I64
    test: I64
    valid: I64
    pipeline: Pipeline
    model: Model
    pred_test: F64
    pred_train: F64
    threshold: float
    metrics: dict[str, float]
    backtest: dict[str, float]
    curve: list[tuple[int, float, float]]
    curve_metric: str
    best_iteration: int
    gain: F64
    permutation: F64
    shap_abs: F64
    shap_dir: F64  # sign of corr(feature value, contribution): the feature's direction of effect
    interactions: list[tuple[str, str, float]]


@dataclass
class TrainingResult:
    config: tp.TrainingConfig
    dataset: Dataset
    task: str
    folds: list[FoldResult]
    oof: F64  # per dataset row, NaN where never tested
    oof_fold: I64
    session_days: I64  # tested sessions only
    session_returns: F64
    session_returns_2x: F64
    trades: pl.DataFrame
    oos_metrics: dict[str, float]
    final: ModelBundle
    size_curve: list[tuple[int, float, float]]
    repro: tp.Reproducibility
    primary_metrics: dict[str, float] = field(default_factory=dict)

    def oof_frame(self) -> pl.DataFrame:
        f = self.dataset.frame
        return pl.DataFrame(
            {
                "instrument_id": f["instrument_id"],
                "ts_open": f["ts_open"],
                "decision_time": f["decision_time"],
                "era": f["era"],
                "fold": self.oof_fold,
                "y": f["y"],
                "pred": self.oof,
            }
        ).filter(pl.col("fold") >= 0)


def make_splits(cfg: tp.TrainingConfig, ds: Dataset) -> list[Split]:
    """Deterministic splits from the dataset rows and the splitter spec (ML-005)."""
    f = ds.frame
    if f.height == 0:
        return []
    t0 = f["decision_time"].to_numpy()
    t1 = np.maximum(f["label_available_at"].to_numpy(), t0)
    groups = f["era"].to_numpy()
    sp = cfg.splitter
    embargo = int(sp.embargo_bars) * bar_ns(cfg.timeframe)
    n_eras = int(np.unique(groups).size)
    if sp.kind == "walk_forward":
        tr, te = int(sp.train_eras), int(sp.test_eras)
        if tr + te > n_eras:  # small datasets: scale the windows down, keep at least two folds
            tr = max(2, int(n_eras * tr / (tr + 2 * te)))
            te = max(1, (n_eras - tr) // 2)
        splitter: Any = WalkForward(tr, te, anchored=sp.anchored, embargo=embargo, label_end_times=t1)
    elif sp.kind == "purged_kfold":
        splitter = PurgedKFold(int(sp.n_splits), embargo=embargo, label_end_times=t1)
    elif sp.kind == "cpcv":
        splitter = CombinatorialPurgedCV(int(sp.n_groups), int(sp.n_test_groups), embargo=embargo, label_end_times=t1)
    else:
        raise ValueError(f"unknown splitter {sp.kind!r} (SM-002)")
    return [s for s in splitter.split(t0, groups) if s.train.size > 0 and s.test.size > 0]


def _valid_tail(train: I64, ds: Dataset, embargo: int) -> tuple[I64, I64]:
    """Last 20 % of the training fold's eras as the early-stopping tail, purged from the core (ML-007)."""
    eras = ds.frame["era"].to_numpy()[train]
    uniq = np.unique(eras)
    if uniq.size < 5:
        return train, np.array([], dtype=np.int64)
    cut = uniq[int(uniq.size * 0.8)]
    valid = train[eras >= cut]
    core = train[eras < cut]
    t0 = ds.frame["decision_time"].to_numpy()
    t1 = np.maximum(ds.frame["label_available_at"].to_numpy(), t0)
    core = purge(core, [valid], t0, t1, embargo)
    return core, valid


def _xy(ds: Dataset, rows: I64) -> tuple[F64, F64]:
    f = ds.frame
    x = f.select([pl.col(c) for c in ds.features]).to_numpy().astype(np.float64)[rows]
    y = f["y"].to_numpy().astype(np.float64)[rows]
    return x, y


def _directions(x: F64, shap: F64) -> F64:
    out = np.zeros(shap.shape[1])
    for j in range(shap.shape[1]):
        out[j] = metrics.ic(x[:, j], shap[:, j]) if x.shape[0] > 2 else 0.0
    return np.nan_to_num(out)


def _score(model: Model, task: str, x: F64) -> F64:
    p = model.predict_proba(x) if task == "classification" else None
    return p if p is not None else model.predict(x)


def _ml_metrics(task: str, pred: F64, y: F64, groups: NDArray[Any], prefix: str, buckets: int) -> dict[str, float]:
    if task == "classification":
        return {
            f"{prefix}auc": metrics.auc(pred, y),
            f"{prefix}logloss": metrics.logloss(pred, y),
            f"{prefix}brier": metrics.brier(pred, y),
            f"{prefix}ece": metrics.calibration_error(pred, y),
        }
    out = {
        f"{prefix}ic": metrics.ic(pred, y),
        f"{prefix}rank_ic": metrics.rank_ic(pred, y, groups),
        f"{prefix}mse": metrics.mse(pred, y),
    }
    if task == "ranking":
        out[f"{prefix}ndcg"] = metrics.ndcg(pred, rank_relevance(y, buckets), groups)
    return out


def skill(task: str, m: dict[str, float], prefix: str = "val_") -> float:
    v = m.get(f"{prefix}auc", math.nan) - 0.5 if task == "classification" else m.get(f"{prefix}ic", math.nan)
    return float(v)


def _permutation(model: Model, task: str, x: F64, y: F64, groups: NDArray[Any], seed: int) -> F64:
    n = min(x.shape[0], 3000)
    if n < 20:
        return np.zeros(x.shape[1])
    rng = np.random.default_rng(seed)
    sel = np.sort(rng.choice(x.shape[0], n, replace=False))
    xs, ys = x[sel], y[sel]
    base = skill(task, _ml_metrics(task, _score(model, task, xs), ys, groups[sel], "val_", 5))
    out = np.zeros(x.shape[1])
    for j in range(x.shape[1]):
        xp = xs.copy()
        xp[:, j] = xp[rng.permutation(n), j]
        v = skill(task, _ml_metrics(task, _score(model, task, xp), ys, groups[sel], "val_", 5))
        out[j] = (base - v) if math.isfinite(base) and math.isfinite(v) else 0.0
    return out


def _fit(
    cfg: tp.TrainingConfig, ds: Dataset, rows: I64, task: str, hp: dict[str, Any], embargo: int
) -> tuple[Pipeline, Model, I64, I64]:
    fam = info(cfg.family)
    lgbm_like = fam.cls.__name__.startswith("LGBM") and not (
        fam.task == "rule_filter" and hp.get("filter") == "logistic"
    )
    core, valid = _valid_tail(rows, ds, embargo) if lgbm_like else (rows, np.array([], dtype=np.int64))
    x, y = _xy(ds, core)
    pipe = Pipeline.fit(ds.features, x)
    z = pipe.transform(x)
    f = ds.frame
    w = sample_weights(
        f["instrument_id"].to_numpy()[core],
        f["bar_idx"].to_numpy()[core],
        f["end_bar"].to_numpy()[core],
        cfg.sample_weighting.method,
        returns=np.nan_to_num(f["ret"].to_numpy()[core]),
        decay=cfg.sample_weighting.decay or 1.0,
    )
    groups = f["ts_open"].to_numpy()
    eval_set = None
    if valid.size:
        xv, yv = _xy(ds, valid)
        eval_set = EvalSet(pipe.transform(xv), yv, groups[valid])
    model = make(cfg.family, hp, seed=int(cfg.seed), threads=int(cfg.resources.cpu_threads), task=task)
    model.fit(z, y, sample_weight=w, eval_set=eval_set, groups=groups[core])
    return pipe, model, core, valid


def _threshold(cfg: tp.TrainingConfig, task: str, train_pred: F64, base_rate: float = 0.5) -> float:
    if info(cfg.family).task == "rule_filter":
        # ML-016: an explicit threshold, else the training rows' base rate (take signals the filter
        # rates above the rule's own hit rate).
        return float(cfg.primary.threshold) if cfg.primary.threshold > 0 else base_rate
    if train_pred.size == 0:
        return math.inf
    return float(np.quantile(train_pred, 1.0 - float(cfg.evaluation.top_quantile)))


@dataclass
class _Eval:
    trades: pl.DataFrame
    returns: F64
    days: I64
    metrics: dict[str, float]


def evaluate(
    cfg: tp.TrainingConfig,
    ds: Dataset,
    vd: VectorData,
    rows: I64,
    pred: F64,
    threshold: float,
    low_threshold: float,
    cost: CostModelConfig,
) -> _Eval:
    """After-cost evaluation of decisions on ``rows`` (ML-012)."""
    f = ds.frame
    inst = f["instrument_id"].to_numpy()[rows]
    ts = f["ts_open"].to_numpy()[rows]
    fam = info(cfg.family)
    if fam.filter_of == "orb":
        take = {(str(i), int(t)) for i, t, p in zip(inst, ts, pred, strict=True) if p >= threshold}
        params = to_dict(cfg.primary.params) if cfg.primary.HasField("params") else {}
        res = filtered_orb_result(vd, params, take, cost, ALLOCATION)
        rets = res.daily_pnl / ALLOCATION
        sd = float(np.std(rets, ddof=1)) if rets.size > 1 else 0.0
        m = {
            "trades": float(res.trades.height),
            "net_return": float(rets.sum()),
            "sharpe": float(np.mean(rets) / sd) if sd > 0 else math.nan,
            "hit_rate": float(res.metrics.get("hit_rate", math.nan)),
        }
        return _Eval(res.trades, rets, session_days(vd), m)
    side = np.where(pred >= threshold, 1, 0)
    if not cfg.evaluation.long_only and fam.task != "rule_filter":
        side = np.where(pred <= low_threshold, -1, side)
    sel = side != 0
    ecfg = SignalEvalConfig(horizon=int(cfg.label.horizon), cap_at_session_close=cfg.timeframe != DAILY)
    r = evaluate_decisions(vd, inst[sel], ts[sel], side[sel], ecfg, cost)
    return _Eval(r.trades, r.session_returns, r.session_days, r.metrics)


def _session_metrics(rets: F64) -> dict[str, float]:
    sd = float(np.std(rets, ddof=1)) if rets.size > 1 else 0.0
    eq = np.cumsum(rets)
    dd = float(np.max(np.maximum.accumulate(eq) - eq)) if eq.size else 0.0
    return {
        "net_return": float(rets.sum()),
        "sharpe": float(np.mean(rets) / sd) if sd > 0 else math.nan,
        "sharpe_annualized": float(np.mean(rets) / sd * math.sqrt(252)) if sd > 0 else math.nan,
        "max_drawdown": dd,
        "sessions": float(rets.size),
    }


def _repro(cfg: tp.TrainingConfig, started: float) -> tp.Reproducibility:
    libs = {}
    for name in ("lightgbm", "numpy", "polars", "tbcore"):
        try:
            libs[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            libs[name] = "unknown"
    return tp.Reproducibility(
        config_hash=config_hash(cfg),
        library_versions=libs,
        hardware=f"{platform.machine()} {platform.system()} {platform.processor()}".strip(),
        seed=int(cfg.seed),
        runtime_seconds=time.time() - started,
        threads=int(cfg.resources.cpu_threads),
    )


def train(
    cfg: tp.TrainingConfig,
    ds: Dataset,
    progress: Progress | None = None,
    should_stop: Callable[[], bool] | None = None,
    splits: list[Split] | None = None,
    size_curve: bool = True,
) -> TrainingResult:
    """Train and evaluate one resolved config on ``ds``."""
    started = time.time()
    emit = progress or (lambda _e: None)
    task = model_task(cfg.family)
    hp = hyperparams(cfg)
    splits = splits if splits is not None else make_splits(cfg, ds)
    if not splits:
        raise ValueError("data_starved: no folds (too few eras for the splitter)")
    embargo = int(cfg.splitter.embargo_bars) * bar_ns(cfg.timeframe)
    f = ds.frame
    groups = f["ts_open"].to_numpy()
    n = f.height
    oof_sum, oof_cnt = np.zeros(n), np.zeros(n)
    oof_fold = np.full(n, -1, dtype=np.int64)
    vd = ds.vector_data()
    cost = CostModelConfig().stressed(float(cfg.evaluation.cost_multiplier))
    buckets = int(cfg.label.rank_buckets or 5)
    folds: list[FoldResult] = []
    emit({"phase": "fold", "fold": 0, "n_folds": len(splits), "log": f"{len(splits)} folds, {n} rows"})
    for k, s in enumerate(splits):
        if should_stop and should_stop():
            raise Cancelled("canceled between folds")
        pipe, model, core, valid = _fit(cfg, ds, s.train, task, hp, embargo)
        xt, yt = _xy(ds, s.test)
        zt = pipe.transform(xt)
        pt = _score(model, task, zt)
        xc, yc = _xy(ds, core)
        zc = pipe.transform(xc)
        pc = _score(model, task, zc)
        thr = _threshold(cfg, task, pc, float(np.mean(yc)) if yc.size else 0.5)
        low = float(np.quantile(pc, float(cfg.evaluation.top_quantile))) if pc.size else -math.inf
        m = {
            **_ml_metrics(task, pc, yc, groups[core], "train_", buckets),
            **_ml_metrics(task, pt, yt, groups[s.test], "val_", buckets),
        }
        ns = NoSkill(task).fit(zc, yc)
        m.update(
            {
                k2.replace("val_", "noskill_"): v
                for k2, v in _ml_metrics(task, ns.predict(zt), yt, groups[s.test], "val_", buckets).items()
            }
        )
        ev = evaluate(cfg, ds, vd, s.test, pt, thr, low, cost)
        fold_days = np.unique(f["era"].to_numpy()[s.test])
        in_fold = np.isin(ev.days, fold_days)
        fold_rets = ev.returns[in_fold] if ev.returns.size else ev.returns
        bt = {**_session_metrics(fold_rets), "trades": ev.metrics.get("trades", 0.0)}
        ex = model.explain(zt[: min(zt.shape[0], 1000)])
        shap = ex.values if ex is not None else np.zeros((1, len(ds.features)))
        gain = model.gain_importance()
        tot = float(np.sum(np.abs(gain)))
        folds.append(
            FoldResult(
                fold=k,
                train=core,
                test=s.test,
                valid=valid,
                pipeline=pipe,
                model=model,
                pred_test=pt,
                pred_train=pc,
                threshold=thr,
                metrics=m,
                backtest=bt,
                curve=list(model.curve),
                curve_metric=model.curve_metric,
                best_iteration=int(model.best_iteration),
                gain=np.abs(gain) / tot if tot > 0 else np.zeros_like(gain),
                permutation=_permutation(model, task, zt, yt, groups[s.test], int(cfg.seed) + k),
                shap_abs=np.mean(np.abs(shap), axis=0),
                shap_dir=_directions(zt[: shap.shape[0]], shap),
                interactions=model.interactions(ds.features),
            )
        )
        oof_sum[s.test] += pt
        oof_cnt[s.test] += 1
        oof_fold[s.test] = k
        emit(
            {
                "phase": "fold",
                "fold": k + 1,
                "n_folds": len(splits),
                "step": int(model.best_iteration),
                "metrics": {kk: v for kk, v in m.items() if math.isfinite(v)},
                "log": f"fold {k + 1}/{len(splits)}: skill {skill(task, m):.4f}",
            }
        )
    oof = np.where(oof_cnt > 0, oof_sum / np.maximum(oof_cnt, 1), np.nan)
    tested = np.flatnonzero(oof_cnt > 0)
    # Stitched evaluation with each fold's own threshold.
    thr_row = np.full(n, math.inf)
    low_row = np.full(n, -math.inf)
    for fr in folds:
        thr_row[fr.test] = fr.threshold
        low_row[fr.test] = (
            float(np.quantile(fr.pred_train, float(cfg.evaluation.top_quantile))) if fr.pred_train.size else -math.inf
        )
    adj = oof[tested] - thr_row[tested]  # >= 0 means take
    low_adj = oof[tested] - low_row[tested]
    emit({"phase": "evaluate", "log": "evaluating out-of-fold decisions after costs"})
    pred_for_eval = np.where(adj >= 0, 1.0, np.where(low_adj <= 0, -1.0, 0.0))
    ev = evaluate(cfg, ds, vd, tested, pred_for_eval, 1.0, -1.0, cost)
    ev2 = evaluate(cfg, ds, vd, tested, pred_for_eval, 1.0, -1.0, cost.stressed(2.0))
    test_days = np.unique(f["era"].to_numpy()[tested])
    mask = (ev.days >= test_days.min()) & (ev.days <= test_days.max()) if test_days.size else np.zeros(0, bool)
    rets, rets2 = ev.returns[mask], ev2.returns[mask]
    days = ev.days[mask]
    all_m = _ml_metrics(task, oof[tested], f["y"].to_numpy()[tested], groups[tested], "oos_", buckets)
    oos = {**all_m, **{f"bt_{k}": v for k, v in _session_metrics(rets).items()}}
    oos["bt_trades"] = ev.metrics.get("trades", 0.0)
    oos["bt_net_return_2x"] = float(rets2.sum())
    oos["bt_cost_drag"] = ev.metrics.get("cost_drag", math.nan)
    oos["bt_turnover"] = ev.metrics.get("turnover", math.nan)
    primary: dict[str, float] = {}
    if info(cfg.family).task == "rule_filter":
        base = evaluate(cfg, ds, vd, tested, np.ones(tested.size), 0.0, -1.0, cost)
        primary = {f"primary_{k}": v for k, v in _session_metrics(base.returns[mask]).items()}
        primary["primary_trades"] = base.metrics.get("trades", 0.0)
        oos.update(primary)
    emit({"phase": "final", "log": "refitting the final model on all rows"})
    allrows = np.arange(n, dtype=np.int64)
    pipe, model, core, _ = _fit(cfg, ds, allrows, task, hp, embargo)
    xa, ya = _xy(ds, core)
    thr_final = _threshold(cfg, task, _score(model, task, pipe.transform(xa)), float(np.mean(ya)) if ya.size else 0.5)
    bundle = ModelBundle(
        family=cfg.family,
        kind=model.family,
        hyperparams=hp,
        seed=int(cfg.seed),
        threads=int(cfg.resources.cpu_threads),
        pipeline=pipe,
        model=model,
        feature_set_id=ds.feature_set.id,
        feature_set_hash=ds.feature_set.hash,
        features=list(ds.features),
        timeframe=cfg.timeframe,
        benchmark=ds.benchmark,
        threshold=thr_final,
        horizon=int(cfg.label.horizon),
        label_kind=cfg.label.kind,
        primary=to_dict(cfg.primary),
    )
    sc: list[tuple[int, float, float]] = []
    if size_curve and folds:
        last = folds[-1]
        half = last.train[last.train.size // 2 :]
        if half.size >= 50:
            p2, m2, c2, _ = _fit(cfg, ds, half, task, hp, embargo)
            xh, yh = _xy(ds, c2)
            xt, yt = _xy(ds, last.test)
            sc.append(
                (
                    int(c2.size),
                    skill(task, _ml_metrics(task, _score(m2, task, p2.transform(xh)), yh, groups[c2], "val_", buckets)),
                    skill(
                        task,
                        _ml_metrics(task, _score(m2, task, p2.transform(xt)), yt, groups[last.test], "val_", buckets),
                    ),
                )
            )
        sc.append(
            (
                int(last.train.size),
                skill(task, last.metrics, "train_"),
                skill(task, last.metrics, "val_"),
            )
        )
    emit({"phase": "done", "log": "training finished"})
    return TrainingResult(
        config=cfg,
        dataset=ds,
        task=task,
        folds=folds,
        oof=oof,
        oof_fold=oof_fold,
        session_days=days,
        session_returns=rets,
        session_returns_2x=rets2,
        trades=ev.trades,
        oos_metrics=oos,
        final=bundle,
        size_curve=sc,
        repro=_repro(cfg, started),
        primary_metrics=primary,
    )
