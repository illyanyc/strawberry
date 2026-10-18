"""Sealed holdout service (N2, D31 rule 5, ADR-0039; EX-010..EX-015).

The only component that reads data at or after a lineage's boundary. It answers ``CheckDataAccess``
for ml and the optimizer, evaluates a candidate's frozen model on the holdout for the owner (step-up,
reason, one touch per family per calendar quarter), and returns pass/fail + DSR only. Touches and
results live in the ``holdout`` store (role ``svc_holdout``); the research side learns only that a
touch happened (the count, for trial inflation, EX-013).
"""

from __future__ import annotations

import math
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

import numpy as np
import polars as pl

from tb.ml.v1 import training_pb2 as tp
from tb.optimizer.v1 import holdout_pb2 as hp
from tb.optimizer.v1 import optimizer_pb2 as op
from tb_optimizer.stats import DSR_GATE, EffectiveN, dsr_of
from tbcore.audit import AuditSink
from tbcore.backtest.costs import CostModelConfig
from tbcore.docstore import DocStore
from tbcore.ml import caller as callers
from tbcore.ml import dataset as mldataset
from tbcore.ml import models
from tbcore.ml.bundle import ModelBundle
from tbcore.ml.caller import PermissionDenied
from tbcore.ml.train import evaluate

KIND_BOUNDARY = "boundary"
KIND_TOUCH = "touch"
KIND_RESULT = "result"


class HoldoutResearch(Protocol):
    """What the holdout service needs from the research side (never the other way round)."""

    def candidate(self, candidate_id: str) -> op.Candidate: ...

    def lineage(self, lineage_id: str) -> op.Lineage | None: ...

    def effective(self, lineage_id: str) -> EffectiveN: ...

    def note_holdout_touch(self, lineage_id: str) -> None: ...

    def set_lineage_boundary(self, lineage_id: str, boundary: int) -> None: ...


def quarter(ns: int) -> str:
    d = datetime.fromtimestamp(ns / 1e9, tz=UTC)
    return f"{d.year}Q{(d.month - 1) // 3 + 1}"


class HoldoutCore:
    def __init__(
        self,
        store: DocStore,
        audit: AuditSink,
        research: HoldoutResearch,
        lake_dir: str = "",
        stepup_max_age_s: int = 300,
        inflation: int = 1,
        clock: object = time.time_ns,
    ) -> None:
        self.store, self.audit, self.research = store, audit, research
        self.lake_dir = lake_dir
        self.max_age = stepup_max_age_s
        self.inflation = inflation
        self._clock = clock

    def now(self) -> int:
        return int(self._clock())  # type: ignore[operator]

    # ---- boundary and the data API (EX-010, EX-014) ----
    def boundary(self, lineage_id: str) -> int:
        b = self.store.get(KIND_BOUNDARY, lineage_id, hp.HoldoutStatus)
        if b is not None:
            return int(b.boundary)
        lin = self.research.lineage(lineage_id)
        return int(lin.holdout_boundary) if lin is not None else 0

    def init_boundary(self, lineage_id: str, family: str, boundary: int) -> None:
        if self.store.get(KIND_BOUNDARY, lineage_id, hp.HoldoutStatus) is None:
            self.store.put(
                KIND_BOUNDARY,
                lineage_id,
                hp.HoldoutStatus(lineage_id=lineage_id, family=family, boundary=boundary),
                lineage_id,
            )

    def check_data(self, lineage_id: str, period: tp.Period, caller: tp.CallerContext) -> tuple[bool, str, int]:
        b = self.boundary(lineage_id)
        if not b:
            return False, f"lineage {lineage_id} has no holdout boundary", 0
        if period.end == 0 or period.end > b:
            self.audit.append(
                callers.name(caller),
                "holdout.data_refused",
                lineage_id,
                {"period_end": int(period.end), "boundary": b},
                correlation_id=caller.correlation_id,
            )
            return False, f"period reaches the sealed holdout (boundary {b}, EX-010)", b
        return True, "", b

    def set_boundary(self, lineage_id: str, boundary: int, caller: tp.CallerContext) -> hp.HoldoutStatus:
        if caller.actor != tp.ACTOR_OWNER or not callers.has_fresh_step_up(caller, self.max_age, self.now()):
            self._deny(
                caller, "holdout.boundary", lineage_id, "moving the holdout boundary needs the owner with step-up"
            )
        cur = self.store.get(KIND_BOUNDARY, lineage_id, hp.HoldoutStatus) or hp.HoldoutStatus(lineage_id=lineage_id)
        if boundary <= cur.boundary:
            raise ValueError("the holdout boundary only moves forward; a spent holdout is never reopened (EX-014)")
        if cur.boundary:
            cur.retired_boundaries.append(cur.boundary)
        cur.boundary = boundary
        self.store.put(KIND_BOUNDARY, lineage_id, cur, lineage_id)
        self.research.set_lineage_boundary(lineage_id, boundary)
        self.audit.append("owner", "holdout.boundary.set", lineage_id, {"boundary": boundary})
        return self.status(lineage_id)

    # ---- evaluation (EX-011, EX-013, EX-015) ----
    def _deny(self, caller: tp.CallerContext, action: str, target: str, why: str) -> None:
        self.audit.append(
            callers.name(caller), f"{action}.denied", target, {"reason": why}, correlation_id=caller.correlation_id
        )
        raise PermissionDenied(why)

    def evaluate(self, candidate_id: str, reason: str, caller: tp.CallerContext) -> hp.HoldoutResult:
        if caller.actor == tp.ACTOR_ANALYST:
            self._deny(caller, "holdout.evaluate", candidate_id, "the analyst never reaches the holdout (EX-012)")
        if caller.actor != tp.ACTOR_OWNER or not callers.has_fresh_step_up(caller, self.max_age, self.now()):
            self._deny(
                caller, "holdout.evaluate", candidate_id, "holdout evaluation needs the owner with a fresh step-up"
            )
        if not reason.strip():
            raise ValueError("a reason is required for every holdout evaluation (EX-011)")
        cand = self.research.candidate(candidate_id)
        lin = self.research.lineage(cand.lineage_id)
        family = lin.family if lin is not None else cand.config.family
        q = quarter(self.now())
        used = [
            t
            for t in self.store.list(KIND_TOUCH, hp.HoldoutTouch, lineage_id=cand.lineage_id)
            if t.family == family and t.quarter == q
        ]
        if used:
            self.audit.append("owner", "holdout.evaluate.refused", candidate_id, {"quarter": q, "family": family})
            raise PermissionDenied(f"family {family} already used its holdout touch in {q} (EX-011)")
        touch = hp.HoldoutTouch(
            candidate_id=candidate_id,
            family=family,
            quarter=q,
            at=self.now(),
            actor_id=caller.actor_id,
            reason=reason[:500],
        )
        # The touch is recorded before evaluating: a crash mid-evaluation still spends it (EX-I2).
        self.store.put(KIND_TOUCH, f"{cand.lineage_id}:{family}:{q}", touch, cand.lineage_id)
        self.research.note_holdout_touch(cand.lineage_id)
        rets, rets2 = self._evaluate_frozen(cand)
        eff = self.research.effective(cand.lineage_id)
        dsr = dsr_of(rets, eff)
        passed = bool(math.isfinite(dsr) and dsr >= DSR_GATE and float(np.sum(rets2)) > 0)
        res = hp.HoldoutResult(
            candidate_id=candidate_id,
            passed=passed,
            dsr=dsr if math.isfinite(dsr) else 0.0,
            evaluated_at=self.now(),
            family=family,
            quarter=q,
        )
        self.store.put(KIND_RESULT, f"{candidate_id}:{q}", res, cand.lineage_id)
        self.audit.append(
            "owner",
            "holdout.evaluate",
            candidate_id,
            {"passed": passed, "dsr": round(res.dsr, 4), "quarter": q, "reason": reason[:200]},
            correlation_id=caller.correlation_id,
        )
        return res

    def _evaluate_frozen(self, cand: op.Candidate) -> tuple[np.ndarray, np.ndarray]:
        """Score the holdout with the candidate's frozen bundle (no refit, EX-015) through the gate's evaluator."""
        bundle = ModelBundle.load(Path(cand.model_version.artifact_uri))
        cfg = tp.TrainingConfig()
        cfg.CopyFrom(cand.config)
        b = self.boundary(cand.lineage_id)
        bars, uni, bench = mldataset.load_bars(cfg, self.lake_dir or None)
        end = int(bars["available_at"].max() or 0) + 1  # type: ignore[arg-type]
        cfg.period.start, cfg.period.end = b, end
        ds = mldataset.build(cfg, bars, uni, bench, boundary=None, task=models.model_task(cfg.family))
        if ds.frame.height == 0:
            return np.zeros(0), np.zeros(0)
        bundle.check_features(ds.feature_set.hash, ds.features, cfg.timeframe)
        pred = bundle.score_frame(ds.frame)
        rows = np.arange(ds.frame.height, dtype=np.int64)
        vd = ds.vector_data()
        cost = CostModelConfig()
        low = -math.inf
        e1 = evaluate(cfg, ds, vd, rows, pred, bundle.threshold, low, cost)
        e2 = evaluate(cfg, ds, vd, rows, pred, bundle.threshold, low, cost.stressed(2.0))
        start_day = int(pl.Series([b]).cast(pl.Datetime("ns", "UTC")).dt.date().cast(pl.Int32)[0])
        mask = e1.days >= start_day
        return e1.returns[mask], e2.returns[mask]

    def status(self, lineage_id: str) -> hp.HoldoutStatus:
        b = self.store.get(KIND_BOUNDARY, lineage_id, hp.HoldoutStatus) or hp.HoldoutStatus(lineage_id=lineage_id)
        lin = self.research.lineage(lineage_id)
        touches = self.store.list(KIND_TOUCH, hp.HoldoutTouch, lineage_id=lineage_id)
        q = quarter(self.now())
        this_q = [t for t in touches if t.quarter == q]
        out = hp.HoldoutStatus(
            lineage_id=lineage_id,
            family=lin.family if lin is not None else b.family,
            boundary=b.boundary or (lin.holdout_boundary if lin is not None else 0),
            touches=touches,
            touches_this_quarter=len(this_q),
            remaining_this_quarter=max(0, 1 - len(this_q)),
            trial_inflation_per_touch=self.inflation,
            retired_boundaries=list(b.retired_boundaries),
        )
        return out
