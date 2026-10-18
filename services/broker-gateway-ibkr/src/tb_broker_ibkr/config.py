"""Settings (``TB_*`` environment variables) and the live-trading gate (OE-010, IB-007)."""

from __future__ import annotations

import re
from datetime import time, timedelta
from typing import Protocol

from pydantic import Field, field_validator

from tb_broker_ibkr.attribution import ATTRIBUTION_MAX_LEN
from tb_broker_ibkr.models import Environment
from tb_broker_ibkr.schedule import MaintenanceSchedule
from tbcore.service import ServiceSettings

_ALIAS = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
_CODE = re.compile(r"^[A-Z]{1,3}[0-9]{5,10}$")


class IbkrSettings(ServiceSettings):
    service: str = "broker-gateway-ibkr"
    ibkr_environment: Environment = Environment.PAPER
    ibkr_host: str = "ib-gateway-paper"
    # gnzsnz image: socat exposes the paper API on 4004 and live on 4003 inside the container network.
    ibkr_port: int = 4004
    # Client 1 is the gateway's master client (TWS_MASTER_CLIENT_ID) so it sees every order (IB-017).
    ibkr_orders_client_id: int = 1
    ibkr_marketdata_client_id: int = 2
    # alias=ACCOUNTCODE pairs, e.g. "bot=DU1234567"; exposed as broker_account_id ibkr-<env>-<alias>.
    ibkr_accounts: str = ""
    ibkr_order_ref_max_len: int = Field(default=ATTRIBUTION_MAX_LEN, ge=20, le=255)
    ibkr_submit_timeout: float = Field(default=5.0, gt=0)
    ibkr_commission_wait: float = Field(default=2.0, ge=0)
    ibkr_snapshot_interval: float = Field(default=30.0, gt=0)
    ibkr_daily_restart: str = "23:45"
    ibkr_cold_restart: str = "17:00"
    ibkr_reauth_grace_seconds: int = Field(default=180, ge=30)
    ibkr_state_dir: str = "/var/lib/broker-gateway-ibkr"
    ibkr_instruments_path: str = ""
    ibkr_record_path: str = ""
    ibkr_connect_timeout: float = Field(default=10.0, gt=0)
    trading_live_enabled: bool = False
    nats_url: str = "nats://nats:4222"
    nats_user: str = ""
    grpc_addr: str = "0.0.0.0:9090"
    # Callers whose service-token public keys are trusted (svc_<caller>_ed25519.pub, SE-040).
    grpc_callers: str = "execution,ledger,marketdata,api-gateway"
    db_host: str = "postgres"
    db_port: int = 5432
    db_name: str = "platform"
    # Empty: no audit log, so every order request fails closed (SE-021).
    db_user: str = ""

    def db_dsn(self, password: str) -> str:
        return f"postgresql://{self.db_user}:{password}@{self.db_host}:{self.db_port}/{self.db_name}"

    @field_validator("ibkr_daily_restart", "ibkr_cold_restart")
    @classmethod
    def _hhmm(cls, value: str) -> str:
        parse_hhmm(value)
        return value

    def account_map(self) -> dict[str, str]:
        return parse_accounts(self.ibkr_accounts, self.ibkr_environment)

    def schedule(self) -> MaintenanceSchedule:
        return MaintenanceSchedule(
            daily_restart=parse_hhmm(self.ibkr_daily_restart),
            cold_restart=parse_hhmm(self.ibkr_cold_restart),
            grace=timedelta(seconds=self.ibkr_reauth_grace_seconds),
        )


def parse_hhmm(value: str) -> time:
    m = re.fullmatch(r"([01][0-9]|2[0-3]):([0-5][0-9])", value)
    if not m:
        raise ValueError(f"expected HH:MM (24h, US/Eastern), got {value!r}")
    return time(int(m[1]), int(m[2]))


def parse_accounts(raw: str, environment: Environment) -> dict[str, str]:
    """``bot=DU1234567,main=DU7654321`` -> ``{"ibkr-paper-bot": "DU1234567", ...}`` (IB-005, IB-006)."""
    out: dict[str, str] = {}
    for item in filter(None, (part.strip() for part in raw.split(","))):
        alias, sep, code = item.partition("=")
        alias, code = alias.strip(), code.strip()
        if not sep or not _ALIAS.match(alias) or not _CODE.match(code):
            raise ValueError("TB_IBKR_ACCOUNTS entries must look like alias=ACCOUNTCODE")
        acct = f"ibkr-{environment.value}-{alias}"
        if acct in out or code in out.values():
            raise ValueError(f"duplicate IBKR account mapping for {alias!r}")
        out[acct] = code
    if not out:
        raise ValueError("TB_IBKR_ACCOUNTS is empty: map at least one account")
    return out


class LiveEnablement(Protocol):
    """Source of audited per-account live enablement (owner action with step-up, SM-004)."""

    def is_live_enabled(self, broker_account_id: str) -> bool: ...

    def orders_enabled(self, broker_account_id: str) -> bool: ...


class NoLiveEnablement:
    """Phase 1 default: the audited enablement flow (section 15) does not exist yet, so nothing is enabled."""

    def is_live_enabled(self, broker_account_id: str) -> bool:
        return False

    def orders_enabled(self, broker_account_id: str) -> bool:
        return False


def live_gate(settings: IbkrSettings, enablement: LiveEnablement, accounts: dict[str, str]) -> str | None:
    """OE-010 / IB-007: return why a live adapter must not start, or None if it may."""
    if settings.ibkr_environment is not Environment.LIVE:
        return None
    if not settings.trading_live_enabled:
        return "TB_TRADING_LIVE_ENABLED is not true"
    disabled = [a for a in accounts if not enablement.is_live_enabled(a)]
    if disabled:
        return f"accounts not live-enabled by an audited owner action: {', '.join(sorted(disabled))}"
    return None
