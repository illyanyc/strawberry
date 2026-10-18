from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class RoundTrigger(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    ROUND_TRIGGER_UNSPECIFIED: _ClassVar[RoundTrigger]
    ROUND_TRIGGER_RUN_COMPLETED: _ClassVar[RoundTrigger]
    ROUND_TRIGGER_BACKTEST_COMPLETED: _ClassVar[RoundTrigger]
    ROUND_TRIGGER_STUDY_COMPLETED: _ClassVar[RoundTrigger]
    ROUND_TRIGGER_LIVE_FEEDBACK: _ClassVar[RoundTrigger]
    ROUND_TRIGGER_SCHEDULE: _ClassVar[RoundTrigger]
    ROUND_TRIGGER_OWNER_REQUEST: _ClassVar[RoundTrigger]
    ROUND_TRIGGER_CONTINUATION: _ClassVar[RoundTrigger]

class RoundDecision(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    ROUND_DECISION_UNSPECIFIED: _ClassVar[RoundDecision]
    ROUND_DECISION_CONTINUE: _ClassVar[RoundDecision]
    ROUND_DECISION_AWAITING_APPROVAL: _ClassVar[RoundDecision]
    ROUND_DECISION_PLATEAU: _ClassVar[RoundDecision]
    ROUND_DECISION_STOP_NO_EDGE: _ClassVar[RoundDecision]
    ROUND_DECISION_STOP_BUDGET: _ClassVar[RoundDecision]
    ROUND_DECISION_STOP_UNREACHABLE: _ClassVar[RoundDecision]
    ROUND_DECISION_STOP_FAILURES: _ClassVar[RoundDecision]
    ROUND_DECISION_STOP_PAUSED: _ClassVar[RoundDecision]
    ROUND_DECISION_ESCALATE_TO_OWNER: _ClassVar[RoundDecision]

class FindingCategory(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    FINDING_CATEGORY_UNSPECIFIED: _ClassVar[FindingCategory]
    FINDING_CATEGORY_DATA_QUALITY: _ClassVar[FindingCategory]
    FINDING_CATEGORY_LEAKAGE_SUSPICION: _ClassVar[FindingCategory]
    FINDING_CATEGORY_OVERFITTING: _ClassVar[FindingCategory]
    FINDING_CATEGORY_UNDERFITTING: _ClassVar[FindingCategory]
    FINDING_CATEGORY_INSTABILITY: _ClassVar[FindingCategory]
    FINDING_CATEGORY_REGIME_DEPENDENCE: _ClassVar[FindingCategory]
    FINDING_CATEGORY_COST_SENSITIVITY: _ClassVar[FindingCategory]
    FINDING_CATEGORY_CALIBRATION: _ClassVar[FindingCategory]
    FINDING_CATEGORY_DRIFT: _ClassVar[FindingCategory]
    FINDING_CATEGORY_EXECUTION_GAP: _ClassVar[FindingCategory]
    FINDING_CATEGORY_NO_EDGE: _ClassVar[FindingCategory]

class Severity(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    SEVERITY_UNSPECIFIED: _ClassVar[Severity]
    SEVERITY_INFO: _ClassVar[Severity]
    SEVERITY_LOW: _ClassVar[Severity]
    SEVERITY_MEDIUM: _ClassVar[Severity]
    SEVERITY_HIGH: _ClassVar[Severity]
    SEVERITY_CRITICAL: _ClassVar[Severity]

class ActionCategory(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    ACTION_CATEGORY_UNSPECIFIED: _ClassVar[ActionCategory]
    ACTION_CATEGORY_FEATURES: _ClassVar[ActionCategory]
    ACTION_CATEGORY_LABELS: _ClassVar[ActionCategory]
    ACTION_CATEGORY_MODEL_TYPE: _ClassVar[ActionCategory]
    ACTION_CATEGORY_SEARCH_SPACE: _ClassVar[ActionCategory]
    ACTION_CATEGORY_DATA_WINDOW: _ClassVar[ActionCategory]
    ACTION_CATEGORY_UNIVERSE: _ClassVar[ActionCategory]
    ACTION_CATEGORY_TRADE_RULES: _ClassVar[ActionCategory]
    ACTION_CATEGORY_STOP_OR_RETIRE: _ClassVar[ActionCategory]

class ActionStatus(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    ACTION_STATUS_UNSPECIFIED: _ClassVar[ActionStatus]
    ACTION_STATUS_PROPOSED: _ClassVar[ActionStatus]
    ACTION_STATUS_APPROVED: _ClassVar[ActionStatus]
    ACTION_STATUS_REJECTED: _ClassVar[ActionStatus]
    ACTION_STATUS_LAUNCHED: _ClassVar[ActionStatus]
    ACTION_STATUS_COMPLETED: _ClassVar[ActionStatus]
    ACTION_STATUS_FAILED: _ClassVar[ActionStatus]
    ACTION_STATUS_SKIPPED: _ClassVar[ActionStatus]

class OverfitRisk(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    OVERFIT_RISK_UNSPECIFIED: _ClassVar[OverfitRisk]
    OVERFIT_RISK_LOW: _ClassVar[OverfitRisk]
    OVERFIT_RISK_MEDIUM: _ClassVar[OverfitRisk]
    OVERFIT_RISK_HIGH: _ClassVar[OverfitRisk]

class LoopState(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    LOOP_STATE_UNSPECIFIED: _ClassVar[LoopState]
    LOOP_STATE_IDLE: _ClassVar[LoopState]
    LOOP_STATE_RUNNING: _ClassVar[LoopState]
    LOOP_STATE_AWAITING_APPROVAL: _ClassVar[LoopState]
    LOOP_STATE_AWAITING_RESULTS: _ClassVar[LoopState]
    LOOP_STATE_PAUSED: _ClassVar[LoopState]
    LOOP_STATE_STOPPED: _ClassVar[LoopState]

class CodeProposalStatus(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    CODE_PROPOSAL_STATUS_UNSPECIFIED: _ClassVar[CodeProposalStatus]
    CODE_PROPOSAL_STATUS_PENDING_CHECKS: _ClassVar[CodeProposalStatus]
    CODE_PROPOSAL_STATUS_CHECKS_FAILED: _ClassVar[CodeProposalStatus]
    CODE_PROPOSAL_STATUS_AWAITING_REVIEW: _ClassVar[CodeProposalStatus]
    CODE_PROPOSAL_STATUS_APPROVED_FOR_MERGE: _ClassVar[CodeProposalStatus]
    CODE_PROPOSAL_STATUS_REJECTED: _ClassVar[CodeProposalStatus]
ROUND_TRIGGER_UNSPECIFIED: RoundTrigger
ROUND_TRIGGER_RUN_COMPLETED: RoundTrigger
ROUND_TRIGGER_BACKTEST_COMPLETED: RoundTrigger
ROUND_TRIGGER_STUDY_COMPLETED: RoundTrigger
ROUND_TRIGGER_LIVE_FEEDBACK: RoundTrigger
ROUND_TRIGGER_SCHEDULE: RoundTrigger
ROUND_TRIGGER_OWNER_REQUEST: RoundTrigger
ROUND_TRIGGER_CONTINUATION: RoundTrigger
ROUND_DECISION_UNSPECIFIED: RoundDecision
ROUND_DECISION_CONTINUE: RoundDecision
ROUND_DECISION_AWAITING_APPROVAL: RoundDecision
ROUND_DECISION_PLATEAU: RoundDecision
ROUND_DECISION_STOP_NO_EDGE: RoundDecision
ROUND_DECISION_STOP_BUDGET: RoundDecision
ROUND_DECISION_STOP_UNREACHABLE: RoundDecision
ROUND_DECISION_STOP_FAILURES: RoundDecision
ROUND_DECISION_STOP_PAUSED: RoundDecision
ROUND_DECISION_ESCALATE_TO_OWNER: RoundDecision
FINDING_CATEGORY_UNSPECIFIED: FindingCategory
FINDING_CATEGORY_DATA_QUALITY: FindingCategory
FINDING_CATEGORY_LEAKAGE_SUSPICION: FindingCategory
FINDING_CATEGORY_OVERFITTING: FindingCategory
FINDING_CATEGORY_UNDERFITTING: FindingCategory
FINDING_CATEGORY_INSTABILITY: FindingCategory
FINDING_CATEGORY_REGIME_DEPENDENCE: FindingCategory
FINDING_CATEGORY_COST_SENSITIVITY: FindingCategory
FINDING_CATEGORY_CALIBRATION: FindingCategory
FINDING_CATEGORY_DRIFT: FindingCategory
FINDING_CATEGORY_EXECUTION_GAP: FindingCategory
FINDING_CATEGORY_NO_EDGE: FindingCategory
SEVERITY_UNSPECIFIED: Severity
SEVERITY_INFO: Severity
SEVERITY_LOW: Severity
SEVERITY_MEDIUM: Severity
SEVERITY_HIGH: Severity
SEVERITY_CRITICAL: Severity
ACTION_CATEGORY_UNSPECIFIED: ActionCategory
ACTION_CATEGORY_FEATURES: ActionCategory
ACTION_CATEGORY_LABELS: ActionCategory
ACTION_CATEGORY_MODEL_TYPE: ActionCategory
ACTION_CATEGORY_SEARCH_SPACE: ActionCategory
ACTION_CATEGORY_DATA_WINDOW: ActionCategory
ACTION_CATEGORY_UNIVERSE: ActionCategory
ACTION_CATEGORY_TRADE_RULES: ActionCategory
ACTION_CATEGORY_STOP_OR_RETIRE: ActionCategory
ACTION_STATUS_UNSPECIFIED: ActionStatus
ACTION_STATUS_PROPOSED: ActionStatus
ACTION_STATUS_APPROVED: ActionStatus
ACTION_STATUS_REJECTED: ActionStatus
ACTION_STATUS_LAUNCHED: ActionStatus
ACTION_STATUS_COMPLETED: ActionStatus
ACTION_STATUS_FAILED: ActionStatus
ACTION_STATUS_SKIPPED: ActionStatus
OVERFIT_RISK_UNSPECIFIED: OverfitRisk
OVERFIT_RISK_LOW: OverfitRisk
OVERFIT_RISK_MEDIUM: OverfitRisk
OVERFIT_RISK_HIGH: OverfitRisk
LOOP_STATE_UNSPECIFIED: LoopState
LOOP_STATE_IDLE: LoopState
LOOP_STATE_RUNNING: LoopState
LOOP_STATE_AWAITING_APPROVAL: LoopState
LOOP_STATE_AWAITING_RESULTS: LoopState
LOOP_STATE_PAUSED: LoopState
LOOP_STATE_STOPPED: LoopState
CODE_PROPOSAL_STATUS_UNSPECIFIED: CodeProposalStatus
CODE_PROPOSAL_STATUS_PENDING_CHECKS: CodeProposalStatus
CODE_PROPOSAL_STATUS_CHECKS_FAILED: CodeProposalStatus
CODE_PROPOSAL_STATUS_AWAITING_REVIEW: CodeProposalStatus
CODE_PROPOSAL_STATUS_APPROVED_FOR_MERGE: CodeProposalStatus
CODE_PROPOSAL_STATUS_REJECTED: CodeProposalStatus

class EvidenceRef(_message.Message):
    __slots__ = ("report_id", "metric", "value", "context")
    REPORT_ID_FIELD_NUMBER: _ClassVar[int]
    METRIC_FIELD_NUMBER: _ClassVar[int]
    VALUE_FIELD_NUMBER: _ClassVar[int]
    CONTEXT_FIELD_NUMBER: _ClassVar[int]
    report_id: str
    metric: str
    value: float
    context: str
    def __init__(self, report_id: _Optional[str] = ..., metric: _Optional[str] = ..., value: _Optional[float] = ..., context: _Optional[str] = ...) -> None: ...

class Finding(_message.Message):
    __slots__ = ("finding_id", "category", "severity", "evidence", "explanation")
    FINDING_ID_FIELD_NUMBER: _ClassVar[int]
    CATEGORY_FIELD_NUMBER: _ClassVar[int]
    SEVERITY_FIELD_NUMBER: _ClassVar[int]
    EVIDENCE_FIELD_NUMBER: _ClassVar[int]
    EXPLANATION_FIELD_NUMBER: _ClassVar[int]
    finding_id: str
    category: FindingCategory
    severity: Severity
    evidence: _containers.RepeatedCompositeFieldContainer[EvidenceRef]
    explanation: str
    def __init__(self, finding_id: _Optional[str] = ..., category: _Optional[_Union[FindingCategory, str]] = ..., severity: _Optional[_Union[Severity, str]] = ..., evidence: _Optional[_Iterable[_Union[EvidenceRef, _Mapping]]] = ..., explanation: _Optional[str] = ...) -> None: ...

class Diagnosis(_message.Message):
    __slots__ = ("diagnosis_id", "lineage_id", "round_id", "findings", "no_edge", "summary")
    DIAGNOSIS_ID_FIELD_NUMBER: _ClassVar[int]
    LINEAGE_ID_FIELD_NUMBER: _ClassVar[int]
    ROUND_ID_FIELD_NUMBER: _ClassVar[int]
    FINDINGS_FIELD_NUMBER: _ClassVar[int]
    NO_EDGE_FIELD_NUMBER: _ClassVar[int]
    SUMMARY_FIELD_NUMBER: _ClassVar[int]
    diagnosis_id: str
    lineage_id: str
    round_id: str
    findings: _containers.RepeatedCompositeFieldContainer[Finding]
    no_edge: bool
    summary: str
    def __init__(self, diagnosis_id: _Optional[str] = ..., lineage_id: _Optional[str] = ..., round_id: _Optional[str] = ..., findings: _Optional[_Iterable[_Union[Finding, _Mapping]]] = ..., no_edge: _Optional[bool] = ..., summary: _Optional[str] = ...) -> None: ...

class PlanAction(_message.Message):
    __slots__ = ("action_id", "rank", "category", "description", "target", "config_diff_json", "hypothesis", "expected_effect", "est_compute_hours", "trial_cost", "overfit_risk", "rank_score", "tool", "status", "status_reason", "launched_ids")
    ACTION_ID_FIELD_NUMBER: _ClassVar[int]
    RANK_FIELD_NUMBER: _ClassVar[int]
    CATEGORY_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    TARGET_FIELD_NUMBER: _ClassVar[int]
    CONFIG_DIFF_JSON_FIELD_NUMBER: _ClassVar[int]
    HYPOTHESIS_FIELD_NUMBER: _ClassVar[int]
    EXPECTED_EFFECT_FIELD_NUMBER: _ClassVar[int]
    EST_COMPUTE_HOURS_FIELD_NUMBER: _ClassVar[int]
    TRIAL_COST_FIELD_NUMBER: _ClassVar[int]
    OVERFIT_RISK_FIELD_NUMBER: _ClassVar[int]
    RANK_SCORE_FIELD_NUMBER: _ClassVar[int]
    TOOL_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    STATUS_REASON_FIELD_NUMBER: _ClassVar[int]
    LAUNCHED_IDS_FIELD_NUMBER: _ClassVar[int]
    action_id: str
    rank: int
    category: ActionCategory
    description: str
    target: str
    config_diff_json: str
    hypothesis: str
    expected_effect: str
    est_compute_hours: float
    trial_cost: int
    overfit_risk: OverfitRisk
    rank_score: float
    tool: str
    status: ActionStatus
    status_reason: str
    launched_ids: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, action_id: _Optional[str] = ..., rank: _Optional[int] = ..., category: _Optional[_Union[ActionCategory, str]] = ..., description: _Optional[str] = ..., target: _Optional[str] = ..., config_diff_json: _Optional[str] = ..., hypothesis: _Optional[str] = ..., expected_effect: _Optional[str] = ..., est_compute_hours: _Optional[float] = ..., trial_cost: _Optional[int] = ..., overfit_risk: _Optional[_Union[OverfitRisk, str]] = ..., rank_score: _Optional[float] = ..., tool: _Optional[str] = ..., status: _Optional[_Union[ActionStatus, str]] = ..., status_reason: _Optional[str] = ..., launched_ids: _Optional[_Iterable[str]] = ...) -> None: ...

class ImprovementPlan(_message.Message):
    __slots__ = ("plan_id", "round_id", "actions")
    PLAN_ID_FIELD_NUMBER: _ClassVar[int]
    ROUND_ID_FIELD_NUMBER: _ClassVar[int]
    ACTIONS_FIELD_NUMBER: _ClassVar[int]
    plan_id: str
    round_id: str
    actions: _containers.RepeatedCompositeFieldContainer[PlanAction]
    def __init__(self, plan_id: _Optional[str] = ..., round_id: _Optional[str] = ..., actions: _Optional[_Iterable[_Union[PlanAction, _Mapping]]] = ...) -> None: ...

class PreRegistration(_message.Message):
    __slots__ = ("hypothesis", "metric", "universe", "period", "stop_rule", "trial_budget", "prereg_hash")
    HYPOTHESIS_FIELD_NUMBER: _ClassVar[int]
    METRIC_FIELD_NUMBER: _ClassVar[int]
    UNIVERSE_FIELD_NUMBER: _ClassVar[int]
    PERIOD_FIELD_NUMBER: _ClassVar[int]
    STOP_RULE_FIELD_NUMBER: _ClassVar[int]
    TRIAL_BUDGET_FIELD_NUMBER: _ClassVar[int]
    PREREG_HASH_FIELD_NUMBER: _ClassVar[int]
    hypothesis: str
    metric: str
    universe: str
    period: str
    stop_rule: str
    trial_budget: int
    prereg_hash: str
    def __init__(self, hypothesis: _Optional[str] = ..., metric: _Optional[str] = ..., universe: _Optional[str] = ..., period: _Optional[str] = ..., stop_rule: _Optional[str] = ..., trial_budget: _Optional[int] = ..., prereg_hash: _Optional[str] = ...) -> None: ...

class BudgetUsage(_message.Message):
    __slots__ = ("trials", "cpu_hours", "llm_usd", "llm_input_tokens", "llm_output_tokens", "wall_clock_seconds", "jobs_launched")
    TRIALS_FIELD_NUMBER: _ClassVar[int]
    CPU_HOURS_FIELD_NUMBER: _ClassVar[int]
    LLM_USD_FIELD_NUMBER: _ClassVar[int]
    LLM_INPUT_TOKENS_FIELD_NUMBER: _ClassVar[int]
    LLM_OUTPUT_TOKENS_FIELD_NUMBER: _ClassVar[int]
    WALL_CLOCK_SECONDS_FIELD_NUMBER: _ClassVar[int]
    JOBS_LAUNCHED_FIELD_NUMBER: _ClassVar[int]
    trials: int
    cpu_hours: float
    llm_usd: float
    llm_input_tokens: int
    llm_output_tokens: int
    wall_clock_seconds: float
    jobs_launched: int
    def __init__(self, trials: _Optional[int] = ..., cpu_hours: _Optional[float] = ..., llm_usd: _Optional[float] = ..., llm_input_tokens: _Optional[int] = ..., llm_output_tokens: _Optional[int] = ..., wall_clock_seconds: _Optional[float] = ..., jobs_launched: _Optional[int] = ...) -> None: ...

class DeflatedOutcome(_message.Message):
    __slots__ = ("lineage_trials", "effective_trials", "best_dsr", "pbo", "best_candidate_id", "gate_passed", "source_report_id")
    LINEAGE_TRIALS_FIELD_NUMBER: _ClassVar[int]
    EFFECTIVE_TRIALS_FIELD_NUMBER: _ClassVar[int]
    BEST_DSR_FIELD_NUMBER: _ClassVar[int]
    PBO_FIELD_NUMBER: _ClassVar[int]
    BEST_CANDIDATE_ID_FIELD_NUMBER: _ClassVar[int]
    GATE_PASSED_FIELD_NUMBER: _ClassVar[int]
    SOURCE_REPORT_ID_FIELD_NUMBER: _ClassVar[int]
    lineage_trials: int
    effective_trials: float
    best_dsr: float
    pbo: float
    best_candidate_id: str
    gate_passed: bool
    source_report_id: str
    def __init__(self, lineage_trials: _Optional[int] = ..., effective_trials: _Optional[float] = ..., best_dsr: _Optional[float] = ..., pbo: _Optional[float] = ..., best_candidate_id: _Optional[str] = ..., gate_passed: _Optional[bool] = ..., source_report_id: _Optional[str] = ...) -> None: ...

class AnalystRound(_message.Message):
    __slots__ = ("round_id", "lineage_id", "cycle_id", "seq", "trigger", "trigger_ref_ids", "input_report_ids", "preregistration", "diagnosis", "plan", "budget_used", "trials_added", "outcome", "decision", "reasoning", "provider", "model", "prompt_hashes", "tool_call_hashes", "autonomy_level", "started_at", "completed_at", "correlation_id", "summary", "recommendation")
    ROUND_ID_FIELD_NUMBER: _ClassVar[int]
    LINEAGE_ID_FIELD_NUMBER: _ClassVar[int]
    CYCLE_ID_FIELD_NUMBER: _ClassVar[int]
    SEQ_FIELD_NUMBER: _ClassVar[int]
    TRIGGER_FIELD_NUMBER: _ClassVar[int]
    TRIGGER_REF_IDS_FIELD_NUMBER: _ClassVar[int]
    INPUT_REPORT_IDS_FIELD_NUMBER: _ClassVar[int]
    PREREGISTRATION_FIELD_NUMBER: _ClassVar[int]
    DIAGNOSIS_FIELD_NUMBER: _ClassVar[int]
    PLAN_FIELD_NUMBER: _ClassVar[int]
    BUDGET_USED_FIELD_NUMBER: _ClassVar[int]
    TRIALS_ADDED_FIELD_NUMBER: _ClassVar[int]
    OUTCOME_FIELD_NUMBER: _ClassVar[int]
    DECISION_FIELD_NUMBER: _ClassVar[int]
    REASONING_FIELD_NUMBER: _ClassVar[int]
    PROVIDER_FIELD_NUMBER: _ClassVar[int]
    MODEL_FIELD_NUMBER: _ClassVar[int]
    PROMPT_HASHES_FIELD_NUMBER: _ClassVar[int]
    TOOL_CALL_HASHES_FIELD_NUMBER: _ClassVar[int]
    AUTONOMY_LEVEL_FIELD_NUMBER: _ClassVar[int]
    STARTED_AT_FIELD_NUMBER: _ClassVar[int]
    COMPLETED_AT_FIELD_NUMBER: _ClassVar[int]
    CORRELATION_ID_FIELD_NUMBER: _ClassVar[int]
    SUMMARY_FIELD_NUMBER: _ClassVar[int]
    RECOMMENDATION_FIELD_NUMBER: _ClassVar[int]
    round_id: str
    lineage_id: str
    cycle_id: str
    seq: int
    trigger: RoundTrigger
    trigger_ref_ids: _containers.RepeatedScalarFieldContainer[str]
    input_report_ids: _containers.RepeatedScalarFieldContainer[str]
    preregistration: PreRegistration
    diagnosis: Diagnosis
    plan: ImprovementPlan
    budget_used: BudgetUsage
    trials_added: int
    outcome: DeflatedOutcome
    decision: RoundDecision
    reasoning: str
    provider: str
    model: str
    prompt_hashes: _containers.RepeatedScalarFieldContainer[str]
    tool_call_hashes: _containers.RepeatedScalarFieldContainer[str]
    autonomy_level: int
    started_at: int
    completed_at: int
    correlation_id: str
    summary: str
    recommendation: str
    def __init__(self, round_id: _Optional[str] = ..., lineage_id: _Optional[str] = ..., cycle_id: _Optional[str] = ..., seq: _Optional[int] = ..., trigger: _Optional[_Union[RoundTrigger, str]] = ..., trigger_ref_ids: _Optional[_Iterable[str]] = ..., input_report_ids: _Optional[_Iterable[str]] = ..., preregistration: _Optional[_Union[PreRegistration, _Mapping]] = ..., diagnosis: _Optional[_Union[Diagnosis, _Mapping]] = ..., plan: _Optional[_Union[ImprovementPlan, _Mapping]] = ..., budget_used: _Optional[_Union[BudgetUsage, _Mapping]] = ..., trials_added: _Optional[int] = ..., outcome: _Optional[_Union[DeflatedOutcome, _Mapping]] = ..., decision: _Optional[_Union[RoundDecision, str]] = ..., reasoning: _Optional[str] = ..., provider: _Optional[str] = ..., model: _Optional[str] = ..., prompt_hashes: _Optional[_Iterable[str]] = ..., tool_call_hashes: _Optional[_Iterable[str]] = ..., autonomy_level: _Optional[int] = ..., started_at: _Optional[int] = ..., completed_at: _Optional[int] = ..., correlation_id: _Optional[str] = ..., summary: _Optional[str] = ..., recommendation: _Optional[str] = ...) -> None: ...

class PromotionLimits(_message.Message):
    __slots__ = ("max_promotions", "window_days", "min_shadow_sessions", "cooldown_days")
    MAX_PROMOTIONS_FIELD_NUMBER: _ClassVar[int]
    WINDOW_DAYS_FIELD_NUMBER: _ClassVar[int]
    MIN_SHADOW_SESSIONS_FIELD_NUMBER: _ClassVar[int]
    COOLDOWN_DAYS_FIELD_NUMBER: _ClassVar[int]
    max_promotions: int
    window_days: int
    min_shadow_sessions: int
    cooldown_days: int
    def __init__(self, max_promotions: _Optional[int] = ..., window_days: _Optional[int] = ..., min_shadow_sessions: _Optional[int] = ..., cooldown_days: _Optional[int] = ...) -> None: ...

class Budget(_message.Message):
    __slots__ = ("max_trials_per_round", "max_cpu_hours_per_day", "max_llm_usd_per_day", "max_llm_usd_per_month", "max_concurrent_jobs", "max_rounds", "max_wall_clock_hours")
    MAX_TRIALS_PER_ROUND_FIELD_NUMBER: _ClassVar[int]
    MAX_CPU_HOURS_PER_DAY_FIELD_NUMBER: _ClassVar[int]
    MAX_LLM_USD_PER_DAY_FIELD_NUMBER: _ClassVar[int]
    MAX_LLM_USD_PER_MONTH_FIELD_NUMBER: _ClassVar[int]
    MAX_CONCURRENT_JOBS_FIELD_NUMBER: _ClassVar[int]
    MAX_ROUNDS_FIELD_NUMBER: _ClassVar[int]
    MAX_WALL_CLOCK_HOURS_FIELD_NUMBER: _ClassVar[int]
    max_trials_per_round: int
    max_cpu_hours_per_day: float
    max_llm_usd_per_day: float
    max_llm_usd_per_month: float
    max_concurrent_jobs: int
    max_rounds: int
    max_wall_clock_hours: float
    def __init__(self, max_trials_per_round: _Optional[int] = ..., max_cpu_hours_per_day: _Optional[float] = ..., max_llm_usd_per_day: _Optional[float] = ..., max_llm_usd_per_month: _Optional[float] = ..., max_concurrent_jobs: _Optional[int] = ..., max_rounds: _Optional[int] = ..., max_wall_clock_hours: _Optional[float] = ...) -> None: ...

class AutonomySetting(_message.Message):
    __slots__ = ("lineage_id", "level", "budget", "promotion_limits", "paused", "updated_by", "updated_at", "reason")
    LINEAGE_ID_FIELD_NUMBER: _ClassVar[int]
    LEVEL_FIELD_NUMBER: _ClassVar[int]
    BUDGET_FIELD_NUMBER: _ClassVar[int]
    PROMOTION_LIMITS_FIELD_NUMBER: _ClassVar[int]
    PAUSED_FIELD_NUMBER: _ClassVar[int]
    UPDATED_BY_FIELD_NUMBER: _ClassVar[int]
    UPDATED_AT_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    lineage_id: str
    level: int
    budget: Budget
    promotion_limits: PromotionLimits
    paused: bool
    updated_by: str
    updated_at: int
    reason: str
    def __init__(self, lineage_id: _Optional[str] = ..., level: _Optional[int] = ..., budget: _Optional[_Union[Budget, _Mapping]] = ..., promotion_limits: _Optional[_Union[PromotionLimits, _Mapping]] = ..., paused: _Optional[bool] = ..., updated_by: _Optional[str] = ..., updated_at: _Optional[int] = ..., reason: _Optional[str] = ...) -> None: ...

class LoopStatus(_message.Message):
    __slots__ = ("lineage_id", "state", "current_round_id", "rounds_completed", "rounds_without_improvement", "budget_used", "autonomy", "last_decision", "llm_usd_today", "llm_usd_month")
    LINEAGE_ID_FIELD_NUMBER: _ClassVar[int]
    STATE_FIELD_NUMBER: _ClassVar[int]
    CURRENT_ROUND_ID_FIELD_NUMBER: _ClassVar[int]
    ROUNDS_COMPLETED_FIELD_NUMBER: _ClassVar[int]
    ROUNDS_WITHOUT_IMPROVEMENT_FIELD_NUMBER: _ClassVar[int]
    BUDGET_USED_FIELD_NUMBER: _ClassVar[int]
    AUTONOMY_FIELD_NUMBER: _ClassVar[int]
    LAST_DECISION_FIELD_NUMBER: _ClassVar[int]
    LLM_USD_TODAY_FIELD_NUMBER: _ClassVar[int]
    LLM_USD_MONTH_FIELD_NUMBER: _ClassVar[int]
    lineage_id: str
    state: LoopState
    current_round_id: str
    rounds_completed: int
    rounds_without_improvement: int
    budget_used: BudgetUsage
    autonomy: AutonomySetting
    last_decision: RoundDecision
    llm_usd_today: float
    llm_usd_month: float
    def __init__(self, lineage_id: _Optional[str] = ..., state: _Optional[_Union[LoopState, str]] = ..., current_round_id: _Optional[str] = ..., rounds_completed: _Optional[int] = ..., rounds_without_improvement: _Optional[int] = ..., budget_used: _Optional[_Union[BudgetUsage, _Mapping]] = ..., autonomy: _Optional[_Union[AutonomySetting, _Mapping]] = ..., last_decision: _Optional[_Union[RoundDecision, str]] = ..., llm_usd_today: _Optional[float] = ..., llm_usd_month: _Optional[float] = ...) -> None: ...

class CodeCheck(_message.Message):
    __slots__ = ("name", "passed", "messages")
    NAME_FIELD_NUMBER: _ClassVar[int]
    PASSED_FIELD_NUMBER: _ClassVar[int]
    MESSAGES_FIELD_NUMBER: _ClassVar[int]
    name: str
    passed: bool
    messages: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, name: _Optional[str] = ..., passed: _Optional[bool] = ..., messages: _Optional[_Iterable[str]] = ...) -> None: ...

class CodeProposal(_message.Message):
    __slots__ = ("proposal_id", "lineage_id", "round_id", "kind", "name", "description", "source", "source_sha256", "patch", "checks", "status", "reviewed_by", "review_note", "created_at", "updated_at")
    PROPOSAL_ID_FIELD_NUMBER: _ClassVar[int]
    LINEAGE_ID_FIELD_NUMBER: _ClassVar[int]
    ROUND_ID_FIELD_NUMBER: _ClassVar[int]
    KIND_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    SOURCE_FIELD_NUMBER: _ClassVar[int]
    SOURCE_SHA256_FIELD_NUMBER: _ClassVar[int]
    PATCH_FIELD_NUMBER: _ClassVar[int]
    CHECKS_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    REVIEWED_BY_FIELD_NUMBER: _ClassVar[int]
    REVIEW_NOTE_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_FIELD_NUMBER: _ClassVar[int]
    UPDATED_AT_FIELD_NUMBER: _ClassVar[int]
    proposal_id: str
    lineage_id: str
    round_id: str
    kind: str
    name: str
    description: str
    source: str
    source_sha256: str
    patch: str
    checks: _containers.RepeatedCompositeFieldContainer[CodeCheck]
    status: CodeProposalStatus
    reviewed_by: str
    review_note: str
    created_at: int
    updated_at: int
    def __init__(self, proposal_id: _Optional[str] = ..., lineage_id: _Optional[str] = ..., round_id: _Optional[str] = ..., kind: _Optional[str] = ..., name: _Optional[str] = ..., description: _Optional[str] = ..., source: _Optional[str] = ..., source_sha256: _Optional[str] = ..., patch: _Optional[str] = ..., checks: _Optional[_Iterable[_Union[CodeCheck, _Mapping]]] = ..., status: _Optional[_Union[CodeProposalStatus, str]] = ..., reviewed_by: _Optional[str] = ..., review_note: _Optional[str] = ..., created_at: _Optional[int] = ..., updated_at: _Optional[int] = ...) -> None: ...

class RoundCompleted(_message.Message):
    __slots__ = ("round",)
    ROUND_FIELD_NUMBER: _ClassVar[int]
    round: AnalystRound
    def __init__(self, round: _Optional[_Union[AnalystRound, _Mapping]] = ...) -> None: ...

class PlanReady(_message.Message):
    __slots__ = ("lineage_id", "round_id", "action_ids", "occurred_at", "correlation_id")
    LINEAGE_ID_FIELD_NUMBER: _ClassVar[int]
    ROUND_ID_FIELD_NUMBER: _ClassVar[int]
    ACTION_IDS_FIELD_NUMBER: _ClassVar[int]
    OCCURRED_AT_FIELD_NUMBER: _ClassVar[int]
    CORRELATION_ID_FIELD_NUMBER: _ClassVar[int]
    lineage_id: str
    round_id: str
    action_ids: _containers.RepeatedScalarFieldContainer[str]
    occurred_at: int
    correlation_id: str
    def __init__(self, lineage_id: _Optional[str] = ..., round_id: _Optional[str] = ..., action_ids: _Optional[_Iterable[str]] = ..., occurred_at: _Optional[int] = ..., correlation_id: _Optional[str] = ...) -> None: ...

class Escalation(_message.Message):
    __slots__ = ("lineage_id", "round_id", "reason", "kind", "occurred_at", "correlation_id")
    LINEAGE_ID_FIELD_NUMBER: _ClassVar[int]
    ROUND_ID_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    KIND_FIELD_NUMBER: _ClassVar[int]
    OCCURRED_AT_FIELD_NUMBER: _ClassVar[int]
    CORRELATION_ID_FIELD_NUMBER: _ClassVar[int]
    lineage_id: str
    round_id: str
    reason: str
    kind: str
    occurred_at: int
    correlation_id: str
    def __init__(self, lineage_id: _Optional[str] = ..., round_id: _Optional[str] = ..., reason: _Optional[str] = ..., kind: _Optional[str] = ..., occurred_at: _Optional[int] = ..., correlation_id: _Optional[str] = ...) -> None: ...

class LowerAutonomy(_message.Message):
    __slots__ = ("lineage_id", "level", "pause", "actor", "reason", "evidence_report_id", "occurred_at", "correlation_id")
    LINEAGE_ID_FIELD_NUMBER: _ClassVar[int]
    LEVEL_FIELD_NUMBER: _ClassVar[int]
    PAUSE_FIELD_NUMBER: _ClassVar[int]
    ACTOR_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    EVIDENCE_REPORT_ID_FIELD_NUMBER: _ClassVar[int]
    OCCURRED_AT_FIELD_NUMBER: _ClassVar[int]
    CORRELATION_ID_FIELD_NUMBER: _ClassVar[int]
    lineage_id: str
    level: int
    pause: bool
    actor: str
    reason: str
    evidence_report_id: str
    occurred_at: int
    correlation_id: str
    def __init__(self, lineage_id: _Optional[str] = ..., level: _Optional[int] = ..., pause: _Optional[bool] = ..., actor: _Optional[str] = ..., reason: _Optional[str] = ..., evidence_report_id: _Optional[str] = ..., occurred_at: _Optional[int] = ..., correlation_id: _Optional[str] = ...) -> None: ...

class ChatMessage(_message.Message):
    __slots__ = ("message_id", "role", "content", "cited_ids", "created_at")
    MESSAGE_ID_FIELD_NUMBER: _ClassVar[int]
    ROLE_FIELD_NUMBER: _ClassVar[int]
    CONTENT_FIELD_NUMBER: _ClassVar[int]
    CITED_IDS_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_FIELD_NUMBER: _ClassVar[int]
    message_id: str
    role: str
    content: str
    cited_ids: _containers.RepeatedScalarFieldContainer[str]
    created_at: int
    def __init__(self, message_id: _Optional[str] = ..., role: _Optional[str] = ..., content: _Optional[str] = ..., cited_ids: _Optional[_Iterable[str]] = ..., created_at: _Optional[int] = ...) -> None: ...

class Conversation(_message.Message):
    __slots__ = ("conversation_id", "title", "lineage_id", "messages", "created_at", "updated_at")
    CONVERSATION_ID_FIELD_NUMBER: _ClassVar[int]
    TITLE_FIELD_NUMBER: _ClassVar[int]
    LINEAGE_ID_FIELD_NUMBER: _ClassVar[int]
    MESSAGES_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_FIELD_NUMBER: _ClassVar[int]
    UPDATED_AT_FIELD_NUMBER: _ClassVar[int]
    conversation_id: str
    title: str
    lineage_id: str
    messages: _containers.RepeatedCompositeFieldContainer[ChatMessage]
    created_at: int
    updated_at: int
    def __init__(self, conversation_id: _Optional[str] = ..., title: _Optional[str] = ..., lineage_id: _Optional[str] = ..., messages: _Optional[_Iterable[_Union[ChatMessage, _Mapping]]] = ..., created_at: _Optional[int] = ..., updated_at: _Optional[int] = ...) -> None: ...

class GetJournalRequest(_message.Message):
    __slots__ = ("lineage_id", "limit", "decision")
    LINEAGE_ID_FIELD_NUMBER: _ClassVar[int]
    LIMIT_FIELD_NUMBER: _ClassVar[int]
    DECISION_FIELD_NUMBER: _ClassVar[int]
    lineage_id: str
    limit: int
    decision: RoundDecision
    def __init__(self, lineage_id: _Optional[str] = ..., limit: _Optional[int] = ..., decision: _Optional[_Union[RoundDecision, str]] = ...) -> None: ...

class GetJournalResponse(_message.Message):
    __slots__ = ("rounds",)
    ROUNDS_FIELD_NUMBER: _ClassVar[int]
    rounds: _containers.RepeatedCompositeFieldContainer[AnalystRound]
    def __init__(self, rounds: _Optional[_Iterable[_Union[AnalystRound, _Mapping]]] = ...) -> None: ...

class GetRoundRequest(_message.Message):
    __slots__ = ("round_id",)
    ROUND_ID_FIELD_NUMBER: _ClassVar[int]
    round_id: str
    def __init__(self, round_id: _Optional[str] = ...) -> None: ...

class GetRoundResponse(_message.Message):
    __slots__ = ("round",)
    ROUND_FIELD_NUMBER: _ClassVar[int]
    round: AnalystRound
    def __init__(self, round: _Optional[_Union[AnalystRound, _Mapping]] = ...) -> None: ...

class ListLineagesRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class ListLineagesResponse(_message.Message):
    __slots__ = ("lineages",)
    LINEAGES_FIELD_NUMBER: _ClassVar[int]
    lineages: _containers.RepeatedCompositeFieldContainer[LoopStatus]
    def __init__(self, lineages: _Optional[_Iterable[_Union[LoopStatus, _Mapping]]] = ...) -> None: ...

class GetLoopStatusRequest(_message.Message):
    __slots__ = ("lineage_id",)
    LINEAGE_ID_FIELD_NUMBER: _ClassVar[int]
    lineage_id: str
    def __init__(self, lineage_id: _Optional[str] = ...) -> None: ...

class GetLoopStatusResponse(_message.Message):
    __slots__ = ("status",)
    STATUS_FIELD_NUMBER: _ClassVar[int]
    status: LoopStatus
    def __init__(self, status: _Optional[_Union[LoopStatus, _Mapping]] = ...) -> None: ...

class ReviewActionsRequest(_message.Message):
    __slots__ = ("round_id", "action_ids", "approve", "reason", "actor")
    ROUND_ID_FIELD_NUMBER: _ClassVar[int]
    ACTION_IDS_FIELD_NUMBER: _ClassVar[int]
    APPROVE_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    ACTOR_FIELD_NUMBER: _ClassVar[int]
    round_id: str
    action_ids: _containers.RepeatedScalarFieldContainer[str]
    approve: bool
    reason: str
    actor: str
    def __init__(self, round_id: _Optional[str] = ..., action_ids: _Optional[_Iterable[str]] = ..., approve: _Optional[bool] = ..., reason: _Optional[str] = ..., actor: _Optional[str] = ...) -> None: ...

class ReviewActionsResponse(_message.Message):
    __slots__ = ("plan",)
    PLAN_FIELD_NUMBER: _ClassVar[int]
    plan: ImprovementPlan
    def __init__(self, plan: _Optional[_Union[ImprovementPlan, _Mapping]] = ...) -> None: ...

class SetLoopPausedRequest(_message.Message):
    __slots__ = ("lineage_id", "paused", "actor", "reason")
    LINEAGE_ID_FIELD_NUMBER: _ClassVar[int]
    PAUSED_FIELD_NUMBER: _ClassVar[int]
    ACTOR_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    lineage_id: str
    paused: bool
    actor: str
    reason: str
    def __init__(self, lineage_id: _Optional[str] = ..., paused: _Optional[bool] = ..., actor: _Optional[str] = ..., reason: _Optional[str] = ...) -> None: ...

class SetLoopPausedResponse(_message.Message):
    __slots__ = ("lineages",)
    LINEAGES_FIELD_NUMBER: _ClassVar[int]
    lineages: _containers.RepeatedCompositeFieldContainer[LoopStatus]
    def __init__(self, lineages: _Optional[_Iterable[_Union[LoopStatus, _Mapping]]] = ...) -> None: ...

class RequestAnalysisRequest(_message.Message):
    __slots__ = ("lineage_id", "ref_id", "ref_kind", "question", "actor")
    LINEAGE_ID_FIELD_NUMBER: _ClassVar[int]
    REF_ID_FIELD_NUMBER: _ClassVar[int]
    REF_KIND_FIELD_NUMBER: _ClassVar[int]
    QUESTION_FIELD_NUMBER: _ClassVar[int]
    ACTOR_FIELD_NUMBER: _ClassVar[int]
    lineage_id: str
    ref_id: str
    ref_kind: str
    question: str
    actor: str
    def __init__(self, lineage_id: _Optional[str] = ..., ref_id: _Optional[str] = ..., ref_kind: _Optional[str] = ..., question: _Optional[str] = ..., actor: _Optional[str] = ...) -> None: ...

class RequestAnalysisResponse(_message.Message):
    __slots__ = ("round_id",)
    ROUND_ID_FIELD_NUMBER: _ClassVar[int]
    round_id: str
    def __init__(self, round_id: _Optional[str] = ...) -> None: ...

class GetTrialLedgerRequest(_message.Message):
    __slots__ = ("lineage_id", "limit")
    LINEAGE_ID_FIELD_NUMBER: _ClassVar[int]
    LIMIT_FIELD_NUMBER: _ClassVar[int]
    lineage_id: str
    limit: int
    def __init__(self, lineage_id: _Optional[str] = ..., limit: _Optional[int] = ...) -> None: ...

class TrialRow(_message.Message):
    __slots__ = ("trial_id", "cycle_id", "source", "status", "spec_hash", "sharpe", "created_at", "family")
    TRIAL_ID_FIELD_NUMBER: _ClassVar[int]
    CYCLE_ID_FIELD_NUMBER: _ClassVar[int]
    SOURCE_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    SPEC_HASH_FIELD_NUMBER: _ClassVar[int]
    SHARPE_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_FIELD_NUMBER: _ClassVar[int]
    FAMILY_FIELD_NUMBER: _ClassVar[int]
    trial_id: str
    cycle_id: str
    source: str
    status: str
    spec_hash: str
    sharpe: float
    created_at: int
    family: str
    def __init__(self, trial_id: _Optional[str] = ..., cycle_id: _Optional[str] = ..., source: _Optional[str] = ..., status: _Optional[str] = ..., spec_hash: _Optional[str] = ..., sharpe: _Optional[float] = ..., created_at: _Optional[int] = ..., family: _Optional[str] = ...) -> None: ...

class GetTrialLedgerResponse(_message.Message):
    __slots__ = ("lineage_id", "trials", "outcome", "cycle_budget", "cycle_used")
    LINEAGE_ID_FIELD_NUMBER: _ClassVar[int]
    TRIALS_FIELD_NUMBER: _ClassVar[int]
    OUTCOME_FIELD_NUMBER: _ClassVar[int]
    CYCLE_BUDGET_FIELD_NUMBER: _ClassVar[int]
    CYCLE_USED_FIELD_NUMBER: _ClassVar[int]
    lineage_id: str
    trials: _containers.RepeatedCompositeFieldContainer[TrialRow]
    outcome: DeflatedOutcome
    cycle_budget: int
    cycle_used: int
    def __init__(self, lineage_id: _Optional[str] = ..., trials: _Optional[_Iterable[_Union[TrialRow, _Mapping]]] = ..., outcome: _Optional[_Union[DeflatedOutcome, _Mapping]] = ..., cycle_budget: _Optional[int] = ..., cycle_used: _Optional[int] = ...) -> None: ...

class ListCodeProposalsRequest(_message.Message):
    __slots__ = ("lineage_id",)
    LINEAGE_ID_FIELD_NUMBER: _ClassVar[int]
    lineage_id: str
    def __init__(self, lineage_id: _Optional[str] = ...) -> None: ...

class ListCodeProposalsResponse(_message.Message):
    __slots__ = ("proposals",)
    PROPOSALS_FIELD_NUMBER: _ClassVar[int]
    proposals: _containers.RepeatedCompositeFieldContainer[CodeProposal]
    def __init__(self, proposals: _Optional[_Iterable[_Union[CodeProposal, _Mapping]]] = ...) -> None: ...

class ReviewCodeProposalRequest(_message.Message):
    __slots__ = ("proposal_id", "approve", "note", "actor")
    PROPOSAL_ID_FIELD_NUMBER: _ClassVar[int]
    APPROVE_FIELD_NUMBER: _ClassVar[int]
    NOTE_FIELD_NUMBER: _ClassVar[int]
    ACTOR_FIELD_NUMBER: _ClassVar[int]
    proposal_id: str
    approve: bool
    note: str
    actor: str
    def __init__(self, proposal_id: _Optional[str] = ..., approve: _Optional[bool] = ..., note: _Optional[str] = ..., actor: _Optional[str] = ...) -> None: ...

class ReviewCodeProposalResponse(_message.Message):
    __slots__ = ("proposal",)
    PROPOSAL_FIELD_NUMBER: _ClassVar[int]
    proposal: CodeProposal
    def __init__(self, proposal: _Optional[_Union[CodeProposal, _Mapping]] = ...) -> None: ...

class NotifyAutonomyChangedRequest(_message.Message):
    __slots__ = ("lineage_id",)
    LINEAGE_ID_FIELD_NUMBER: _ClassVar[int]
    lineage_id: str
    def __init__(self, lineage_id: _Optional[str] = ...) -> None: ...

class NotifyAutonomyChangedResponse(_message.Message):
    __slots__ = ("status",)
    STATUS_FIELD_NUMBER: _ClassVar[int]
    status: LoopStatus
    def __init__(self, status: _Optional[_Union[LoopStatus, _Mapping]] = ...) -> None: ...

class CreateConversationRequest(_message.Message):
    __slots__ = ("title", "lineage_id")
    TITLE_FIELD_NUMBER: _ClassVar[int]
    LINEAGE_ID_FIELD_NUMBER: _ClassVar[int]
    title: str
    lineage_id: str
    def __init__(self, title: _Optional[str] = ..., lineage_id: _Optional[str] = ...) -> None: ...

class CreateConversationResponse(_message.Message):
    __slots__ = ("conversation",)
    CONVERSATION_FIELD_NUMBER: _ClassVar[int]
    conversation: Conversation
    def __init__(self, conversation: _Optional[_Union[Conversation, _Mapping]] = ...) -> None: ...

class GetConversationRequest(_message.Message):
    __slots__ = ("conversation_id",)
    CONVERSATION_ID_FIELD_NUMBER: _ClassVar[int]
    conversation_id: str
    def __init__(self, conversation_id: _Optional[str] = ...) -> None: ...

class GetConversationResponse(_message.Message):
    __slots__ = ("conversation",)
    CONVERSATION_FIELD_NUMBER: _ClassVar[int]
    conversation: Conversation
    def __init__(self, conversation: _Optional[_Union[Conversation, _Mapping]] = ...) -> None: ...

class ListConversationsRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class ListConversationsResponse(_message.Message):
    __slots__ = ("conversations",)
    CONVERSATIONS_FIELD_NUMBER: _ClassVar[int]
    conversations: _containers.RepeatedCompositeFieldContainer[Conversation]
    def __init__(self, conversations: _Optional[_Iterable[_Union[Conversation, _Mapping]]] = ...) -> None: ...

class PostMessageRequest(_message.Message):
    __slots__ = ("conversation_id", "content", "actor")
    CONVERSATION_ID_FIELD_NUMBER: _ClassVar[int]
    CONTENT_FIELD_NUMBER: _ClassVar[int]
    ACTOR_FIELD_NUMBER: _ClassVar[int]
    conversation_id: str
    content: str
    actor: str
    def __init__(self, conversation_id: _Optional[str] = ..., content: _Optional[str] = ..., actor: _Optional[str] = ...) -> None: ...

class PostMessageResponse(_message.Message):
    __slots__ = ("reply",)
    REPLY_FIELD_NUMBER: _ClassVar[int]
    reply: ChatMessage
    def __init__(self, reply: _Optional[_Union[ChatMessage, _Mapping]] = ...) -> None: ...
