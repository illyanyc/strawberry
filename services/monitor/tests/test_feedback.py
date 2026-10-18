"""Section 13 analyst feedback slice (MN-020..MN-026): LiveFeedbackReport golden tests on the recorded
paper ORB sessions, injected decay and drift, minimum-sample rules, and trigger routing."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest
from tb_monitor import feedback as fb
from tb_monitor.feedback_runner import FeedbackRunner, load_config

from tb.alerts.v1 import alerts_pb2
from tb.analyst.v1 import analyst_pb2
from tb.monitor.v1 import feedback_pb2
from tbcore.backtest.recording import Recording

ROOT = Path(__file__).resolve().parents[3]
RECORDING = ROOT / "tests" / "fixtures" / "parity" / "orb-paper-gate.tbrec"
NOW = datetime(2026, 10, 19, 21, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def trips() -> list[tuple[int, float]]:
    return fb.round_trips(Recording.load(RECORDING).executions, "orb15aa")


def _window(returns: list[float], sessions: int, **kw: object) -> fb.LiveWindow:
    return fb.LiveWindow(
        strategy_id="orb-15m",
        lineage_id="orb-lineage",
        model_version="orb@1",
        mode="paper",
        window_start=1,
        window_end=2,
        trade_returns_bps=returns,
        sessions=sessions,
        **kw,  # type: ignore[arg-type]
    )


def test_mn020_round_trips_from_the_recorded_paper_sessions(trips: list[tuple[int, float]]) -> None:
    # Five recorded ORB sessions: every entry closed by its stop, target, or the end-of-day exit.
    assert len(trips) == 19
    assert all(np.isfinite(r) for _, r in trips)
    # Manual-owner executions in the same recording are never counted.
    assert all(t > 0 for t, _ in trips)


def test_mn020_golden_report_on_recorded_sessions_is_within_expectation(trips: list[tuple[int, float]]) -> None:
    rets = [r for _, r in trips]
    exp = fb.expectation_from(rets, report_id="bt-orb-paper-gate")
    rep = fb.build_report(
        _window(rets, 5, session_returns_bps=fb.session_returns(trips)), exp, fb.FeedbackConfig(min_trades=10), NOW
    )
    assert rep.report_id.startswith("fb-") and rep.trades == 19 and rep.sessions == 5
    assert rep.sample_sufficient
    assert not rep.decay.breached and not rep.triggered
    mt = next(m for m in rep.metrics if m.name == "mean_trade_bps")
    assert mt.expected_p10 <= mt.live_value <= mt.expected_p90
    assert fb.retrain_suggested(rep, NOW) is None
    # Deterministic: same inputs, same report.
    again = fb.build_report(
        _window(rets, 5, session_returns_bps=fb.session_returns(trips)), exp, fb.FeedbackConfig(min_trades=10), NOW
    )
    assert again.SerializeToString(deterministic=True) == rep.SerializeToString(deterministic=True)


def test_mn023_injected_decay_triggers_retrain_suggested(trips: list[tuple[int, float]]) -> None:
    rets = [r for _, r in trips]
    exp = fb.expectation_from(rets)
    decayed = [r - 2.5 * exp.trade_sd_bps for r in rets] * 2  # 38 trades, every one far below expectation
    rep = fb.build_report(_window(decayed, 10), exp, fb.FeedbackConfig(), NOW)
    assert rep.sample_sufficient and rep.decay.breached
    names = {c.condition for c in rep.triggered}
    assert {"decay", "below_p10"} <= names
    ev = fb.retrain_suggested(rep, NOW)
    assert ev is not None and ev.severity == "critical" and ev.report_id == rep.report_id
    assert fb.subjects_for(ev) == ["monitor.retrain_suggested.orb-lineage", "monitor.decay.orb-lineage"]


def test_mn024_below_minimum_samples_no_trigger_fires(trips: list[tuple[int, float]]) -> None:
    rets = [r for _, r in trips]
    exp = fb.expectation_from(rets)
    decayed = [r - 3 * exp.trade_sd_bps for r in rets]  # 19 trades < default minimum 30
    rep = fb.build_report(_window(decayed, 5), exp, fb.FeedbackConfig(), NOW)
    assert rep.decay.breached  # the statistic is still reported
    assert not rep.sample_sufficient and not rep.triggered
    assert fb.retrain_suggested(rep, NOW) is None


def test_mn023_feature_drift_on_a_top_feature_triggers_and_low_rank_does_not() -> None:
    rng = np.random.default_rng(3)
    ref = rng.normal(0, 1, 5000)
    shifted = rng.normal(1.0, 1, 2000)
    w = _window(
        [1.0] * 40,
        10,
        live_features={"rvol": shifted, "gap": shifted},
        reference_features={"rvol": ref, "gap": ref},
        importance_rank={"rvol": 1, "gap": 12},
    )
    exp = fb.Expectation(trade_mean_bps=1.0, trade_sd_bps=1.0)
    rep = fb.build_report(w, exp, fb.FeedbackConfig(), NOW)
    drift = [c for c in rep.triggered if c.condition == "feature_drift"]
    assert len(drift) == 1 and "rvol" in drift[0].detail
    assert rep.feature_drift[0].psi > 0.25 and rep.feature_drift[0].ks > 0.2
    ev = fb.retrain_suggested(rep, NOW)
    assert ev is not None and ev.severity == "warning"
    assert "monitor.drift.orb-lineage" in fb.subjects_for(ev)


def test_mn023_calibration_needs_enough_resolved_predictions() -> None:
    rng = np.random.default_rng(5)
    probs = rng.uniform(0.6, 0.9, 300)
    outcomes = (rng.uniform(0, 1, 300) < 0.3).astype(int)  # badly calibrated
    exp = fb.Expectation(trade_mean_bps=1.0, trade_sd_bps=1.0)
    rep = fb.build_report(
        _window([1.0] * 40, 10, resolved_probs=probs, resolved_outcomes=outcomes), exp, fb.FeedbackConfig(), NOW
    )
    assert any(c.condition == "calibration" for c in rep.triggered)
    few = fb.build_report(
        _window([1.0] * 40, 10, resolved_probs=probs[:50], resolved_outcomes=outcomes[:50]),
        exp,
        fb.FeedbackConfig(),
        NOW,
    )
    assert not few.sample_sufficient and not few.triggered


def test_mn023_divergence_and_unseen_regime() -> None:
    exp = fb.Expectation(trade_mean_bps=1.0, trade_sd_bps=1.0, regime_shares={"low_vol": 0.97, "high_vol": 0.03})
    par = feedback_pb2.ParitySummary(trade_match_rate=0.7, missed_trades=4, extra_trades=2)
    rep = fb.build_report(_window([1.0] * 40, 10, parity=par, current_regime="high_vol"), exp, fb.FeedbackConfig(), NOW)
    names = {c.condition for c in rep.triggered}
    assert {"divergence", "unseen_regime"} <= names
    assert not rep.regime.seen_in_training


def test_mn021_report_has_no_account_balance_or_quantity_fields(trips: list[tuple[int, float]]) -> None:
    rep = fb.build_report(
        _window([r for _, r in trips], 5), fb.expectation_from([r for _, r in trips]), fb.FeedbackConfig(), NOW
    )
    text = str(rep)
    assert "sim-paper" not in text and "account" not in text.lower()
    assert "qty" not in text and "100" not in text.split("trades")[0]


def test_statistics_sanity() -> None:
    rng = np.random.default_rng(1)
    a = rng.normal(0, 1, 4000)
    assert fb.psi(a, rng.normal(0, 1, 4000)) < 0.02 and fb.psi(a, rng.normal(2, 1, 4000)) > 1.0
    assert fb.ks(a, a) == 0.0
    stat, breached = fb.cusum_lower([0.0] * 50, 0.0, 1.0, 0.5, 5.0)
    assert stat == 0.0 and not breached
    assert fb.cusum_lower([-2.0] * 10, 0.0, 1.0, 0.5, 5.0)[1]
    brier, ece = fb.calibration([0.5] * 100, [0, 1] * 50)
    assert brier == pytest.approx(0.25) and ece == pytest.approx(0.0)


class _Pub:
    def __init__(self) -> None:
        self.out: list[tuple[str, object, str]] = []

    async def __call__(self, subject: str, msg: object, msg_id: str) -> None:
        self.out.append((subject, msg, msg_id))


def _runner(tmp_path: Path, expectation: dict[str, object], min_trades: int) -> tuple[FeedbackRunner, _Pub]:
    cfg = tmp_path / "feedback.json"
    cfg.write_text(
        json.dumps(
            [
                {
                    "strategy_id": "orb-15m",
                    "short_id": "orb15aa",
                    "lineage_id": "orb-lineage",
                    "mode": "paper",
                    "model_version": "orb@1",
                    "expectation": expectation,
                }
            ]
        )
    )
    pub = _Pub()
    runner = FeedbackRunner(load_config(str(cfg)), fb.FeedbackConfig(min_trades=min_trades), pub)
    for x in Recording.load(RECORDING).executions:
        runner.on_execution(x)
    return runner, pub


def test_mn022_runner_publishes_report_and_no_trigger_within_expectation(
    tmp_path: Path, trips: list[tuple[int, float]]
) -> None:
    rets = [r for _, r in trips]
    exp = fb.expectation_from(rets)
    runner, pub = _runner(
        tmp_path,
        {"trade_mean_bps": exp.trade_mean_bps, "trade_sd_bps": exp.trade_sd_bps, "distributions": exp.distributions},
        min_trades=10,
    )
    asyncio.run(runner.run(NOW))
    assert [s for s, _, _ in pub.out] == ["monitor.feedback.orb-15m"]


def test_mn025_injected_decay_publishes_trigger_and_lowers_autonomy(
    tmp_path: Path, trips: list[tuple[int, float]]
) -> None:
    rets = [r for _, r in trips]
    exp = fb.expectation_from(rets)
    # Expectation far above what the recorded sessions delivered: the same as decay in live trading.
    high = {
        "trade_mean_bps": exp.trade_mean_bps + 3 * exp.trade_sd_bps,
        "trade_sd_bps": exp.trade_sd_bps,
        "distributions": {"mean_trade_bps": [m + 3 * exp.trade_sd_bps for m in exp.distributions["mean_trade_bps"]]},
    }
    runner, pub = _runner(tmp_path, high, min_trades=10)
    asyncio.run(runner.run(NOW))
    subjects = [s for s, _, _ in pub.out]
    assert subjects[0] == "monitor.feedback.orb-15m"
    assert "monitor.retrain_suggested.orb-lineage" in subjects and "monitor.decay.orb-lineage" in subjects
    assert "analyst.autonomy.lower" in subjects and "alert.critical" in subjects
    lower = next(m for s, m, _ in pub.out if s == "analyst.autonomy.lower")
    assert (
        isinstance(lower, analyst_pb2.LowerAutonomy) and lower.level == 0 and lower.pause and lower.actor == "monitor"
    )
    # Below the minimum sample size the same decay publishes only the report.
    runner2, pub2 = _runner(tmp_path, high, min_trades=30)
    asyncio.run(runner2.run(NOW))
    assert [s for s, _, _ in pub2.out] == ["monitor.feedback.orb-15m"]


def test_mn025_critical_alert_naming_a_strategy_lowers_once(tmp_path: Path) -> None:
    runner, pub = _runner(tmp_path, {"trade_mean_bps": 1, "trade_sd_bps": 1}, min_trades=10)
    a = alerts_pb2.Alert(
        alert_id="a1", severity=alerts_pb2.SEVERITY_CRITICAL, subject="kill switch strategy orb-15m latched"
    )
    asyncio.run(runner.on_alert(a, NOW))
    asyncio.run(runner.on_alert(a, NOW))
    asyncio.run(
        runner.on_alert(
            alerts_pb2.Alert(alert_id="a2", severity=alerts_pb2.SEVERITY_WARNING, subject="orb-15m slow"), NOW
        )
    )
    assert [s for s, _, _ in pub.out].count("analyst.autonomy.lower") == 1
