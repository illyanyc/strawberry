"""Pydantic models for everything an LLM returns (AN-002, AN-010, AN-011, AN-054).

The LLM never returns free-form instructions to the orchestrator: each role has one strict schema
(``extra="forbid"``, bounded lengths, enumerated categories). Anything that does not validate is
rejected before it can influence a decision.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

FindingCategory = Literal[
    "data_quality",
    "leakage_suspicion",
    "overfitting",
    "underfitting",
    "instability",
    "regime_dependence",
    "cost_sensitivity",
    "calibration",
    "drift",
    "execution_gap",
    "no_edge",
]
SeverityName = Literal["info", "low", "medium", "high", "critical"]
ActionCategory = Literal[
    "features", "labels", "model_type", "search_space", "data_window", "universe", "trade_rules", "stop_or_retire"
]
ActionTarget = Literal["training_config", "study_spec", "feature_set", "none"]
# Tools an action may execute; anything else is invalid (AN-011). Forbidden operations are absent.
ActionTool = Literal[
    "launch_training", "launch_ablation", "launch_backtest", "launch_study", "write_code_proposal", "none"
]
Recommendation = Literal["continue", "keep_champion", "promote_within_level", "nominate_live_eligible", "retire"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class EvidenceOut(_Strict):
    report_id: str = Field(min_length=1, max_length=80)
    metric: str = Field(min_length=1, max_length=160)
    value: float
    context: str = Field(default="", max_length=120)


class FindingOut(_Strict):
    category: FindingCategory
    severity: SeverityName
    evidence: list[EvidenceOut] = Field(min_length=1, max_length=8)
    explanation: str = Field(min_length=1, max_length=2000)


class ActionOut(_Strict):
    category: ActionCategory
    description: str = Field(min_length=1, max_length=600)
    target: ActionTarget
    tool: ActionTool
    config_diff: dict[str, Any] = Field(default_factory=dict)
    hypothesis: str = Field(default="", max_length=600)
    expected_effect: str = Field(default="", max_length=400)
    # Expected improvement of the lineage's best deflated Sharpe probability (0..1).
    expected_gain: float = Field(ge=0.0, le=1.0)
    est_compute_hours: float = Field(ge=0.0, le=1000.0)
    trial_cost: int = Field(ge=0, le=500)
    overfit_risk: Literal["low", "medium", "high"]
    reduces_complexity: bool = False
    adds_complexity: bool = False


class PreRegistrationOut(_Strict):
    hypothesis: str = Field(min_length=1, max_length=600)
    metric: str = Field(min_length=1, max_length=80)
    universe: str = Field(min_length=1, max_length=200)
    period: str = Field(min_length=1, max_length=200)
    stop_rule: str = Field(min_length=1, max_length=300)


class PlannerOutput(_Strict):
    findings: list[FindingOut] = Field(default_factory=list, max_length=20)
    no_edge: bool = False
    summary: str = Field(default="", max_length=2000)
    plan: list[ActionOut] = Field(default_factory=list, max_length=8)
    preregistration: PreRegistrationOut | None = None
    reasoning: str = Field(default="", max_length=4000)
    recommendation: Recommendation = "continue"
    # Tool names the planner wanted but did not get; recorded for red-team evidence (AN-032).
    requested_tools: list[str] = Field(default_factory=list, max_length=10)


QuarantineTopic = Literal["data_issue", "regime_change", "cost_change", "model_change", "feature_request", "other"]


class UntrustedSummary(_Strict):
    """The only thing the planner learns from untrusted text (AN-054): enumerated fields, no prose."""

    topics: list[QuarantineTopic] = Field(default_factory=list, max_length=6)
    sentiment: Literal["negative", "neutral", "positive"] = "neutral"
    suspected_injection: bool = False
    mentions_urgency: bool = False


InputSeries = Literal["open", "high", "low", "close", "volume"]


class CandidateFeatureOut(_Strict):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{2,40}$")
    description: str = Field(min_length=1, max_length=500)
    inputs: list[InputSeries] = Field(min_length=1, max_length=5)
    lookback: int = Field(ge=1, le=2000)
    source: str = Field(min_length=1, max_length=20000)


class ChatAnswerOut(_Strict):
    answer: str = Field(min_length=1, max_length=6000)
    cited_ids: list[str] = Field(default_factory=list, max_length=30)
