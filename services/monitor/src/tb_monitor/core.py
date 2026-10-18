"""Full monitoring orchestrator (section 13 Phase 5, ADR-0045).

Feeds bus messages into per-strategy books, evaluates each monitored strategy's health against its
expectation and containment policy, acts on breaches (``containment``), writes reports, and builds the
graduation evidence section 15 consumes. Messages arrive from the recorder (live) or from its session
files (history replay at start), so restarts keep the windows (ADR-0046).
"""

from __future__ import annotations

import logging
import math
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import numpy as np
from google.protobuf.message import Message

from tb.alerts.v1 import alerts_pb2
from tb.features.v1 import features_pb2
from tb.ledger.v1 import ledger_pb2
from tb.marketdata.v1 import marketdata_pb2
from tb.monitor.v1 import feedback_pb2
from tb.monitor.v1 import monitor_pb2 as mp
from tb.orders.v1 import execution_pb2 as ex
from tb.orders.v1 import orders_pb2
from tb.signals.v1 import signals_pb2
from tb_monitor import analytics as an
from tb_monitor import divergence
from tb_monitor.book import StrategyBook, attribution
from tb_monitor.containment import Containment, decide, drawdown_component
from tb_monitor.feedback import ks, psi
from tb_monitor.recorder import SessionRecorder
from tb_monitor.strategies import Monitored
from tbcore.docstore import DocStore

log = logging.getLogger("monitor.core")
K_HEALTH, K_PERF, K_DRIFT, K_DIV, K_EXEC, K_GRAD, K_DAILY = (
    "health",
    "performance",
    "drift",
    "divergence",
    "execution",
    "graduation",
    "daily_report",
)
MODES = {
    ex.STRATEGY_MODE_SHADOW: "shadow",
    ex.STRATEGY_MODE_PAPER: "paper",
    ex.STRATEGY_MODE_LIVE: "live",
    ex.STRATEGY_MODE_HALTED: "halted",
    ex.STRATEGY_MODE_DISABLED: "disabled",
}
Publish = Callable[[str, Message, str], Any]


def now_ns() -> int:
    return time.time_ns()


class Monitor:
    def __init__(
        self,
        strategies: dict[str, Monitored],
        store: DocStore,
        containment: Containment,
        publish: Publish,
        recorder: SessionRecorder | None = None,
        session_of: Callable[[int], str] | None = None,
        drift_window: int = 5_000,
        divergence_window: int = 5,
    ) -> None:
        self.strategies = strategies
        self.store = store
        self.containment = containment
        self.publish = publish
        self.recorder = recorder
        self.session_of = session_of or (lambda t: datetime.fromtimestamp(t / 1e9, tz=UTC).date().isoformat())
        self.drift_window = drift_window
        self.div_window = divergence_window
        self.books = {s.strategy_id: StrategyBook(s.strategy_id, s.short_id, s.allocation) for s in strategies.values()}
        self.by_short = {s.short_id: s.strategy_id for s in strategies.values() if s.short_id}
        self.breaks: dict[str, int] = {}
        self.seen_breaks: set[str] = set()
        self.critical_alerts: dict[str, int] = {}
        self._lock = threading.RLock()

    # ---- inputs

    def on_message(self, subject: str, data: bytes) -> None:
        with self._lock:
            if subject.startswith("exec."):
                x = orders_pb2.ExecutionReport.FromString(data)
                sid = self.by_short.get(attribution(x.client_order_id)[0])
                if sid:
                    self.books[sid].on_execution(x)
            elif subject == "ord.intent":
                it = orders_pb2.OrderIntent.FromString(data)
                if it.strategy_id in self.books:
                    self.books[it.strategy_id].on_intent(it)
            elif subject.startswith("ord.order."):
                o = orders_pb2.Order.FromString(data)
                if o.strategy_id in self.books:
                    self.books[o.strategy_id].on_order(o)
            elif subject.startswith("md.bar.") and not subject.startswith(("md.bar.partial.", "md.bar.correction.")):
                b = marketdata_pb2.Bar.FromString(data)
                if b.is_final:
                    for bk in self.books.values():
                        bk.on_bar(b)
            elif subject.startswith("sig."):
                s = signals_pb2.Signal.FromString(data)
                if s.strategy_id in self.books:
                    self.books[s.strategy_id].on_signal(s)
            elif subject.startswith("feat."):
                f = features_pb2.FeatureVector.FromString(data)
                for m in self.strategies.values():
                    fs = m.reference.feature_set.split("@")[0] if m.reference is not None else ""
                    if fs == f.feature_set_id and (f.instrument_id in m.instruments or not m.instruments):
                        self.books[m.strategy_id].on_feature(f)
            elif subject.startswith("recon.result."):
                r = ledger_pb2.ReconciliationResult.FromString(data)
                for b in r.breaks:
                    if b.type == ledger_pb2.BREAK_TYPE_TIMING or b.break_id in self.seen_breaks:
                        continue
                    self.seen_breaks.add(b.break_id)
                    for owner in {*b.affected_owners, *(o.owner for o in b.owner_breakdown)}:
                        if owner in self.books:
                            self.breaks[owner] = self.breaks.get(owner, 0) + 1

    def on_alert_critical(self, text: str) -> None:
        for sid in self.strategies:
            if sid in text:
                self.critical_alerts[sid] = self.critical_alerts.get(sid, 0) + 1

    def close_session(self, date: str) -> None:
        with self._lock:
            for bk in self.books.values():
                bk.close_session(date)

    def set_modes(self, strategies: list[ex.Strategy]) -> None:
        for s in strategies:
            m = self.strategies.get(s.strategy_id)
            if m is not None:
                m.mode = MODES.get(s.mode, m.mode)

    # ---- reports

    def performance(self, sid: str) -> mp.PerformanceReport:
        m, bk = self.strategies[sid], self.books[sid]
        live_s, live_t = bk.session_returns_bps(), bk.trade_returns_bps()
        rep = mp.PerformanceReport(
            report_id=f"perf-{sid}-{len(live_s)}",
            strategy_id=sid,
            window_start=bk.first_seen,
            window_end=now_ns(),
            sessions=len(live_s),
            trades=len(live_t),
            session_returns_bps=live_s,
            drawdown_pct=bk.current_drawdown_pct(),
            max_drawdown_pct=self.close_drawdown_pct(sid),
            generated_at=now_ns(),
        )
        e = m.expectation
        if e is not None and e.session_bps.size:
            rep.backtest_report_id = e.backtest_report_id
            rep.bands.extend(an.performance_bands(e.session_bps, e.trade_bps, live_s, live_t, e.trades_per_session))
            dd = an.drawdown_bands(e.session_bps, max(1, len(live_s)))
            rep.drawdown_p95_pct, rep.drawdown_p99_pct = dd.p95, dd.p99
        return rep

    def close_drawdown_pct(self, sid: str) -> float:
        """Close-to-close drawdown since deployment plus the current mark (comparable to the bootstrap)."""
        bk = self.books[sid]
        pts = [float(bk.allocation), *(float(v) for _, v in sorted(bk.session_equity.items())), float(bk.equity())]
        eq = np.asarray(pts)
        peak = np.maximum.accumulate(eq)
        return float(np.max(np.where(peak > 0, 1 - eq / peak, 0)) * 100)

    def drift(self, sid: str) -> mp.DriftReport:
        m, bk = self.strategies[sid], self.books[sid]
        rep = mp.DriftReport(
            report_id=f"drift-{sid}-{len(bk.features)}-{len(bk.signals)}",
            strategy_id=sid,
            model_version=m.model_version,
            generated_at=now_ns(),
        )
        ref = m.reference
        if ref is None:
            return rep
        feats = list(bk.features)[-self.drift_window :]
        rep.reference_run_id, rep.live_samples, rep.reference_samples = ref.run_id, len(feats), len(ref.predictions)
        if feats:
            rep.window_start, rep.window_end = feats[0].as_of, feats[-1].as_of
        live: dict[str, list[float]] = {k: [] for k in ref.features}
        for f in feats:
            for k in ref.features:
                live[k].append(f.values.get(k, math.nan))
        for k, vals in live.items():
            miss = sum(1 for v in vals if not math.isfinite(v))
            if vals:
                rep.missing.append(mp.FeatureMissing(feature=k, rate=miss / len(vals)))
        for name, p, k_, rank in an.drift_features(
            live, {k: v.tolist() for k, v in ref.features.items()}, ref.importance
        ):
            rep.features.append(feedback_pb2.FeatureDrift(feature=name, psi=p, ks=k_, importance_rank=rank))
        sigs = list(bk.signals)[-self.drift_window :]
        scores = [s.score for s in sigs]
        if scores and ref.predictions.size:
            rep.prediction_drift.CopyFrom(
                feedback_pb2.FeatureDrift(
                    feature="prediction",
                    psi=psi(ref.predictions.tolist(), scores),
                    ks=ks(ref.predictions.tolist(), scores),
                )
            )
        if ref.labels.size:
            rep.reference_ic = an.spearman(ref.predictions, ref.labels)
        days = {self.session_of(s.as_of) for s in sigs}
        if days:
            rep.signal_rate_live = sum(1 for s in sigs if s.direction == signals_pb2.DIRECTION_LONG) / len(days)
        if m.expectation is not None:
            rep.signal_rate_reference = m.expectation.trades_per_session
        return rep

    def execution(self, sid: str) -> mp.ExecutionQualityReport:
        m, bk = self.strategies[sid], self.books[sid]
        lim, filled = bk.limit_orders()
        rep = an.execution_quality(
            sid, bk.fills, m.expectation.shortfall_bps_modeled if m.expectation else 0.0, lim, filled
        )
        rep.report_id = f"exec-{sid}-{len(bk.fills)}"
        rep.generated_at = now_ns()
        return rep

    def divergence_history(self, sid: str) -> list[mp.DivergenceReport]:
        rows = self.store.list(K_DIV, mp.DivergenceReport, lineage_id=sid)
        return sorted(rows, key=lambda r: r.session_date, reverse=True)

    def run_divergence(self, sid: str, target: str, warmup: int | None = None) -> mp.DivergenceReport | None:
        m = self.strategies[sid]
        if self.recorder is None or not m.entry.get("runtime"):
            return None
        w = warmup if warmup is not None else (10 if m.horizon == "swing" else 0)
        rep = divergence.run(
            m.entry, self.recorder, target, w, m.expectation.slippage_bps_modeled if m.expectation else 0.0
        )
        self.store.put(K_DIV, rep.report_id, rep, sid)
        self.publish(f"monitor.report.divergence.{sid}", rep, rep.report_id)
        return rep

    # ---- health and containment

    def health(self, sid: str) -> tuple[mp.StrategyHealth, dict[str, Message], bool]:
        m = self.strategies[sid]
        pol, _ = self.containment.policy(sid)
        perf = self.performance(sid)
        drift = self.drift(sid)
        exe = self.execution(sid)
        divs = self.divergence_history(sid)[: self.div_window]
        comps: list[mp.HealthComponent] = []
        dd = perf.max_drawdown_pct
        comps.append(drawdown_component(dd, perf.drawdown_p95_pct, pol, perf.sessions or 1, perf.report_id))
        if perf.drawdown_p95_pct <= 0:
            comps[-1].detail = "no backtest expectation"
        mt = next((b for b in perf.bands if b.metric == "return_pct"), None)
        comps.append(
            an.component(
                "performance",
                mt.percentile if mt else math.nan,
                10.0,
                5.0,
                perf.trades,
                pol.min_trades,
                f"return percentile {mt.percentile:.1f} in the backtest band" if mt else "no backtest expectation",
                perf.report_id,
                higher_is_worse=False,
            )
        )
        rate, gap, n, complete = divergence.window_stats(divs)
        dv_samples = n if complete else 0
        evid = "+".join(r.report_id for r in divs[:1])
        mr = an.component(
            "divergence",
            rate,
            pol.match_rate_warning,
            pol.match_rate_breach,
            dv_samples,
            pol.min_matched_intents,
            f"match rate over {len(divs)} sessions"
            if complete
            else ("recording incomplete" if divs else "no daily parity yet"),
            evid,
            higher_is_worse=False,
        )
        sg = an.component(
            "divergence",
            gap,  # only fills worse than modeled count; a favourable gap means a conservative cost model
            pol.slippage_gap_warning_bps,
            pol.slippage_gap_breach_bps,
            dv_samples,
            pol.min_matched_intents,
            f"slippage gap {gap:+.2f} bps vs modeled",
            evid,
        )
        comps.append(mr if an.SEVERITY[mr.status] >= an.SEVERITY[sg.status] else sg)
        top = [d for d in drift.features if 0 < d.importance_rank <= pol.feature_psi_top_k]
        worst = max(top, key=lambda d: d.psi) if top else None
        comps.append(
            an.component(
                "feature_drift",
                worst.psi if worst else math.nan,
                pol.feature_psi_warning,
                pol.feature_psi_breach,
                drift.live_samples,
                pol.min_drift_samples,
                f"PSI {worst.psi:.3f} on {worst.feature} (rank {worst.importance_rank})"
                if worst
                else "no training reference",
                drift.report_id,
            )
        )
        pp = drift.prediction_drift.psi if drift.HasField("prediction_drift") else math.nan
        comps.append(
            an.component(
                "prediction_drift",
                pp,
                pol.prediction_psi_warning,
                pol.prediction_psi_breach,
                len(self.books[sid].signals),
                pol.min_drift_samples,
                "prediction PSI vs out-of-fold",
                drift.report_id,
            )
        )
        modeled = exe.modeled_shortfall_bps
        ratio = exe.shortfall_bps / modeled if modeled > 0 else math.nan
        sf = an.component(
            "execution_quality",
            ratio,
            pol.shortfall_warning_multiple,
            pol.shortfall_breach_multiple,
            exe.fills if modeled > 0 else 0,
            pol.min_fills,
            f"shortfall {exe.shortfall_bps:.2f} bps vs modeled {modeled:.2f}",
            exe.report_id,
        )
        if sf.status == mp.HEALTH_STATUS_BREACH and exe.shortfall_bps - modeled < pol.shortfall_min_excess_bps:
            sf.status, sf.detail = mp.HEALTH_STATUS_WARNING, sf.detail + " (excess below the minimum)"
        comps.append(sf)
        fb = self.store.get("feedback", sid, feedback_pb2.LiveFeedbackReport)
        if fb is not None and fb.decay.samples:
            comps.append(
                an.component(
                    "decay",
                    fb.decay.statistic,
                    fb.decay.threshold * 0.6,
                    fb.decay.threshold,
                    fb.decay.samples,
                    pol.min_trades,
                    "lower CUSUM on per-trade returns",
                    fb.report_id,
                )
            )
        score, status = an.overall(comps)
        dd_p99 = perf.drawdown_p99_pct > 0 and dd > perf.drawdown_p99_pct
        if status == mp.HEALTH_STATUS_BREACH and dd_p99 and mr.status == mp.HEALTH_STATUS_BREACH:
            status = mp.HEALTH_STATUS_SEVERE
        h = mp.StrategyHealth(
            strategy_id=sid,
            lineage_id=m.lineage_id,
            model_version=m.model_version,
            mode=m.mode,
            score=score,
            status=mp.HealthStatus.ValueType(status),
            components=comps,
            sessions=perf.sessions,
            trades=perf.trades,
            updated_at=now_ns(),
            horizon=m.horizon,
        )
        return h, {K_PERF: perf, K_DRIFT: drift, K_EXEC: exe}, dd_p99

    def evaluate(self, sid: str, act: bool = True) -> mp.StrategyHealth:
        with self._lock:
            h, reports, dd_p99 = self.health(sid)
        prev = self.store.get(K_HEALTH, sid, mp.StrategyHealth)
        acts = self.containment.actions(sid, 1)
        if acts:
            h.last_action_id = acts[0].action_id
        self.store.put(K_HEALTH, sid, h, sid)
        for kind, rep in reports.items():
            self.store.put(kind, sid, rep, sid)
        self.publish(f"monitor.strategy.{sid}", h, f"{sid}:{h.updated_at // 60_000_000_000}")
        self._warn(h, prev)
        if prev is None or prev.status != h.status:
            for kind, rep in reports.items():
                self.publish(f"monitor.report.{kind}.{sid}", rep, f"{getattr(rep, 'report_id', sid)}:{h.status}")
        if act and h.mode in ("shadow", "paper", "live"):
            pol, _ = self.containment.policy(sid)
            d = decide(h, pol, dd_p99)
            if d is not None:
                a = self.containment.act(h, d, h.mode)
                if a is not None and a.executed and a.to_mode:
                    self.strategies[sid].mode = a.to_mode
                    h.mode, h.last_action_id = a.to_mode, a.action_id
                    self.store.put(K_HEALTH, sid, h, sid)
        return h

    def _warn(self, h: mp.StrategyHealth, prev: mp.StrategyHealth | None) -> None:
        """MN-031: a component entering ``warning`` raises a warning alert (breaches go through
        containment, which alerts as critical)."""
        before = {c.name: c.status for c in prev.components} if prev is not None else {}
        hot = (mp.HEALTH_STATUS_WARNING, mp.HEALTH_STATUS_BREACH, mp.HEALTH_STATUS_SEVERE)
        for c in h.components:
            if c.status != mp.HEALTH_STATUS_WARNING or before.get(c.name) in hot:
                continue
            title = c.name.replace("_", " ").capitalize()
            runbook = (
                "docs/runbooks/containment.md"
                if c.name in ("drawdown", "performance")
                else "docs/runbooks/model-monitoring.md"
            )
            self.publish(
                "alert.warning",
                alerts_pb2.Alert(
                    alert_id=f"health-{h.strategy_id}-{c.name}-{h.updated_at}",
                    severity=alerts_pb2.SEVERITY_WARNING,
                    source="monitor",
                    subject=f"{title} warning",
                    message=f"{h.strategy_id}: {c.detail} (value {c.value:.4g}, warning {c.warning_threshold:.4g}, "
                    f"breach {c.breach_threshold:.4g}) [evidence {c.evidence_id}]",
                    dedupe_key=f"health-{h.strategy_id}-{c.name}",
                    runbook_url=runbook,
                    occurred_at=h.updated_at,
                    correlation_id=c.evidence_id,
                ),
                f"warn:{h.strategy_id}:{c.name}:{c.evidence_id}",
            )

    def evaluate_all(self, act: bool = True) -> list[mp.StrategyHealth]:
        out = []
        for sid in list(self.strategies):
            try:
                out.append(self.evaluate(sid, act))
            except Exception:
                log.exception("health evaluation failed", extra={"strategy": sid})
        return out

    # ---- graduation evidence (MN-040)

    def graduation(
        self, sid: str, min_sessions: int | None = None, min_trades: int | None = None
    ) -> mp.GraduationEvidence:
        m = self.strategies[sid]
        swing = m.horizon == "swing"
        need_s = min_sessions if min_sessions is not None else (40 if swing else 20)
        need_t = min_trades if min_trades is not None else (30 if swing else 100)
        perf, exe = self.performance(sid), self.execution(sid)
        divs = self.divergence_history(sid)
        rate, gap, n, complete = divergence.window_stats(divs[: max(len(divs), 1)])
        live = np.asarray(perf.session_returns_bps, dtype=np.float64)
        sharpe = float(live.mean() / live.std(ddof=1)) if live.size > 1 and live.std(ddof=1) > 0 else 0.0
        pol, _ = self.containment.policy(sid)
        e = mp.GraduationEvidence(
            strategy_id=sid,
            lineage_id=m.lineage_id,
            model_version=m.model_version,
            mode=m.mode,
            horizon=m.horizon,
            paper_since=self.books[sid].first_seen,
            sessions=perf.sessions,
            trades=perf.trades,
            return_pct=float((np.prod(1 + live / 1e4) - 1) * 100) if live.size else 0.0,
            sharpe=sharpe,
            max_drawdown_pct=perf.max_drawdown_pct,
            drawdown_p95_pct=perf.drawdown_p95_pct,
            match_rate=rate if math.isfinite(rate) else 0.0,
            slippage_gap_bps=gap,
            shortfall_bps=exe.shortfall_bps,
            modeled_shortfall_bps=exe.modeled_shortfall_bps,
            reconciliation_breaks=self.breaks.get(sid, 0),
            critical_alerts=self.critical_alerts.get(sid, 0),
            session_returns_bps=perf.session_returns_bps,
            divergence_report_ids=[r.report_id for r in divs[:60]],
            generated_at=now_ns(),
        )

        def crit(name: str, req: str, act: str, ok: bool, evidence: str = "", only: bool = False) -> None:
            e.criteria.append(
                mp.GraduationCriterion(
                    name=name, required=req, actual=act, passed=ok, evidence_only=only, evidence_id=evidence
                )
            )

        crit("sessions", f">= {need_s}", str(perf.sessions), perf.sessions >= need_s)
        crit("trades", f">= {need_t}", str(perf.trades), perf.trades >= need_t)
        crit(
            "divergence",
            f"match rate >= {pol.match_rate_warning}, slippage gap <= {pol.slippage_gap_warning_bps} bps",
            f"{e.match_rate:.3f}, {gap:+.2f} bps",
            complete and n > 0 and rate >= pol.match_rate_warning and gap <= pol.slippage_gap_warning_bps,
            divs[0].report_id if divs else "",
        )
        crit("reconciliation", "no unexplained breaks", str(e.reconciliation_breaks), e.reconciliation_breaks == 0)
        crit(
            "drawdown",
            "within the backtest p95 over the same horizon",
            f"{e.max_drawdown_pct:.2f}% vs {e.drawdown_p95_pct:.2f}%",
            e.drawdown_p95_pct > 0 and e.max_drawdown_pct <= e.drawdown_p95_pct,
            perf.report_id,
        )
        sf_ok = exe.modeled_shortfall_bps > 0 and exe.shortfall_bps <= 1.5 * exe.modeled_shortfall_bps
        crit(
            "implementation_shortfall",
            "<= 1.5x modeled",
            f"{exe.shortfall_bps:.2f} vs {exe.modeled_shortfall_bps:.2f} bps",
            sf_ok,
            exe.report_id,
        )
        crit("dsr_combined", ">= 0.95 on research + paper (section 15)", "computed by section 15", False, only=True)
        e.criteria_met = all(c.passed for c in e.criteria if not c.evidence_only)
        self.store.put(K_GRAD, sid, e, sid)
        return e
