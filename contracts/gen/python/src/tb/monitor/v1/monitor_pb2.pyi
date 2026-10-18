from tb.alerts.v1 import alerts_pb2 as _alerts_pb2
from tb.monitor.v1 import feedback_pb2 as _feedback_pb2
from tb.orders.v1 import execution_pb2 as _execution_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class HealthStatus(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    HEALTH_STATUS_UNSPECIFIED: _ClassVar[HealthStatus]
    HEALTH_STATUS_OK: _ClassVar[HealthStatus]
    HEALTH_STATUS_WARNING: _ClassVar[HealthStatus]
    HEALTH_STATUS_BREACH: _ClassVar[HealthStatus]
    HEALTH_STATUS_SEVERE: _ClassVar[HealthStatus]
    HEALTH_STATUS_INSUFFICIENT_DATA: _ClassVar[HealthStatus]

class ContainmentActionKind(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    CONTAINMENT_ACTION_KIND_UNSPECIFIED: _ClassVar[ContainmentActionKind]
    CONTAINMENT_ACTION_KIND_ALERT: _ClassVar[ContainmentActionKind]
    CONTAINMENT_ACTION_KIND_DEMOTE: _ClassVar[ContainmentActionKind]
    CONTAINMENT_ACTION_KIND_KILL_CANCEL_OPEN: _ClassVar[ContainmentActionKind]
    CONTAINMENT_ACTION_KIND_KILL_FLATTEN: _ClassVar[ContainmentActionKind]
HEALTH_STATUS_UNSPECIFIED: HealthStatus
HEALTH_STATUS_OK: HealthStatus
HEALTH_STATUS_WARNING: HealthStatus
HEALTH_STATUS_BREACH: HealthStatus
HEALTH_STATUS_SEVERE: HealthStatus
HEALTH_STATUS_INSUFFICIENT_DATA: HealthStatus
CONTAINMENT_ACTION_KIND_UNSPECIFIED: ContainmentActionKind
CONTAINMENT_ACTION_KIND_ALERT: ContainmentActionKind
CONTAINMENT_ACTION_KIND_DEMOTE: ContainmentActionKind
CONTAINMENT_ACTION_KIND_KILL_CANCEL_OPEN: ContainmentActionKind
CONTAINMENT_ACTION_KIND_KILL_FLATTEN: ContainmentActionKind

class HealthComponent(_message.Message):
    __slots__ = ("name", "status", "value", "warning_threshold", "breach_threshold", "score", "detail", "evidence_id", "samples")
    NAME_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    VALUE_FIELD_NUMBER: _ClassVar[int]
    WARNING_THRESHOLD_FIELD_NUMBER: _ClassVar[int]
    BREACH_THRESHOLD_FIELD_NUMBER: _ClassVar[int]
    SCORE_FIELD_NUMBER: _ClassVar[int]
    DETAIL_FIELD_NUMBER: _ClassVar[int]
    EVIDENCE_ID_FIELD_NUMBER: _ClassVar[int]
    SAMPLES_FIELD_NUMBER: _ClassVar[int]
    name: str
    status: HealthStatus
    value: float
    warning_threshold: float
    breach_threshold: float
    score: float
    detail: str
    evidence_id: str
    samples: int
    def __init__(self, name: _Optional[str] = ..., status: _Optional[_Union[HealthStatus, str]] = ..., value: _Optional[float] = ..., warning_threshold: _Optional[float] = ..., breach_threshold: _Optional[float] = ..., score: _Optional[float] = ..., detail: _Optional[str] = ..., evidence_id: _Optional[str] = ..., samples: _Optional[int] = ...) -> None: ...

class StrategyHealth(_message.Message):
    __slots__ = ("strategy_id", "lineage_id", "model_version", "mode", "score", "status", "components", "sessions", "trades", "updated_at", "last_action_id", "horizon")
    STRATEGY_ID_FIELD_NUMBER: _ClassVar[int]
    LINEAGE_ID_FIELD_NUMBER: _ClassVar[int]
    MODEL_VERSION_FIELD_NUMBER: _ClassVar[int]
    MODE_FIELD_NUMBER: _ClassVar[int]
    SCORE_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    COMPONENTS_FIELD_NUMBER: _ClassVar[int]
    SESSIONS_FIELD_NUMBER: _ClassVar[int]
    TRADES_FIELD_NUMBER: _ClassVar[int]
    UPDATED_AT_FIELD_NUMBER: _ClassVar[int]
    LAST_ACTION_ID_FIELD_NUMBER: _ClassVar[int]
    HORIZON_FIELD_NUMBER: _ClassVar[int]
    strategy_id: str
    lineage_id: str
    model_version: str
    mode: str
    score: float
    status: HealthStatus
    components: _containers.RepeatedCompositeFieldContainer[HealthComponent]
    sessions: int
    trades: int
    updated_at: int
    last_action_id: str
    horizon: str
    def __init__(self, strategy_id: _Optional[str] = ..., lineage_id: _Optional[str] = ..., model_version: _Optional[str] = ..., mode: _Optional[str] = ..., score: _Optional[float] = ..., status: _Optional[_Union[HealthStatus, str]] = ..., components: _Optional[_Iterable[_Union[HealthComponent, _Mapping]]] = ..., sessions: _Optional[int] = ..., trades: _Optional[int] = ..., updated_at: _Optional[int] = ..., last_action_id: _Optional[str] = ..., horizon: _Optional[str] = ...) -> None: ...

class PerformanceBand(_message.Message):
    __slots__ = ("metric", "live", "p05", "p10", "p50", "p90", "p95", "p99", "percentile", "horizon_sessions")
    METRIC_FIELD_NUMBER: _ClassVar[int]
    LIVE_FIELD_NUMBER: _ClassVar[int]
    P05_FIELD_NUMBER: _ClassVar[int]
    P10_FIELD_NUMBER: _ClassVar[int]
    P50_FIELD_NUMBER: _ClassVar[int]
    P90_FIELD_NUMBER: _ClassVar[int]
    P95_FIELD_NUMBER: _ClassVar[int]
    P99_FIELD_NUMBER: _ClassVar[int]
    PERCENTILE_FIELD_NUMBER: _ClassVar[int]
    HORIZON_SESSIONS_FIELD_NUMBER: _ClassVar[int]
    metric: str
    live: float
    p05: float
    p10: float
    p50: float
    p90: float
    p95: float
    p99: float
    percentile: float
    horizon_sessions: int
    def __init__(self, metric: _Optional[str] = ..., live: _Optional[float] = ..., p05: _Optional[float] = ..., p10: _Optional[float] = ..., p50: _Optional[float] = ..., p90: _Optional[float] = ..., p95: _Optional[float] = ..., p99: _Optional[float] = ..., percentile: _Optional[float] = ..., horizon_sessions: _Optional[int] = ...) -> None: ...

class PerformanceReport(_message.Message):
    __slots__ = ("report_id", "strategy_id", "window_start", "window_end", "sessions", "trades", "bands", "session_returns_bps", "drawdown_pct", "max_drawdown_pct", "drawdown_p95_pct", "drawdown_p99_pct", "backtest_report_id", "generated_at")
    REPORT_ID_FIELD_NUMBER: _ClassVar[int]
    STRATEGY_ID_FIELD_NUMBER: _ClassVar[int]
    WINDOW_START_FIELD_NUMBER: _ClassVar[int]
    WINDOW_END_FIELD_NUMBER: _ClassVar[int]
    SESSIONS_FIELD_NUMBER: _ClassVar[int]
    TRADES_FIELD_NUMBER: _ClassVar[int]
    BANDS_FIELD_NUMBER: _ClassVar[int]
    SESSION_RETURNS_BPS_FIELD_NUMBER: _ClassVar[int]
    DRAWDOWN_PCT_FIELD_NUMBER: _ClassVar[int]
    MAX_DRAWDOWN_PCT_FIELD_NUMBER: _ClassVar[int]
    DRAWDOWN_P95_PCT_FIELD_NUMBER: _ClassVar[int]
    DRAWDOWN_P99_PCT_FIELD_NUMBER: _ClassVar[int]
    BACKTEST_REPORT_ID_FIELD_NUMBER: _ClassVar[int]
    GENERATED_AT_FIELD_NUMBER: _ClassVar[int]
    report_id: str
    strategy_id: str
    window_start: int
    window_end: int
    sessions: int
    trades: int
    bands: _containers.RepeatedCompositeFieldContainer[PerformanceBand]
    session_returns_bps: _containers.RepeatedScalarFieldContainer[float]
    drawdown_pct: float
    max_drawdown_pct: float
    drawdown_p95_pct: float
    drawdown_p99_pct: float
    backtest_report_id: str
    generated_at: int
    def __init__(self, report_id: _Optional[str] = ..., strategy_id: _Optional[str] = ..., window_start: _Optional[int] = ..., window_end: _Optional[int] = ..., sessions: _Optional[int] = ..., trades: _Optional[int] = ..., bands: _Optional[_Iterable[_Union[PerformanceBand, _Mapping]]] = ..., session_returns_bps: _Optional[_Iterable[float]] = ..., drawdown_pct: _Optional[float] = ..., max_drawdown_pct: _Optional[float] = ..., drawdown_p95_pct: _Optional[float] = ..., drawdown_p99_pct: _Optional[float] = ..., backtest_report_id: _Optional[str] = ..., generated_at: _Optional[int] = ...) -> None: ...

class FeatureMissing(_message.Message):
    __slots__ = ("feature", "rate")
    FEATURE_FIELD_NUMBER: _ClassVar[int]
    RATE_FIELD_NUMBER: _ClassVar[int]
    feature: str
    rate: float
    def __init__(self, feature: _Optional[str] = ..., rate: _Optional[float] = ...) -> None: ...

class DriftReport(_message.Message):
    __slots__ = ("report_id", "strategy_id", "model_version", "window_start", "window_end", "features", "prediction_drift", "missing", "calibration", "signal_rate_live", "signal_rate_reference", "rolling_ic", "reference_ic", "resolved", "live_samples", "reference_samples", "reference_run_id", "conditions", "generated_at")
    REPORT_ID_FIELD_NUMBER: _ClassVar[int]
    STRATEGY_ID_FIELD_NUMBER: _ClassVar[int]
    MODEL_VERSION_FIELD_NUMBER: _ClassVar[int]
    WINDOW_START_FIELD_NUMBER: _ClassVar[int]
    WINDOW_END_FIELD_NUMBER: _ClassVar[int]
    FEATURES_FIELD_NUMBER: _ClassVar[int]
    PREDICTION_DRIFT_FIELD_NUMBER: _ClassVar[int]
    MISSING_FIELD_NUMBER: _ClassVar[int]
    CALIBRATION_FIELD_NUMBER: _ClassVar[int]
    SIGNAL_RATE_LIVE_FIELD_NUMBER: _ClassVar[int]
    SIGNAL_RATE_REFERENCE_FIELD_NUMBER: _ClassVar[int]
    ROLLING_IC_FIELD_NUMBER: _ClassVar[int]
    REFERENCE_IC_FIELD_NUMBER: _ClassVar[int]
    RESOLVED_FIELD_NUMBER: _ClassVar[int]
    LIVE_SAMPLES_FIELD_NUMBER: _ClassVar[int]
    REFERENCE_SAMPLES_FIELD_NUMBER: _ClassVar[int]
    REFERENCE_RUN_ID_FIELD_NUMBER: _ClassVar[int]
    CONDITIONS_FIELD_NUMBER: _ClassVar[int]
    GENERATED_AT_FIELD_NUMBER: _ClassVar[int]
    report_id: str
    strategy_id: str
    model_version: str
    window_start: int
    window_end: int
    features: _containers.RepeatedCompositeFieldContainer[_feedback_pb2.FeatureDrift]
    prediction_drift: _feedback_pb2.FeatureDrift
    missing: _containers.RepeatedCompositeFieldContainer[FeatureMissing]
    calibration: _feedback_pb2.Calibration
    signal_rate_live: float
    signal_rate_reference: float
    rolling_ic: float
    reference_ic: float
    resolved: int
    live_samples: int
    reference_samples: int
    reference_run_id: str
    conditions: _containers.RepeatedCompositeFieldContainer[_feedback_pb2.TriggeredCondition]
    generated_at: int
    def __init__(self, report_id: _Optional[str] = ..., strategy_id: _Optional[str] = ..., model_version: _Optional[str] = ..., window_start: _Optional[int] = ..., window_end: _Optional[int] = ..., features: _Optional[_Iterable[_Union[_feedback_pb2.FeatureDrift, _Mapping]]] = ..., prediction_drift: _Optional[_Union[_feedback_pb2.FeatureDrift, _Mapping]] = ..., missing: _Optional[_Iterable[_Union[FeatureMissing, _Mapping]]] = ..., calibration: _Optional[_Union[_feedback_pb2.Calibration, _Mapping]] = ..., signal_rate_live: _Optional[float] = ..., signal_rate_reference: _Optional[float] = ..., rolling_ic: _Optional[float] = ..., reference_ic: _Optional[float] = ..., resolved: _Optional[int] = ..., live_samples: _Optional[int] = ..., reference_samples: _Optional[int] = ..., reference_run_id: _Optional[str] = ..., conditions: _Optional[_Iterable[_Union[_feedback_pb2.TriggeredCondition, _Mapping]]] = ..., generated_at: _Optional[int] = ...) -> None: ...

class DivergenceReport(_message.Message):
    __slots__ = ("report_id", "strategy_id", "session_date", "parity", "matched_intents", "missed_intents", "extra_intents", "decision_mismatches", "pnl_live_bps", "pnl_replay_bps", "unexplained_bps", "recording_uri", "recording_complete", "passed", "conditions", "generated_at")
    REPORT_ID_FIELD_NUMBER: _ClassVar[int]
    STRATEGY_ID_FIELD_NUMBER: _ClassVar[int]
    SESSION_DATE_FIELD_NUMBER: _ClassVar[int]
    PARITY_FIELD_NUMBER: _ClassVar[int]
    MATCHED_INTENTS_FIELD_NUMBER: _ClassVar[int]
    MISSED_INTENTS_FIELD_NUMBER: _ClassVar[int]
    EXTRA_INTENTS_FIELD_NUMBER: _ClassVar[int]
    DECISION_MISMATCHES_FIELD_NUMBER: _ClassVar[int]
    PNL_LIVE_BPS_FIELD_NUMBER: _ClassVar[int]
    PNL_REPLAY_BPS_FIELD_NUMBER: _ClassVar[int]
    UNEXPLAINED_BPS_FIELD_NUMBER: _ClassVar[int]
    RECORDING_URI_FIELD_NUMBER: _ClassVar[int]
    RECORDING_COMPLETE_FIELD_NUMBER: _ClassVar[int]
    PASSED_FIELD_NUMBER: _ClassVar[int]
    CONDITIONS_FIELD_NUMBER: _ClassVar[int]
    GENERATED_AT_FIELD_NUMBER: _ClassVar[int]
    report_id: str
    strategy_id: str
    session_date: str
    parity: _feedback_pb2.ParitySummary
    matched_intents: int
    missed_intents: int
    extra_intents: int
    decision_mismatches: int
    pnl_live_bps: float
    pnl_replay_bps: float
    unexplained_bps: float
    recording_uri: str
    recording_complete: bool
    passed: bool
    conditions: _containers.RepeatedCompositeFieldContainer[_feedback_pb2.TriggeredCondition]
    generated_at: int
    def __init__(self, report_id: _Optional[str] = ..., strategy_id: _Optional[str] = ..., session_date: _Optional[str] = ..., parity: _Optional[_Union[_feedback_pb2.ParitySummary, _Mapping]] = ..., matched_intents: _Optional[int] = ..., missed_intents: _Optional[int] = ..., extra_intents: _Optional[int] = ..., decision_mismatches: _Optional[int] = ..., pnl_live_bps: _Optional[float] = ..., pnl_replay_bps: _Optional[float] = ..., unexplained_bps: _Optional[float] = ..., recording_uri: _Optional[str] = ..., recording_complete: _Optional[bool] = ..., passed: _Optional[bool] = ..., conditions: _Optional[_Iterable[_Union[_feedback_pb2.TriggeredCondition, _Mapping]]] = ..., generated_at: _Optional[int] = ...) -> None: ...

class LatencyStats(_message.Message):
    __slots__ = ("p50_ms", "p90_ms", "p99_ms", "count")
    P50_MS_FIELD_NUMBER: _ClassVar[int]
    P90_MS_FIELD_NUMBER: _ClassVar[int]
    P99_MS_FIELD_NUMBER: _ClassVar[int]
    COUNT_FIELD_NUMBER: _ClassVar[int]
    p50_ms: float
    p90_ms: float
    p99_ms: float
    count: int
    def __init__(self, p50_ms: _Optional[float] = ..., p90_ms: _Optional[float] = ..., p99_ms: _Optional[float] = ..., count: _Optional[int] = ...) -> None: ...

class ExecutionQualityReport(_message.Message):
    __slots__ = ("report_id", "strategy_id", "window_start", "window_end", "shortfall_bps", "shortfall_p90_bps", "modeled_shortfall_bps", "shortfall_ratio", "limit_fill_rate", "limit_orders", "decision_to_submit", "submit_to_fill", "decision_to_fill", "fills", "conditions", "generated_at")
    REPORT_ID_FIELD_NUMBER: _ClassVar[int]
    STRATEGY_ID_FIELD_NUMBER: _ClassVar[int]
    WINDOW_START_FIELD_NUMBER: _ClassVar[int]
    WINDOW_END_FIELD_NUMBER: _ClassVar[int]
    SHORTFALL_BPS_FIELD_NUMBER: _ClassVar[int]
    SHORTFALL_P90_BPS_FIELD_NUMBER: _ClassVar[int]
    MODELED_SHORTFALL_BPS_FIELD_NUMBER: _ClassVar[int]
    SHORTFALL_RATIO_FIELD_NUMBER: _ClassVar[int]
    LIMIT_FILL_RATE_FIELD_NUMBER: _ClassVar[int]
    LIMIT_ORDERS_FIELD_NUMBER: _ClassVar[int]
    DECISION_TO_SUBMIT_FIELD_NUMBER: _ClassVar[int]
    SUBMIT_TO_FILL_FIELD_NUMBER: _ClassVar[int]
    DECISION_TO_FILL_FIELD_NUMBER: _ClassVar[int]
    FILLS_FIELD_NUMBER: _ClassVar[int]
    CONDITIONS_FIELD_NUMBER: _ClassVar[int]
    GENERATED_AT_FIELD_NUMBER: _ClassVar[int]
    report_id: str
    strategy_id: str
    window_start: int
    window_end: int
    shortfall_bps: float
    shortfall_p90_bps: float
    modeled_shortfall_bps: float
    shortfall_ratio: float
    limit_fill_rate: float
    limit_orders: int
    decision_to_submit: LatencyStats
    submit_to_fill: LatencyStats
    decision_to_fill: LatencyStats
    fills: int
    conditions: _containers.RepeatedCompositeFieldContainer[_feedback_pb2.TriggeredCondition]
    generated_at: int
    def __init__(self, report_id: _Optional[str] = ..., strategy_id: _Optional[str] = ..., window_start: _Optional[int] = ..., window_end: _Optional[int] = ..., shortfall_bps: _Optional[float] = ..., shortfall_p90_bps: _Optional[float] = ..., modeled_shortfall_bps: _Optional[float] = ..., shortfall_ratio: _Optional[float] = ..., limit_fill_rate: _Optional[float] = ..., limit_orders: _Optional[int] = ..., decision_to_submit: _Optional[_Union[LatencyStats, _Mapping]] = ..., submit_to_fill: _Optional[_Union[LatencyStats, _Mapping]] = ..., decision_to_fill: _Optional[_Union[LatencyStats, _Mapping]] = ..., fills: _Optional[int] = ..., conditions: _Optional[_Iterable[_Union[_feedback_pb2.TriggeredCondition, _Mapping]]] = ..., generated_at: _Optional[int] = ...) -> None: ...

class ContainmentPolicy(_message.Message):
    __slots__ = ("strategy_id", "enabled", "drawdown_warning_multiple", "drawdown_breach_multiple", "match_rate_warning", "match_rate_breach", "slippage_gap_warning_bps", "slippage_gap_breach_bps", "feature_psi_warning", "feature_psi_breach", "feature_psi_top_k", "prediction_psi_warning", "prediction_psi_breach", "shortfall_warning_multiple", "shortfall_breach_multiple", "shortfall_min_excess_bps", "breach_action", "severe_action", "min_trades", "min_fills", "min_matched_intents", "min_drift_samples", "version", "updated_at", "updated_by")
    STRATEGY_ID_FIELD_NUMBER: _ClassVar[int]
    ENABLED_FIELD_NUMBER: _ClassVar[int]
    DRAWDOWN_WARNING_MULTIPLE_FIELD_NUMBER: _ClassVar[int]
    DRAWDOWN_BREACH_MULTIPLE_FIELD_NUMBER: _ClassVar[int]
    MATCH_RATE_WARNING_FIELD_NUMBER: _ClassVar[int]
    MATCH_RATE_BREACH_FIELD_NUMBER: _ClassVar[int]
    SLIPPAGE_GAP_WARNING_BPS_FIELD_NUMBER: _ClassVar[int]
    SLIPPAGE_GAP_BREACH_BPS_FIELD_NUMBER: _ClassVar[int]
    FEATURE_PSI_WARNING_FIELD_NUMBER: _ClassVar[int]
    FEATURE_PSI_BREACH_FIELD_NUMBER: _ClassVar[int]
    FEATURE_PSI_TOP_K_FIELD_NUMBER: _ClassVar[int]
    PREDICTION_PSI_WARNING_FIELD_NUMBER: _ClassVar[int]
    PREDICTION_PSI_BREACH_FIELD_NUMBER: _ClassVar[int]
    SHORTFALL_WARNING_MULTIPLE_FIELD_NUMBER: _ClassVar[int]
    SHORTFALL_BREACH_MULTIPLE_FIELD_NUMBER: _ClassVar[int]
    SHORTFALL_MIN_EXCESS_BPS_FIELD_NUMBER: _ClassVar[int]
    BREACH_ACTION_FIELD_NUMBER: _ClassVar[int]
    SEVERE_ACTION_FIELD_NUMBER: _ClassVar[int]
    MIN_TRADES_FIELD_NUMBER: _ClassVar[int]
    MIN_FILLS_FIELD_NUMBER: _ClassVar[int]
    MIN_MATCHED_INTENTS_FIELD_NUMBER: _ClassVar[int]
    MIN_DRIFT_SAMPLES_FIELD_NUMBER: _ClassVar[int]
    VERSION_FIELD_NUMBER: _ClassVar[int]
    UPDATED_AT_FIELD_NUMBER: _ClassVar[int]
    UPDATED_BY_FIELD_NUMBER: _ClassVar[int]
    strategy_id: str
    enabled: bool
    drawdown_warning_multiple: float
    drawdown_breach_multiple: float
    match_rate_warning: float
    match_rate_breach: float
    slippage_gap_warning_bps: float
    slippage_gap_breach_bps: float
    feature_psi_warning: float
    feature_psi_breach: float
    feature_psi_top_k: int
    prediction_psi_warning: float
    prediction_psi_breach: float
    shortfall_warning_multiple: float
    shortfall_breach_multiple: float
    shortfall_min_excess_bps: float
    breach_action: ContainmentActionKind
    severe_action: ContainmentActionKind
    min_trades: int
    min_fills: int
    min_matched_intents: int
    min_drift_samples: int
    version: int
    updated_at: int
    updated_by: str
    def __init__(self, strategy_id: _Optional[str] = ..., enabled: _Optional[bool] = ..., drawdown_warning_multiple: _Optional[float] = ..., drawdown_breach_multiple: _Optional[float] = ..., match_rate_warning: _Optional[float] = ..., match_rate_breach: _Optional[float] = ..., slippage_gap_warning_bps: _Optional[float] = ..., slippage_gap_breach_bps: _Optional[float] = ..., feature_psi_warning: _Optional[float] = ..., feature_psi_breach: _Optional[float] = ..., feature_psi_top_k: _Optional[int] = ..., prediction_psi_warning: _Optional[float] = ..., prediction_psi_breach: _Optional[float] = ..., shortfall_warning_multiple: _Optional[float] = ..., shortfall_breach_multiple: _Optional[float] = ..., shortfall_min_excess_bps: _Optional[float] = ..., breach_action: _Optional[_Union[ContainmentActionKind, str]] = ..., severe_action: _Optional[_Union[ContainmentActionKind, str]] = ..., min_trades: _Optional[int] = ..., min_fills: _Optional[int] = ..., min_matched_intents: _Optional[int] = ..., min_drift_samples: _Optional[int] = ..., version: _Optional[int] = ..., updated_at: _Optional[int] = ..., updated_by: _Optional[str] = ...) -> None: ...

class ContainmentAction(_message.Message):
    __slots__ = ("action_id", "strategy_id", "kind", "from_mode", "to_mode", "reason", "evidence", "evidence_report_id", "executed", "error", "decided_at", "executed_at", "kill_switch_event_id", "orders_canceled", "correlation_id")
    ACTION_ID_FIELD_NUMBER: _ClassVar[int]
    STRATEGY_ID_FIELD_NUMBER: _ClassVar[int]
    KIND_FIELD_NUMBER: _ClassVar[int]
    FROM_MODE_FIELD_NUMBER: _ClassVar[int]
    TO_MODE_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    EVIDENCE_FIELD_NUMBER: _ClassVar[int]
    EVIDENCE_REPORT_ID_FIELD_NUMBER: _ClassVar[int]
    EXECUTED_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    DECIDED_AT_FIELD_NUMBER: _ClassVar[int]
    EXECUTED_AT_FIELD_NUMBER: _ClassVar[int]
    KILL_SWITCH_EVENT_ID_FIELD_NUMBER: _ClassVar[int]
    ORDERS_CANCELED_FIELD_NUMBER: _ClassVar[int]
    CORRELATION_ID_FIELD_NUMBER: _ClassVar[int]
    action_id: str
    strategy_id: str
    kind: ContainmentActionKind
    from_mode: str
    to_mode: str
    reason: str
    evidence: _containers.RepeatedCompositeFieldContainer[HealthComponent]
    evidence_report_id: str
    executed: bool
    error: str
    decided_at: int
    executed_at: int
    kill_switch_event_id: str
    orders_canceled: int
    correlation_id: str
    def __init__(self, action_id: _Optional[str] = ..., strategy_id: _Optional[str] = ..., kind: _Optional[_Union[ContainmentActionKind, str]] = ..., from_mode: _Optional[str] = ..., to_mode: _Optional[str] = ..., reason: _Optional[str] = ..., evidence: _Optional[_Iterable[_Union[HealthComponent, _Mapping]]] = ..., evidence_report_id: _Optional[str] = ..., executed: _Optional[bool] = ..., error: _Optional[str] = ..., decided_at: _Optional[int] = ..., executed_at: _Optional[int] = ..., kill_switch_event_id: _Optional[str] = ..., orders_canceled: _Optional[int] = ..., correlation_id: _Optional[str] = ...) -> None: ...

class AlertRecord(_message.Message):
    __slots__ = ("alert", "status", "first_seen", "last_seen", "count", "acked_by", "acked_at", "silence_id", "silenced_until", "origin")
    ALERT_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    FIRST_SEEN_FIELD_NUMBER: _ClassVar[int]
    LAST_SEEN_FIELD_NUMBER: _ClassVar[int]
    COUNT_FIELD_NUMBER: _ClassVar[int]
    ACKED_BY_FIELD_NUMBER: _ClassVar[int]
    ACKED_AT_FIELD_NUMBER: _ClassVar[int]
    SILENCE_ID_FIELD_NUMBER: _ClassVar[int]
    SILENCED_UNTIL_FIELD_NUMBER: _ClassVar[int]
    ORIGIN_FIELD_NUMBER: _ClassVar[int]
    alert: _alerts_pb2.Alert
    status: str
    first_seen: int
    last_seen: int
    count: int
    acked_by: str
    acked_at: int
    silence_id: str
    silenced_until: int
    origin: str
    def __init__(self, alert: _Optional[_Union[_alerts_pb2.Alert, _Mapping]] = ..., status: _Optional[str] = ..., first_seen: _Optional[int] = ..., last_seen: _Optional[int] = ..., count: _Optional[int] = ..., acked_by: _Optional[str] = ..., acked_at: _Optional[int] = ..., silence_id: _Optional[str] = ..., silenced_until: _Optional[int] = ..., origin: _Optional[str] = ...) -> None: ...

class DailyReport(_message.Message):
    __slots__ = ("date", "premarket_ok", "premarket", "eod", "updated_at")
    DATE_FIELD_NUMBER: _ClassVar[int]
    PREMARKET_OK_FIELD_NUMBER: _ClassVar[int]
    PREMARKET_FIELD_NUMBER: _ClassVar[int]
    EOD_FIELD_NUMBER: _ClassVar[int]
    UPDATED_AT_FIELD_NUMBER: _ClassVar[int]
    date: str
    premarket_ok: bool
    premarket: str
    eod: str
    updated_at: int
    def __init__(self, date: _Optional[str] = ..., premarket_ok: _Optional[bool] = ..., premarket: _Optional[str] = ..., eod: _Optional[str] = ..., updated_at: _Optional[int] = ...) -> None: ...

class GraduationCriterion(_message.Message):
    __slots__ = ("name", "required", "actual", "passed", "evidence_only", "evidence_id")
    NAME_FIELD_NUMBER: _ClassVar[int]
    REQUIRED_FIELD_NUMBER: _ClassVar[int]
    ACTUAL_FIELD_NUMBER: _ClassVar[int]
    PASSED_FIELD_NUMBER: _ClassVar[int]
    EVIDENCE_ONLY_FIELD_NUMBER: _ClassVar[int]
    EVIDENCE_ID_FIELD_NUMBER: _ClassVar[int]
    name: str
    required: str
    actual: str
    passed: bool
    evidence_only: bool
    evidence_id: str
    def __init__(self, name: _Optional[str] = ..., required: _Optional[str] = ..., actual: _Optional[str] = ..., passed: _Optional[bool] = ..., evidence_only: _Optional[bool] = ..., evidence_id: _Optional[str] = ...) -> None: ...

class GraduationEvidence(_message.Message):
    __slots__ = ("strategy_id", "lineage_id", "model_version", "mode", "horizon", "paper_since", "sessions", "trades", "return_pct", "sharpe", "max_drawdown_pct", "drawdown_p95_pct", "match_rate", "slippage_gap_bps", "shortfall_bps", "modeled_shortfall_bps", "reconciliation_breaks", "critical_alerts", "session_returns_bps", "criteria", "criteria_met", "divergence_report_ids", "generated_at")
    STRATEGY_ID_FIELD_NUMBER: _ClassVar[int]
    LINEAGE_ID_FIELD_NUMBER: _ClassVar[int]
    MODEL_VERSION_FIELD_NUMBER: _ClassVar[int]
    MODE_FIELD_NUMBER: _ClassVar[int]
    HORIZON_FIELD_NUMBER: _ClassVar[int]
    PAPER_SINCE_FIELD_NUMBER: _ClassVar[int]
    SESSIONS_FIELD_NUMBER: _ClassVar[int]
    TRADES_FIELD_NUMBER: _ClassVar[int]
    RETURN_PCT_FIELD_NUMBER: _ClassVar[int]
    SHARPE_FIELD_NUMBER: _ClassVar[int]
    MAX_DRAWDOWN_PCT_FIELD_NUMBER: _ClassVar[int]
    DRAWDOWN_P95_PCT_FIELD_NUMBER: _ClassVar[int]
    MATCH_RATE_FIELD_NUMBER: _ClassVar[int]
    SLIPPAGE_GAP_BPS_FIELD_NUMBER: _ClassVar[int]
    SHORTFALL_BPS_FIELD_NUMBER: _ClassVar[int]
    MODELED_SHORTFALL_BPS_FIELD_NUMBER: _ClassVar[int]
    RECONCILIATION_BREAKS_FIELD_NUMBER: _ClassVar[int]
    CRITICAL_ALERTS_FIELD_NUMBER: _ClassVar[int]
    SESSION_RETURNS_BPS_FIELD_NUMBER: _ClassVar[int]
    CRITERIA_FIELD_NUMBER: _ClassVar[int]
    CRITERIA_MET_FIELD_NUMBER: _ClassVar[int]
    DIVERGENCE_REPORT_IDS_FIELD_NUMBER: _ClassVar[int]
    GENERATED_AT_FIELD_NUMBER: _ClassVar[int]
    strategy_id: str
    lineage_id: str
    model_version: str
    mode: str
    horizon: str
    paper_since: int
    sessions: int
    trades: int
    return_pct: float
    sharpe: float
    max_drawdown_pct: float
    drawdown_p95_pct: float
    match_rate: float
    slippage_gap_bps: float
    shortfall_bps: float
    modeled_shortfall_bps: float
    reconciliation_breaks: int
    critical_alerts: int
    session_returns_bps: _containers.RepeatedScalarFieldContainer[float]
    criteria: _containers.RepeatedCompositeFieldContainer[GraduationCriterion]
    criteria_met: bool
    divergence_report_ids: _containers.RepeatedScalarFieldContainer[str]
    generated_at: int
    def __init__(self, strategy_id: _Optional[str] = ..., lineage_id: _Optional[str] = ..., model_version: _Optional[str] = ..., mode: _Optional[str] = ..., horizon: _Optional[str] = ..., paper_since: _Optional[int] = ..., sessions: _Optional[int] = ..., trades: _Optional[int] = ..., return_pct: _Optional[float] = ..., sharpe: _Optional[float] = ..., max_drawdown_pct: _Optional[float] = ..., drawdown_p95_pct: _Optional[float] = ..., match_rate: _Optional[float] = ..., slippage_gap_bps: _Optional[float] = ..., shortfall_bps: _Optional[float] = ..., modeled_shortfall_bps: _Optional[float] = ..., reconciliation_breaks: _Optional[int] = ..., critical_alerts: _Optional[int] = ..., session_returns_bps: _Optional[_Iterable[float]] = ..., criteria: _Optional[_Iterable[_Union[GraduationCriterion, _Mapping]]] = ..., criteria_met: _Optional[bool] = ..., divergence_report_ids: _Optional[_Iterable[str]] = ..., generated_at: _Optional[int] = ...) -> None: ...

class ListStrategyHealthRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class ListStrategyHealthResponse(_message.Message):
    __slots__ = ("strategies",)
    STRATEGIES_FIELD_NUMBER: _ClassVar[int]
    strategies: _containers.RepeatedCompositeFieldContainer[StrategyHealth]
    def __init__(self, strategies: _Optional[_Iterable[_Union[StrategyHealth, _Mapping]]] = ...) -> None: ...

class GetStrategyHealthRequest(_message.Message):
    __slots__ = ("strategy_id",)
    STRATEGY_ID_FIELD_NUMBER: _ClassVar[int]
    strategy_id: str
    def __init__(self, strategy_id: _Optional[str] = ...) -> None: ...

class GetStrategyHealthResponse(_message.Message):
    __slots__ = ("health",)
    HEALTH_FIELD_NUMBER: _ClassVar[int]
    health: StrategyHealth
    def __init__(self, health: _Optional[_Union[StrategyHealth, _Mapping]] = ...) -> None: ...

class GetPerformanceReportRequest(_message.Message):
    __slots__ = ("strategy_id",)
    STRATEGY_ID_FIELD_NUMBER: _ClassVar[int]
    strategy_id: str
    def __init__(self, strategy_id: _Optional[str] = ...) -> None: ...

class GetPerformanceReportResponse(_message.Message):
    __slots__ = ("report",)
    REPORT_FIELD_NUMBER: _ClassVar[int]
    report: PerformanceReport
    def __init__(self, report: _Optional[_Union[PerformanceReport, _Mapping]] = ...) -> None: ...

class GetDriftReportRequest(_message.Message):
    __slots__ = ("strategy_id",)
    STRATEGY_ID_FIELD_NUMBER: _ClassVar[int]
    strategy_id: str
    def __init__(self, strategy_id: _Optional[str] = ...) -> None: ...

class GetDriftReportResponse(_message.Message):
    __slots__ = ("report",)
    REPORT_FIELD_NUMBER: _ClassVar[int]
    report: DriftReport
    def __init__(self, report: _Optional[_Union[DriftReport, _Mapping]] = ...) -> None: ...

class GetDivergenceReportRequest(_message.Message):
    __slots__ = ("strategy_id", "session_date")
    STRATEGY_ID_FIELD_NUMBER: _ClassVar[int]
    SESSION_DATE_FIELD_NUMBER: _ClassVar[int]
    strategy_id: str
    session_date: str
    def __init__(self, strategy_id: _Optional[str] = ..., session_date: _Optional[str] = ...) -> None: ...

class GetDivergenceReportResponse(_message.Message):
    __slots__ = ("report", "history")
    REPORT_FIELD_NUMBER: _ClassVar[int]
    HISTORY_FIELD_NUMBER: _ClassVar[int]
    report: DivergenceReport
    history: _containers.RepeatedCompositeFieldContainer[DivergenceReport]
    def __init__(self, report: _Optional[_Union[DivergenceReport, _Mapping]] = ..., history: _Optional[_Iterable[_Union[DivergenceReport, _Mapping]]] = ...) -> None: ...

class GetExecutionQualityRequest(_message.Message):
    __slots__ = ("strategy_id",)
    STRATEGY_ID_FIELD_NUMBER: _ClassVar[int]
    strategy_id: str
    def __init__(self, strategy_id: _Optional[str] = ...) -> None: ...

class GetExecutionQualityResponse(_message.Message):
    __slots__ = ("report",)
    REPORT_FIELD_NUMBER: _ClassVar[int]
    report: ExecutionQualityReport
    def __init__(self, report: _Optional[_Union[ExecutionQualityReport, _Mapping]] = ...) -> None: ...

class GetLiveFeedbackRequest(_message.Message):
    __slots__ = ("strategy_id",)
    STRATEGY_ID_FIELD_NUMBER: _ClassVar[int]
    strategy_id: str
    def __init__(self, strategy_id: _Optional[str] = ...) -> None: ...

class GetLiveFeedbackResponse(_message.Message):
    __slots__ = ("report",)
    REPORT_FIELD_NUMBER: _ClassVar[int]
    report: _feedback_pb2.LiveFeedbackReport
    def __init__(self, report: _Optional[_Union[_feedback_pb2.LiveFeedbackReport, _Mapping]] = ...) -> None: ...

class ListAlertsRequest(_message.Message):
    __slots__ = ("severity", "status", "since", "limit")
    SEVERITY_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    SINCE_FIELD_NUMBER: _ClassVar[int]
    LIMIT_FIELD_NUMBER: _ClassVar[int]
    severity: str
    status: str
    since: int
    limit: int
    def __init__(self, severity: _Optional[str] = ..., status: _Optional[str] = ..., since: _Optional[int] = ..., limit: _Optional[int] = ...) -> None: ...

class ListAlertsResponse(_message.Message):
    __slots__ = ("alerts",)
    ALERTS_FIELD_NUMBER: _ClassVar[int]
    alerts: _containers.RepeatedCompositeFieldContainer[AlertRecord]
    def __init__(self, alerts: _Optional[_Iterable[_Union[AlertRecord, _Mapping]]] = ...) -> None: ...

class AckAlertRequest(_message.Message):
    __slots__ = ("dedupe_key", "actor", "note")
    DEDUPE_KEY_FIELD_NUMBER: _ClassVar[int]
    ACTOR_FIELD_NUMBER: _ClassVar[int]
    NOTE_FIELD_NUMBER: _ClassVar[int]
    dedupe_key: str
    actor: _execution_pb2.Actor
    note: str
    def __init__(self, dedupe_key: _Optional[str] = ..., actor: _Optional[_Union[_execution_pb2.Actor, _Mapping]] = ..., note: _Optional[str] = ...) -> None: ...

class AckAlertResponse(_message.Message):
    __slots__ = ("alert",)
    ALERT_FIELD_NUMBER: _ClassVar[int]
    alert: AlertRecord
    def __init__(self, alert: _Optional[_Union[AlertRecord, _Mapping]] = ...) -> None: ...

class SilenceAlertRequest(_message.Message):
    __slots__ = ("dedupe_key", "duration_seconds", "reason", "actor")
    DEDUPE_KEY_FIELD_NUMBER: _ClassVar[int]
    DURATION_SECONDS_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    ACTOR_FIELD_NUMBER: _ClassVar[int]
    dedupe_key: str
    duration_seconds: int
    reason: str
    actor: _execution_pb2.Actor
    def __init__(self, dedupe_key: _Optional[str] = ..., duration_seconds: _Optional[int] = ..., reason: _Optional[str] = ..., actor: _Optional[_Union[_execution_pb2.Actor, _Mapping]] = ...) -> None: ...

class SilenceAlertResponse(_message.Message):
    __slots__ = ("silence_id", "ends_at")
    SILENCE_ID_FIELD_NUMBER: _ClassVar[int]
    ENDS_AT_FIELD_NUMBER: _ClassVar[int]
    silence_id: str
    ends_at: int
    def __init__(self, silence_id: _Optional[str] = ..., ends_at: _Optional[int] = ...) -> None: ...

class GetDailyReportRequest(_message.Message):
    __slots__ = ("date",)
    DATE_FIELD_NUMBER: _ClassVar[int]
    date: str
    def __init__(self, date: _Optional[str] = ...) -> None: ...

class GetDailyReportResponse(_message.Message):
    __slots__ = ("report",)
    REPORT_FIELD_NUMBER: _ClassVar[int]
    report: DailyReport
    def __init__(self, report: _Optional[_Union[DailyReport, _Mapping]] = ...) -> None: ...

class GetContainmentPolicyRequest(_message.Message):
    __slots__ = ("strategy_id",)
    STRATEGY_ID_FIELD_NUMBER: _ClassVar[int]
    strategy_id: str
    def __init__(self, strategy_id: _Optional[str] = ...) -> None: ...

class GetContainmentPolicyResponse(_message.Message):
    __slots__ = ("policy", "inherited")
    POLICY_FIELD_NUMBER: _ClassVar[int]
    INHERITED_FIELD_NUMBER: _ClassVar[int]
    policy: ContainmentPolicy
    inherited: bool
    def __init__(self, policy: _Optional[_Union[ContainmentPolicy, _Mapping]] = ..., inherited: _Optional[bool] = ...) -> None: ...

class SetContainmentPolicyRequest(_message.Message):
    __slots__ = ("policy", "actor", "reason")
    POLICY_FIELD_NUMBER: _ClassVar[int]
    ACTOR_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    policy: ContainmentPolicy
    actor: _execution_pb2.Actor
    reason: str
    def __init__(self, policy: _Optional[_Union[ContainmentPolicy, _Mapping]] = ..., actor: _Optional[_Union[_execution_pb2.Actor, _Mapping]] = ..., reason: _Optional[str] = ...) -> None: ...

class SetContainmentPolicyResponse(_message.Message):
    __slots__ = ("policy", "loosened")
    POLICY_FIELD_NUMBER: _ClassVar[int]
    LOOSENED_FIELD_NUMBER: _ClassVar[int]
    policy: ContainmentPolicy
    loosened: bool
    def __init__(self, policy: _Optional[_Union[ContainmentPolicy, _Mapping]] = ..., loosened: _Optional[bool] = ...) -> None: ...

class ListContainmentActionsRequest(_message.Message):
    __slots__ = ("strategy_id", "limit")
    STRATEGY_ID_FIELD_NUMBER: _ClassVar[int]
    LIMIT_FIELD_NUMBER: _ClassVar[int]
    strategy_id: str
    limit: int
    def __init__(self, strategy_id: _Optional[str] = ..., limit: _Optional[int] = ...) -> None: ...

class ListContainmentActionsResponse(_message.Message):
    __slots__ = ("actions",)
    ACTIONS_FIELD_NUMBER: _ClassVar[int]
    actions: _containers.RepeatedCompositeFieldContainer[ContainmentAction]
    def __init__(self, actions: _Optional[_Iterable[_Union[ContainmentAction, _Mapping]]] = ...) -> None: ...

class GetGraduationEvidenceRequest(_message.Message):
    __slots__ = ("strategy_id",)
    STRATEGY_ID_FIELD_NUMBER: _ClassVar[int]
    strategy_id: str
    def __init__(self, strategy_id: _Optional[str] = ...) -> None: ...

class GetGraduationEvidenceResponse(_message.Message):
    __slots__ = ("evidence",)
    EVIDENCE_FIELD_NUMBER: _ClassVar[int]
    evidence: GraduationEvidence
    def __init__(self, evidence: _Optional[_Union[GraduationEvidence, _Mapping]] = ...) -> None: ...
