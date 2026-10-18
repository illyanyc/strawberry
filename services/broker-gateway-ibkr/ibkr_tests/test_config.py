"""Settings and the live gate: OE-010, IB-005, IB-006, IB-007; ops surface OP-010."""

from __future__ import annotations

from datetime import time

import pytest
from fastapi.testclient import TestClient
from prometheus_client import CollectorRegistry
from tb_broker_ibkr.config import IbkrSettings, NoLiveEnablement, live_gate, parse_accounts, parse_hhmm
from tb_broker_ibkr.metrics import PrometheusMetrics
from tb_broker_ibkr.models import Environment, SessionState

from tbcore.service import create_ops_app


class Enabled:
    def __init__(self, *accounts: str) -> None:
        self._accounts = set(accounts)

    def is_live_enabled(self, broker_account_id: str) -> bool:
        return broker_account_id in self._accounts

    def orders_enabled(self, broker_account_id: str) -> bool:
        return False


def settings(**env: object) -> IbkrSettings:
    return IbkrSettings(ibkr_accounts="bot=U0000001", **env)  # type: ignore[arg-type]


def test_ib005_account_map_parsing() -> None:
    assert parse_accounts("bot=DU0000001, main=DU0000002", Environment.PAPER) == {
        "ibkr-paper-bot": "DU0000001",
        "ibkr-paper-main": "DU0000002",
    }


@pytest.mark.parametrize(
    "raw", ["", "bot", "bot=", "=DU0000001", "Bot=DU0000001", "bot=du1", "a=DU0000001,a=DU0000002"]
)
def test_ib005_bad_account_maps_rejected(raw: str) -> None:
    with pytest.raises(ValueError):
        parse_accounts(raw, Environment.PAPER)


def test_schedule_from_settings() -> None:
    s = settings(ibkr_daily_restart="23:50", ibkr_cold_restart="18:30", ibkr_reauth_grace_seconds=240)
    sched = s.schedule()
    assert (sched.daily_restart, sched.cold_restart, sched.grace.total_seconds()) == (time(23, 50), time(18, 30), 240)
    with pytest.raises(ValueError):
        parse_hhmm("5pm")


def test_ib007_paper_is_never_gated() -> None:
    s = IbkrSettings(ibkr_accounts="bot=DU0000001")
    assert live_gate(s, NoLiveEnablement(), s.account_map()) is None


@pytest.mark.parametrize(
    ("flag", "enabled", "blocked"),
    [
        (False, Enabled("ibkr-live-bot"), "TB_TRADING_LIVE_ENABLED"),
        (True, NoLiveEnablement(), "audited owner action"),
        (True, Enabled("ibkr-live-other"), "audited owner action"),
        (False, NoLiveEnablement(), "TB_TRADING_LIVE_ENABLED"),
    ],
)
def test_oe010_live_needs_flag_and_audited_enablement(flag: bool, enabled: object, blocked: str) -> None:
    s = settings(ibkr_environment=Environment.LIVE, trading_live_enabled=flag)
    reason = live_gate(s, enabled, s.account_map())  # type: ignore[arg-type]
    assert reason is not None and blocked in reason


def test_oe010_live_starts_with_both() -> None:
    s = settings(ibkr_environment=Environment.LIVE, trading_live_enabled=True)
    assert live_gate(s, Enabled("ibkr-live-bot"), s.account_map()) is None


def test_op010_ops_surface_exports_ibkr_metrics() -> None:
    registry = CollectorRegistry()
    metrics = PrometheusMetrics(registry, "paper")
    metrics.set_state("orders", SessionState.READY)
    metrics.set_reauth_required("orders", True)
    app = create_ops_app(IbkrSettings(ibkr_accounts="bot=DU0000001"), registry=registry)
    body = TestClient(app).get("/metrics").text
    assert 'tb_ibkr_session_ready{connection="orders",environment="paper"} 1.0' in body
    assert 'tb_ibkr_reauth_required{connection="orders",environment="paper"} 1.0' in body
    assert "tb_build_info" in body
