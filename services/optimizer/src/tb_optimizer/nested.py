"""Nested evaluation: inner CPCV selects, outer walk-forward estimates (EX-001..EX-006; D31 rule 6, D9).

For each outer walk-forward fold an Optuna sub-study searches on that fold's training window only
(inner CPCV with purge and embargo, labels ending before the fold's test window, bars cut at the test
start). Its best configuration is refit on the window and tested on the fold; the stitched fold
results are the study's honest estimate. A final sub-study (``outer_fold = -1``) on the whole study
period picks the deployable candidate. Every trial of every sub-study, including pruned, failed, and
abandoned ones, goes to the trial ledger through ``on_trial`` (D31 rule 1).
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import optuna
import polars as pl
from google.protobuf import json_format

from tb.ml.v1 import training_pb2 as tp
from tb.optimizer.v1 import optimizer_pb2 as op
from tb_optimizer.stats import Series
from tbcore.ml import config as mlconfig
from tbcore.ml import dataset as mldataset
from tbcore.ml import models, schema
from tbcore.ml.config import bar_ns
from tbcore.ml.train import TrainingResult, train
from tbcore.validation.splitters import CombinatorialPurgedCV, Split
from tbcore.validation.stats import sharpe

optuna.logging.set_verbosity(optuna.logging.WARNING)


class Abandon(BaseException):
    """The study was stopped while this trial ran (recorded as ABANDONED, EX-004); a BaseException so
    Optuna's ``catch`` does not swallow it."""


DEFAULT_SPACES: dict[str, list[op.ParamSpace]] = {
    "lgbm": [
        op.ParamSpace(name="num_leaves", kind="int", low=4, high=64),
        op.ParamSpace(name="learning_rate", kind="log_float", low=0.01, high=0.2),
        op.ParamSpace(name="min_data_in_leaf", kind="int", low=20, high=500),
        op.ParamSpace(name="lambda_l2", kind="log_float", low=0.001, high=10.0),
        op.ParamSpace(name="feature_fraction", kind="float", low=0.5, high=1.0),
    ],
    "ridge": [op.ParamSpace(name="alpha", kind="log_float", low=0.001, high=1000.0)],
    "logistic": [op.ParamSpace(name="alpha", kind="log_float", low=0.001, high=1000.0)],
}


def default_space(family: str) -> list[op.ParamSpace]:
    if family.startswith("lgbm") or models.info(family).task == "rule_filter":
        return DEFAULT_SPACES["lgbm"]
    return DEFAULT_SPACES.get(family, [])


@dataclass
class Control:
    should_stop: Callable[[], bool] = lambda: False
    wait_if_paused: Callable[[], None] = lambda: None
    on_trial: Callable[[op.Trial, Series | None], None] = lambda t, s: None
    on_progress: Callable[[str, int], None] = lambda msg, fold: None
    budget_left: Callable[[], bool] = lambda: True


@dataclass
class OuterResult:
    fold: int
    test: tp.Period
    best_trial_id: str
    params: dict[str, Any]
    config: tp.TrainingConfig
    series: Series
    series_15x: Series
    series_2x: Series
    trades: int
    metrics: dict[str, float]
    decisions: pl.DataFrame = field(default_factory=pl.DataFrame)  # instrument_id, ts_open, score, take


@dataclass
class NestedResult:
    outer: list[OuterResult] = field(default_factory=list)
    final_config: tp.TrainingConfig | None = None
    final_trial_id: str = ""
    final_params: dict[str, Any] = field(default_factory=dict)
    trials: list[op.Trial] = field(default_factory=list)
    series: dict[str, Series] = field(default_factory=dict)
    stopped: bool = False
    best_inner: dict[int, tuple[str, float]] = field(default_factory=dict)

    def stitched(self, which: str = "1x") -> Series:
        parts = [o.series if which == "1x" else (o.series_15x if which == "1.5x" else o.series_2x) for o in self.outer]
        if not parts:
            return Series(np.zeros(0, dtype=np.int64), np.zeros(0))
        days = np.concatenate([p.days for p in parts])
        rets = np.concatenate([p.rets for p in parts])
        order = np.argsort(days, kind="stable")
        return Series(days[order], rets[order])


class DatasetCache:
    """One dataset per (feature set, label, primary params) over the whole study period (boundary-capped)."""

    def __init__(self, bars: pl.DataFrame, universe: list[str], benchmark: str, period_end: int) -> None:
        self.bars, self.universe, self.benchmark, self.end = bars, universe, benchmark, period_end
        self._cache: dict[str, mldataset.Dataset] = {}

    def get(self, cfg: tp.TrainingConfig) -> mldataset.Dataset:
        key = json.dumps(
            {
                "fs": mlconfig.to_dict(cfg.feature_set),
                "label": mlconfig.to_dict(cfg.label),
                "p": mlconfig.to_dict(cfg.primary),
            },
            sort_keys=True,
        )
        if key not in self._cache:
            self._cache[key] = mldataset.build(
                cfg, self.bars, self.universe, self.benchmark, boundary=self.end, task=models.model_task(cfg.family)
            )
        return self._cache[key]


def subset(ds: mldataset.Dataset, rows: np.ndarray, bar_end: int | None) -> mldataset.Dataset:
    """Rows of ``ds`` (sorted) with bars cut at ``bar_end`` so evaluation exits never cross it."""
    frame = ds.frame[np.sort(rows)]
    bars = ds.bars if bar_end is None else ds.bars.filter(pl.col("ts_open") < bar_end)
    return mldataset.Dataset(frame, ds.features, ds.task, ds.manifest, bars, ds.benchmark, ds.feature_set)


def outer_folds(
    ds: mldataset.Dataset, spec: op.StudySpec, cfg: tp.TrainingConfig
) -> list[tuple[list[int], list[int], int]]:
    """Outer walk-forward folds as (train eras, test eras, test start ns), anchored (EX-001)."""
    eras = np.unique(ds.frame["era"].to_numpy())
    o = spec.evaluation.outer
    k = int(o.n_splits or 3)
    test = int(o.test_eras or max(1, eras.size // (k + 2)))
    train0 = int(o.train_eras or max(2, eras.size - k * test))
    out: list[tuple[list[int], list[int], int]] = []
    start = train0
    while start + test <= eras.size and len(out) < k:
        tr = (
            eras[:start].tolist() if (o.anchored or not o.train_eras) else eras[max(0, start - train0) : start].tolist()
        )
        te = eras[start : start + test].tolist()
        t0 = int(ds.frame.filter(pl.col("era") == te[0])["decision_time"].min() or 0)  # type: ignore[arg-type]
        out.append((tr, te, t0))
        start += test
    return out


def _rows(ds: mldataset.Dataset, eras: list[int], label_before: int | None = None) -> np.ndarray:
    f = ds.frame
    m = np.isin(f["era"].to_numpy(), eras)
    if label_before is not None:
        m &= f["label_available_at"].to_numpy() < label_before
    return np.flatnonzero(m)


def _series(res: TrainingResult, which: str = "1x") -> Series:
    r = (
        res.session_returns
        if which == "1x"
        else (res.session_returns_15x if which == "1.5x" else res.session_returns_2x)
    )
    return Series(np.asarray(res.session_days, dtype=np.int64), np.asarray(r, dtype=np.float64))


def _merge(parts: list[Series]) -> Series:
    if not parts:
        return Series(np.zeros(0, dtype=np.int64), np.zeros(0))
    days = np.concatenate([p.days for p in parts])
    rets = np.concatenate([p.rets for p in parts])
    uniq, inv = np.unique(days, return_inverse=True)
    sums = np.zeros(uniq.size)
    cnt = np.zeros(uniq.size)
    np.add.at(sums, inv, rets)
    np.add.at(cnt, inv, 1.0)
    return Series(uniq, sums / cnt)


def _metric(name: str, res: list[TrainingResult], merged: Series) -> float:
    if name in ("", "sharpe"):
        v = sharpe(merged.rets) if merged.rets.size >= 3 else math.nan
    elif name == "net_return":
        v = float(merged.rets.sum())
    else:
        vals = [r.oos_metrics.get(f"oos_{name}", math.nan) for r in res]
        if name == "ic" and all(not math.isfinite(x) for x in vals):
            vals = [r.oos_metrics.get("oos_auc", math.nan) - 0.5 for r in res]
        vals = [x for x in vals if math.isfinite(x)]
        v = float(np.mean(vals)) if vals else math.nan
    return float(v) if math.isfinite(v) else -1e9


class NestedRunner:
    def __init__(
        self,
        spec: op.StudySpec,
        base: tp.TrainingConfig,
        cache: DatasetCache,
        control: Control,
        study_id: str,
        lineage_id: str,
        cycle_id: str,
        source: int,
        code_commit: str,
        analyst_round_id: str = "",
        storage: str | None = None,
    ) -> None:
        self.spec, self.base, self.cache, self.ctl = spec, base, cache, control
        self.study_id, self.lineage_id, self.cycle_id = study_id, lineage_id, cycle_id
        self.source, self.commit, self.round = source, code_commit, analyst_round_id
        self.storage = storage
        self.spec_hash = hashlib.sha256(json_format.MessageToJson(spec, sort_keys=True).encode()).hexdigest()[:16]
        self.prereg_hash = hashlib.sha256(
            json_format.MessageToJson(spec.preregistration, sort_keys=True).encode()
        ).hexdigest()[:16]
        self.result = NestedResult()
        self._n = 0
        fams = list(spec.families) or [base.family]
        self.families = fams
        self.space = list(spec.search_space) or [p for f in fams for p in default_space(f)]
        self.dims = sorted(
            {
                *(["hyperparams"] if self.space else []),
                *(["features"] if len(spec.feature_sets) > 1 else []),
                *(["labels"] if len(spec.label_specs) > 1 else []),
                *(["rules"] if any(p.name.startswith("primary.") for p in self.space) else []),
                *(["models"] if len(fams) > 1 else []),
            }
        )

    # ---- configuration from a trial ----
    def _config(self, trial: optuna.trial.BaseTrial) -> tuple[tp.TrainingConfig, dict[str, Any]]:
        cfg = tp.TrainingConfig()
        cfg.CopyFrom(self.base)
        params: dict[str, Any] = {}
        fam = trial.suggest_categorical("family", self.families) if len(self.families) > 1 else self.families[0]
        cfg.family = str(fam)
        params["family"] = cfg.family
        if len(self.spec.feature_sets) > 1:
            names = [f"{f.name}@{f.version or 1}" for f in self.spec.feature_sets]
            pick = str(trial.suggest_categorical("feature_set", names))
            cfg.feature_set.name, ver = pick.split("@")
            cfg.feature_set.version = int(ver)
            params["feature_set"] = pick
        elif self.spec.feature_sets:
            cfg.feature_set.CopyFrom(self.spec.feature_sets[0])
        if len(self.spec.label_specs) > 1:
            idx = int(trial.suggest_categorical("label", [str(i) for i in range(len(self.spec.label_specs))]))
            cfg.label.CopyFrom(self.spec.label_specs[idx])
            params["label"] = f"{cfg.label.kind}:h{cfg.label.horizon}"
        elif self.spec.label_specs:
            cfg.label.CopyFrom(self.spec.label_specs[0])
        hp: dict[str, Any] = {}
        prim: dict[str, Any] = mlconfig.to_dict(cfg.primary.params) if cfg.primary.HasField("params") else {}
        for p in self.space:
            if p.families and cfg.family not in p.families:
                continue
            if p.kind == "categorical":
                v: Any = trial.suggest_categorical(p.name, list(p.choices))
            elif p.kind == "int":
                v = trial.suggest_int(p.name, int(p.low), int(p.high), step=int(p.step or 1))
            elif p.kind == "log_float":
                v = trial.suggest_float(p.name, p.low, p.high, log=True)
            else:
                v = trial.suggest_float(p.name, p.low, p.high, step=p.step or None)
            params[p.name] = v
            if p.name.startswith("primary."):
                prim[p.name.split(".", 1)[1]] = v
            else:
                hp[p.name] = v
        fam_schema = models.info(cfg.family).schema
        hp = {k: v for k, v in hp.items() if k in fam_schema.get("properties", {})}
        cfg.hyperparams.Clear()
        cfg.hyperparams.update({**mlconfig.hyperparams(self.base), **hp})
        if prim:
            cfg.primary.params.Clear()
            cfg.primary.params.update(prim)
        cfg.label.horizon = cfg.label.horizon or 0
        rc = mlconfig.resolve(cfg, schema.defaults(fam_schema))
        return rc, params

    def _inner_splits(self, ds: mldataset.Dataset, cfg: tp.TrainingConfig) -> list[Split]:
        f = ds.frame
        t0 = f["decision_time"].to_numpy()
        t1 = np.maximum(f["label_available_at"].to_numpy(), t0)
        inner = self.spec.evaluation.inner
        n_groups = int(inner.n_groups or 4)
        k = int(inner.n_test_groups or 1)
        embargo = max(int(inner.embargo_bars), int(cfg.label.horizon)) * bar_ns(cfg.timeframe)
        cv = CombinatorialPurgedCV(n_groups, k, embargo=embargo, label_end_times=t1)
        return [s for s in cv.split(t0, f["era"].to_numpy()) if s.train.size and s.test.size]

    def _record(
        self,
        outer_fold: int,
        trial: optuna.trial.BaseTrial | None,
        params: dict[str, Any],
        status: int,
        inner: float,
        series: Series | None,
        compute: float,
        snapshot: str,
        cfg: tp.TrainingConfig | None,
    ) -> op.Trial:
        self._n += 1
        tid = f"{self.study_id}-f{outer_fold if outer_fold >= 0 else 'F'}-t{self._n:04d}"
        r = series.rets if series is not None else np.zeros(0)
        sd = float(np.std(r, ddof=1)) if r.size > 1 else 0.0
        t = op.Trial(
            trial_id=tid,
            lineage_id=self.lineage_id,
            cycle_id=self.cycle_id,
            study_id=self.study_id,
            number=self._n,
            outer_fold=outer_fold,
            spec_hash=self.spec_hash,
            preregistration_hash=self.prereg_hash,
            data_snapshot_id=snapshot,
            code_commit=self.commit,
            seed=int(self.spec.seed or 7),
            source=self.source,  # type: ignore[arg-type]
            status=status,  # type: ignore[arg-type]
            inner_metric=inner if math.isfinite(inner) and inner > -1e8 else 0.0,
            sharpe=float(np.mean(r) / sd) if sd > 0 else 0.0,
            n_obs=int(r.size),
            compute_seconds=compute,
            created_at=time.time_ns(),
            dimensions=self.dims,
            analyst_round_id=self.round,
            config_hash=mlconfig.config_hash(cfg) if cfg is not None else "",
        )
        t.params.update({k: v for k, v in params.items()})
        self.result.trials.append(t)
        if series is not None:
            self.result.series[tid] = series
        self.ctl.on_trial(t, series)
        if trial is not None:
            trial.set_user_attr("trial_id", tid)
        return t

    def _objective(self, outer_fold: int, train_eras: list[int], bar_end: int | None, label_before: int | None):  # type: ignore[no-untyped-def]
        def objective(trial: optuna.trial.Trial) -> float:
            self.ctl.wait_if_paused()
            if self.ctl.should_stop():
                raise Abandon()
            started = time.time()
            cfg, params = self._config(trial)
            full = self.cache.get(cfg)
            ds = subset(full, _rows(full, train_eras, label_before), bar_end)
            results: list[TrainingResult] = []
            parts: list[Series] = []
            status = op.TRIAL_STATUS_COMPLETE
            try:
                splits = self._inner_splits(ds, cfg)
                for i, s in enumerate(splits):
                    if self.ctl.should_stop():
                        raise Abandon()
                    r = train(cfg, ds, splits=[s], size_curve=False, refit_final=False, explain=False)
                    results.append(r)
                    parts.append(_series(r))
                    running = _metric(self.spec.objective.metric, results, _merge(parts))
                    trial.report(running, i)
                    if trial.should_prune():
                        status = op.TRIAL_STATUS_PRUNED
                        raise optuna.TrialPruned()
            except optuna.TrialPruned:
                merged = _merge(parts)
                self._record(
                    outer_fold,
                    trial,
                    params,
                    status,
                    _metric(self.spec.objective.metric, results, merged),
                    merged,
                    time.time() - started,
                    full.snapshot_id,
                    cfg,
                )
                raise
            except Abandon:
                merged = _merge(parts)
                self._record(
                    outer_fold,
                    trial,
                    params,
                    op.TRIAL_STATUS_ABANDONED,
                    -1e9,
                    merged if parts else None,
                    time.time() - started,
                    full.snapshot_id,
                    cfg,
                )
                raise
            except Exception:
                self._record(
                    outer_fold,
                    trial,
                    params,
                    op.TRIAL_STATUS_FAILED,
                    -1e9,
                    None,
                    time.time() - started,
                    full.snapshot_id,
                    cfg,
                )
                raise
            merged = _merge(parts)
            val = _metric(self.spec.objective.metric, results, merged)
            self._record(outer_fold, trial, params, status, val, merged, time.time() - started, full.snapshot_id, cfg)
            return val

        return objective

    def _budget_callback(self, study: optuna.Study, trial: optuna.trial.FrozenTrial) -> None:
        if not self.ctl.budget_left():  # EX-060: stop at the study, lineage, cycle, or analyst budget
            study.stop()

    def _sub_study(
        self, outer_fold: int, train_eras: list[int], bar_end: int | None, label_before: int | None, n_trials: int
    ) -> tuple[optuna.Study, tp.TrainingConfig | None, dict[str, Any], str]:
        seed = int(self.spec.seed or 7) + (outer_fold + 1) * 101
        sampler: optuna.samplers.BaseSampler = (
            optuna.samplers.RandomSampler(seed=seed)
            if self.spec.sampler == "random"
            else optuna.samplers.TPESampler(seed=seed)
        )
        pruner: optuna.pruners.BasePruner = optuna.pruners.NopPruner()
        if self.spec.pruner == "median":
            pruner = optuna.pruners.MedianPruner(n_startup_trials=3, n_warmup_steps=1)
        elif self.spec.pruner == "asha":
            pruner = optuna.pruners.SuccessiveHalvingPruner()
        study = optuna.create_study(
            study_name=f"{self.study_id}:outer{outer_fold}",
            direction="minimize" if self.spec.objective.direction == "minimize" else "maximize",
            sampler=sampler,
            pruner=pruner,
            storage=self.storage,
            load_if_exists=True,
        )
        self.ctl.on_progress(f"outer fold {outer_fold}: searching {n_trials} trials", outer_fold)
        try:
            if self.ctl.budget_left():
                study.optimize(
                    self._objective(outer_fold, train_eras, bar_end, label_before),
                    n_trials=n_trials,
                    catch=(Exception,),
                    callbacks=[self._budget_callback],
                )
        except Abandon:
            self.result.stopped = True
        done = [t for t in study.trials if t.state == optuna.trial.TrialState.COMPLETE]
        if not done:
            return study, None, {}, ""
        best = (
            max(done, key=lambda t: float(t.value or -1e9))
            if self.spec.objective.direction != "minimize"
            else min(done, key=lambda t: float(t.value or 1e9))
        )
        cfg, params = self._config(optuna.trial.FixedTrial(best.params))
        tid = str(best.user_attrs.get("trial_id", ""))
        self.result.best_inner[outer_fold] = (tid, float(best.value or 0.0))
        return study, cfg, params, tid

    def run(self) -> NestedResult:
        ref_cfg, _ = self._config(optuna.trial.FixedTrial(self._first_params()))
        ref = self.cache.get(ref_cfg)
        folds = outer_folds(ref, self.spec, ref_cfg)
        total = int(self.spec.budget.max_trials or 12)
        per = max(1, total // (len(folds) + 1))
        embargo = max(int(self.spec.evaluation.outer.embargo_bars), int(ref_cfg.label.horizon)) * bar_ns(
            ref_cfg.timeframe
        )
        for k, (tr_eras, te_eras, t0) in enumerate(folds):
            if self.result.stopped or self.ctl.should_stop():
                self.result.stopped = True
                break
            _, cfg, params, tid = self._sub_study(k, tr_eras, t0, t0 - embargo, per)
            if cfg is None:
                continue
            full = self.cache.get(cfg)
            tr_rows = _rows(full, tr_eras, t0 - embargo)
            te_rows = _rows(full, te_eras)
            if not tr_rows.size or not te_rows.size:
                continue
            te_end_era = te_eras[-1]
            nxt = full.frame.filter(pl.col("era") > te_end_era)["decision_time"]
            bar_end = int(nxt.min()) if nxt.len() else None  # type: ignore[arg-type]
            rows = np.concatenate([tr_rows, te_rows])
            ds = subset(full, rows, bar_end)
            loc = {int(r): i for i, r in enumerate(np.sort(rows))}
            split = Split(0, np.array([loc[int(r)] for r in tr_rows]), np.array([loc[int(r)] for r in te_rows]))
            res = train(cfg, ds, splits=[split], size_curve=False, refit_final=False, explain=False)
            tdec = full.frame["decision_time"].to_numpy()[te_rows]
            thr = res.folds[0].threshold if res.folds else math.inf
            decisions = res.oof_frame().select(
                "instrument_id", "ts_open", pl.col("pred").alias("score"), (pl.col("pred") >= thr).alias("take")
            )
            self.result.outer.append(
                OuterResult(
                    fold=k,
                    test=tp.Period(start=int(tdec.min()), end=int(tdec.max()) + 1),
                    best_trial_id=tid,
                    params=params,
                    config=cfg,
                    series=_series(res),
                    series_15x=_series(res, "1.5x"),
                    series_2x=_series(res, "2x"),
                    trades=int(res.oos_metrics.get("bt_trades", 0)),
                    metrics={k2: float(v) for k2, v in res.oos_metrics.items() if math.isfinite(float(v))},
                    decisions=decisions,
                )
            )
            self.ctl.on_progress(f"outer fold {k}: tested", k)
        if not self.result.stopped and not self.ctl.should_stop():
            all_eras = np.unique(ref.frame["era"].to_numpy()).tolist()
            _, cfg, params, tid = self._sub_study(-1, all_eras, None, None, max(1, total - per * len(folds)))
            self.result.final_config, self.result.final_params, self.result.final_trial_id = cfg, params, tid
        return self.result

    def _first_params(self) -> dict[str, Any]:
        p: dict[str, Any] = {}
        if len(self.families) > 1:
            p["family"] = self.families[0]
        if len(self.spec.feature_sets) > 1:
            f = self.spec.feature_sets[0]
            p["feature_set"] = f"{f.name}@{f.version or 1}"
        if len(self.spec.label_specs) > 1:
            p["label"] = "0"
        for s in self.space:
            if s.families and self.families[0] not in s.families:
                continue
            if s.kind == "categorical":
                p[s.name] = s.choices[0]
            elif s.kind == "int":
                p[s.name] = int(s.low)
            else:
                p[s.name] = float(s.low)
        return p
