"""ml worker core (section 10, ADR-0037): job queue, submit-time validation and the cost guard, training
runs, diagnostics reads for the UI and the analyst, ablations, model registration, and batch prediction.

The gRPC adapter (``grpc_api``) resolves the actor from the service token and calls these methods; the
core enforces ML-011 (validation), ML-024 (analyst tagging, budget, trial ledger), ML-025 (ablations),
ML-030 (priority queue), ML-031 (cost guard), ML-032 (devices), and ML-022 (``ml.run.completed``).
"""

from __future__ import annotations

import heapq
import itertools
import json
import logging
import math
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
from google.protobuf import json_format

from tb.ml.v1 import ml_pb2 as mlp
from tb.ml.v1 import training_pb2 as tp
from tb.optimizer.v1 import optimizer_pb2 as op
from tb.signals.v1 import signals_pb2 as sp
from tb_ml.devices import BurstHook, DeviceError, select
from tb_ml.link import NoLink, ResearchLink
from tbcore.audit import AuditSink, MemoryAudit
from tbcore.buspub import MemoryPublisher, Publisher
from tbcore.docstore import DocStore
from tbcore.features.core import compute
from tbcore.ml import config as mlconfig
from tbcore.ml import dataset as mldataset
from tbcore.ml import featuresets, models, runs, schema, synthetic
from tbcore.ml.bundle import ModelBundle, feature_vector_id
from tbcore.ml.caller import PermissionDenied, name
from tbcore.ml.tracking import NullTracker, Tracker
from tbcore.ml.train import Cancelled, TrainingResult, train
from tbcore.strategy.api import new_ulid

log = logging.getLogger("tb_ml")

PRIORITY = {"retrain": 0, tp.ACTOR_SCHEDULE: 0, tp.ACTOR_OWNER: 1, tp.ACTOR_SYSTEM: 1, tp.ACTOR_ANALYST: 2}
KIND_JOB = "job"
KIND_MODEL = "model_version"
# Per-row-per-fit CPU cost (seconds) by family, calibrated on the 4-vCPU CI VM (ML-031 estimate).
COST_PER_ROW = {"no_skill": 2e-7, "ridge": 1e-6, "logistic": 3e-6}
LGBM_COST_PER_ROW_PER_100_TREES = 1.2e-5
NOTE_LIMIT = 2000


class NotFound(KeyError):
    pass


class InvalidConfig(ValueError):
    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


@dataclass
class _Job:
    job: tp.TrainingJob
    progress: list[tp.JobProgress] = field(default_factory=list)
    cond: threading.Condition = field(default_factory=threading.Condition)
    cancel: bool = False
    dataset: mldataset.Dataset | None = None
    drop_groups: list[str] = field(default_factory=list)


def _now() -> int:
    return time.time_ns()


class MLCore:
    def __init__(
        self,
        store: DocStore,
        artifacts_dir: str | Path,
        link: ResearchLink | None = None,
        audit: AuditSink | None = None,
        bus: Publisher | None = None,
        tracker: Tracker | None = None,
        lake_dir: str = "",
        budget_cpu_seconds: float = 3600.0,
        workers: int = 1,
        burst: BurstHook | None = None,
        start: bool = True,
    ) -> None:
        self.store = store
        self.artifacts = Path(artifacts_dir)
        self.link: ResearchLink = link or NoLink()
        self.audit: AuditSink = audit or MemoryAudit()
        self.bus: Publisher = bus or MemoryPublisher()
        self.tracker = tracker or NullTracker()
        self.lake_dir = lake_dir
        self.budget = budget_cpu_seconds
        self.burst = burst
        self._jobs: dict[str, _Job] = {}
        self._queue: list[tuple[int, int, str]] = []
        self._seq = itertools.count()
        self._qcond = threading.Condition()
        self._stop = False
        self._threads = [
            threading.Thread(target=self._worker, name=f"ml-worker-{i}", daemon=True) for i in range(workers)
        ]
        if start:
            for t in self._threads:
                t.start()

    # ---- catalog ----
    def families(self) -> list[tp.ModelFamily]:
        out = []
        for f in models.FAMILIES.values():
            out.append(
                tp.ModelFamily(
                    family=f.family,
                    tier=f.tier,
                    task=f.task,
                    description=f.description,
                    hyperparam_schema_json=json.dumps(f.schema, sort_keys=True),
                    cpu_threads_hint=f.cpu_threads_hint,
                    memory_mb_hint=f.memory_mb_hint,
                    supports_gpu=f.supports_gpu,
                    devices=list(f.devices),
                    horizon=f.horizon,
                    experimental=False,
                )
            )
        return out

    def feature_sets(self) -> list[tp.FeatureSetInfo]:
        out = []
        for d in featuresets.FEATURE_SETS.values():
            fs = d.resolve("market")
            out.append(
                tp.FeatureSetInfo(
                    name=d.name,
                    version=d.version,
                    hash=fs.hash,
                    features=fs.names,
                    description=f"{d.description} ({', '.join(d.timeframes)})",
                    max_lookback=fs.max_lookback,
                )
            )
        return out

    @staticmethod
    def label_presets() -> list[tp.LabelPreset]:
        return [
            tp.LabelPreset(
                name="intraday_residual_h4",
                timeframe="15m",
                spec=tp.LabelSpec(kind="residual_return", horizon=4, vol_window=20),
                description="Volatility-scaled residual return over 4 decision bars, capped at the close (D24)",
            ),
            tp.LabelPreset(
                name="intraday_residual_h2",
                timeframe="15m",
                spec=tp.LabelSpec(kind="residual_return", horizon=2, vol_window=20),
                description="Volatility-scaled residual return over 2 decision bars",
            ),
            tp.LabelPreset(
                name="swing_rank_h5",
                timeframe="1d",
                spec=tp.LabelSpec(kind="residual_rank", horizon=5, vol_window=20, rank_buckets=5),
                description="Per-date rank of the 5-session volatility-scaled residual return (LambdaRank, D24)",
            ),
            tp.LabelPreset(
                name="swing_residual_h5",
                timeframe="1d",
                spec=tp.LabelSpec(kind="residual_return", horizon=5, vol_window=20),
                description="5-session volatility-scaled residual return",
            ),
            tp.LabelPreset(
                name="meta_triple_barrier",
                timeframe="",
                spec=tp.LabelSpec(kind="meta_label", pt=1.0, sl=1.0),
                description="Meta-label of a primary rule's trades (ORB exits or triple barrier)",
            ),
        ]

    # ---- validation and estimate (ML-011, ML-031, ML-032) ----
    def prepare(self, cfg: tp.TrainingConfig, caller: tp.CallerContext) -> tuple[tp.TrainingConfig, int, list[str]]:
        """Resolve defaults and validate; returns (resolved config, boundary, errors)."""
        errors: list[str] = []
        if cfg.family not in models.FAMILIES:
            return cfg, 0, [f"unknown model family {cfg.family!r}"]
        fam = models.info(cfg.family)
        errors += schema.validate(fam.schema, mlconfig.hyperparams(cfg))
        try:
            rc = mlconfig.resolve(cfg, schema.defaults(fam.schema))
        except ValueError as exc:
            return cfg, 0, [str(exc)]
        if rc.timeframe not in ("15m", "1d"):
            errors.append(f"timeframe {rc.timeframe!r}: 15m (intraday) or 1d (swing)")
        if (fam.horizon == "intraday" and rc.timeframe == "1d") or (fam.horizon == "swing" and rc.timeframe != "1d"):
            errors.append(f"family {fam.family} is for {fam.horizon} bars")
        try:
            fsd = featuresets.get(rc.feature_set.name, rc.feature_set.version)
            if rc.timeframe not in fsd.timeframes:
                errors.append(f"feature set {fsd.name} is for {list(fsd.timeframes)}")
        except ValueError as exc:
            errors.append(str(exc))
        try:
            select(rc.resources.device, fam, self.burst is not None)
        except DeviceError as exc:
            errors.append(str(exc))
        boundary = 0
        if rc.data_source.startswith("synthetic:"):
            try:
                boundary = synthetic.holdout_boundary(synthetic.preset(rc.data_source.split(":", 1)[1]))
            except ValueError as exc:
                errors.append(str(exc))
        if rc.lineage_id:
            lin = self.link.lineage(rc.lineage_id)
            if lin is None:
                errors.append(f"unknown lineage {rc.lineage_id!r}")
            elif lin.holdout_boundary:
                boundary = min(boundary, lin.holdout_boundary) if boundary else lin.holdout_boundary
        if rc.period.end and boundary and rc.period.end > boundary:
            errors.append(f"period ends after the holdout boundary {boundary} (ML-011, EX-010)")
        elif boundary and not rc.period.end:
            rc.period.end = boundary
        if rc.lineage_id and not errors:
            ok, why, _ = self.link.check_data(rc.lineage_id, rc.period, caller)
            if not ok:
                errors.append(f"holdout data access refused: {why}")
        return rc, boundary, errors

    def estimate(self, cfg: tp.TrainingConfig) -> tp.JobEstimate:
        rows = self._rows_estimate(cfg)
        fam = models.info(cfg.family)
        if cfg.splitter.kind == "cpcv":
            folds = math.comb(max(cfg.splitter.n_groups, 2), max(cfg.splitter.n_test_groups, 1))
        elif cfg.splitter.kind == "purged_kfold":
            folds = max(cfg.splitter.n_splits, 2)
        else:
            folds = 4
        fits = folds + 2  # folds + final refit + size-curve fit
        hp = schema.coerce(fam.schema, mlconfig.hyperparams(cfg))
        if fam.cls.__name__.startswith("LGBM") or fam.task == "rule_filter":
            per_row = LGBM_COST_PER_ROW_PER_100_TREES * hp.get("n_estimators", 300) / 100
            if fam.task == "rule_filter":
                rows = max(rows // 30, 1)
        else:
            per_row = COST_PER_ROW.get(fam.family, 1e-6)
        threads = max(int(cfg.resources.cpu_threads or 1), 1)
        cpu = rows * fits * per_row * 1.5 + 2.0
        nfeat = (
            len(featuresets.get(cfg.feature_set.name).keys) if cfg.feature_set.name in featuresets.FEATURE_SETS else 16
        )
        return tp.JobEstimate(
            rows=rows,
            fits=fits,
            cpu_seconds=cpu,
            memory_mb=int(rows * (nfeat + 16) * 8 * 4 / 1e6) + 256,
            over_budget=cpu / threads > self.budget,
            device=select(cfg.resources.device, fam, self.burst is not None),
        )

    def _rows_estimate(self, cfg: tp.TrainingConfig) -> int:
        if cfg.data_source.startswith("synthetic:"):
            s = synthetic.preset(cfg.data_source.split(":", 1)[1])
            per = 1 if s.timeframe == "1d" else 26
            return int(s.n_instruments * s.n_sessions * 0.8 * per)
        n = max(len(cfg.universe.instrument_ids), 1)
        days = max((cfg.period.end - cfg.period.start) / 86_400e9, 1.0) * 252 / 365
        return int(n * days * (1 if cfg.timeframe == "1d" else 26))

    # ---- submit / run ----
    def submit(
        self,
        cfg: tp.TrainingConfig,
        caller: tp.CallerContext,
        confirm_over_budget: bool = False,
        kind: str = "training",
        base_run_id: str = "",
        dataset: mldataset.Dataset | None = None,
        drop_groups: list[str] | None = None,
    ) -> tp.TrainingJob:
        rc, _, errors = self.prepare(cfg, caller)
        if errors:
            raise InvalidConfig(errors)
        est = self.estimate(rc)
        if caller.actor == tp.ACTOR_ANALYST:
            self._check_analyst_budget(rc, est)  # ML-024
            if est.over_budget:
                raise PermissionDenied("analyst jobs over the compute budget are refused (ML-031)")
        elif est.over_budget and not confirm_over_budget:
            raise InvalidConfig(
                [f"estimated {est.cpu_seconds:.0f} CPU s exceeds the budget; confirm to launch (ML-031)"]
            )
        now = _now()
        job_id = new_ulid(now // 1_000_000, f"job|{mlconfig.config_hash(rc)}|{now}")
        job = tp.TrainingJob(
            job_id=job_id,
            run_id="run_" + job_id,
            config=rc,
            status=tp.JOB_STATUS_QUEUED,
            submitted_at=now,
            launched_by=caller,
            priority=PRIORITY.get(kind, PRIORITY.get(caller.actor, 1)),
            estimate=est,
            phase="queued",
            kind=kind,
            base_run_id=base_run_id,
        )
        j = _Job(job=job, dataset=dataset, drop_groups=list(drop_groups or []))
        with self._qcond:
            self._jobs[job_id] = j
            heapq.heappush(self._queue, (job.priority, next(self._seq), job_id))
            self._qcond.notify()
        self.store.put(KIND_JOB, job_id, job, lineage_id=rc.lineage_id)
        self.audit.append(
            name(caller),
            f"ml.{kind}.submit",
            job_id,
            {"family": rc.family, "config_hash": mlconfig.config_hash(rc), "analyst_round_id": caller.analyst_round_id},
            correlation_id=caller.correlation_id,
        )
        return job

    def _check_analyst_budget(self, cfg: tp.TrainingConfig, est: tp.JobEstimate) -> None:
        if not cfg.lineage_id:
            raise PermissionDenied("analyst jobs must belong to a lineage (ML-024)")
        lin = self.link.lineage(cfg.lineage_id)
        if lin is None:
            raise PermissionDenied(f"unknown lineage {cfg.lineage_id}")
        if lin.autonomy_level < 1:
            raise PermissionDenied("analyst launches need autonomy level >= 1 (EX-031)")
        b, u = lin.analyst_budget, lin.analyst_used
        if b.max_trials and u.trials + 1 > b.max_trials:
            raise PermissionDenied("analyst trial budget spent (EX-036)")
        if b.max_cpu_hours and u.cpu_hours + est.cpu_seconds / 3600 > b.max_cpu_hours:
            raise PermissionDenied("analyst CPU budget spent (EX-036)")

    def _worker(self) -> None:
        while True:
            with self._qcond:
                while not self._queue and not self._stop:
                    self._qcond.wait(timeout=1.0)
                if self._stop:
                    return
                _, _, job_id = heapq.heappop(self._queue)
            j = self._jobs[job_id]
            if j.cancel:
                continue
            self._run(j)

    def run_pending(self) -> None:
        """Run every queued job on the calling thread (tests and the CLI)."""
        while True:
            with self._qcond:
                if not self._queue:
                    return
                _, _, job_id = heapq.heappop(self._queue)
            j = self._jobs[job_id]
            if not j.cancel:
                self._run(j)

    def close(self) -> None:
        with self._qcond:
            self._stop = True
            self._qcond.notify_all()

    def _emit(self, j: _Job, **kw: Any) -> None:
        with j.cond:
            p = tp.JobProgress(job_id=j.job.job_id, seq=len(j.progress) + 1, at=_now(), status=j.job.status, **kw)
            j.progress.append(p)
            j.cond.notify_all()

    def _set(self, j: _Job, **fields: Any) -> None:
        for k, v in fields.items():
            setattr(j.job, k, v)
        self.store.put(KIND_JOB, j.job.job_id, j.job, lineage_id=j.job.config.lineage_id)

    def _run(self, j: _Job) -> None:
        cfg = j.job.config
        self._set(j, status=tp.JOB_STATUS_RUNNING, started_at=_now(), phase="dataset")
        self._emit(j, phase="dataset", log=f"building dataset for {cfg.family} on {cfg.data_source}")
        result: TrainingResult | None = None
        try:
            ds = j.dataset or self._dataset(cfg)
            if j.drop_groups:
                ds = ds.with_features(featuresets.drop_groups(ds.features, j.drop_groups))
            mldataset.save(ds, self.artifacts / "datasets")
            self._set(j, phase="fold")

            def progress(e: dict[str, Any]) -> None:
                n = max(int(e.get("n_folds", 0)), 1)
                frac = 0.1 + 0.7 * int(e.get("fold", 0)) / n if e.get("phase") == "fold" else j.job.progress
                if e.get("phase") in ("evaluate", "final"):
                    frac = 0.85
                self._set(j, progress=frac, phase=str(e.get("phase", "")))
                self._emit(
                    j,
                    phase=str(e.get("phase", "")),
                    fold=int(e.get("fold", 0)),
                    n_folds=int(e.get("n_folds", 0)),
                    step=int(e.get("step", 0)),
                    metrics={k: float(v) for k, v in e.get("metrics", {}).items()},
                    log=str(e.get("log", "")),
                    progress=frac,
                )

            result = train(cfg, ds, progress=progress, should_stop=lambda: j.cancel)
            self._set(j, phase="diagnostics", progress=0.9)
            run = runs.persist(
                result,
                j.job.run_id,
                j.job.job_id,
                self.store,
                self.artifacts,
                caller=j.job.launched_by,
                tracker=self.tracker,
                base_run_id=j.job.base_run_id,
            )
            if cfg.lineage_id:
                trial = self._record_trial(j, run, result)
                run.trial_id = trial
                self.store.put(runs.KIND_RUN, run.run_id, run, lineage_id=cfg.lineage_id, parent_id=j.job.base_run_id)
            self._set(j, status=tp.JOB_STATUS_SUCCEEDED, finished_at=_now(), progress=1.0, phase="done")
            self._emit(j, phase="done", log="run stored", progress=1.0)
            self._completed(j, run.diagnostics_report_id, run.trial_id)
        except Cancelled:
            self._set(j, status=tp.JOB_STATUS_CANCELED, finished_at=_now(), phase="canceled")
            self._emit(j, phase="canceled", log="canceled")
            self._completed(j, "", "")
        except Exception as exc:  # job failures are recorded and reported, never crash the worker
            log.exception("job %s failed", j.job.job_id)
            self._set(j, status=tp.JOB_STATUS_FAILED, finished_at=_now(), error=f"{type(exc).__name__}: {exc}"[:500])
            self._emit(j, phase="failed", log=j.job.error)
            self._completed(j, "", "")

    def _dataset(self, cfg: tp.TrainingConfig) -> mldataset.Dataset:
        bars, uni, bench = mldataset.load_bars(cfg, self.lake_dir or None)
        return mldataset.build(
            cfg, bars, uni, bench, boundary=cfg.period.end or None, task=models.model_task(cfg.family)
        )

    def _record_trial(self, j: _Job, run: tp.TrainingRun, res: TrainingResult) -> str:
        """Every lineage run is a trial in the lineage ledger, whoever launched it (ML-024, EX-020)."""
        r = res.session_returns
        sd = float(np.std(r, ddof=1)) if r.size > 1 else 0.0
        trial = op.Trial(
            trial_id="tr_" + j.job.job_id,
            lineage_id=run.config.lineage_id,
            study_id="",
            number=0,
            outer_fold=-1,
            spec_hash=mlconfig.config_hash(run.config),
            data_snapshot_id=run.dataset.snapshot_id,
            code_commit=run.repro.code_commit,
            seed=int(run.config.seed),
            source=j.job.launched_by.actor,
            status=op.TRIAL_STATUS_COMPLETE,
            inner_metric=float(res.oos_metrics.get("bt_sharpe", math.nan))
            if math.isfinite(res.oos_metrics.get("bt_sharpe", math.nan))
            else 0.0,
            sharpe=float(np.mean(r) / sd) if sd > 0 else 0.0,
            n_obs=int(r.size),
            oof_returns_ref=str(Path(run.artifact_uri) / "returns.parquet"),
            compute_seconds=float(run.repro.runtime_seconds),
            created_at=_now(),
            run_ids=[run.run_id],
            dimensions=["ablation" if j.job.kind == "ablation" else "single_run"],
            analyst_round_id=j.job.launched_by.analyst_round_id,
            config_hash=mlconfig.config_hash(run.config),
        )
        trial.params.update(mlconfig.hyperparams(run.config))
        self.link.record_trial(trial, j.job.launched_by)
        return trial.trial_id

    def _completed(self, j: _Job, report_id: str, trial_id: str) -> None:
        msg = tp.MLRunCompleted(
            run_id=j.job.run_id if j.job.status == tp.JOB_STATUS_SUCCEEDED else "",
            job_id=j.job.job_id,
            report_id=report_id,
            lineage_id=j.job.config.lineage_id,
            trial_id=trial_id,
            launched_by=j.job.launched_by.actor,
            analyst_round_id=j.job.launched_by.analyst_round_id,
            status=j.job.status,
            completed_at=_now(),
            correlation_id=j.job.launched_by.correlation_id,
            base_run_id=j.job.base_run_id,
            kind=j.job.kind,
        )
        try:
            self.bus.publish("ml.run.completed", msg, msg_id=f"ml.run.completed.{j.job.job_id}")
        except Exception:  # the run is stored; a bus outage must not lose it (the analyst can poll ListRuns)
            log.exception("publish ml.run.completed failed for %s", j.job.job_id)

    # ---- jobs ----
    def job(self, job_id: str) -> tp.TrainingJob:
        j = self._jobs.get(job_id)
        if j is not None:
            return j.job
        stored = self.store.get(KIND_JOB, job_id, tp.TrainingJob)
        if stored is None:
            raise NotFound(job_id)
        return stored

    def jobs(self, lineage_id: str = "", limit: int = 50) -> list[tp.TrainingJob]:
        rows = self.store.list(KIND_JOB, tp.TrainingJob, lineage_id=lineage_id or None, limit=limit or 50)
        return list(reversed(rows))

    def cancel(self, job_id: str, caller: tp.CallerContext) -> tp.TrainingJob:
        j = self._jobs.get(job_id)
        if j is None:
            raise NotFound(job_id)
        if caller.actor == tp.ACTOR_ANALYST and j.job.launched_by.actor != tp.ACTOR_ANALYST:
            raise PermissionDenied("the analyst may cancel only its own jobs")
        j.cancel = True
        if j.job.status == tp.JOB_STATUS_QUEUED:
            self._set(j, status=tp.JOB_STATUS_CANCELED, finished_at=_now(), phase="canceled")
            self._emit(j, phase="canceled", log="canceled before start")
        self.audit.append(name(caller), "ml.job.cancel", job_id, {})
        return j.job

    def stream(self, job_id: str, after_seq: int = 0, timeout: float = 3600.0) -> Iterator[tp.JobProgress]:
        j = self._jobs.get(job_id)
        if j is None:
            raise NotFound(job_id)
        seq = after_seq
        deadline = time.time() + timeout
        while time.time() < deadline:
            with j.cond:
                while len(j.progress) <= seq and j.job.status in (tp.JOB_STATUS_QUEUED, tp.JOB_STATUS_RUNNING):
                    if not j.cond.wait(timeout=1.0) and time.time() > deadline:
                        return
                batch = j.progress[seq:]
            for p in batch:
                seq = p.seq
                yield p
            if j.job.status not in (tp.JOB_STATUS_QUEUED, tp.JOB_STATUS_RUNNING) and seq >= len(j.progress):
                return

    # ---- runs and diagnostics ----
    def run(self, run_id: str) -> tp.TrainingRun:
        r = self.store.get(runs.KIND_RUN, run_id, tp.TrainingRun)
        if r is None:
            raise NotFound(run_id)
        return r

    def list_runs(self, lineage_id: str = "", family: str = "", limit: int = 50) -> list[tp.TrainingRun]:
        rows = self.store.list(runs.KIND_RUN, tp.TrainingRun, lineage_id=lineage_id or None)
        rows = [r for r in rows if not family or r.config.family == family]
        return list(reversed(rows))[: limit or 50]

    def diagnostics(self, run_id: str) -> tp.DiagnosticsReport:
        run = self.run(run_id)
        rep = self.store.get(runs.KIND_DIAG, run.diagnostics_report_id, tp.DiagnosticsReport)
        if rep is None:
            raise NotFound(run.diagnostics_report_id)
        return rep

    def importance(self, run_id: str, method: str) -> tp.FeatureImportance:
        imp = self.store.get(runs.KIND_IMPORTANCE, f"{run_id}:{method or 'gain'}", tp.FeatureImportance)
        if imp is None:
            raise NotFound(f"{run_id}:{method}")
        return imp

    def error_slices(self, run_id: str, slice_by: str) -> list[tp.ErrorSlice]:
        return [s for s in self.diagnostics(run_id).error_slices if not slice_by or s.slice_by == slice_by]

    def profile(self, snapshot_id: str) -> tp.DatasetProfile:
        p = self.store.get(runs.KIND_PROFILE, snapshot_id, tp.DatasetProfile)
        if p is None:
            raise NotFound(snapshot_id)
        return p

    def model_card(self, run_id: str) -> tp.ModelCard:
        run = self.run(run_id)
        path = Path(run.model_card_uri)
        md = path.read_text() if path.exists() else ""
        return tp.ModelCard(
            run_id=run_id, markdown=md, fields={"family": run.config.family, "snapshot": run.dataset.snapshot_id}
        )

    def annotate(self, run_id: str, note: str, caller: tp.CallerContext) -> None:
        run = self.run(run_id)
        run.notes.append(f"[{name(caller)}] {note[:NOTE_LIMIT]}")  # untrusted text, stored as data
        self.store.put(runs.KIND_RUN, run_id, run, lineage_id=run.lineage_id, parent_id=run.base_run_id)
        self.audit.append(name(caller), "ml.run.annotate", run_id, {"chars": len(note)})

    # ---- ablations (ML-025) ----
    def ablate(self, run_id: str, variants: list[tp.AblationVariant], caller: tp.CallerContext) -> list[tp.TrainingJob]:
        base = self.run(run_id)
        jobs = []
        base_ds = self._dataset(base.config)
        if base_ds.snapshot_id != base.dataset.snapshot_id:
            raise InvalidConfig([f"base snapshot {base.dataset.snapshot_id} cannot be rebuilt (data changed)"])
        for v in variants:
            cfg = tp.TrainingConfig()
            cfg.CopyFrom(base.config)
            cfg.name = f"{base.config.name or base.run_id} / {v.name or 'ablation'}"
            same_rows = True
            if v.HasField("label") and v.label.kind:
                cfg.label.CopyFrom(v.label)
                same_rows = False
            if v.HasField("hyperparams"):
                hp = {**mlconfig.hyperparams(cfg), **json_format.MessageToDict(v.hyperparams)}
                cfg.hyperparams.Clear()
                cfg.hyperparams.update(hp)
            if v.add_feature_groups:
                raise InvalidConfig(["add_feature_groups needs a feature set that contains the group; use a new run"])
            jobs.append(
                self.submit(
                    cfg,
                    caller,
                    kind="ablation",
                    base_run_id=run_id,
                    dataset=base_ds if same_rows else None,
                    drop_groups=list(v.drop_feature_groups),
                )
            )
        return jobs

    # ---- registry and prediction ----
    def register(self, run_id: str, name_: str, caller: tp.CallerContext) -> mlp.ModelVersion:
        if caller.actor == tp.ACTOR_ANALYST:
            raise PermissionDenied("model registration is an owner or optimizer action")
        run = self.run(run_id)
        existing = [m for m in self.store.list(KIND_MODEL, mlp.ModelVersion) if m.model_id == name_]
        mv = mlp.ModelVersion(
            model_id=name_,
            version=str(len(existing) + 1),
            family=run.config.family,
            training_run_id=run_id,
            dataset_snapshot_id=run.dataset.snapshot_id,
            feature_set_id=run.dataset.feature_set_id,
            metrics=dict(run.oos_metrics),
            stage=mlp.MODEL_STAGE_CANDIDATE,
            registry_stage=mlp.REGISTRY_STAGE_CANDIDATE,
            code_commit=run.repro.code_commit,
            config_hash=run.repro.config_hash,
            seed=run.config.seed,
            env_lock_hash=run.repro.env_lock_hash,
            name=name_,
            artifact_uri=str(Path(run.artifact_uri) / "model"),
            task=models.model_task(run.config.family),
            timeframe=run.config.timeframe,
            created_at=_now(),
        )
        self.store.put(KIND_MODEL, f"{name_}:{mv.version}", mv)
        self.audit.append(name(caller), "ml.model.register", f"{name_}:{mv.version}", {"run_id": run_id})
        return mv

    def predict(self, model_version: str, instrument_ids: list[str], as_of: int, strategy_id: str) -> list[sp.Signal]:
        mv = self.store.get(KIND_MODEL, model_version, mlp.ModelVersion)
        if mv is None:
            raise NotFound(model_version)
        bundle = ModelBundle.load(mv.artifact_uri)
        run = self.run(mv.training_run_id)
        cfg = tp.TrainingConfig()
        cfg.CopyFrom(run.config)
        bars, uni, bench = mldataset.load_bars(cfg, self.lake_dir or None)
        want = instrument_ids or uni
        b = bars.filter((pl.col("available_at") <= as_of) & pl.col("instrument_id").is_in([*want, bench]))
        fs = featuresets.get(cfg.feature_set.name, cfg.feature_set.version).resolve(bench)
        bundle.check_features(fs.hash, fs.names, cfg.timeframe)  # ML-040: fail closed on mismatch
        feats = mldataset.to_ns(compute(fs, mldataset.to_dt(b)))
        last = (
            feats.filter(pl.col("instrument_id").is_in(want))
            .group_by("instrument_id")
            .agg(pl.all().sort_by("ts_open").last())
        )
        scores = bundle.score_frame(last)
        out = []
        for row, s in zip(last.iter_rows(named=True), scores, strict=True):
            snap = feature_vector_id([row[f] for f in bundle.features])
            out.append(
                sp.Signal(
                    signal_id=new_ulid(as_of // 1_000_000, f"{model_version}|{row['instrument_id']}|{as_of}"),
                    strategy_id=strategy_id,
                    model_version=model_version,
                    instrument_id=str(row["instrument_id"]),
                    as_of=as_of,
                    direction=sp.DIRECTION_LONG if s >= bundle.threshold else sp.DIRECTION_FLAT,
                    score=float(s),
                    probability=float(s) if bundle.task == "classification" else 0.0,
                    horizon=int(bundle.horizon * mlconfig.bar_ns(cfg.timeframe)),
                    feature_snapshot_id=snap,
                )
            )
        return out
