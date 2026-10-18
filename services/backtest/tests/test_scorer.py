"""D31 promotion scorer (BT-080)."""

from __future__ import annotations

import dataclasses

import numpy as np
from tb_backtest.scorer import AnalystView, CandidateEvidence, GatePolicy, analyst_view, score


def evidence(edge: float, n_trials: int = 20, champion: bool = False, **kw: object) -> CandidateEvidence:
    rng = np.random.default_rng(4)
    trials = rng.normal(0, 0.01, (1000, n_trials))
    trials[:, 0] += edge
    champ = rng.normal(0, 0.01, 1000) if champion else None
    base: dict[str, object] = {"trades": 500, "net_pnl_2x": 1000.0, "max_drawdown": 0.1}
    base.update(kw)
    return CandidateEvidence("c1", trials[:, 0], trials, champion_returns=champ, **base)  # type: ignore[arg-type]


def test_bt080_real_edge_passes_and_noise_fails() -> None:
    good = score(evidence(0.004, champion=True))
    assert good.passed and good.dsr >= 0.95 and good.reasons == ()
    noise = score(evidence(0.0))
    assert not noise.passed and "dsr" in noise.reasons


def test_bt080_each_constraint_can_fail() -> None:
    assert score(evidence(0.004, net_pnl_2x=-1.0)).reasons == ("cost_stress",)
    assert score(evidence(0.004, trades=5)).reasons == ("trades",)
    assert score(evidence(0.004, max_drawdown=0.5)).reasons == ("drawdown",)
    strict = GatePolicy(dsr_min=0.9999999)
    assert "dsr" in score(evidence(0.0005), strict).reasons


def test_bt080_analyst_sees_only_pass_fail_and_dsr() -> None:
    v = score(evidence(0.004))
    view = analyst_view(v)
    assert isinstance(view, AnalystView)
    assert [f.name for f in dataclasses.fields(view)] == ["candidate_id", "passed", "dsr"]
    assert view.passed == v.passed
