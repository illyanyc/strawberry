"""Backtest configuration: fully serializable; its hash is the run identity (BT-001, N5)."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from tbcore.backtest.costs import CostModelConfig

Survivorship = Literal["complete", "incomplete", "unknown"]


class StrategySpec(BaseModel):
    """One strategy as a registry entry (the execution seed format, ``infra/compose/strategies.json``)
    plus its runtime block; the same document configures the simcore and the strategy host."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    strategy_id: str
    short_id: str
    name: str = ""
    horizon: Literal["intraday", "swing"] = "intraday"
    broker_account_id: str = "sim-paper"
    allocation: Decimal
    allow_short: bool = False
    segregation_allowlist: tuple[str, ...] = ()
    version: str = "1"
    runtime_class: str
    timeframe: str = "15m"
    instruments: tuple[str, ...]
    params: dict[str, Any] = Field(default_factory=dict)
    # Strategy-scope risk limits (execution registry keys, RM-003).
    limits: dict[str, str] = Field(default_factory=dict)
    # Event blackout around scheduled releases (CX-016), as in the registry's runtime block.
    blackout: dict[str, Any] | None = None

    def registry_entry(self, code_commit: str = "") -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "short_id": self.short_id,
            "name": self.name or self.strategy_id,
            "horizon": self.horizon,
            "broker_account_id": self.broker_account_id,
            "mode": "paper",
            "allocation": str(self.allocation),
            "allow_short": self.allow_short,
            "segregation_allowlist": list(self.segregation_allowlist),
            "version": self.version,
            "code_commit": code_commit,
            "params_hash": hashlib.sha256(json.dumps(self.params, sort_keys=True, default=str).encode()).hexdigest()[
                :16
            ],
            "runtime": {
                "class": self.runtime_class,
                "timeframe": self.timeframe,
                "instruments": list(self.instruments),
                "params": self.params,
                **({"blackout": self.blackout} if self.blackout else {}),
            },
            "limits": dict(self.limits),
        }


class UniverseRef(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    instruments: tuple[str, ...]
    symbols: dict[str, str] = Field(default_factory=dict)
    point_in_time: bool = True
    # Reports carry this label; anything but "complete" is warned (U5, O3, BT-005).
    survivorship: Survivorship = "unknown"


class FillModelConfig(BaseModel):
    """Fill model parameters (ADR-0029, BT-020..BT-024)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # "recorded" replays executions from a bus recording (parity, BT-070).
    mode: Literal["model", "recorded"] = "model"
    latency_ms: int = Field(default=250, ge=0)
    # Bars used to simulate executions; None = the decision timeframe.
    fill_timeframe: str | None = None
    participation_cap: float = Field(default=0.05, gt=0, le=1)
    limit_through_ticks: int = Field(default=1, ge=0)
    limit_queue_haircut: float = Field(default=0.5, gt=0, le=1)
    tick: float = Field(default=0.01, gt=0)
    allow_extended_hours: bool = False
    delisting_haircut: float = Field(default=0.0, ge=0, le=1)
    # Trailing sessions for ADV and daily volatility (impact inputs, BT-012).
    impact_lookback_sessions: int = Field(default=20, ge=2)


class ManualPosition(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    instrument_id: str
    qty: Decimal
    avg_cost: Decimal


class HaltWindow(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    instrument_id: str
    start: int
    end: int


class AccountSimConfig(BaseModel):
    """The simulated broker account (D30 policy, manual bucket, borrow snapshot, halts)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    broker_account_id: str = "sim-paper"
    broker: Literal["alpaca", "ibkr", "simulator"] = "simulator"
    cash: Decimal
    supports_bracket: bool = True
    margin_multiple: Decimal = Decimal(1)
    margin_util_cap: Decimal = Decimal(1)
    pdt_guard: Literal["off", "legacy"] = "off"
    segregation: bool = True
    manual_positions: tuple[ManualPosition, ...] = ()
    # Execution's intraday flatten before the close (OE-016, TB_INTRADAY_FLATTEN_BEFORE_CLOSE).
    flatten_before_close_s: int = Field(default=600, ge=0)
    shortable: tuple[str, ...] = ()
    # Annual borrow fee in bps per instrument; missing instruments use the cost model's default (BT-014).
    borrow_fee_bps: dict[str, float] = Field(default_factory=dict)
    # A dedicated strategy account (D19): strategies with allow_short may short here (VP-005).
    dedicated: bool = False
    halts: tuple[HaltWindow, ...] = ()


class BacktestConfig(BaseModel):
    """Everything a run depends on besides code and data files (BT-001)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    strategies: tuple[StrategySpec, ...]
    model_version: str | None = None
    universe: UniverseRef
    timeframe: str = "15m"
    start: datetime
    end: datetime
    data_snapshot_id: str = ""
    cost_model: CostModelConfig = Field(default_factory=CostModelConfig)
    fill_model: FillModelConfig = Field(default_factory=FillModelConfig)
    # Global pre-trade limits, same schema as live (tb.orders.v1.RiskLimits field names); empty =
    # the OMS defaults (RM-003).
    risk_limits: dict[str, str] = Field(default_factory=dict)
    account: AccountSimConfig
    seed: int = 0
    min_trades: int = Field(default=30, ge=0)
    code_commit: str = ""

    @field_validator("strategies")
    @classmethod
    def _nonempty(cls, v: tuple[StrategySpec, ...]) -> tuple[StrategySpec, ...]:
        if not v:
            raise ValueError("at least one strategy is required")
        if len({s.short_id for s in v}) != len(v):
            raise ValueError("strategy short IDs must be unique")
        return v

    def canonical_json(self) -> str:
        return json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))

    @property
    def config_hash(self) -> str:
        """SHA-256 of the canonical JSON: the run identity (BT-001)."""
        return hashlib.sha256(self.canonical_json().encode()).hexdigest()

    def registry(self) -> dict[str, Any]:
        return {"strategies": [s.registry_entry(self.code_commit) for s in self.strategies]}

    def registry_json(self) -> bytes:
        return json.dumps(self.registry(), sort_keys=True).encode()
