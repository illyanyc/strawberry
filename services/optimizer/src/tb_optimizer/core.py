"""Optimizer core (section 11, ADR-0038, ADR-0040): lineages, research cycles, studies, the trial ledger,
lineage statistics, gate reports, the registry and permission table, retraining, and the research side
of the holdout service.

Also implements ``tb_ml.link.ResearchLink`` (ml asks for lineages, the holdout boundary, and records
trials) and ``tb_optimizer.holdout.HoldoutResearch`` (the holdout asks for candidates and lineage N).
"""

from __future__ import annotations

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

from tb.alerts.v1 import alerts_pb2 as al
from tb.ml.v1 import ml_pb2 as mlp
from tb.ml.v1 import training_pb2 as tp
from tb.optimizer.v1 import optimizer_pb2 as op
from tb_optimizer import gates, registry
from tb_optimizer.holdout import HoldoutCore
from tb_optimizer.nested import Control, DatasetCache, NestedResult, NestedRunner
from tb_optimizer.stats import EffectiveN, Series, align, cumulative, cycle_budget, effective_n
from tbcore.audit import AuditSink, MemoryAudit, digest
from tbcore.backtest import tearsheet
from tbcore.buspub import MemoryPublisher, Publisher
from tbcore.docstore import DocStore, MemoryDocStore
from tbcore.ml import caller as callers
from tbcore.ml import dataset as mldataset
from tbcore.ml import models, runs, synthetic
from tbcore.ml.caller import PermissionDenied
from tbcore.ml.runs import code_commit
from tbcore.ml.tracking import NullTracker, Tracker
from tbcore.ml.train import train
from tbcore.strategy.api import new_ulid
from tbcore.validation.stats import deflated_sharpe, sharpe

log = logging.getLogger("tb_optimizer")

K_LINEAGE, K_CYCLE, K_STUDY, K_TRIAL, K_CAND, K_GATE = "lineage", "cycle", "study", "trial", "candidate", "gate_report"
K_STAGE, K_POLICY, K_HOLDOUT_COUNT, K_MODEL = "stage_change", "retrain_policy", "holdout_count", "model_version"
YEAR_NS = 365.25 * 86_400 * 1e9


class NotFound(KeyError):
    pass


@dataclass
class _Study:
    study: op.Study
    caller: tp.CallerContext
    progress: list[op.StudyProgress] = field(default_factory=list)
    cond: threading.Condition = field(default_factory=threading.Condition)
    stop: bool = False
    paused: bool = False
    thread: threading.Thread | None = None
    started: float = 0.0
    cpu_seconds: float = 0.0


def _now() -> int:
    return time.time_ns()


class OptimizerCore:
    def __init__(
        self,
        store: DocStore | None = None,
        artifacts_dir: str | Path = "data/ml-artifacts",
        holdout_store: DocStore | None = None,
        audit: AuditSink | None = None,
        bus: Publisher | None = None,
        tracker: Tracker | None = None,
        lake_dir: str = "",
        holdout_months: int = 12,
        optuna_storage: str | None = None,
        deploy_registry: str = "",
        paper_account_intraday: str = "sim-paper",
        paper_account_swing: str = "sim-paper",
        graduation_min_sessions: int = 20,
        global_analyst_trials: int = 0,
        clock: Any = time.time_ns,
    ) -> None:
        self.store = store or MemoryDocStore()
        self.artifacts = Path(artifacts_dir)
        self.audit: AuditSink = audit or MemoryAudit()
        self.bus: Publisher = bus or MemoryPublisher()
        self.tracker = tracker or NullTracker()
        self.lake_dir = lake_dir
        self.holdout_months = holdout_months
        self.optuna_storage = optuna_storage
        self.deploy_registry = deploy_registry
        self.accounts = (paper_account_intraday, paper_account_swing)
        self.grad_sessions = graduation_min_sessions
        self.global_analyst_trials = global_analyst_trials
        self._clock = clock
        self.holdout = HoldoutCore(holdout_store or MemoryDocStore(), self.audit, self, lake_dir, clock=clock)
        self._studies: dict[str, _Study] = {}
        self._lock = threading.RLock()

    def now(self) -> int:
        return int(self._clock())

    # ---------------------------------------------------------------- lineages and cycles
    def lineage(self, lineage_id: str) -> op.Lineage | None:
        return self.store.get(K_LINEAGE, lineage_id, op.Lineage)

    def _lineage(self, lineage_id: str) -> op.Lineage:
        lin = self.lineage(lineage_id)
        if lin is None:
            raise NotFound(f"lineage {lineage_id}")
        return lin

    def lineages(self) -> list[op.Lineage]:
        return self.store.list(K_LINEAGE, op.Lineage)

    def create_lineage(self, lin: op.Lineage, caller: tp.CallerContext) -> op.Lineage:
        if caller.actor == tp.ACTOR_ANALYST:
            raise PermissionDenied("lineages are created by the owner (EX-020)")
        if not lin.family or lin.family not in models.FAMILIES:
            raise ValueError(f"unknown family {lin.family!r}")
        out = op.Lineage()
        out.CopyFrom(lin)
        out.lineage_id = out.lineage_id or new_ulid(self.now() // 1_000_000, f"lin|{lin.name}|{self.now()}")
        if self.lineage(out.lineage_id) is not None:
            raise ValueError(f"lineage {out.lineage_id} exists")
        out.timeframe = out.timeframe or "15m"
        if not out.holdout_boundary:
            out.holdout_boundary = self._default_boundary(out.universe)
        out.autonomy_level = 0  # EX-033: every lineage starts suggest-only; raising needs step-up
        out.created_at = self.now()
        if not out.HasField("promotion_limits"):
            out.promotion_limits.CopyFrom(registry.DEFAULT_LIMITS)
        self.store.put(K_LINEAGE, out.lineage_id, out, out.lineage_id)
        self.holdout.init_boundary(out.lineage_id, out.family, out.holdout_boundary)
        self.audit.append(callers.name(caller), "optimizer.lineage.create", out.lineage_id, {"family": out.family})
        return out

    def _default_boundary(self, universe: str) -> int:
        if universe.startswith("synthetic:"):
            return synthetic.holdout_boundary(synthetic.preset(universe.split(":", 1)[1]))
        return int(self.now() - self.holdout_months * 30.44 * 86_400 * 1e9)

    def set_autonomy(
        self,
        lineage_id: str,
        level: int,
        budget: op.Budget | None,
        limits: op.PromotionLimits | None,
        caller: tp.CallerContext,
    ) -> op.Lineage:
        lin = self._lineage(lineage_id)
        if not 0 <= level <= 2:
            raise ValueError("autonomy level is 0, 1, or 2")
        raising = level > lin.autonomy_level or budget is not None or limits is not None
        if raising and (caller.actor != tp.ACTOR_OWNER or not callers.has_fresh_step_up(caller, now_ns=self.now())):
            self._deny(
                caller,
                "optimizer.autonomy",
                lineage_id,
                "raising autonomy or budgets needs the owner with step-up (EX-033)",
            )
        lin.autonomy_level = level
        if budget is not None:
            lin.analyst_budget.CopyFrom(budget)
        if limits is not None:
            lin.promotion_limits.CopyFrom(limits)
        self.store.put(K_LINEAGE, lineage_id, lin, lineage_id)
        self.audit.append(callers.name(caller), "optimizer.autonomy.set", lineage_id, {"level": level})
        return lin

    def _available_years(self, lin: op.Lineage) -> float:
        if lin.universe.startswith("synthetic:"):
            spec = synthetic.preset(lin.universe.split(":", 1)[1])
            return max((lin.holdout_boundary - synthetic.data_start(spec)) / YEAR_NS, 0.0)
        studies = self.store.list(K_STUDY, op.Study, lineage_id=lin.lineage_id)
        spans = [
            (s.spec.period.end or lin.holdout_boundary) - s.spec.period.start for s in studies if s.spec.period.start
        ]
        return max(spans) / YEAR_NS if spans else 5.0

    def open_cycle(self, lineage_id: str, target_sharpe: float, caller: tp.CallerContext) -> op.ResearchCycle:
        if caller.actor != tp.ACTOR_OWNER:
            self._deny(caller, "optimizer.cycle", lineage_id, "only the owner opens a research cycle (D31 rule 3)")
        lin = self._lineage(lineage_id)
        if lin.current_cycle_id:
            cur = self.store.get(K_CYCLE, lin.current_cycle_id, op.ResearchCycle)
            if cur is not None and cur.status == op.CYCLE_STATUS_OPEN:
                cur.status = op.CYCLE_STATUS_CLOSED
                self.store.put(K_CYCLE, cur.cycle_id, cur, lineage_id)
        years = self._available_years(lin)
        ts = target_sharpe or 1.0
        cyc = op.ResearchCycle(
            cycle_id=new_ulid(self.now() // 1_000_000, f"cyc|{lineage_id}|{self.now()}"),
            lineage_id=lineage_id,
            opened_at=self.now(),
            opened_by=caller,
            effective_trial_budget=cycle_budget(years, ts),
            status=op.CYCLE_STATUS_OPEN,
            available_years=years,
            target_sharpe=ts,
        )
        self.store.put(K_CYCLE, cyc.cycle_id, cyc, lineage_id)
        lin.current_cycle_id = cyc.cycle_id
        self.store.put(K_LINEAGE, lineage_id, lin, lineage_id)
        self.audit.append(
            "owner", "optimizer.cycle.open", lineage_id, {"budget": cyc.effective_trial_budget, "years": years}
        )
        return cyc

    def _cycle(self, lin: op.Lineage) -> op.ResearchCycle | None:
        return self.store.get(K_CYCLE, lin.current_cycle_id, op.ResearchCycle) if lin.current_cycle_id else None

    def _cycle_spent(self, lin: op.Lineage) -> bool:
        """EX-025: effective trials of the open cycle against its MinBTL budget."""
        cyc = self._cycle(lin)
        if cyc is None or cyc.status != op.CYCLE_STATUS_OPEN:
            return True
        trials = [t for t in self.trials(lineage_id=lin.lineage_id) if t.cycle_id == cyc.cycle_id]
        eff = effective_n(trials, align(self._series(trials)))
        used = eff.n_effective if trials else 0.0
        if used != cyc.effective_trials_used:
            cyc.effective_trials_used = used
        if used >= cyc.effective_trial_budget:
            cyc.status = op.CYCLE_STATUS_SPENT
            self.store.put(K_CYCLE, cyc.cycle_id, cyc, lin.lineage_id)
            self.audit.append("system", "optimizer.cycle.spent", cyc.cycle_id, {"used": used})
            return True
        self.store.put(K_CYCLE, cyc.cycle_id, cyc, lin.lineage_id)
        return False

    # ---------------------------------------------------------------- trial ledger (EX-020)
    def trials(self, lineage_id: str | None = None, study_id: str | None = None) -> list[op.Trial]:
        return self.store.list(K_TRIAL, op.Trial, lineage_id=lineage_id, parent_id=study_id)

    def _series_path(self, trial_id: str) -> Path:
        return self.artifacts / "trials" / f"{trial_id}.parquet"

    def _write_trial(self, t: op.Trial, s: Series | None) -> None:
        if s is not None and s.rets.size:
            p = self._series_path(t.trial_id)
            p.parent.mkdir(parents=True, exist_ok=True)
            pl.DataFrame({"session_day": s.days, "ret": s.rets}).write_parquet(p)
            t.oof_returns_ref = str(p)
        self.store.put(K_TRIAL, t.trial_id, t, t.lineage_id, t.study_id)

    def _series(self, trials: list[op.Trial]) -> dict[str, Series]:
        out = {}
        for t in trials:
            if t.oof_returns_ref and Path(t.oof_returns_ref).exists():
                f = pl.read_parquet(t.oof_returns_ref)
                col = "ret" if "ret" in f.columns else f.columns[-1]
                out[t.trial_id] = Series(
                    f["session_day"].to_numpy().astype(np.int64), f[col].to_numpy().astype(np.float64)
                )
        return out

    def record_trial(self, trial: op.Trial, caller: tp.CallerContext) -> op.Trial:
        """External trials (single training runs) join the lineage ledger (ML-024, EX-020)."""
        lin = self._lineage(trial.lineage_id)
        t = op.Trial()
        t.CopyFrom(trial)
        t.cycle_id = t.cycle_id or lin.current_cycle_id
        t.created_at = t.created_at or self.now()
        if not t.trial_id:
            t.trial_id = new_ulid(self.now() // 1_000_000, f"trial|{t.lineage_id}|{self.now()}")
        self.store.put(K_TRIAL, t.trial_id, t, t.lineage_id, t.study_id)
        if t.source == tp.ACTOR_ANALYST:
            lin.analyst_used.trials += 1
            lin.analyst_used.cpu_hours += t.compute_seconds / 3600
            self.store.put(K_LINEAGE, lin.lineage_id, lin, lin.lineage_id)
            self._budget_alert(lin)
        return t

    def _alert(self, severity: str, subject: str, message: str, dedupe: str) -> None:
        sev = {"info": al.SEVERITY_INFO, "warning": al.SEVERITY_WARNING, "critical": al.SEVERITY_CRITICAL}[severity]
        a = al.Alert(
            alert_id=new_ulid(self.now() // 1_000_000, dedupe + str(self.now())),
            severity=sev,
            source="optimizer",
            subject=subject,
            message=message,
            dedupe_key=dedupe,
            occurred_at=self.now(),
        )
        try:
            self.bus.publish(f"alert.{severity}", a, msg_id=a.alert_id)
        except Exception:
            log.exception("alert publish failed")

    def _budget_alert(self, lin: op.Lineage) -> None:
        """EX-036: alert at 80 % of the analyst trial or CPU budget."""
        b, u = lin.analyst_budget, lin.analyst_used
        hot = (b.max_trials and u.trials >= 0.8 * b.max_trials) or (
            b.max_cpu_hours and u.cpu_hours >= 0.8 * b.max_cpu_hours
        )
        if hot:
            self.audit.append("system", "optimizer.analyst_budget.80pct", lin.lineage_id, {"trials": u.trials})
            self._alert(
                "warning",
                f"analyst budget 80 % used: {lin.lineage_id}",
                f"lineage {lin.name or lin.lineage_id}: {u.trials} trials, {u.cpu_hours:.2f} CPU h used",
                f"analyst-budget:{lin.lineage_id}",
            )

    def holdout_touches(self, lineage_id: str) -> int:
        c = self.store.get(K_HOLDOUT_COUNT, lineage_id, op.TrialLedger)
        return int(c.holdout_evaluations) if c else 0

    def note_holdout_touch(self, lineage_id: str) -> None:
        c = self.store.get(K_HOLDOUT_COUNT, lineage_id, op.TrialLedger) or op.TrialLedger(lineage_id=lineage_id)
        c.holdout_evaluations += 1
        self.store.put(K_HOLDOUT_COUNT, lineage_id, c, lineage_id)

    def set_lineage_boundary(self, lineage_id: str, boundary: int) -> None:
        lin = self._lineage(lineage_id)
        lin.holdout_boundary = boundary
        self.store.put(K_LINEAGE, lineage_id, lin, lineage_id)

    def effective(self, lineage_id: str) -> EffectiveN:
        trials = self.trials(lineage_id=lineage_id)
        return effective_n(trials, align(self._series(trials)), self.holdout_touches(lineage_id))

    def ledger(self, lineage_id: str) -> op.TrialLedger:
        self._lineage(lineage_id)
        trials = self.trials(lineage_id=lineage_id)
        touches = self.holdout_touches(lineage_id)
        led = op.TrialLedger(lineage_id=lineage_id, trials_total=len(trials) + touches, holdout_evaluations=touches)
        for t in trials:
            src = "abandoned" if t.status == op.TRIAL_STATUS_ABANDONED else callers.ACTOR_NAMES.get(t.source, "unknown")
            led.by_source[src] = led.by_source.get(src, 0) + 1
            st = op.TrialStatus.Name(t.status).removeprefix("TRIAL_STATUS_").lower()
            led.by_status[st] = led.by_status.get(st, 0) + 1
            for d in t.dimensions:
                led.by_dimension[d] = led.by_dimension.get(d, 0) + 1
        led.cycles.extend(self.store.list(K_CYCLE, op.ResearchCycle, lineage_id=lineage_id))
        return led

    def cumulative(self, lineage_id: str) -> op.CumulativeOverfittingStats:
        lin = self._lineage(lineage_id)
        trials = self.trials(lineage_id=lineage_id)
        return cumulative(
            lineage_id, trials, self._series(trials), self.holdout_touches(lineage_id), 1, self._cycle(lin)
        )

    # ---------------------------------------------------------------- studies
    def _deny(self, caller: tp.CallerContext, action: str, target: str, why: str) -> None:
        self.audit.append(
            callers.name(caller), f"{action}.denied", target, {"reason": why}, correlation_id=caller.correlation_id
        )
        raise PermissionDenied(why)

    def create_study(self, spec: op.StudySpec, caller: tp.CallerContext) -> op.Study:
        lin = self._lineage(spec.lineage_id)
        if caller.actor == tp.ACTOR_ANALYST:
            if lin.autonomy_level < 1:
                self._deny(
                    caller, "optimizer.study", lin.lineage_id, "analyst studies need autonomy level >= 1 (EX-031)"
                )
            b, u = lin.analyst_budget, lin.analyst_used
            if b.max_trials and u.trials + max(spec.budget.max_trials, 1) > b.max_trials:
                self._deny(caller, "optimizer.study", lin.lineage_id, "analyst trial budget would be exceeded (EX-036)")
            if self.global_analyst_trials:
                used = sum(x.analyst_used.trials for x in self.lineages())
                if used + spec.budget.max_trials > self.global_analyst_trials:
                    self._deny(
                        caller, "optimizer.study", lin.lineage_id, "global analyst budget would be exceeded (EX-036)"
                    )
        s = op.StudySpec()
        s.CopyFrom(spec)
        if not s.HasField("base_config"):
            s.base_config.family = (list(s.families) or [lin.family])[0]
        s.base_config.timeframe = s.timeframe or s.base_config.timeframe or lin.timeframe
        s.timeframe = s.base_config.timeframe
        s.base_config.data_source = s.base_config.data_source or (
            lin.universe if lin.universe.startswith("synthetic:") else "lake"
        )
        s.base_config.lineage_id = lin.lineage_id
        if not s.families:
            s.families.append(s.base_config.family or lin.family)
        for f in s.families:
            if f not in models.FAMILIES:
                raise ValueError(f"unknown family {f!r}")
        if not s.period.end:
            s.period.end = lin.holdout_boundary
        ok, why, _ = self.holdout.check_data(lin.lineage_id, s.period, caller)  # EX-010
        if not ok:
            raise PermissionDenied(why)
        s.base_config.period.CopyFrom(s.period)
        s.budget.max_trials = s.budget.max_trials or 12
        s.seed = s.seed or 7
        now = self.now()
        st = op.Study(
            study_id=new_ulid(now // 1_000_000, f"study|{lin.lineage_id}|{now}"),
            spec=s,
            spec_hash=digest(json_format.MessageToJson(s, sort_keys=True))[:16],
            status=op.STUDY_STATUS_CREATED,
            created_at=now,
            created_by=caller,
            cycle_id=lin.current_cycle_id,
        )
        self.store.put(K_STUDY, st.study_id, st, lin.lineage_id)
        self.audit.append(callers.name(caller), "optimizer.study.create", st.study_id, {"spec_hash": st.spec_hash})
        return st

    def study(self, study_id: str) -> op.Study:
        w = self._studies.get(study_id)
        if w is not None:
            return w.study
        st = self.store.get(K_STUDY, study_id, op.Study)
        if st is None:
            raise NotFound(f"study {study_id}")
        return st

    def studies(self, lineage_id: str = "", limit: int = 50) -> list[op.Study]:
        return list(reversed(self.store.list(K_STUDY, op.Study, lineage_id=lineage_id or None)))[: limit or 50]

    def start_study(
        self, study_id: str, budget: op.Budget | None, caller: tp.CallerContext, wait: bool = False
    ) -> op.Study:
        st = self.study(study_id)
        if st.status != op.STUDY_STATUS_CREATED:
            raise ValueError(f"study {study_id} is {op.StudyStatus.Name(st.status)}")
        if caller.actor == tp.ACTOR_ANALYST and st.created_by.actor != tp.ACTOR_ANALYST:
            self._deny(caller, "optimizer.study.start", study_id, "the analyst may start only its own studies")
        pr = st.spec.preregistration
        if not (pr.hypothesis and pr.metric and pr.stop_rule):
            raise ValueError("pre-register hypothesis, metric, and stop rule before starting (EX-026)")
        if pr.metric != (st.spec.objective.metric or "sharpe"):
            raise ValueError("the objective metric must equal the pre-registered metric (EX-026)")
        lin = self._lineage(st.spec.lineage_id)
        if self._cycle_spent(lin):
            raise PermissionDenied("research cycle missing or spent; the owner must open a new cycle (EX-025)")
        if budget is not None and budget.max_trials:
            st.spec.budget.CopyFrom(budget)
        st.status = op.STUDY_STATUS_RUNNING
        st.started_at = self.now()
        w = _Study(study=st, caller=caller, started=time.time())
        self._studies[study_id] = w
        self.store.put(K_STUDY, study_id, st, st.spec.lineage_id)
        self.audit.append(
            callers.name(caller), "optimizer.study.start", study_id, {"max_trials": st.spec.budget.max_trials}
        )
        if wait:
            self._run(w)
        else:
            w.thread = threading.Thread(target=self._run, args=(w,), name=f"study-{study_id}", daemon=True)
            w.thread.start()
        return st

    def control(self, study_id: str, action: str, caller: tp.CallerContext) -> op.Study:
        w = self._studies.get(study_id)
        if w is None:
            raise NotFound(f"study {study_id} is not running here")
        if caller.actor == tp.ACTOR_ANALYST and w.caller.actor != tp.ACTOR_ANALYST and action != "pause":
            self._deny(caller, f"optimizer.study.{action}", study_id, "the analyst may control only its own studies")
        with w.cond:
            if action == "pause":
                w.paused, w.study.status = True, op.STUDY_STATUS_PAUSED
            elif action == "resume":
                w.paused, w.study.status = False, op.STUDY_STATUS_RUNNING
            elif action == "stop":
                w.stop, w.paused = True, False
            w.cond.notify_all()
        self.audit.append(callers.name(caller), f"optimizer.study.{action}", study_id, {})
        self.store.put(K_STUDY, study_id, w.study, w.study.spec.lineage_id)
        return w.study

    def _emit(self, w: _Study, message: str, fold: int = 0, trial: op.Trial | None = None) -> None:
        with w.cond:
            p = op.StudyProgress(
                study_id=w.study.study_id,
                seq=len(w.progress) + 1,
                at=self.now(),
                status=w.study.status,
                trials_completed=w.study.trials_completed,
                outer_fold=fold,
                message=message,
            )
            if trial is not None:
                p.trial.CopyFrom(trial)
            w.progress.append(p)
            w.cond.notify_all()

    def stream(self, study_id: str, after_seq: int = 0, timeout: float = 3600.0) -> Iterator[op.StudyProgress]:
        w = self._studies.get(study_id)
        if w is None:
            raise NotFound(study_id)
        seq, deadline = after_seq, time.time() + timeout
        while time.time() < deadline:
            with w.cond:
                while len(w.progress) <= seq and w.study.status in (op.STUDY_STATUS_RUNNING, op.STUDY_STATUS_PAUSED):
                    w.cond.wait(timeout=1.0)
                    if time.time() > deadline:
                        return
                batch = w.progress[seq:]
            for p in batch:
                seq = p.seq
                yield p
            if w.study.status not in (op.STUDY_STATUS_RUNNING, op.STUDY_STATUS_PAUSED) and seq >= len(w.progress):
                return

    def _budget_left(self, w: _Study, lin_id: str) -> bool:
        st, b = w.study, w.study.spec.budget
        n = st.trials_completed + st.trials_pruned + st.trials_failed
        if b.max_trials and n >= b.max_trials:
            return False
        if b.max_wall_seconds and time.time() - w.started > b.max_wall_seconds:
            return False
        if b.max_cpu_hours and w.cpu_seconds / 3600 >= b.max_cpu_hours:
            return False
        lin = self._lineage(lin_id)
        if w.caller.actor == tp.ACTOR_ANALYST:
            ab = lin.analyst_budget
            if ab.max_trials and lin.analyst_used.trials >= ab.max_trials:
                return False
            if ab.max_cpu_hours and lin.analyst_used.cpu_hours >= ab.max_cpu_hours:
                return False
        return not self._cycle_spent(lin)

    def _run(self, w: _Study) -> None:
        st = w.study
        spec = st.spec
        lin_id = spec.lineage_id
        try:
            base = tp.TrainingConfig()
            base.CopyFrom(spec.base_config)
            bars, uni, bench = mldataset.load_bars(base, self.lake_dir or None)
            if spec.universe.instrument_ids:
                uni = list(spec.universe.instrument_ids)
            cache = DatasetCache(bars, uni, bench, spec.period.end)

            def wait_if_paused() -> None:
                with w.cond:
                    while w.paused and not w.stop:
                        w.cond.wait(timeout=1.0)

            def on_trial(t: op.Trial, s: Series | None) -> None:
                self._write_trial(t, s)
                w.cpu_seconds += t.compute_seconds
                if t.status == op.TRIAL_STATUS_COMPLETE:
                    st.trials_completed += 1
                elif t.status == op.TRIAL_STATUS_PRUNED:
                    st.trials_pruned += 1
                elif t.status == op.TRIAL_STATUS_FAILED:
                    st.trials_failed += 1
                st.budget_used.trials = st.trials_completed + st.trials_pruned + st.trials_failed
                st.budget_used.cpu_hours = w.cpu_seconds / 3600
                st.budget_used.wall_seconds = int(time.time() - w.started)
                if w.caller.actor == tp.ACTOR_ANALYST:
                    lin = self._lineage(lin_id)
                    lin.analyst_used.trials += 1
                    lin.analyst_used.cpu_hours += t.compute_seconds / 3600
                    self.store.put(K_LINEAGE, lin_id, lin, lin_id)
                    self._budget_alert(lin)
                self.store.put(K_STUDY, st.study_id, st, lin_id)
                self._emit(w, f"trial {t.number} {op.TrialStatus.Name(t.status)}", t.outer_fold, t)

            ctl = Control(
                should_stop=lambda: w.stop,
                wait_if_paused=wait_if_paused,
                on_trial=on_trial,
                on_progress=lambda m, f: self._emit(w, m, f),
                budget_left=lambda: self._budget_left(w, lin_id),
            )
            runner = NestedRunner(
                spec,
                base,
                cache,
                ctl,
                st.study_id,
                lin_id,
                st.cycle_id,
                w.caller.actor,
                code_commit(),
                w.caller.analyst_round_id,
                self.optuna_storage,
            )
            nested = runner.run()
            outer = nested.stitched()
            sr = sharpe(outer.rets) if outer.rets.size >= 3 else math.nan
            st.outer_estimate.CopyFrom(
                op.OuterEstimate(
                    sharpe=sr if math.isfinite(sr) else 0.0,
                    sharpe_annualized=sr * math.sqrt(252) if math.isfinite(sr) else 0.0,
                    net_return=float(outer.rets.sum()),
                    n_obs=int(outer.rets.size),
                    positive_fold_fraction=sum(1 for o in nested.outer if o.series.rets.sum() > 0)
                    / max(len(nested.outer), 1),
                    trades=sum(o.trades for o in nested.outer),
                )
            )
            for k, (tid, val) in sorted(nested.best_inner.items()):
                t = next((x for x in nested.trials if x.trial_id == tid), None)
                if t is not None:
                    st.best_trials.append(op.StudyBest(trial_id=tid, outer_fold=k, inner_metric=val, params=t.params))
            st.trial_count_total = len(self.trials(lineage_id=lin_id))
            stats = self.cumulative(lin_id)
            st.pbo = stats.pbo
            if nested.final_config is not None and not w.stop and nested.outer:
                self._emit(w, "building the candidate and its gate report")
                cand = self._candidate(w, nested, cache, base)
                st.candidate_id = cand.candidate_id
                st.gate_report_id = cand.gate_report_id
                g = self.store.get(K_GATE, cand.gate_report_id, op.GateReport)
                st.deflated_sharpe = g.dsr if g else 0.0
            st.status = op.STUDY_STATUS_STOPPED if w.stop or nested.stopped else op.STUDY_STATUS_COMPLETED
            st.message = "stopped" if st.status == op.STUDY_STATUS_STOPPED else "completed"
            if not st.candidate_id and not w.stop:
                # EX-027: a study that never reached its final selection is not complete.
                st.status = op.STUDY_STATUS_STOPPED
                st.message = (
                    "stopped before the final selection: research cycle budget spent (EX-025)"
                    if self._cycle_spent(self._lineage(lin_id))
                    else "stopped before the final selection: budget exhausted (EX-060)"
                )
        except Exception as exc:
            log.exception("study %s failed", st.study_id)
            st.status = op.STUDY_STATUS_FAILED
            st.message = f"{type(exc).__name__}: {exc}"[:500]
        st.finished_at = self.now()
        self.store.put(K_STUDY, st.study_id, st, lin_id)
        self._emit(w, st.message)
        try:
            self.bus.publish(
                "optimizer.study.completed",
                op.StudyCompleted(
                    study_id=st.study_id,
                    lineage_id=lin_id,
                    status=st.status,
                    candidate_id=st.candidate_id,
                    gate_report_id=st.gate_report_id,
                    completed_at=st.finished_at,
                    launched_by=w.caller.actor,
                    analyst_round_id=w.caller.analyst_round_id,
                    correlation_id=w.caller.correlation_id,
                ),
                msg_id=f"optimizer.study.completed.{st.study_id}",
            )
        except Exception:
            log.exception("publish optimizer.study.completed failed")
        self.audit.append(
            "system", "optimizer.study.finish", st.study_id, {"status": st.status, "candidate": st.candidate_id}
        )

    def _candidate(self, w: _Study, nested: NestedResult, cache: DatasetCache, base: tp.TrainingConfig) -> op.Candidate:
        """Final selection → full run (persisted), model version, candidate, gate report (EX-002, EX-024)."""
        assert nested.final_config is not None
        cfg = nested.final_config
        cfg.lineage_id = w.study.spec.lineage_id
        cfg.name = f"{w.study.spec.name or 'study'} candidate"
        ds = cache.get(cfg)
        res = train(cfg, ds)
        cid = "cand_" + w.study.study_id
        run_id = "run_" + cid
        run = runs.persist(
            res,
            run_id,
            "",
            self.store,
            self.artifacts,
            caller=w.caller,
            tracker=self.tracker,
            trial_id=nested.final_trial_id,
        )
        mv = mlp.ModelVersion(
            model_id=f"{w.study.spec.lineage_id}",
            version=cid,
            family=cfg.family,
            training_run_id=run_id,
            dataset_snapshot_id=ds.snapshot_id,
            feature_set_id=ds.feature_set.id,
            metrics=dict(run.oos_metrics),
            stage=mlp.MODEL_STAGE_CANDIDATE,
            registry_stage=mlp.REGISTRY_STAGE_CANDIDATE,
            code_commit=run.repro.code_commit,
            config_hash=run.repro.config_hash,
            seed=cfg.seed,
            env_lock_hash=run.repro.env_lock_hash,
            name=cfg.family,
            artifact_uri=str(Path(run.artifact_uri) / "model"),
            task=models.model_task(cfg.family),
            timeframe=cfg.timeframe,
            created_at=self.now(),
        )
        self.store.put(K_MODEL, f"{mv.model_id}:{mv.version}", mv)
        cfg.universe.instrument_ids[:] = sorted(set(ds.frame["instrument_id"].to_list()))
        cfg.universe.benchmark = ds.benchmark
        cand = op.Candidate(
            candidate_id=cid,
            lineage_id=w.study.spec.lineage_id,
            study_id=w.study.study_id,
            run_id=run_id,
            model_version=mv,
            config=cfg,
            stage=mlp.REGISTRY_STAGE_CANDIDATE,
            created_at=self.now(),
            synthetic_data=cfg.data_source.startswith("synthetic:"),
            strategy_id=f"ml-{cid[-10:].lower()}",
            stage_changed_at=self.now(),
            origin=w.study.spec.kind or "training",
        )
        # Outer series of the selection procedure (champion comparisons, EX-052).
        cdir = self.artifacts / "candidates" / cid
        cdir.mkdir(parents=True, exist_ok=True)
        outer = nested.stitched()
        pl.DataFrame({"session_day": outer.days, "ret": outer.rets}).write_parquet(cdir / "outer.parquet")
        try:
            event = gates.event_evidence(nested, ds, cfg)
        except Exception:
            log.exception("event-driven rerun failed for %s", cid)
            event = None
        if event is not None:
            pl.DataFrame({"session_day": event.series.days, "ret": event.series.rets}).write_parquet(
                cdir / "event.parquet"
            )
        lin = self._lineage(cand.lineage_id)
        champ_series, champ_id = self._champion_series(lin)
        trials = self.trials(lineage_id=lin.lineage_id)
        mat = align(self._series(trials))
        eff = effective_n(trials, mat, self.holdout_touches(lin.lineage_id))
        surv = "complete" if cand.synthetic_data else "unknown"
        rep = gates.build_gate_report(cid, nested, ds, cfg, mat, eff, champ_series, champ_id, res, surv, event)
        self.store.put(K_GATE, rep.report_id, rep, lin.lineage_id, cid)
        if event is not None and event.result is not None:
            tearsheet.write(
                Path(run.artifact_uri) / "tearsheet_event.html",
                event.result,
                overfitting={"DSR (lineage N)": rep.dsr, "effective N": rep.n_effective, "PBO": rep.pbo},
            )
        cand.gate_report_id = rep.report_id
        cand.registry_entry_json = json_format_dumps(registry.registry_entry(cand, "shadow", *self.accounts))
        self.store.put(K_CAND, cid, cand, lin.lineage_id, w.study.study_id)
        self.audit.append("system", "optimizer.candidate.create", cid, {"gate": rep.passed, "dsr": rep.dsr})
        self.bus.publish(
            "backtest.completed",
            tp.BacktestCompleted(backtest_id=rep.report_id, run_id=run_id, candidate_id=cid, completed_at=self.now()),
            msg_id=f"backtest.completed.{rep.report_id}",
        )
        if rep.passed:
            d = self.check(cid, mlp.REGISTRY_STAGE_VALIDATED, w.caller)
            if d.allowed:
                cand = self._transition(cand, mlp.REGISTRY_STAGE_VALIDATED, w.caller, "gate report passed (EX-051)")
        return cand

    def _champion_series(self, lin: op.Lineage) -> tuple[Series | None, str]:
        if not lin.champion_candidate_id:
            return None, ""
        cdir = self.artifacts / "candidates" / lin.champion_candidate_id
        for name in ("event.parquet", "outer.parquet"):
            p = cdir / name
            if p.exists():
                f = pl.read_parquet(p)
                return Series(
                    f["session_day"].to_numpy().astype(np.int64), f["ret"].to_numpy()
                ), lin.champion_candidate_id
        return None, lin.champion_candidate_id

    # ---------------------------------------------------------------- candidates and registry
    def candidate(self, candidate_id: str) -> op.Candidate:
        c = self.store.get(K_CAND, candidate_id, op.Candidate)
        if c is None:
            raise NotFound(f"candidate {candidate_id}")
        return c

    def candidates(self, lineage_id: str = "", stage: int = 0) -> list[op.Candidate]:
        out = self.store.list(K_CAND, op.Candidate, lineage_id=lineage_id or None)
        return [c for c in out if not stage or c.stage == stage]

    def gate_report(self, candidate_id: str, caller: tp.CallerContext) -> op.GateReport:
        c = self.candidate(candidate_id)
        r = self.store.get(K_GATE, c.gate_report_id, op.GateReport)
        if r is None:
            raise NotFound(f"gate report for {candidate_id}")
        return gates.analyst_view(r) if caller.actor == tp.ACTOR_ANALYST else r

    def _context(self, cand: op.Candidate) -> registry.Context:
        lin = self._lineage(cand.lineage_id)
        gate = self.store.get(K_GATE, cand.gate_report_id, op.GateReport) if cand.gate_report_id else None
        changes = self.store.list(K_STAGE, op.StageChanged, lineage_id=lin.lineage_id)
        lim = lin.promotion_limits if lin.HasField("promotion_limits") else registry.DEFAULT_LIMITS
        since = self.now() - lim.window_seconds * 1_000_000_000
        promos = [
            c
            for c in changes
            if c.to_stage == mlp.REGISTRY_STAGE_PAPER and c.caller.actor == tp.ACTOR_ANALYST and c.changed_at >= since
        ]
        demos = [c.changed_at for c in changes if c.reason.startswith("demotion")]
        champ = bool(lin.champion_candidate_id) and lin.champion_candidate_id != cand.candidate_id
        return registry.Context(
            lineage=lin,
            gate=gate,
            has_paper_champion=champ,
            analyst_paper_promotions_in_window=len(promos),
            last_demotion_at=max(demos) if demos else 0,
            now=self.now(),
            graduation_min_sessions=self.grad_sessions,
        )

    def check(self, candidate_id: str, to: int, caller: tp.CallerContext) -> op.PermissionDecision:
        cand = self.candidate(candidate_id)
        return registry.decide(caller, cand, to, self._context(cand))

    def _transition(
        self, cand: op.Candidate, to: int, caller: tp.CallerContext, reason: str, pack_hash: str = ""
    ) -> op.Candidate:
        frm = cand.stage
        cand.stage = to  # type: ignore[assignment]
        cand.stage_changed_at = self.now()
        cand.model_version.registry_stage = to  # type: ignore[assignment]
        lin = self._lineage(cand.lineage_id)
        if to in (mlp.REGISTRY_STAGE_SHADOW, mlp.REGISTRY_STAGE_PAPER):
            mode = "paper" if to == mlp.REGISTRY_STAGE_PAPER else "shadow"
            entry = registry.registry_entry(cand, mode, *self.accounts)
            cand.registry_entry_json = json_format_dumps(entry)
            self._deploy(entry)
        if to == mlp.REGISTRY_STAGE_PAPER:
            old = lin.champion_candidate_id
            lin.champion_candidate_id = cand.candidate_id
            self.store.put(K_LINEAGE, lin.lineage_id, lin, lin.lineage_id)
            if old and old != cand.candidate_id:
                prev = self.candidate(old)
                prev.stage = mlp.REGISTRY_STAGE_RETIRED
                prev.stage_changed_at = self.now()
                self.store.put(K_CAND, old, prev, lin.lineage_id, prev.study_id)
                self._undeploy(prev.strategy_id)
        if to == mlp.REGISTRY_STAGE_RETIRED:
            self._undeploy(cand.strategy_id)
            if lin.champion_candidate_id == cand.candidate_id:
                lin.champion_candidate_id = ""
                self.store.put(K_LINEAGE, lin.lineage_id, lin, lin.lineage_id)
        self.store.put(K_CAND, cand.candidate_id, cand, cand.lineage_id, cand.study_id)
        ev = op.StageChanged(
            candidate_id=cand.candidate_id,
            lineage_id=cand.lineage_id,
            from_stage=frm,
            to_stage=to,  # type: ignore[arg-type]
            caller=caller,
            changed_at=self.now(),
            reason=reason,
        )
        self.store.put(K_STAGE, f"{cand.candidate_id}:{ev.changed_at}", ev, cand.lineage_id, cand.candidate_id)
        self.audit.append(
            callers.name(caller),
            "optimizer.stage.change",
            cand.candidate_id,
            {
                "from": registry.stage_name(frm),
                "to": registry.stage_name(to),
                "reason": reason,
                "step_up": caller.step_up.method,
            },
            after_hash=pack_hash,
            correlation_id=caller.correlation_id,
        )
        self.bus.publish(
            "ml.registry.stage_changed", ev, msg_id=f"ml.registry.stage_changed.{cand.candidate_id}.{ev.changed_at}"
        )
        return cand

    def promote(
        self, candidate_id: str, to: int, caller: tp.CallerContext
    ) -> tuple[op.Candidate, op.PermissionDecision]:
        cand = self.candidate(candidate_id)
        d = registry.decide(caller, cand, to, self._context(cand))
        if not d.allowed:
            self.audit.append(
                callers.name(caller),
                "optimizer.promote.denied",
                candidate_id,
                {"to": registry.stage_name(to), "reason": d.reason, "gates": list(d.failed_gates)},
            )
            return cand, d
        pack_hash = ""
        if to in (mlp.REGISTRY_STAGE_PAPER, mlp.REGISTRY_STAGE_LIVE_ELIGIBLE):
            pack = self.evidence_pack(candidate_id, to)
            pack_hash = digest(pack.markdown)
        return self._transition(cand, to, caller, "promotion", pack_hash), d

    def deploy_shadow(self, candidate_id: str, caller: tp.CallerContext) -> tuple[op.Candidate, op.PermissionDecision]:
        return self.promote(candidate_id, mlp.REGISTRY_STAGE_SHADOW, caller)

    def nominate(self, candidate_id: str, rationale: str, caller: tp.CallerContext) -> op.PermissionDecision:
        cand = self.candidate(candidate_id)
        lin = self._lineage(cand.lineage_id)
        if caller.actor == tp.ACTOR_ANALYST and lin.autonomy_level < 2:
            self.audit.append("analyst", "optimizer.nominate.denied", candidate_id, {"reason": "needs level 2"})
            return op.PermissionDecision(allowed=False, reason="nominating needs analyst level 2 (EX-034)")
        self.audit.append(
            callers.name(caller), "optimizer.nominate.live_eligible", candidate_id, {"rationale": rationale[:500]}
        )
        self._alert(
            "info",
            f"live-eligible nomination: {candidate_id}",
            f"{callers.name(caller)} nominates {candidate_id} for live_eligible (no stage change): {rationale[:200]}",
            f"nominate:{candidate_id}",
        )
        return op.PermissionDecision(allowed=True, reason="owner notified; stage unchanged (EX-034)")

    def demote(self, candidate_id: str, to: int, reason: str, caller: tp.CallerContext) -> op.Candidate:
        if caller.actor not in (tp.ACTOR_OWNER, tp.ACTOR_SYSTEM):
            self._deny(caller, "optimizer.demote", candidate_id, "demotion comes from monitoring or the owner (EX-055)")
        if to not in (mlp.REGISTRY_STAGE_PAPER, mlp.REGISTRY_STAGE_RETIRED):
            raise ValueError("demotion goes to paper or retired")
        cand = self.candidate(candidate_id)
        cand.demoted = True
        if to == mlp.REGISTRY_STAGE_PAPER and cand.stage in (mlp.REGISTRY_STAGE_LIVE_ELIGIBLE, mlp.REGISTRY_STAGE_LIVE):
            return self._transition(cand, to, caller, f"demotion: {reason}")
        if to == mlp.REGISTRY_STAGE_RETIRED:
            return self._transition(cand, to, caller, f"demotion: {reason}")
        self.store.put(K_CAND, candidate_id, cand, cand.lineage_id, cand.study_id)
        return cand

    def record_sessions(self, candidate_id: str, sessions: int, caller: tp.CallerContext) -> op.Candidate:
        """Shadow/paper sessions observed (from monitoring or the owner) for EX-033 and EX-053."""
        if caller.actor not in (tp.ACTOR_OWNER, tp.ACTOR_SYSTEM):
            raise PermissionDenied("session counts come from monitoring or the owner")
        cand = self.candidate(candidate_id)
        cand.shadow_sessions = sessions
        self.store.put(K_CAND, candidate_id, cand, cand.lineage_id, cand.study_id)
        return cand

    def evidence_pack(self, candidate_id: str, to: int = 0) -> op.EvidencePack:
        cand = self.candidate(candidate_id)
        gate = self.store.get(K_GATE, cand.gate_report_id, op.GateReport) if cand.gate_report_id else None
        target = to or registry.NEXT.get(cand.stage, cand.stage)
        pack = registry.evidence_pack(
            cand, gate, self.cumulative(cand.lineage_id), self._lineage(cand.lineage_id), target
        )
        pack.created_at = self.now()
        cdir = self.artifacts / "candidates" / candidate_id
        cdir.mkdir(parents=True, exist_ok=True)
        (cdir / "evidence.md").write_text(pack.markdown)
        (cdir / "evidence.html").write_text(pack.html)
        return pack

    def _deploy(self, entry: dict[str, Any]) -> None:
        """Write the entry to the research deployment registry read by strategy-runtime (EX-056)."""
        if not self.deploy_registry:
            return
        p = Path(self.deploy_registry)
        p.parent.mkdir(parents=True, exist_ok=True)
        data = json_load(p) if p.exists() else {"strategies": []}
        data["strategies"] = [s for s in data["strategies"] if s["strategy_id"] != entry["strategy_id"]] + [entry]
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json_format_dumps(data))
        tmp.replace(p)

    def _undeploy(self, strategy_id: str) -> None:
        if not self.deploy_registry or not Path(self.deploy_registry).exists():
            return
        p = Path(self.deploy_registry)
        data = json_load(p)
        data["strategies"] = [s for s in data["strategies"] if s["strategy_id"] != strategy_id]
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json_format_dumps(data))
        tmp.replace(p)

    def lineage_graph(self, candidate_id: str) -> op.LineageGraph:
        cand = self.candidate(candidate_id)
        g = op.LineageGraph()
        cfg = cand.config

        def node(i: str, kind: str, label: str) -> None:
            g.nodes.append(op.GraphNode(id=i, kind=kind, label=label))

        node(cand.candidate_id, "candidate", f"{registry.stage_name(cand.stage)}")
        node(cand.model_version.version, "model_version", cand.model_version.name)
        node(cand.run_id, "run", cfg.family)
        node(cand.study_id, "study", "study")
        node(cand.model_version.dataset_snapshot_id, "dataset", cfg.data_source)
        node(f"{cfg.feature_set.name}@{cfg.feature_set.version}", "feature_set", cfg.feature_set.name)
        node(f"label:{cfg.label.kind}:h{cfg.label.horizon}", "label", cfg.label.kind)
        n_trials = len(self.trials(study_id=cand.study_id))
        node(f"{cand.study_id}:trials", "trial", f"{n_trials} trials")
        for a, b, k in (
            (cand.model_version.dataset_snapshot_id, cand.run_id, "trained_on"),
            (f"{cfg.feature_set.name}@{cfg.feature_set.version}", cand.model_version.dataset_snapshot_id, "features"),
            (f"label:{cfg.label.kind}:h{cfg.label.horizon}", cand.model_version.dataset_snapshot_id, "labels"),
            (cand.study_id, f"{cand.study_id}:trials", "ran"),
            (f"{cand.study_id}:trials", cand.run_id, "selected"),
            (cand.run_id, cand.model_version.version, "produced"),
            (cand.model_version.version, cand.candidate_id, "registered"),
        ):
            g.edges.append(op.GraphEdge(**{"from": a, "to": b, "kind": k}))
        return g

    # ---------------------------------------------------------------- diagnostics
    def study_diagnostics(self, study_id: str) -> op.StudyDiagnostics:
        st = self.study(study_id)
        trials = self.trials(study_id=study_id)
        out = op.StudyDiagnostics(study_id=study_id)
        series = self._series(trials)
        done = [t for t in trials if t.status == op.TRIAL_STATUS_COMPLETE]
        best = -math.inf
        last_improve = 0
        srs: list[float] = []
        for i, t in enumerate(trials, 1):
            if t.status == op.TRIAL_STATUS_COMPLETE:
                srs.append(t.sharpe)
                if t.inner_metric > best + 0.01:
                    last_improve = i
                best = max(best, t.inner_metric)
            var = float(np.var(srs, ddof=1)) if len(srs) > 1 else 0.0
            s = series.get(t.trial_id)
            deflated = 0.0
            if s is not None and s.rets.size >= 3 and math.isfinite(best):
                deflated = _psr_deflated(s.rets, len(srs), var)
            out.convergence.append(
                op.ConvergencePoint(trial=i, best_raw=best if math.isfinite(best) else 0.0, best_deflated=deflated)
            )
        out.plateau_estimate = last_improve
        out.param_importance.update(_importance(done))
        out.pruned_rate = sum(t.status == op.TRIAL_STATUS_PRUNED for t in trials) / max(len(trials), 1)
        out.failure_rate = sum(t.status == op.TRIAL_STATUS_FAILED for t in trials) / max(len(trials), 1)
        final = [t for t in done if t.outer_fold == -1]
        if final and st.best_trials:
            bt = next((b for b in st.best_trials if b.outer_fold == -1), None)
            if bt is not None:
                fake = NestedResult(trials=trials, final_trial_id=bt.trial_id)
                out.param_stability.extend(gates.param_stability(fake))
        if st.spec.objective.extra_metrics:
            pts = []
            for t in done:
                s = series.get(t.trial_id)
                if s is None or s.rets.size == 0:
                    continue
                eq = np.cumsum(s.rets)
                dd = float(np.max(np.maximum.accumulate(eq) - eq))
                pts.append((t.trial_id, t.inner_metric, dd))
            for tid, m, dd in pts:
                if not any(m2 >= m and d2 <= dd and (m2 > m or d2 < dd) for _, m2, d2 in pts):
                    out.pareto_front.append(
                        op.ParetoPoint(trial_id=tid, values={"inner_metric": m, "max_drawdown": dd})
                    )
        return out

    # ---------------------------------------------------------------- retraining (EX-040, EX-041)
    def set_policy(self, policy: op.RetrainingPolicy, caller: tp.CallerContext) -> op.RetrainingPolicy:
        if caller.actor != tp.ACTOR_OWNER:
            self._deny(caller, "optimizer.retrain_policy", policy.lineage_id, "retraining policies are owner settings")
        self._lineage(policy.lineage_id)
        p = op.RetrainingPolicy()
        p.CopyFrom(policy)
        lin = self._lineage(policy.lineage_id)
        p.cadence_seconds = p.cadence_seconds or (30 * 86_400 if lin.timeframe == "1d" else 7 * 86_400)
        p.psi_threshold = p.psi_threshold or 0.25
        self.store.put(K_POLICY, p.lineage_id, p, p.lineage_id)
        self.audit.append("owner", "optimizer.retrain_policy.set", p.lineage_id, {"triggers": list(p.triggers)})
        return p

    def trigger_retrain(self, lineage_id: str, reason: str, caller: tp.CallerContext, wait: bool = False) -> op.Study:
        lin = self._lineage(lineage_id)
        if caller.actor == tp.ACTOR_ANALYST:
            self._deny(caller, "optimizer.retrain", lineage_id, "the analyst launches studies, not policy retrains")
        if lin.autonomy_level >= 1 and caller.actor == tp.ACTOR_SCHEDULE:
            req = op.TriggerRetrainRequest(lineage_id=lineage_id, reason=reason, caller=caller)
            self.bus.publish(
                f"optimizer.retrain_requested.{lineage_id}", req, msg_id=f"retrain.{lineage_id}.{self.now()}"
            )
            self.audit.append("schedule", "optimizer.retrain.routed_to_analyst", lineage_id, {"reason": reason})
            return op.Study(message="routed to the analyst loop (EX-040)")
        prev = [s for s in self.studies(lineage_id) if s.status == op.STUDY_STATUS_COMPLETED]
        champ = self.candidate(lin.champion_candidate_id) if lin.champion_candidate_id else None
        base = next((s for s in prev if champ and s.study_id == champ.study_id), prev[0] if prev else None)
        if base is None:
            raise ValueError("no completed study to retrain from")
        spec = op.StudySpec()
        spec.CopyFrom(base.spec)
        spec.kind = "retrain"
        spec.name = f"retrain: {base.spec.name}"
        spec.period.end = lin.holdout_boundary  # the same spec on the extended window
        spec.preregistration.stop_rule = spec.preregistration.stop_rule or "budget"
        spec.preregistration.period.CopyFrom(spec.period)
        actor = tp.CallerContext(actor=tp.ACTOR_SCHEDULE, actor_id="retrain-policy", reason=reason)
        st = self.create_study(spec, actor)
        self.audit.append(
            callers.name(caller), "optimizer.retrain.trigger", lineage_id, {"reason": reason, "study": st.study_id}
        )
        return self.start_study(st.study_id, None, actor, wait=wait)

    def tick(self, now: int | None = None) -> list[str]:
        """Run due scheduled retrains (called every minute by the service)."""
        t = now or self.now()
        started = []
        for p in self.store.list(K_POLICY, op.RetrainingPolicy):
            if not p.enabled or "scheduled" not in p.triggers:
                continue
            runs_ = [s for s in self.studies(p.lineage_id) if s.spec.kind == "retrain"]
            last = max((s.created_at for s in runs_), default=self._lineage(p.lineage_id).created_at)
            if t - last >= p.cadence_seconds * 1_000_000_000:
                try:
                    st = self.trigger_retrain(p.lineage_id, "scheduled", tp.CallerContext(actor=tp.ACTOR_SCHEDULE))
                    started.append(st.study_id)
                except (ValueError, PermissionDenied) as exc:
                    log.warning("scheduled retrain for %s skipped: %s", p.lineage_id, exc)
        return started

    # ---------------------------------------------------------------- ResearchLink for ml
    def check_data(self, lineage_id: str, period: tp.Period, caller: tp.CallerContext) -> tuple[bool, str, int]:
        return self.holdout.check_data(lineage_id, period, caller)


def _psr_deflated(rets: np.ndarray, n: int, var: float) -> float:
    sr = sharpe(rets)
    if not math.isfinite(sr):
        return 0.0
    v = deflated_sharpe(sr, max(n, 1), var, 0.0, 3.0, rets.size)
    return float(v) if math.isfinite(v) else 0.0


def _importance(trials: list[op.Trial]) -> dict[str, float]:
    """Rank-correlation importance of each parameter for the inner metric (fANOVA proxy, deterministic)."""
    if len(trials) < 3:
        return {}
    y = np.array([t.inner_metric for t in trials])
    out: dict[str, float] = {}
    dicts = [json_format.MessageToDict(t.params) for t in trials]
    keys = {k for d in dicts for k in d}
    for k in keys:
        vals = [d.get(k) for d in dicts]
        if all(isinstance(v, float) for v in vals):
            x = np.array(vals, dtype=np.float64)
            if np.std(x) > 0 and np.std(y) > 0:
                rx = np.argsort(np.argsort(x))
                ry = np.argsort(np.argsort(y))
                out[k] = abs(float(np.corrcoef(rx, ry)[0, 1]))
        else:
            groups: dict[str, list[float]] = {}
            for v, m in zip(vals, y, strict=True):
                groups.setdefault(str(v), []).append(float(m))
            if len(groups) > 1 and np.var(y) > 0:
                means = np.array([np.mean(g) for g in groups.values()])
                sizes = np.array([len(g) for g in groups.values()])
                out[k] = float(np.sum(sizes * (means - y.mean()) ** 2) / (len(y) * np.var(y)))
    tot = sum(out.values())
    return {k: v / tot for k, v in out.items()} if tot > 0 else out


def json_format_dumps(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, indent=1, default=str)


def json_load(p: Path) -> dict[str, Any]:
    out: dict[str, Any] = json.loads(p.read_text())
    return out
