"""Gate 3 parity: the event-driven replay of the ORB paper stream matches paper (BT-070, BT-071).

The fixture is the bus recording of the Phase 2 paper gate (``make paper-gate`` with
``TB_PAPER_RECORDING``): five sessions of the ORB baseline through the real strategy runtime, OMS,
simulated broker, and ledger. ``make parity-gate`` regenerates it in CI and reruns these checks.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from tbcore.backtest.config import BacktestConfig
from tbcore.backtest.recording import Recording
from tbcore.parity import compare_recording, main

FIXTURES = Path(__file__).resolve().parents[4] / "tests" / "fixtures" / "parity"
RECORDING = FIXTURES / "orb-paper-gate.tbrec"
CONFIG = FIXTURES / "orb-paper-gate.config.json"


@pytest.fixture(scope="module")
def recording() -> Recording:
    return Recording.load(RECORDING)


def config(mode: str) -> BacktestConfig:
    cfg = BacktestConfig.model_validate_json(CONFIG.read_text(encoding="utf-8"))
    return cfg.model_copy(update={"fill_model": cfg.fill_model.model_copy(update={"mode": mode})})


def test_bt070_recorded_fills_reproduce_paper_exactly(recording: Recording) -> None:
    rep = compare_recording(recording, config("recorded"))
    assert rep.passed, rep.to_json()
    assert rep.intents_paper == rep.intents_backtest == len(rep.matched_intents) == 25  # 19 entries, 6 strategy exits
    assert not (rep.intent_mismatches or rep.decision_mismatches or rep.missed_legs or rep.extra_legs)
    assert Decimal(rep.paper_realized) == Decimal(rep.backtest_realized) == Decimal("1300.20")
    assert all(
        Decimal(leg["paper_qty"]) == Decimal(leg["backtest_qty"]) and leg["slippage_bps"] == 0 for leg in rep.legs
    )


def test_bt071_modeled_fills_match_trade_for_trade_and_pnl_is_explained(recording: Recording) -> None:
    rep = compare_recording(recording, config("model"))
    assert rep.passed, rep.to_json()
    assert len(rep.matched_intents) == 25 and not (rep.missed_legs or rep.extra_legs)
    diff = Decimal(rep.pnl_difference)
    explained = Decimal(rep.price_effect) + Decimal(rep.size_effect) + Decimal(rep.fee_effect)
    assert abs(diff - explained) <= Decimal(rep.tolerance)
    assert diff > 0, "the model must be more pessimistic than the paper simulator (next-bar fills, slippage, fees)"
    assert any(leg["slippage_bps"] for leg in rep.legs)


def test_bt071_a_different_strategy_version_fails_parity(recording: Recording) -> None:
    cfg = config("recorded")
    spec = cfg.strategies[0]
    changed = spec.model_copy(update={"params": {**spec.params, "target_r": "3"}})
    rep = compare_recording(recording, cfg.model_copy(update={"strategies": (changed,)}))
    assert not rep.passed
    assert rep.intent_mismatches and any("take_profit" in m for m in rep.intent_mismatches)


def test_bt070_cli_exit_code(tmp_path: Path) -> None:
    out = tmp_path / "parity.json"
    assert main([str(RECORDING), "--config", str(CONFIG), "--mode", "recorded", "--out", str(out)]) == 0
    assert '"passed": true' in out.read_text()
