from tb.common.v1 import common_pb2 as _common_pb2
from tb.orders.v1 import orders_pb2 as _orders_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class StrategyMode(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    STRATEGY_MODE_UNSPECIFIED: _ClassVar[StrategyMode]
    STRATEGY_MODE_DISABLED: _ClassVar[StrategyMode]
    STRATEGY_MODE_SHADOW: _ClassVar[StrategyMode]
    STRATEGY_MODE_PAPER: _ClassVar[StrategyMode]
    STRATEGY_MODE_LIVE: _ClassVar[StrategyMode]
    STRATEGY_MODE_HALTED: _ClassVar[StrategyMode]
STRATEGY_MODE_UNSPECIFIED: StrategyMode
STRATEGY_MODE_DISABLED: StrategyMode
STRATEGY_MODE_SHADOW: StrategyMode
STRATEGY_MODE_PAPER: StrategyMode
STRATEGY_MODE_LIVE: StrategyMode
STRATEGY_MODE_HALTED: StrategyMode

class RiskLimits(_message.Message):
    __slots__ = ("max_order_notional", "max_order_qty", "max_position_notional", "max_position_pct_allocation", "max_gross_exposure", "max_net_exposure", "max_positions", "max_concentration_pct", "max_daily_loss", "max_drawdown", "max_orders_per_minute", "max_cancels_per_minute", "max_consecutive_losses", "price_collar_pct", "duplicate_window_seconds", "fat_finger_multiple", "max_open_orders")
    MAX_ORDER_NOTIONAL_FIELD_NUMBER: _ClassVar[int]
    MAX_ORDER_QTY_FIELD_NUMBER: _ClassVar[int]
    MAX_POSITION_NOTIONAL_FIELD_NUMBER: _ClassVar[int]
    MAX_POSITION_PCT_ALLOCATION_FIELD_NUMBER: _ClassVar[int]
    MAX_GROSS_EXPOSURE_FIELD_NUMBER: _ClassVar[int]
    MAX_NET_EXPOSURE_FIELD_NUMBER: _ClassVar[int]
    MAX_POSITIONS_FIELD_NUMBER: _ClassVar[int]
    MAX_CONCENTRATION_PCT_FIELD_NUMBER: _ClassVar[int]
    MAX_DAILY_LOSS_FIELD_NUMBER: _ClassVar[int]
    MAX_DRAWDOWN_FIELD_NUMBER: _ClassVar[int]
    MAX_ORDERS_PER_MINUTE_FIELD_NUMBER: _ClassVar[int]
    MAX_CANCELS_PER_MINUTE_FIELD_NUMBER: _ClassVar[int]
    MAX_CONSECUTIVE_LOSSES_FIELD_NUMBER: _ClassVar[int]
    PRICE_COLLAR_PCT_FIELD_NUMBER: _ClassVar[int]
    DUPLICATE_WINDOW_SECONDS_FIELD_NUMBER: _ClassVar[int]
    FAT_FINGER_MULTIPLE_FIELD_NUMBER: _ClassVar[int]
    MAX_OPEN_ORDERS_FIELD_NUMBER: _ClassVar[int]
    max_order_notional: _common_pb2.Decimal
    max_order_qty: _common_pb2.Decimal
    max_position_notional: _common_pb2.Decimal
    max_position_pct_allocation: _common_pb2.Decimal
    max_gross_exposure: _common_pb2.Decimal
    max_net_exposure: _common_pb2.Decimal
    max_positions: int
    max_concentration_pct: _common_pb2.Decimal
    max_daily_loss: _common_pb2.Decimal
    max_drawdown: _common_pb2.Decimal
    max_orders_per_minute: int
    max_cancels_per_minute: int
    max_consecutive_losses: int
    price_collar_pct: _common_pb2.Decimal
    duplicate_window_seconds: int
    fat_finger_multiple: _common_pb2.Decimal
    max_open_orders: int
    def __init__(self, max_order_notional: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., max_order_qty: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., max_position_notional: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., max_position_pct_allocation: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., max_gross_exposure: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., max_net_exposure: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., max_positions: _Optional[int] = ..., max_concentration_pct: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., max_daily_loss: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., max_drawdown: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., max_orders_per_minute: _Optional[int] = ..., max_cancels_per_minute: _Optional[int] = ..., max_consecutive_losses: _Optional[int] = ..., price_collar_pct: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., duplicate_window_seconds: _Optional[int] = ..., fat_finger_multiple: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., max_open_orders: _Optional[int] = ...) -> None: ...

class Strategy(_message.Message):
    __slots__ = ("strategy_id", "short_id", "name", "horizon", "mode", "broker_account_id", "version", "code_commit", "params_hash", "allow_short", "segregation_allowlist", "limits", "mode_reason", "updated_at", "runtime_hosted", "live_capital_cap", "intended_allocation", "paper_account_id", "ladder_step", "ladder_window_start", "paper_twin_of", "live_promoted_at", "live_evidence_hash")
    STRATEGY_ID_FIELD_NUMBER: _ClassVar[int]
    SHORT_ID_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    HORIZON_FIELD_NUMBER: _ClassVar[int]
    MODE_FIELD_NUMBER: _ClassVar[int]
    BROKER_ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    VERSION_FIELD_NUMBER: _ClassVar[int]
    CODE_COMMIT_FIELD_NUMBER: _ClassVar[int]
    PARAMS_HASH_FIELD_NUMBER: _ClassVar[int]
    ALLOW_SHORT_FIELD_NUMBER: _ClassVar[int]
    SEGREGATION_ALLOWLIST_FIELD_NUMBER: _ClassVar[int]
    LIMITS_FIELD_NUMBER: _ClassVar[int]
    MODE_REASON_FIELD_NUMBER: _ClassVar[int]
    UPDATED_AT_FIELD_NUMBER: _ClassVar[int]
    RUNTIME_HOSTED_FIELD_NUMBER: _ClassVar[int]
    LIVE_CAPITAL_CAP_FIELD_NUMBER: _ClassVar[int]
    INTENDED_ALLOCATION_FIELD_NUMBER: _ClassVar[int]
    PAPER_ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    LADDER_STEP_FIELD_NUMBER: _ClassVar[int]
    LADDER_WINDOW_START_FIELD_NUMBER: _ClassVar[int]
    PAPER_TWIN_OF_FIELD_NUMBER: _ClassVar[int]
    LIVE_PROMOTED_AT_FIELD_NUMBER: _ClassVar[int]
    LIVE_EVIDENCE_HASH_FIELD_NUMBER: _ClassVar[int]
    strategy_id: str
    short_id: str
    name: str
    horizon: _common_pb2.Horizon
    mode: StrategyMode
    broker_account_id: str
    version: str
    code_commit: str
    params_hash: str
    allow_short: bool
    segregation_allowlist: _containers.RepeatedScalarFieldContainer[str]
    limits: RiskLimits
    mode_reason: str
    updated_at: int
    runtime_hosted: bool
    live_capital_cap: _common_pb2.Decimal
    intended_allocation: _common_pb2.Decimal
    paper_account_id: str
    ladder_step: int
    ladder_window_start: int
    paper_twin_of: str
    live_promoted_at: int
    live_evidence_hash: str
    def __init__(self, strategy_id: _Optional[str] = ..., short_id: _Optional[str] = ..., name: _Optional[str] = ..., horizon: _Optional[_Union[_common_pb2.Horizon, str]] = ..., mode: _Optional[_Union[StrategyMode, str]] = ..., broker_account_id: _Optional[str] = ..., version: _Optional[str] = ..., code_commit: _Optional[str] = ..., params_hash: _Optional[str] = ..., allow_short: _Optional[bool] = ..., segregation_allowlist: _Optional[_Iterable[str]] = ..., limits: _Optional[_Union[RiskLimits, _Mapping]] = ..., mode_reason: _Optional[str] = ..., updated_at: _Optional[int] = ..., runtime_hosted: _Optional[bool] = ..., live_capital_cap: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., intended_allocation: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., paper_account_id: _Optional[str] = ..., ladder_step: _Optional[int] = ..., ladder_window_start: _Optional[int] = ..., paper_twin_of: _Optional[str] = ..., live_promoted_at: _Optional[int] = ..., live_evidence_hash: _Optional[str] = ...) -> None: ...

class ReadinessItem(_message.Message):
    __slots__ = ("id", "section", "title", "status", "automated", "checked_by", "checked_at", "note", "evidence_url", "detail")
    ID_FIELD_NUMBER: _ClassVar[int]
    SECTION_FIELD_NUMBER: _ClassVar[int]
    TITLE_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    AUTOMATED_FIELD_NUMBER: _ClassVar[int]
    CHECKED_BY_FIELD_NUMBER: _ClassVar[int]
    CHECKED_AT_FIELD_NUMBER: _ClassVar[int]
    NOTE_FIELD_NUMBER: _ClassVar[int]
    EVIDENCE_URL_FIELD_NUMBER: _ClassVar[int]
    DETAIL_FIELD_NUMBER: _ClassVar[int]
    id: str
    section: str
    title: str
    status: str
    automated: bool
    checked_by: str
    checked_at: int
    note: str
    evidence_url: str
    detail: str
    def __init__(self, id: _Optional[str] = ..., section: _Optional[str] = ..., title: _Optional[str] = ..., status: _Optional[str] = ..., automated: _Optional[bool] = ..., checked_by: _Optional[str] = ..., checked_at: _Optional[int] = ..., note: _Optional[str] = ..., evidence_url: _Optional[str] = ..., detail: _Optional[str] = ...) -> None: ...

class LiveReadiness(_message.Message):
    __slots__ = ("broker_account_id", "items", "complete", "updated_at", "record_hash")
    BROKER_ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    ITEMS_FIELD_NUMBER: _ClassVar[int]
    COMPLETE_FIELD_NUMBER: _ClassVar[int]
    UPDATED_AT_FIELD_NUMBER: _ClassVar[int]
    RECORD_HASH_FIELD_NUMBER: _ClassVar[int]
    broker_account_id: str
    items: _containers.RepeatedCompositeFieldContainer[ReadinessItem]
    complete: bool
    updated_at: int
    record_hash: str
    def __init__(self, broker_account_id: _Optional[str] = ..., items: _Optional[_Iterable[_Union[ReadinessItem, _Mapping]]] = ..., complete: _Optional[bool] = ..., updated_at: _Optional[int] = ..., record_hash: _Optional[str] = ...) -> None: ...

class GraduationCriterion(_message.Message):
    __slots__ = ("name", "required", "actual", "passed", "source", "detail")
    NAME_FIELD_NUMBER: _ClassVar[int]
    REQUIRED_FIELD_NUMBER: _ClassVar[int]
    ACTUAL_FIELD_NUMBER: _ClassVar[int]
    PASSED_FIELD_NUMBER: _ClassVar[int]
    SOURCE_FIELD_NUMBER: _ClassVar[int]
    DETAIL_FIELD_NUMBER: _ClassVar[int]
    name: str
    required: str
    actual: str
    passed: bool
    source: str
    detail: str
    def __init__(self, name: _Optional[str] = ..., required: _Optional[str] = ..., actual: _Optional[str] = ..., passed: _Optional[bool] = ..., source: _Optional[str] = ..., detail: _Optional[str] = ...) -> None: ...

class GraduationReport(_message.Message):
    __slots__ = ("strategy_id", "criteria", "overall", "computed_at", "evidence_links", "report_hash", "paper_sessions", "paper_trades", "registry_stage", "paper_session_returns", "window_start", "window_end")
    STRATEGY_ID_FIELD_NUMBER: _ClassVar[int]
    CRITERIA_FIELD_NUMBER: _ClassVar[int]
    OVERALL_FIELD_NUMBER: _ClassVar[int]
    COMPUTED_AT_FIELD_NUMBER: _ClassVar[int]
    EVIDENCE_LINKS_FIELD_NUMBER: _ClassVar[int]
    REPORT_HASH_FIELD_NUMBER: _ClassVar[int]
    PAPER_SESSIONS_FIELD_NUMBER: _ClassVar[int]
    PAPER_TRADES_FIELD_NUMBER: _ClassVar[int]
    REGISTRY_STAGE_FIELD_NUMBER: _ClassVar[int]
    PAPER_SESSION_RETURNS_FIELD_NUMBER: _ClassVar[int]
    WINDOW_START_FIELD_NUMBER: _ClassVar[int]
    WINDOW_END_FIELD_NUMBER: _ClassVar[int]
    strategy_id: str
    criteria: _containers.RepeatedCompositeFieldContainer[GraduationCriterion]
    overall: bool
    computed_at: int
    evidence_links: _containers.RepeatedScalarFieldContainer[str]
    report_hash: str
    paper_sessions: int
    paper_trades: int
    registry_stage: str
    paper_session_returns: _containers.RepeatedScalarFieldContainer[float]
    window_start: int
    window_end: int
    def __init__(self, strategy_id: _Optional[str] = ..., criteria: _Optional[_Iterable[_Union[GraduationCriterion, _Mapping]]] = ..., overall: _Optional[bool] = ..., computed_at: _Optional[int] = ..., evidence_links: _Optional[_Iterable[str]] = ..., report_hash: _Optional[str] = ..., paper_sessions: _Optional[int] = ..., paper_trades: _Optional[int] = ..., registry_stage: _Optional[str] = ..., paper_session_returns: _Optional[_Iterable[float]] = ..., window_start: _Optional[int] = ..., window_end: _Optional[int] = ...) -> None: ...

class LiveAccountStatus(_message.Message):
    __slots__ = ("broker_account_id", "enabled", "enabled_by", "audit_seq", "updated_at", "session_state", "credential_stored", "readiness_complete", "open_breaks", "live_strategies", "read_only_sessions", "orders_enabled", "environment")
    BROKER_ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    ENABLED_FIELD_NUMBER: _ClassVar[int]
    ENABLED_BY_FIELD_NUMBER: _ClassVar[int]
    AUDIT_SEQ_FIELD_NUMBER: _ClassVar[int]
    UPDATED_AT_FIELD_NUMBER: _ClassVar[int]
    SESSION_STATE_FIELD_NUMBER: _ClassVar[int]
    CREDENTIAL_STORED_FIELD_NUMBER: _ClassVar[int]
    READINESS_COMPLETE_FIELD_NUMBER: _ClassVar[int]
    OPEN_BREAKS_FIELD_NUMBER: _ClassVar[int]
    LIVE_STRATEGIES_FIELD_NUMBER: _ClassVar[int]
    READ_ONLY_SESSIONS_FIELD_NUMBER: _ClassVar[int]
    ORDERS_ENABLED_FIELD_NUMBER: _ClassVar[int]
    ENVIRONMENT_FIELD_NUMBER: _ClassVar[int]
    broker_account_id: str
    enabled: bool
    enabled_by: str
    audit_seq: int
    updated_at: int
    session_state: str
    credential_stored: bool
    readiness_complete: bool
    open_breaks: int
    live_strategies: _containers.RepeatedScalarFieldContainer[str]
    read_only_sessions: int
    orders_enabled: bool
    environment: str
    def __init__(self, broker_account_id: _Optional[str] = ..., enabled: _Optional[bool] = ..., enabled_by: _Optional[str] = ..., audit_seq: _Optional[int] = ..., updated_at: _Optional[int] = ..., session_state: _Optional[str] = ..., credential_stored: _Optional[bool] = ..., readiness_complete: _Optional[bool] = ..., open_breaks: _Optional[int] = ..., live_strategies: _Optional[_Iterable[str]] = ..., read_only_sessions: _Optional[int] = ..., orders_enabled: _Optional[bool] = ..., environment: _Optional[str] = ...) -> None: ...

class LiveStatus(_message.Message):
    __slots__ = ("flag_enabled", "accounts", "live_strategies", "candidates", "as_of")
    FLAG_ENABLED_FIELD_NUMBER: _ClassVar[int]
    ACCOUNTS_FIELD_NUMBER: _ClassVar[int]
    LIVE_STRATEGIES_FIELD_NUMBER: _ClassVar[int]
    CANDIDATES_FIELD_NUMBER: _ClassVar[int]
    AS_OF_FIELD_NUMBER: _ClassVar[int]
    flag_enabled: bool
    accounts: _containers.RepeatedCompositeFieldContainer[LiveAccountStatus]
    live_strategies: _containers.RepeatedCompositeFieldContainer[Strategy]
    candidates: _containers.RepeatedCompositeFieldContainer[GraduationReport]
    as_of: int
    def __init__(self, flag_enabled: _Optional[bool] = ..., accounts: _Optional[_Iterable[_Union[LiveAccountStatus, _Mapping]]] = ..., live_strategies: _Optional[_Iterable[_Union[Strategy, _Mapping]]] = ..., candidates: _Optional[_Iterable[_Union[GraduationReport, _Mapping]]] = ..., as_of: _Optional[int] = ...) -> None: ...

class GetLiveStatusRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class GetLiveStatusResponse(_message.Message):
    __slots__ = ("status",)
    STATUS_FIELD_NUMBER: _ClassVar[int]
    status: LiveStatus
    def __init__(self, status: _Optional[_Union[LiveStatus, _Mapping]] = ...) -> None: ...

class GetLiveReadinessRequest(_message.Message):
    __slots__ = ("broker_account_id",)
    BROKER_ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    broker_account_id: str
    def __init__(self, broker_account_id: _Optional[str] = ...) -> None: ...

class GetLiveReadinessResponse(_message.Message):
    __slots__ = ("readiness",)
    READINESS_FIELD_NUMBER: _ClassVar[int]
    readiness: LiveReadiness
    def __init__(self, readiness: _Optional[_Union[LiveReadiness, _Mapping]] = ...) -> None: ...

class SetReadinessItemRequest(_message.Message):
    __slots__ = ("broker_account_id", "item_id", "status", "note", "evidence_url", "actor")
    BROKER_ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    ITEM_ID_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    NOTE_FIELD_NUMBER: _ClassVar[int]
    EVIDENCE_URL_FIELD_NUMBER: _ClassVar[int]
    ACTOR_FIELD_NUMBER: _ClassVar[int]
    broker_account_id: str
    item_id: str
    status: str
    note: str
    evidence_url: str
    actor: Actor
    def __init__(self, broker_account_id: _Optional[str] = ..., item_id: _Optional[str] = ..., status: _Optional[str] = ..., note: _Optional[str] = ..., evidence_url: _Optional[str] = ..., actor: _Optional[_Union[Actor, _Mapping]] = ...) -> None: ...

class SetReadinessItemResponse(_message.Message):
    __slots__ = ("readiness",)
    READINESS_FIELD_NUMBER: _ClassVar[int]
    readiness: LiveReadiness
    def __init__(self, readiness: _Optional[_Union[LiveReadiness, _Mapping]] = ...) -> None: ...

class GetGraduationReportRequest(_message.Message):
    __slots__ = ("strategy_id",)
    STRATEGY_ID_FIELD_NUMBER: _ClassVar[int]
    strategy_id: str
    def __init__(self, strategy_id: _Optional[str] = ...) -> None: ...

class GetGraduationReportResponse(_message.Message):
    __slots__ = ("report",)
    REPORT_FIELD_NUMBER: _ClassVar[int]
    report: GraduationReport
    def __init__(self, report: _Optional[_Union[GraduationReport, _Mapping]] = ...) -> None: ...

class EnableLiveAccountRequest(_message.Message):
    __slots__ = ("broker_account_id", "confirmation", "actor", "stage")
    BROKER_ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    CONFIRMATION_FIELD_NUMBER: _ClassVar[int]
    ACTOR_FIELD_NUMBER: _ClassVar[int]
    STAGE_FIELD_NUMBER: _ClassVar[int]
    broker_account_id: str
    confirmation: str
    actor: Actor
    stage: str
    def __init__(self, broker_account_id: _Optional[str] = ..., confirmation: _Optional[str] = ..., actor: _Optional[_Union[Actor, _Mapping]] = ..., stage: _Optional[str] = ...) -> None: ...

class EnableLiveAccountResponse(_message.Message):
    __slots__ = ("account",)
    ACCOUNT_FIELD_NUMBER: _ClassVar[int]
    account: LiveAccountStatus
    def __init__(self, account: _Optional[_Union[LiveAccountStatus, _Mapping]] = ...) -> None: ...

class DisableLiveAccountRequest(_message.Message):
    __slots__ = ("broker_account_id", "reason", "actor")
    BROKER_ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    ACTOR_FIELD_NUMBER: _ClassVar[int]
    broker_account_id: str
    reason: str
    actor: Actor
    def __init__(self, broker_account_id: _Optional[str] = ..., reason: _Optional[str] = ..., actor: _Optional[_Union[Actor, _Mapping]] = ...) -> None: ...

class DisableLiveAccountResponse(_message.Message):
    __slots__ = ("account", "orders_canceled", "demoted_strategies")
    ACCOUNT_FIELD_NUMBER: _ClassVar[int]
    ORDERS_CANCELED_FIELD_NUMBER: _ClassVar[int]
    DEMOTED_STRATEGIES_FIELD_NUMBER: _ClassVar[int]
    account: LiveAccountStatus
    orders_canceled: int
    demoted_strategies: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, account: _Optional[_Union[LiveAccountStatus, _Mapping]] = ..., orders_canceled: _Optional[int] = ..., demoted_strategies: _Optional[_Iterable[str]] = ...) -> None: ...

class PromoteToLiveRequest(_message.Message):
    __slots__ = ("strategy_id", "live_account_id", "capital_cap", "intended_allocation", "limit_overrides", "confirmation", "skip_paper_twin", "actor")
    STRATEGY_ID_FIELD_NUMBER: _ClassVar[int]
    LIVE_ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    CAPITAL_CAP_FIELD_NUMBER: _ClassVar[int]
    INTENDED_ALLOCATION_FIELD_NUMBER: _ClassVar[int]
    LIMIT_OVERRIDES_FIELD_NUMBER: _ClassVar[int]
    CONFIRMATION_FIELD_NUMBER: _ClassVar[int]
    SKIP_PAPER_TWIN_FIELD_NUMBER: _ClassVar[int]
    ACTOR_FIELD_NUMBER: _ClassVar[int]
    strategy_id: str
    live_account_id: str
    capital_cap: _common_pb2.Decimal
    intended_allocation: _common_pb2.Decimal
    limit_overrides: RiskLimits
    confirmation: str
    skip_paper_twin: bool
    actor: Actor
    def __init__(self, strategy_id: _Optional[str] = ..., live_account_id: _Optional[str] = ..., capital_cap: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., intended_allocation: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., limit_overrides: _Optional[_Union[RiskLimits, _Mapping]] = ..., confirmation: _Optional[str] = ..., skip_paper_twin: _Optional[bool] = ..., actor: _Optional[_Union[Actor, _Mapping]] = ...) -> None: ...

class PromoteToLiveResponse(_message.Message):
    __slots__ = ("strategy", "report", "live_limits", "paper_twin")
    STRATEGY_FIELD_NUMBER: _ClassVar[int]
    REPORT_FIELD_NUMBER: _ClassVar[int]
    LIVE_LIMITS_FIELD_NUMBER: _ClassVar[int]
    PAPER_TWIN_FIELD_NUMBER: _ClassVar[int]
    strategy: Strategy
    report: GraduationReport
    live_limits: RiskLimits
    paper_twin: Strategy
    def __init__(self, strategy: _Optional[_Union[Strategy, _Mapping]] = ..., report: _Optional[_Union[GraduationReport, _Mapping]] = ..., live_limits: _Optional[_Union[RiskLimits, _Mapping]] = ..., paper_twin: _Optional[_Union[Strategy, _Mapping]] = ...) -> None: ...

class DemoteFromLiveRequest(_message.Message):
    __slots__ = ("strategy_id", "reason", "position_policy", "actor")
    STRATEGY_ID_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    POSITION_POLICY_FIELD_NUMBER: _ClassVar[int]
    ACTOR_FIELD_NUMBER: _ClassVar[int]
    strategy_id: str
    reason: str
    position_policy: str
    actor: Actor
    def __init__(self, strategy_id: _Optional[str] = ..., reason: _Optional[str] = ..., position_policy: _Optional[str] = ..., actor: _Optional[_Union[Actor, _Mapping]] = ...) -> None: ...

class DemoteFromLiveResponse(_message.Message):
    __slots__ = ("strategy", "orders_canceled", "flatten_intents")
    STRATEGY_FIELD_NUMBER: _ClassVar[int]
    ORDERS_CANCELED_FIELD_NUMBER: _ClassVar[int]
    FLATTEN_INTENTS_FIELD_NUMBER: _ClassVar[int]
    strategy: Strategy
    orders_canceled: int
    flatten_intents: int
    def __init__(self, strategy: _Optional[_Union[Strategy, _Mapping]] = ..., orders_canceled: _Optional[int] = ..., flatten_intents: _Optional[int] = ...) -> None: ...

class RaiseLiveCapRequest(_message.Message):
    __slots__ = ("strategy_id", "new_cap", "confirmation", "actor")
    STRATEGY_ID_FIELD_NUMBER: _ClassVar[int]
    NEW_CAP_FIELD_NUMBER: _ClassVar[int]
    CONFIRMATION_FIELD_NUMBER: _ClassVar[int]
    ACTOR_FIELD_NUMBER: _ClassVar[int]
    strategy_id: str
    new_cap: _common_pb2.Decimal
    confirmation: str
    actor: Actor
    def __init__(self, strategy_id: _Optional[str] = ..., new_cap: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., confirmation: _Optional[str] = ..., actor: _Optional[_Union[Actor, _Mapping]] = ...) -> None: ...

class RaiseLiveCapResponse(_message.Message):
    __slots__ = ("strategy", "live_window_report")
    STRATEGY_FIELD_NUMBER: _ClassVar[int]
    LIVE_WINDOW_REPORT_FIELD_NUMBER: _ClassVar[int]
    strategy: Strategy
    live_window_report: GraduationReport
    def __init__(self, strategy: _Optional[_Union[Strategy, _Mapping]] = ..., live_window_report: _Optional[_Union[GraduationReport, _Mapping]] = ...) -> None: ...

class KillSwitch(_message.Message):
    __slots__ = ("event_id", "scope", "target", "action", "engaged_by", "reason", "engaged_at", "latched", "released_at", "released_by", "cancel_completed_ns")
    EVENT_ID_FIELD_NUMBER: _ClassVar[int]
    SCOPE_FIELD_NUMBER: _ClassVar[int]
    TARGET_FIELD_NUMBER: _ClassVar[int]
    ACTION_FIELD_NUMBER: _ClassVar[int]
    ENGAGED_BY_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    ENGAGED_AT_FIELD_NUMBER: _ClassVar[int]
    LATCHED_FIELD_NUMBER: _ClassVar[int]
    RELEASED_AT_FIELD_NUMBER: _ClassVar[int]
    RELEASED_BY_FIELD_NUMBER: _ClassVar[int]
    CANCEL_COMPLETED_NS_FIELD_NUMBER: _ClassVar[int]
    event_id: str
    scope: _orders_pb2.KillSwitchScope
    target: str
    action: _orders_pb2.KillSwitchAction
    engaged_by: str
    reason: str
    engaged_at: int
    latched: bool
    released_at: int
    released_by: str
    cancel_completed_ns: int
    def __init__(self, event_id: _Optional[str] = ..., scope: _Optional[_Union[_orders_pb2.KillSwitchScope, str]] = ..., target: _Optional[str] = ..., action: _Optional[_Union[_orders_pb2.KillSwitchAction, str]] = ..., engaged_by: _Optional[str] = ..., reason: _Optional[str] = ..., engaged_at: _Optional[int] = ..., latched: _Optional[bool] = ..., released_at: _Optional[int] = ..., released_by: _Optional[str] = ..., cancel_completed_ns: _Optional[int] = ...) -> None: ...

class Halt(_message.Message):
    __slots__ = ("halt_id", "broker_account_id", "strategy_id", "instrument_id", "reason", "since", "cleared_at", "source")
    HALT_ID_FIELD_NUMBER: _ClassVar[int]
    BROKER_ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    STRATEGY_ID_FIELD_NUMBER: _ClassVar[int]
    INSTRUMENT_ID_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    SINCE_FIELD_NUMBER: _ClassVar[int]
    CLEARED_AT_FIELD_NUMBER: _ClassVar[int]
    SOURCE_FIELD_NUMBER: _ClassVar[int]
    halt_id: str
    broker_account_id: str
    strategy_id: str
    instrument_id: str
    reason: str
    since: int
    cleared_at: int
    source: str
    def __init__(self, halt_id: _Optional[str] = ..., broker_account_id: _Optional[str] = ..., strategy_id: _Optional[str] = ..., instrument_id: _Optional[str] = ..., reason: _Optional[str] = ..., since: _Optional[int] = ..., cleared_at: _Optional[int] = ..., source: _Optional[str] = ...) -> None: ...

class IntentRecord(_message.Message):
    __slots__ = ("intent", "decision", "state", "received_at")
    INTENT_FIELD_NUMBER: _ClassVar[int]
    DECISION_FIELD_NUMBER: _ClassVar[int]
    STATE_FIELD_NUMBER: _ClassVar[int]
    RECEIVED_AT_FIELD_NUMBER: _ClassVar[int]
    intent: _orders_pb2.OrderIntent
    decision: _orders_pb2.RiskDecision
    state: str
    received_at: int
    def __init__(self, intent: _Optional[_Union[_orders_pb2.OrderIntent, _Mapping]] = ..., decision: _Optional[_Union[_orders_pb2.RiskDecision, _Mapping]] = ..., state: _Optional[str] = ..., received_at: _Optional[int] = ...) -> None: ...

class Fill(_message.Message):
    __slots__ = ("broker_account_id", "broker_exec_id", "order_id", "intent_id", "strategy_id", "instrument_id", "side", "qty", "price", "fees", "event_time", "correlation_id", "client_order_id")
    BROKER_ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    BROKER_EXEC_ID_FIELD_NUMBER: _ClassVar[int]
    ORDER_ID_FIELD_NUMBER: _ClassVar[int]
    INTENT_ID_FIELD_NUMBER: _ClassVar[int]
    STRATEGY_ID_FIELD_NUMBER: _ClassVar[int]
    INSTRUMENT_ID_FIELD_NUMBER: _ClassVar[int]
    SIDE_FIELD_NUMBER: _ClassVar[int]
    QTY_FIELD_NUMBER: _ClassVar[int]
    PRICE_FIELD_NUMBER: _ClassVar[int]
    FEES_FIELD_NUMBER: _ClassVar[int]
    EVENT_TIME_FIELD_NUMBER: _ClassVar[int]
    CORRELATION_ID_FIELD_NUMBER: _ClassVar[int]
    CLIENT_ORDER_ID_FIELD_NUMBER: _ClassVar[int]
    broker_account_id: str
    broker_exec_id: str
    order_id: str
    intent_id: str
    strategy_id: str
    instrument_id: str
    side: _common_pb2.Side
    qty: _common_pb2.Decimal
    price: _common_pb2.Decimal
    fees: _common_pb2.Decimal
    event_time: int
    correlation_id: str
    client_order_id: str
    def __init__(self, broker_account_id: _Optional[str] = ..., broker_exec_id: _Optional[str] = ..., order_id: _Optional[str] = ..., intent_id: _Optional[str] = ..., strategy_id: _Optional[str] = ..., instrument_id: _Optional[str] = ..., side: _Optional[_Union[_common_pb2.Side, str]] = ..., qty: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., price: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., fees: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., event_time: _Optional[int] = ..., correlation_id: _Optional[str] = ..., client_order_id: _Optional[str] = ...) -> None: ...

class AccountTradingStatus(_message.Message):
    __slots__ = ("broker_account_id", "session_state", "reconciliation_status", "reconciliation_fresh", "intraday_margin_deficit", "margin_call", "day_trade_count", "pdt_guard", "accepting_intents", "blocked_reasons", "environment", "margin_utilization")
    BROKER_ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    SESSION_STATE_FIELD_NUMBER: _ClassVar[int]
    RECONCILIATION_STATUS_FIELD_NUMBER: _ClassVar[int]
    RECONCILIATION_FRESH_FIELD_NUMBER: _ClassVar[int]
    INTRADAY_MARGIN_DEFICIT_FIELD_NUMBER: _ClassVar[int]
    MARGIN_CALL_FIELD_NUMBER: _ClassVar[int]
    DAY_TRADE_COUNT_FIELD_NUMBER: _ClassVar[int]
    PDT_GUARD_FIELD_NUMBER: _ClassVar[int]
    ACCEPTING_INTENTS_FIELD_NUMBER: _ClassVar[int]
    BLOCKED_REASONS_FIELD_NUMBER: _ClassVar[int]
    ENVIRONMENT_FIELD_NUMBER: _ClassVar[int]
    MARGIN_UTILIZATION_FIELD_NUMBER: _ClassVar[int]
    broker_account_id: str
    session_state: str
    reconciliation_status: str
    reconciliation_fresh: bool
    intraday_margin_deficit: bool
    margin_call: bool
    day_trade_count: int
    pdt_guard: str
    accepting_intents: bool
    blocked_reasons: _containers.RepeatedScalarFieldContainer[str]
    environment: str
    margin_utilization: _common_pb2.Decimal
    def __init__(self, broker_account_id: _Optional[str] = ..., session_state: _Optional[str] = ..., reconciliation_status: _Optional[str] = ..., reconciliation_fresh: _Optional[bool] = ..., intraday_margin_deficit: _Optional[bool] = ..., margin_call: _Optional[bool] = ..., day_trade_count: _Optional[int] = ..., pdt_guard: _Optional[str] = ..., accepting_intents: _Optional[bool] = ..., blocked_reasons: _Optional[_Iterable[str]] = ..., environment: _Optional[str] = ..., margin_utilization: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ...) -> None: ...

class StrategyTradingStatus(_message.Message):
    __slots__ = ("strategy_id", "mode", "accepting_intents", "blocked_reasons", "last_heartbeat")
    STRATEGY_ID_FIELD_NUMBER: _ClassVar[int]
    MODE_FIELD_NUMBER: _ClassVar[int]
    ACCEPTING_INTENTS_FIELD_NUMBER: _ClassVar[int]
    BLOCKED_REASONS_FIELD_NUMBER: _ClassVar[int]
    LAST_HEARTBEAT_FIELD_NUMBER: _ClassVar[int]
    strategy_id: str
    mode: StrategyMode
    accepting_intents: bool
    blocked_reasons: _containers.RepeatedScalarFieldContainer[str]
    last_heartbeat: int
    def __init__(self, strategy_id: _Optional[str] = ..., mode: _Optional[_Union[StrategyMode, str]] = ..., accepting_intents: _Optional[bool] = ..., blocked_reasons: _Optional[_Iterable[str]] = ..., last_heartbeat: _Optional[int] = ...) -> None: ...

class TradingStatus(_message.Message):
    __slots__ = ("global_state", "accounts", "strategies", "active_kill_switches", "active_halts", "as_of", "clock_ok", "open_orders", "runtime_last_heartbeat")
    GLOBAL_STATE_FIELD_NUMBER: _ClassVar[int]
    ACCOUNTS_FIELD_NUMBER: _ClassVar[int]
    STRATEGIES_FIELD_NUMBER: _ClassVar[int]
    ACTIVE_KILL_SWITCHES_FIELD_NUMBER: _ClassVar[int]
    ACTIVE_HALTS_FIELD_NUMBER: _ClassVar[int]
    AS_OF_FIELD_NUMBER: _ClassVar[int]
    CLOCK_OK_FIELD_NUMBER: _ClassVar[int]
    OPEN_ORDERS_FIELD_NUMBER: _ClassVar[int]
    RUNTIME_LAST_HEARTBEAT_FIELD_NUMBER: _ClassVar[int]
    global_state: str
    accounts: _containers.RepeatedCompositeFieldContainer[AccountTradingStatus]
    strategies: _containers.RepeatedCompositeFieldContainer[StrategyTradingStatus]
    active_kill_switches: _containers.RepeatedCompositeFieldContainer[KillSwitch]
    active_halts: _containers.RepeatedCompositeFieldContainer[Halt]
    as_of: int
    clock_ok: bool
    open_orders: int
    runtime_last_heartbeat: int
    def __init__(self, global_state: _Optional[str] = ..., accounts: _Optional[_Iterable[_Union[AccountTradingStatus, _Mapping]]] = ..., strategies: _Optional[_Iterable[_Union[StrategyTradingStatus, _Mapping]]] = ..., active_kill_switches: _Optional[_Iterable[_Union[KillSwitch, _Mapping]]] = ..., active_halts: _Optional[_Iterable[_Union[Halt, _Mapping]]] = ..., as_of: _Optional[int] = ..., clock_ok: _Optional[bool] = ..., open_orders: _Optional[int] = ..., runtime_last_heartbeat: _Optional[int] = ...) -> None: ...

class Actor(_message.Message):
    __slots__ = ("name", "step_up_method", "mobile", "ip", "device")
    NAME_FIELD_NUMBER: _ClassVar[int]
    STEP_UP_METHOD_FIELD_NUMBER: _ClassVar[int]
    MOBILE_FIELD_NUMBER: _ClassVar[int]
    IP_FIELD_NUMBER: _ClassVar[int]
    DEVICE_FIELD_NUMBER: _ClassVar[int]
    name: str
    step_up_method: str
    mobile: bool
    ip: str
    device: str
    def __init__(self, name: _Optional[str] = ..., step_up_method: _Optional[str] = ..., mobile: _Optional[bool] = ..., ip: _Optional[str] = ..., device: _Optional[str] = ...) -> None: ...

class SubmitIntentRequest(_message.Message):
    __slots__ = ("intent", "actor")
    INTENT_FIELD_NUMBER: _ClassVar[int]
    ACTOR_FIELD_NUMBER: _ClassVar[int]
    intent: _orders_pb2.OrderIntent
    actor: Actor
    def __init__(self, intent: _Optional[_Union[_orders_pb2.OrderIntent, _Mapping]] = ..., actor: _Optional[_Union[Actor, _Mapping]] = ...) -> None: ...

class SubmitIntentResponse(_message.Message):
    __slots__ = ("decision", "orders", "duplicate")
    DECISION_FIELD_NUMBER: _ClassVar[int]
    ORDERS_FIELD_NUMBER: _ClassVar[int]
    DUPLICATE_FIELD_NUMBER: _ClassVar[int]
    decision: _orders_pb2.RiskDecision
    orders: _containers.RepeatedCompositeFieldContainer[_orders_pb2.Order]
    duplicate: bool
    def __init__(self, decision: _Optional[_Union[_orders_pb2.RiskDecision, _Mapping]] = ..., orders: _Optional[_Iterable[_Union[_orders_pb2.Order, _Mapping]]] = ..., duplicate: _Optional[bool] = ...) -> None: ...

class PreviewIntentRequest(_message.Message):
    __slots__ = ("intent",)
    INTENT_FIELD_NUMBER: _ClassVar[int]
    intent: _orders_pb2.OrderIntent
    def __init__(self, intent: _Optional[_Union[_orders_pb2.OrderIntent, _Mapping]] = ...) -> None: ...

class PreviewIntentResponse(_message.Message):
    __slots__ = ("decision", "estimated_notional", "estimated_price", "ownership_note")
    DECISION_FIELD_NUMBER: _ClassVar[int]
    ESTIMATED_NOTIONAL_FIELD_NUMBER: _ClassVar[int]
    ESTIMATED_PRICE_FIELD_NUMBER: _ClassVar[int]
    OWNERSHIP_NOTE_FIELD_NUMBER: _ClassVar[int]
    decision: _orders_pb2.RiskDecision
    estimated_notional: _common_pb2.Decimal
    estimated_price: _common_pb2.Decimal
    ownership_note: str
    def __init__(self, decision: _Optional[_Union[_orders_pb2.RiskDecision, _Mapping]] = ..., estimated_notional: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., estimated_price: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., ownership_note: _Optional[str] = ...) -> None: ...

class CancelIntentRequest(_message.Message):
    __slots__ = ("intent_id", "actor")
    INTENT_ID_FIELD_NUMBER: _ClassVar[int]
    ACTOR_FIELD_NUMBER: _ClassVar[int]
    intent_id: str
    actor: Actor
    def __init__(self, intent_id: _Optional[str] = ..., actor: _Optional[_Union[Actor, _Mapping]] = ...) -> None: ...

class CancelIntentResponse(_message.Message):
    __slots__ = ("canceled",)
    CANCELED_FIELD_NUMBER: _ClassVar[int]
    canceled: int
    def __init__(self, canceled: _Optional[int] = ...) -> None: ...

class ListOrdersRequest(_message.Message):
    __slots__ = ("broker_account_id", "strategy_id", "open_only", "since", "limit")
    BROKER_ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    STRATEGY_ID_FIELD_NUMBER: _ClassVar[int]
    OPEN_ONLY_FIELD_NUMBER: _ClassVar[int]
    SINCE_FIELD_NUMBER: _ClassVar[int]
    LIMIT_FIELD_NUMBER: _ClassVar[int]
    broker_account_id: str
    strategy_id: str
    open_only: bool
    since: int
    limit: int
    def __init__(self, broker_account_id: _Optional[str] = ..., strategy_id: _Optional[str] = ..., open_only: _Optional[bool] = ..., since: _Optional[int] = ..., limit: _Optional[int] = ...) -> None: ...

class ListOrdersResponse(_message.Message):
    __slots__ = ("orders",)
    ORDERS_FIELD_NUMBER: _ClassVar[int]
    orders: _containers.RepeatedCompositeFieldContainer[_orders_pb2.Order]
    def __init__(self, orders: _Optional[_Iterable[_Union[_orders_pb2.Order, _Mapping]]] = ...) -> None: ...

class ListIntentsRequest(_message.Message):
    __slots__ = ("strategy_id", "rejected_only", "since", "limit")
    STRATEGY_ID_FIELD_NUMBER: _ClassVar[int]
    REJECTED_ONLY_FIELD_NUMBER: _ClassVar[int]
    SINCE_FIELD_NUMBER: _ClassVar[int]
    LIMIT_FIELD_NUMBER: _ClassVar[int]
    strategy_id: str
    rejected_only: bool
    since: int
    limit: int
    def __init__(self, strategy_id: _Optional[str] = ..., rejected_only: _Optional[bool] = ..., since: _Optional[int] = ..., limit: _Optional[int] = ...) -> None: ...

class ListIntentsResponse(_message.Message):
    __slots__ = ("intents",)
    INTENTS_FIELD_NUMBER: _ClassVar[int]
    intents: _containers.RepeatedCompositeFieldContainer[IntentRecord]
    def __init__(self, intents: _Optional[_Iterable[_Union[IntentRecord, _Mapping]]] = ...) -> None: ...

class ListFillsRequest(_message.Message):
    __slots__ = ("broker_account_id", "strategy_id", "since", "limit")
    BROKER_ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    STRATEGY_ID_FIELD_NUMBER: _ClassVar[int]
    SINCE_FIELD_NUMBER: _ClassVar[int]
    LIMIT_FIELD_NUMBER: _ClassVar[int]
    broker_account_id: str
    strategy_id: str
    since: int
    limit: int
    def __init__(self, broker_account_id: _Optional[str] = ..., strategy_id: _Optional[str] = ..., since: _Optional[int] = ..., limit: _Optional[int] = ...) -> None: ...

class ListFillsResponse(_message.Message):
    __slots__ = ("fills",)
    FILLS_FIELD_NUMBER: _ClassVar[int]
    fills: _containers.RepeatedCompositeFieldContainer[Fill]
    def __init__(self, fills: _Optional[_Iterable[_Union[Fill, _Mapping]]] = ...) -> None: ...

class GetTraceRequest(_message.Message):
    __slots__ = ("correlation_id",)
    CORRELATION_ID_FIELD_NUMBER: _ClassVar[int]
    correlation_id: str
    def __init__(self, correlation_id: _Optional[str] = ...) -> None: ...

class GetTraceResponse(_message.Message):
    __slots__ = ("intents", "orders", "fills")
    INTENTS_FIELD_NUMBER: _ClassVar[int]
    ORDERS_FIELD_NUMBER: _ClassVar[int]
    FILLS_FIELD_NUMBER: _ClassVar[int]
    intents: _containers.RepeatedCompositeFieldContainer[IntentRecord]
    orders: _containers.RepeatedCompositeFieldContainer[_orders_pb2.Order]
    fills: _containers.RepeatedCompositeFieldContainer[Fill]
    def __init__(self, intents: _Optional[_Iterable[_Union[IntentRecord, _Mapping]]] = ..., orders: _Optional[_Iterable[_Union[_orders_pb2.Order, _Mapping]]] = ..., fills: _Optional[_Iterable[_Union[Fill, _Mapping]]] = ...) -> None: ...

class ListStrategiesRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class ListStrategiesResponse(_message.Message):
    __slots__ = ("strategies",)
    STRATEGIES_FIELD_NUMBER: _ClassVar[int]
    strategies: _containers.RepeatedCompositeFieldContainer[Strategy]
    def __init__(self, strategies: _Optional[_Iterable[_Union[Strategy, _Mapping]]] = ...) -> None: ...

class RegisterStrategyRequest(_message.Message):
    __slots__ = ("strategy", "actor")
    STRATEGY_FIELD_NUMBER: _ClassVar[int]
    ACTOR_FIELD_NUMBER: _ClassVar[int]
    strategy: Strategy
    actor: Actor
    def __init__(self, strategy: _Optional[_Union[Strategy, _Mapping]] = ..., actor: _Optional[_Union[Actor, _Mapping]] = ...) -> None: ...

class RegisterStrategyResponse(_message.Message):
    __slots__ = ("strategy",)
    STRATEGY_FIELD_NUMBER: _ClassVar[int]
    strategy: Strategy
    def __init__(self, strategy: _Optional[_Union[Strategy, _Mapping]] = ...) -> None: ...

class SetStrategyModeRequest(_message.Message):
    __slots__ = ("strategy_id", "mode", "reason", "actor")
    STRATEGY_ID_FIELD_NUMBER: _ClassVar[int]
    MODE_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    ACTOR_FIELD_NUMBER: _ClassVar[int]
    strategy_id: str
    mode: StrategyMode
    reason: str
    actor: Actor
    def __init__(self, strategy_id: _Optional[str] = ..., mode: _Optional[_Union[StrategyMode, str]] = ..., reason: _Optional[str] = ..., actor: _Optional[_Union[Actor, _Mapping]] = ...) -> None: ...

class SetStrategyModeResponse(_message.Message):
    __slots__ = ("strategy",)
    STRATEGY_FIELD_NUMBER: _ClassVar[int]
    strategy: Strategy
    def __init__(self, strategy: _Optional[_Union[Strategy, _Mapping]] = ...) -> None: ...

class DemoteStrategyRequest(_message.Message):
    __slots__ = ("strategy_id", "reason", "evidence_id", "actor", "correlation_id")
    STRATEGY_ID_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    EVIDENCE_ID_FIELD_NUMBER: _ClassVar[int]
    ACTOR_FIELD_NUMBER: _ClassVar[int]
    CORRELATION_ID_FIELD_NUMBER: _ClassVar[int]
    strategy_id: str
    reason: str
    evidence_id: str
    actor: Actor
    correlation_id: str
    def __init__(self, strategy_id: _Optional[str] = ..., reason: _Optional[str] = ..., evidence_id: _Optional[str] = ..., actor: _Optional[_Union[Actor, _Mapping]] = ..., correlation_id: _Optional[str] = ...) -> None: ...

class DemoteStrategyResponse(_message.Message):
    __slots__ = ("strategy", "changed", "kill_switch_event_id", "orders_canceled")
    STRATEGY_FIELD_NUMBER: _ClassVar[int]
    FROM_FIELD_NUMBER: _ClassVar[int]
    CHANGED_FIELD_NUMBER: _ClassVar[int]
    KILL_SWITCH_EVENT_ID_FIELD_NUMBER: _ClassVar[int]
    ORDERS_CANCELED_FIELD_NUMBER: _ClassVar[int]
    strategy: Strategy
    changed: bool
    kill_switch_event_id: str
    orders_canceled: int
    def __init__(self, strategy: _Optional[_Union[Strategy, _Mapping]] = ..., changed: _Optional[bool] = ..., kill_switch_event_id: _Optional[str] = ..., orders_canceled: _Optional[int] = ..., **kwargs) -> None: ...

class GetRiskLimitsRequest(_message.Message):
    __slots__ = ("scope",)
    SCOPE_FIELD_NUMBER: _ClassVar[int]
    scope: str
    def __init__(self, scope: _Optional[str] = ...) -> None: ...

class GetRiskLimitsResponse(_message.Message):
    __slots__ = ("scope", "limits", "effective")
    SCOPE_FIELD_NUMBER: _ClassVar[int]
    LIMITS_FIELD_NUMBER: _ClassVar[int]
    EFFECTIVE_FIELD_NUMBER: _ClassVar[int]
    scope: str
    limits: RiskLimits
    effective: RiskLimits
    def __init__(self, scope: _Optional[str] = ..., limits: _Optional[_Union[RiskLimits, _Mapping]] = ..., effective: _Optional[_Union[RiskLimits, _Mapping]] = ...) -> None: ...

class SetRiskLimitsRequest(_message.Message):
    __slots__ = ("scope", "limits", "actor")
    SCOPE_FIELD_NUMBER: _ClassVar[int]
    LIMITS_FIELD_NUMBER: _ClassVar[int]
    ACTOR_FIELD_NUMBER: _ClassVar[int]
    scope: str
    limits: RiskLimits
    actor: Actor
    def __init__(self, scope: _Optional[str] = ..., limits: _Optional[_Union[RiskLimits, _Mapping]] = ..., actor: _Optional[_Union[Actor, _Mapping]] = ...) -> None: ...

class SetRiskLimitsResponse(_message.Message):
    __slots__ = ("limits", "raised")
    LIMITS_FIELD_NUMBER: _ClassVar[int]
    RAISED_FIELD_NUMBER: _ClassVar[int]
    limits: RiskLimits
    raised: bool
    def __init__(self, limits: _Optional[_Union[RiskLimits, _Mapping]] = ..., raised: _Optional[bool] = ...) -> None: ...

class TriggerKillSwitchRequest(_message.Message):
    __slots__ = ("scope", "target", "action", "reason", "actor")
    SCOPE_FIELD_NUMBER: _ClassVar[int]
    TARGET_FIELD_NUMBER: _ClassVar[int]
    ACTION_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    ACTOR_FIELD_NUMBER: _ClassVar[int]
    scope: _orders_pb2.KillSwitchScope
    target: str
    action: _orders_pb2.KillSwitchAction
    reason: str
    actor: Actor
    def __init__(self, scope: _Optional[_Union[_orders_pb2.KillSwitchScope, str]] = ..., target: _Optional[str] = ..., action: _Optional[_Union[_orders_pb2.KillSwitchAction, str]] = ..., reason: _Optional[str] = ..., actor: _Optional[_Union[Actor, _Mapping]] = ...) -> None: ...

class TriggerKillSwitchResponse(_message.Message):
    __slots__ = ("kill_switch", "orders_canceled", "flatten_intents")
    KILL_SWITCH_FIELD_NUMBER: _ClassVar[int]
    ORDERS_CANCELED_FIELD_NUMBER: _ClassVar[int]
    FLATTEN_INTENTS_FIELD_NUMBER: _ClassVar[int]
    kill_switch: KillSwitch
    orders_canceled: int
    flatten_intents: int
    def __init__(self, kill_switch: _Optional[_Union[KillSwitch, _Mapping]] = ..., orders_canceled: _Optional[int] = ..., flatten_intents: _Optional[int] = ...) -> None: ...

class ReleaseKillSwitchRequest(_message.Message):
    __slots__ = ("event_id", "reason", "actor")
    EVENT_ID_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    ACTOR_FIELD_NUMBER: _ClassVar[int]
    event_id: str
    reason: str
    actor: Actor
    def __init__(self, event_id: _Optional[str] = ..., reason: _Optional[str] = ..., actor: _Optional[_Union[Actor, _Mapping]] = ...) -> None: ...

class ReleaseKillSwitchResponse(_message.Message):
    __slots__ = ("kill_switch",)
    KILL_SWITCH_FIELD_NUMBER: _ClassVar[int]
    kill_switch: KillSwitch
    def __init__(self, kill_switch: _Optional[_Union[KillSwitch, _Mapping]] = ...) -> None: ...

class ListKillSwitchesRequest(_message.Message):
    __slots__ = ("active_only",)
    ACTIVE_ONLY_FIELD_NUMBER: _ClassVar[int]
    active_only: bool
    def __init__(self, active_only: _Optional[bool] = ...) -> None: ...

class ListKillSwitchesResponse(_message.Message):
    __slots__ = ("kill_switches",)
    KILL_SWITCHES_FIELD_NUMBER: _ClassVar[int]
    kill_switches: _containers.RepeatedCompositeFieldContainer[KillSwitch]
    def __init__(self, kill_switches: _Optional[_Iterable[_Union[KillSwitch, _Mapping]]] = ...) -> None: ...

class GetTradingStatusRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class GetTradingStatusResponse(_message.Message):
    __slots__ = ("status",)
    STATUS_FIELD_NUMBER: _ClassVar[int]
    status: TradingStatus
    def __init__(self, status: _Optional[_Union[TradingStatus, _Mapping]] = ...) -> None: ...
