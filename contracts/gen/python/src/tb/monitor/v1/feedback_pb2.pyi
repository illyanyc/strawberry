from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class MetricVsExpected(_message.Message):
    __slots__ = ("name", "live_value", "expected_p10", "expected_p50", "expected_p90", "percentile")
    NAME_FIELD_NUMBER: _ClassVar[int]
    LIVE_VALUE_FIELD_NUMBER: _ClassVar[int]
    EXPECTED_P10_FIELD_NUMBER: _ClassVar[int]
    EXPECTED_P50_FIELD_NUMBER: _ClassVar[int]
    EXPECTED_P90_FIELD_NUMBER: _ClassVar[int]
    PERCENTILE_FIELD_NUMBER: _ClassVar[int]
    name: str
    live_value: float
    expected_p10: float
    expected_p50: float
    expected_p90: float
    percentile: float
    def __init__(self, name: _Optional[str] = ..., live_value: _Optional[float] = ..., expected_p10: _Optional[float] = ..., expected_p50: _Optional[float] = ..., expected_p90: _Optional[float] = ..., percentile: _Optional[float] = ...) -> None: ...

class ParitySummary(_message.Message):
    __slots__ = ("trade_match_rate", "missed_trades", "extra_trades", "slippage_bps_live", "slippage_bps_modeled", "pnl_gap_bps")
    TRADE_MATCH_RATE_FIELD_NUMBER: _ClassVar[int]
    MISSED_TRADES_FIELD_NUMBER: _ClassVar[int]
    EXTRA_TRADES_FIELD_NUMBER: _ClassVar[int]
    SLIPPAGE_BPS_LIVE_FIELD_NUMBER: _ClassVar[int]
    SLIPPAGE_BPS_MODELED_FIELD_NUMBER: _ClassVar[int]
    PNL_GAP_BPS_FIELD_NUMBER: _ClassVar[int]
    trade_match_rate: float
    missed_trades: int
    extra_trades: int
    slippage_bps_live: float
    slippage_bps_modeled: float
    pnl_gap_bps: float
    def __init__(self, trade_match_rate: _Optional[float] = ..., missed_trades: _Optional[int] = ..., extra_trades: _Optional[int] = ..., slippage_bps_live: _Optional[float] = ..., slippage_bps_modeled: _Optional[float] = ..., pnl_gap_bps: _Optional[float] = ...) -> None: ...

class DecayTest(_message.Message):
    __slots__ = ("method", "statistic", "threshold", "breached", "samples")
    METHOD_FIELD_NUMBER: _ClassVar[int]
    STATISTIC_FIELD_NUMBER: _ClassVar[int]
    THRESHOLD_FIELD_NUMBER: _ClassVar[int]
    BREACHED_FIELD_NUMBER: _ClassVar[int]
    SAMPLES_FIELD_NUMBER: _ClassVar[int]
    method: str
    statistic: float
    threshold: float
    breached: bool
    samples: int
    def __init__(self, method: _Optional[str] = ..., statistic: _Optional[float] = ..., threshold: _Optional[float] = ..., breached: _Optional[bool] = ..., samples: _Optional[int] = ...) -> None: ...

class FeatureDrift(_message.Message):
    __slots__ = ("feature", "psi", "ks", "importance_rank")
    FEATURE_FIELD_NUMBER: _ClassVar[int]
    PSI_FIELD_NUMBER: _ClassVar[int]
    KS_FIELD_NUMBER: _ClassVar[int]
    IMPORTANCE_RANK_FIELD_NUMBER: _ClassVar[int]
    feature: str
    psi: float
    ks: float
    importance_rank: int
    def __init__(self, feature: _Optional[str] = ..., psi: _Optional[float] = ..., ks: _Optional[float] = ..., importance_rank: _Optional[int] = ...) -> None: ...

class Calibration(_message.Message):
    __slots__ = ("brier", "ece", "resolved", "reference_ece")
    BRIER_FIELD_NUMBER: _ClassVar[int]
    ECE_FIELD_NUMBER: _ClassVar[int]
    RESOLVED_FIELD_NUMBER: _ClassVar[int]
    REFERENCE_ECE_FIELD_NUMBER: _ClassVar[int]
    brier: float
    ece: float
    resolved: int
    reference_ece: float
    def __init__(self, brier: _Optional[float] = ..., ece: _Optional[float] = ..., resolved: _Optional[int] = ..., reference_ece: _Optional[float] = ...) -> None: ...

class ErrorSlice(_message.Message):
    __slots__ = ("slice_by", "slice", "metric", "value", "samples")
    SLICE_BY_FIELD_NUMBER: _ClassVar[int]
    SLICE_FIELD_NUMBER: _ClassVar[int]
    METRIC_FIELD_NUMBER: _ClassVar[int]
    VALUE_FIELD_NUMBER: _ClassVar[int]
    SAMPLES_FIELD_NUMBER: _ClassVar[int]
    slice_by: str
    slice: str
    metric: str
    value: float
    samples: int
    def __init__(self, slice_by: _Optional[str] = ..., slice: _Optional[str] = ..., metric: _Optional[str] = ..., value: _Optional[float] = ..., samples: _Optional[int] = ...) -> None: ...

class RegimeContext(_message.Message):
    __slots__ = ("current", "seen_in_training", "training_share")
    CURRENT_FIELD_NUMBER: _ClassVar[int]
    SEEN_IN_TRAINING_FIELD_NUMBER: _ClassVar[int]
    TRAINING_SHARE_FIELD_NUMBER: _ClassVar[int]
    current: str
    seen_in_training: bool
    training_share: float
    def __init__(self, current: _Optional[str] = ..., seen_in_training: _Optional[bool] = ..., training_share: _Optional[float] = ...) -> None: ...

class ExecutionQuality(_message.Message):
    __slots__ = ("shortfall_bps", "modeled_shortfall_bps", "limit_fill_rate")
    SHORTFALL_BPS_FIELD_NUMBER: _ClassVar[int]
    MODELED_SHORTFALL_BPS_FIELD_NUMBER: _ClassVar[int]
    LIMIT_FILL_RATE_FIELD_NUMBER: _ClassVar[int]
    shortfall_bps: float
    modeled_shortfall_bps: float
    limit_fill_rate: float
    def __init__(self, shortfall_bps: _Optional[float] = ..., modeled_shortfall_bps: _Optional[float] = ..., limit_fill_rate: _Optional[float] = ...) -> None: ...

class TriggeredCondition(_message.Message):
    __slots__ = ("condition", "value", "threshold", "severity", "detail")
    CONDITION_FIELD_NUMBER: _ClassVar[int]
    VALUE_FIELD_NUMBER: _ClassVar[int]
    THRESHOLD_FIELD_NUMBER: _ClassVar[int]
    SEVERITY_FIELD_NUMBER: _ClassVar[int]
    DETAIL_FIELD_NUMBER: _ClassVar[int]
    condition: str
    value: float
    threshold: float
    severity: str
    detail: str
    def __init__(self, condition: _Optional[str] = ..., value: _Optional[float] = ..., threshold: _Optional[float] = ..., severity: _Optional[str] = ..., detail: _Optional[str] = ...) -> None: ...

class LiveFeedbackReport(_message.Message):
    __slots__ = ("report_id", "strategy_id", "lineage_id", "model_version", "mode", "window_start", "window_end", "sessions", "trades", "resolved_predictions", "metrics", "parity", "decay", "feature_drift", "prediction_drift", "calibration", "error_slices", "regime", "execution", "triggered", "sample_sufficient", "backtest_report_id", "training_run_id", "generated_at", "correlation_id")
    REPORT_ID_FIELD_NUMBER: _ClassVar[int]
    STRATEGY_ID_FIELD_NUMBER: _ClassVar[int]
    LINEAGE_ID_FIELD_NUMBER: _ClassVar[int]
    MODEL_VERSION_FIELD_NUMBER: _ClassVar[int]
    MODE_FIELD_NUMBER: _ClassVar[int]
    WINDOW_START_FIELD_NUMBER: _ClassVar[int]
    WINDOW_END_FIELD_NUMBER: _ClassVar[int]
    SESSIONS_FIELD_NUMBER: _ClassVar[int]
    TRADES_FIELD_NUMBER: _ClassVar[int]
    RESOLVED_PREDICTIONS_FIELD_NUMBER: _ClassVar[int]
    METRICS_FIELD_NUMBER: _ClassVar[int]
    PARITY_FIELD_NUMBER: _ClassVar[int]
    DECAY_FIELD_NUMBER: _ClassVar[int]
    FEATURE_DRIFT_FIELD_NUMBER: _ClassVar[int]
    PREDICTION_DRIFT_FIELD_NUMBER: _ClassVar[int]
    CALIBRATION_FIELD_NUMBER: _ClassVar[int]
    ERROR_SLICES_FIELD_NUMBER: _ClassVar[int]
    REGIME_FIELD_NUMBER: _ClassVar[int]
    EXECUTION_FIELD_NUMBER: _ClassVar[int]
    TRIGGERED_FIELD_NUMBER: _ClassVar[int]
    SAMPLE_SUFFICIENT_FIELD_NUMBER: _ClassVar[int]
    BACKTEST_REPORT_ID_FIELD_NUMBER: _ClassVar[int]
    TRAINING_RUN_ID_FIELD_NUMBER: _ClassVar[int]
    GENERATED_AT_FIELD_NUMBER: _ClassVar[int]
    CORRELATION_ID_FIELD_NUMBER: _ClassVar[int]
    report_id: str
    strategy_id: str
    lineage_id: str
    model_version: str
    mode: str
    window_start: int
    window_end: int
    sessions: int
    trades: int
    resolved_predictions: int
    metrics: _containers.RepeatedCompositeFieldContainer[MetricVsExpected]
    parity: ParitySummary
    decay: DecayTest
    feature_drift: _containers.RepeatedCompositeFieldContainer[FeatureDrift]
    prediction_drift: FeatureDrift
    calibration: Calibration
    error_slices: _containers.RepeatedCompositeFieldContainer[ErrorSlice]
    regime: RegimeContext
    execution: ExecutionQuality
    triggered: _containers.RepeatedCompositeFieldContainer[TriggeredCondition]
    sample_sufficient: bool
    backtest_report_id: str
    training_run_id: str
    generated_at: int
    correlation_id: str
    def __init__(self, report_id: _Optional[str] = ..., strategy_id: _Optional[str] = ..., lineage_id: _Optional[str] = ..., model_version: _Optional[str] = ..., mode: _Optional[str] = ..., window_start: _Optional[int] = ..., window_end: _Optional[int] = ..., sessions: _Optional[int] = ..., trades: _Optional[int] = ..., resolved_predictions: _Optional[int] = ..., metrics: _Optional[_Iterable[_Union[MetricVsExpected, _Mapping]]] = ..., parity: _Optional[_Union[ParitySummary, _Mapping]] = ..., decay: _Optional[_Union[DecayTest, _Mapping]] = ..., feature_drift: _Optional[_Iterable[_Union[FeatureDrift, _Mapping]]] = ..., prediction_drift: _Optional[_Union[FeatureDrift, _Mapping]] = ..., calibration: _Optional[_Union[Calibration, _Mapping]] = ..., error_slices: _Optional[_Iterable[_Union[ErrorSlice, _Mapping]]] = ..., regime: _Optional[_Union[RegimeContext, _Mapping]] = ..., execution: _Optional[_Union[ExecutionQuality, _Mapping]] = ..., triggered: _Optional[_Iterable[_Union[TriggeredCondition, _Mapping]]] = ..., sample_sufficient: _Optional[bool] = ..., backtest_report_id: _Optional[str] = ..., training_run_id: _Optional[str] = ..., generated_at: _Optional[int] = ..., correlation_id: _Optional[str] = ...) -> None: ...

class RetrainSuggested(_message.Message):
    __slots__ = ("trigger_id", "strategy_id", "lineage_id", "model_version", "mode", "conditions", "report_id", "severity", "occurred_at", "correlation_id")
    TRIGGER_ID_FIELD_NUMBER: _ClassVar[int]
    STRATEGY_ID_FIELD_NUMBER: _ClassVar[int]
    LINEAGE_ID_FIELD_NUMBER: _ClassVar[int]
    MODEL_VERSION_FIELD_NUMBER: _ClassVar[int]
    MODE_FIELD_NUMBER: _ClassVar[int]
    CONDITIONS_FIELD_NUMBER: _ClassVar[int]
    REPORT_ID_FIELD_NUMBER: _ClassVar[int]
    SEVERITY_FIELD_NUMBER: _ClassVar[int]
    OCCURRED_AT_FIELD_NUMBER: _ClassVar[int]
    CORRELATION_ID_FIELD_NUMBER: _ClassVar[int]
    trigger_id: str
    strategy_id: str
    lineage_id: str
    model_version: str
    mode: str
    conditions: _containers.RepeatedCompositeFieldContainer[TriggeredCondition]
    report_id: str
    severity: str
    occurred_at: int
    correlation_id: str
    def __init__(self, trigger_id: _Optional[str] = ..., strategy_id: _Optional[str] = ..., lineage_id: _Optional[str] = ..., model_version: _Optional[str] = ..., mode: _Optional[str] = ..., conditions: _Optional[_Iterable[_Union[TriggeredCondition, _Mapping]]] = ..., report_id: _Optional[str] = ..., severity: _Optional[str] = ..., occurred_at: _Optional[int] = ..., correlation_id: _Optional[str] = ...) -> None: ...
