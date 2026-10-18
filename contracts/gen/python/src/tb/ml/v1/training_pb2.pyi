from google.protobuf import struct_pb2 as _struct_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class Actor(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    ACTOR_UNSPECIFIED: _ClassVar[Actor]
    ACTOR_OWNER: _ClassVar[Actor]
    ACTOR_ANALYST: _ClassVar[Actor]
    ACTOR_SCHEDULE: _ClassVar[Actor]
    ACTOR_SYSTEM: _ClassVar[Actor]

class JobStatus(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    JOB_STATUS_UNSPECIFIED: _ClassVar[JobStatus]
    JOB_STATUS_QUEUED: _ClassVar[JobStatus]
    JOB_STATUS_RUNNING: _ClassVar[JobStatus]
    JOB_STATUS_SUCCEEDED: _ClassVar[JobStatus]
    JOB_STATUS_FAILED: _ClassVar[JobStatus]
    JOB_STATUS_CANCELED: _ClassVar[JobStatus]

class Severity(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    SEVERITY_UNSPECIFIED: _ClassVar[Severity]
    SEVERITY_INFO: _ClassVar[Severity]
    SEVERITY_WARNING: _ClassVar[Severity]
    SEVERITY_CRITICAL: _ClassVar[Severity]
ACTOR_UNSPECIFIED: Actor
ACTOR_OWNER: Actor
ACTOR_ANALYST: Actor
ACTOR_SCHEDULE: Actor
ACTOR_SYSTEM: Actor
JOB_STATUS_UNSPECIFIED: JobStatus
JOB_STATUS_QUEUED: JobStatus
JOB_STATUS_RUNNING: JobStatus
JOB_STATUS_SUCCEEDED: JobStatus
JOB_STATUS_FAILED: JobStatus
JOB_STATUS_CANCELED: JobStatus
SEVERITY_UNSPECIFIED: Severity
SEVERITY_INFO: Severity
SEVERITY_WARNING: Severity
SEVERITY_CRITICAL: Severity

class StepUp(_message.Message):
    __slots__ = ("method", "verified_at", "session_id")
    METHOD_FIELD_NUMBER: _ClassVar[int]
    VERIFIED_AT_FIELD_NUMBER: _ClassVar[int]
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    method: str
    verified_at: int
    session_id: str
    def __init__(self, method: _Optional[str] = ..., verified_at: _Optional[int] = ..., session_id: _Optional[str] = ...) -> None: ...

class CallerContext(_message.Message):
    __slots__ = ("actor", "actor_id", "analyst_round_id", "reason", "step_up", "correlation_id")
    ACTOR_FIELD_NUMBER: _ClassVar[int]
    ACTOR_ID_FIELD_NUMBER: _ClassVar[int]
    ANALYST_ROUND_ID_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    STEP_UP_FIELD_NUMBER: _ClassVar[int]
    CORRELATION_ID_FIELD_NUMBER: _ClassVar[int]
    actor: Actor
    actor_id: str
    analyst_round_id: str
    reason: str
    step_up: StepUp
    correlation_id: str
    def __init__(self, actor: _Optional[_Union[Actor, str]] = ..., actor_id: _Optional[str] = ..., analyst_round_id: _Optional[str] = ..., reason: _Optional[str] = ..., step_up: _Optional[_Union[StepUp, _Mapping]] = ..., correlation_id: _Optional[str] = ...) -> None: ...

class Period(_message.Message):
    __slots__ = ("start", "end")
    START_FIELD_NUMBER: _ClassVar[int]
    END_FIELD_NUMBER: _ClassVar[int]
    start: int
    end: int
    def __init__(self, start: _Optional[int] = ..., end: _Optional[int] = ...) -> None: ...

class FeatureSetRef(_message.Message):
    __slots__ = ("name", "version", "hash")
    NAME_FIELD_NUMBER: _ClassVar[int]
    VERSION_FIELD_NUMBER: _ClassVar[int]
    HASH_FIELD_NUMBER: _ClassVar[int]
    name: str
    version: int
    hash: str
    def __init__(self, name: _Optional[str] = ..., version: _Optional[int] = ..., hash: _Optional[str] = ...) -> None: ...

class LabelSpec(_message.Message):
    __slots__ = ("kind", "horizon", "benchmark", "vol_window", "pt", "sl", "rank_buckets")
    KIND_FIELD_NUMBER: _ClassVar[int]
    HORIZON_FIELD_NUMBER: _ClassVar[int]
    BENCHMARK_FIELD_NUMBER: _ClassVar[int]
    VOL_WINDOW_FIELD_NUMBER: _ClassVar[int]
    PT_FIELD_NUMBER: _ClassVar[int]
    SL_FIELD_NUMBER: _ClassVar[int]
    RANK_BUCKETS_FIELD_NUMBER: _ClassVar[int]
    kind: str
    horizon: int
    benchmark: str
    vol_window: int
    pt: float
    sl: float
    rank_buckets: int
    def __init__(self, kind: _Optional[str] = ..., horizon: _Optional[int] = ..., benchmark: _Optional[str] = ..., vol_window: _Optional[int] = ..., pt: _Optional[float] = ..., sl: _Optional[float] = ..., rank_buckets: _Optional[int] = ...) -> None: ...

class UniverseSpec(_message.Message):
    __slots__ = ("name", "instrument_ids", "benchmark")
    NAME_FIELD_NUMBER: _ClassVar[int]
    INSTRUMENT_IDS_FIELD_NUMBER: _ClassVar[int]
    BENCHMARK_FIELD_NUMBER: _ClassVar[int]
    name: str
    instrument_ids: _containers.RepeatedScalarFieldContainer[str]
    benchmark: str
    def __init__(self, name: _Optional[str] = ..., instrument_ids: _Optional[_Iterable[str]] = ..., benchmark: _Optional[str] = ...) -> None: ...

class SplitterSpec(_message.Message):
    __slots__ = ("kind", "n_splits", "train_eras", "test_eras", "anchored", "n_groups", "n_test_groups", "embargo_bars")
    KIND_FIELD_NUMBER: _ClassVar[int]
    N_SPLITS_FIELD_NUMBER: _ClassVar[int]
    TRAIN_ERAS_FIELD_NUMBER: _ClassVar[int]
    TEST_ERAS_FIELD_NUMBER: _ClassVar[int]
    ANCHORED_FIELD_NUMBER: _ClassVar[int]
    N_GROUPS_FIELD_NUMBER: _ClassVar[int]
    N_TEST_GROUPS_FIELD_NUMBER: _ClassVar[int]
    EMBARGO_BARS_FIELD_NUMBER: _ClassVar[int]
    kind: str
    n_splits: int
    train_eras: int
    test_eras: int
    anchored: bool
    n_groups: int
    n_test_groups: int
    embargo_bars: int
    def __init__(self, kind: _Optional[str] = ..., n_splits: _Optional[int] = ..., train_eras: _Optional[int] = ..., test_eras: _Optional[int] = ..., anchored: _Optional[bool] = ..., n_groups: _Optional[int] = ..., n_test_groups: _Optional[int] = ..., embargo_bars: _Optional[int] = ...) -> None: ...

class SampleWeighting(_message.Message):
    __slots__ = ("method", "decay")
    METHOD_FIELD_NUMBER: _ClassVar[int]
    DECAY_FIELD_NUMBER: _ClassVar[int]
    method: str
    decay: float
    def __init__(self, method: _Optional[str] = ..., decay: _Optional[float] = ...) -> None: ...

class Resources(_message.Message):
    __slots__ = ("cpu_threads", "memory_mb", "device", "max_wall_seconds")
    CPU_THREADS_FIELD_NUMBER: _ClassVar[int]
    MEMORY_MB_FIELD_NUMBER: _ClassVar[int]
    DEVICE_FIELD_NUMBER: _ClassVar[int]
    MAX_WALL_SECONDS_FIELD_NUMBER: _ClassVar[int]
    cpu_threads: int
    memory_mb: int
    device: str
    max_wall_seconds: int
    def __init__(self, cpu_threads: _Optional[int] = ..., memory_mb: _Optional[int] = ..., device: _Optional[str] = ..., max_wall_seconds: _Optional[int] = ...) -> None: ...

class PrimaryRule(_message.Message):
    __slots__ = ("rule", "params", "threshold")
    RULE_FIELD_NUMBER: _ClassVar[int]
    PARAMS_FIELD_NUMBER: _ClassVar[int]
    THRESHOLD_FIELD_NUMBER: _ClassVar[int]
    rule: str
    params: _struct_pb2.Struct
    threshold: float
    def __init__(self, rule: _Optional[str] = ..., params: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ..., threshold: _Optional[float] = ...) -> None: ...

class EvaluationSpec(_message.Message):
    __slots__ = ("top_quantile", "long_only", "cost_multiplier", "event_backtest")
    TOP_QUANTILE_FIELD_NUMBER: _ClassVar[int]
    LONG_ONLY_FIELD_NUMBER: _ClassVar[int]
    COST_MULTIPLIER_FIELD_NUMBER: _ClassVar[int]
    EVENT_BACKTEST_FIELD_NUMBER: _ClassVar[int]
    top_quantile: float
    long_only: bool
    cost_multiplier: float
    event_backtest: bool
    def __init__(self, top_quantile: _Optional[float] = ..., long_only: _Optional[bool] = ..., cost_multiplier: _Optional[float] = ..., event_backtest: _Optional[bool] = ...) -> None: ...

class TrainingConfig(_message.Message):
    __slots__ = ("name", "family", "hyperparams", "feature_set", "label", "universe", "timeframe", "period", "splitter", "sample_weighting", "seed", "resources", "primary", "lineage_id", "data_source", "evaluation")
    NAME_FIELD_NUMBER: _ClassVar[int]
    FAMILY_FIELD_NUMBER: _ClassVar[int]
    HYPERPARAMS_FIELD_NUMBER: _ClassVar[int]
    FEATURE_SET_FIELD_NUMBER: _ClassVar[int]
    LABEL_FIELD_NUMBER: _ClassVar[int]
    UNIVERSE_FIELD_NUMBER: _ClassVar[int]
    TIMEFRAME_FIELD_NUMBER: _ClassVar[int]
    PERIOD_FIELD_NUMBER: _ClassVar[int]
    SPLITTER_FIELD_NUMBER: _ClassVar[int]
    SAMPLE_WEIGHTING_FIELD_NUMBER: _ClassVar[int]
    SEED_FIELD_NUMBER: _ClassVar[int]
    RESOURCES_FIELD_NUMBER: _ClassVar[int]
    PRIMARY_FIELD_NUMBER: _ClassVar[int]
    LINEAGE_ID_FIELD_NUMBER: _ClassVar[int]
    DATA_SOURCE_FIELD_NUMBER: _ClassVar[int]
    EVALUATION_FIELD_NUMBER: _ClassVar[int]
    name: str
    family: str
    hyperparams: _struct_pb2.Struct
    feature_set: FeatureSetRef
    label: LabelSpec
    universe: UniverseSpec
    timeframe: str
    period: Period
    splitter: SplitterSpec
    sample_weighting: SampleWeighting
    seed: int
    resources: Resources
    primary: PrimaryRule
    lineage_id: str
    data_source: str
    evaluation: EvaluationSpec
    def __init__(self, name: _Optional[str] = ..., family: _Optional[str] = ..., hyperparams: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ..., feature_set: _Optional[_Union[FeatureSetRef, _Mapping]] = ..., label: _Optional[_Union[LabelSpec, _Mapping]] = ..., universe: _Optional[_Union[UniverseSpec, _Mapping]] = ..., timeframe: _Optional[str] = ..., period: _Optional[_Union[Period, _Mapping]] = ..., splitter: _Optional[_Union[SplitterSpec, _Mapping]] = ..., sample_weighting: _Optional[_Union[SampleWeighting, _Mapping]] = ..., seed: _Optional[int] = ..., resources: _Optional[_Union[Resources, _Mapping]] = ..., primary: _Optional[_Union[PrimaryRule, _Mapping]] = ..., lineage_id: _Optional[str] = ..., data_source: _Optional[str] = ..., evaluation: _Optional[_Union[EvaluationSpec, _Mapping]] = ...) -> None: ...

class JobEstimate(_message.Message):
    __slots__ = ("rows", "fits", "cpu_seconds", "gpu_seconds", "memory_mb", "over_budget", "device")
    ROWS_FIELD_NUMBER: _ClassVar[int]
    FITS_FIELD_NUMBER: _ClassVar[int]
    CPU_SECONDS_FIELD_NUMBER: _ClassVar[int]
    GPU_SECONDS_FIELD_NUMBER: _ClassVar[int]
    MEMORY_MB_FIELD_NUMBER: _ClassVar[int]
    OVER_BUDGET_FIELD_NUMBER: _ClassVar[int]
    DEVICE_FIELD_NUMBER: _ClassVar[int]
    rows: int
    fits: int
    cpu_seconds: float
    gpu_seconds: float
    memory_mb: int
    over_budget: bool
    device: str
    def __init__(self, rows: _Optional[int] = ..., fits: _Optional[int] = ..., cpu_seconds: _Optional[float] = ..., gpu_seconds: _Optional[float] = ..., memory_mb: _Optional[int] = ..., over_budget: _Optional[bool] = ..., device: _Optional[str] = ...) -> None: ...

class TrainingJob(_message.Message):
    __slots__ = ("job_id", "run_id", "config", "status", "submitted_at", "started_at", "finished_at", "launched_by", "priority", "progress", "message", "estimate", "error", "phase", "kind", "base_run_id")
    JOB_ID_FIELD_NUMBER: _ClassVar[int]
    RUN_ID_FIELD_NUMBER: _ClassVar[int]
    CONFIG_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    SUBMITTED_AT_FIELD_NUMBER: _ClassVar[int]
    STARTED_AT_FIELD_NUMBER: _ClassVar[int]
    FINISHED_AT_FIELD_NUMBER: _ClassVar[int]
    LAUNCHED_BY_FIELD_NUMBER: _ClassVar[int]
    PRIORITY_FIELD_NUMBER: _ClassVar[int]
    PROGRESS_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    ESTIMATE_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    PHASE_FIELD_NUMBER: _ClassVar[int]
    KIND_FIELD_NUMBER: _ClassVar[int]
    BASE_RUN_ID_FIELD_NUMBER: _ClassVar[int]
    job_id: str
    run_id: str
    config: TrainingConfig
    status: JobStatus
    submitted_at: int
    started_at: int
    finished_at: int
    launched_by: CallerContext
    priority: int
    progress: float
    message: str
    estimate: JobEstimate
    error: str
    phase: str
    kind: str
    base_run_id: str
    def __init__(self, job_id: _Optional[str] = ..., run_id: _Optional[str] = ..., config: _Optional[_Union[TrainingConfig, _Mapping]] = ..., status: _Optional[_Union[JobStatus, str]] = ..., submitted_at: _Optional[int] = ..., started_at: _Optional[int] = ..., finished_at: _Optional[int] = ..., launched_by: _Optional[_Union[CallerContext, _Mapping]] = ..., priority: _Optional[int] = ..., progress: _Optional[float] = ..., message: _Optional[str] = ..., estimate: _Optional[_Union[JobEstimate, _Mapping]] = ..., error: _Optional[str] = ..., phase: _Optional[str] = ..., kind: _Optional[str] = ..., base_run_id: _Optional[str] = ...) -> None: ...

class JobProgress(_message.Message):
    __slots__ = ("job_id", "seq", "at", "phase", "fold", "n_folds", "step", "metrics", "log", "status", "progress")
    class MetricsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: float
        def __init__(self, key: _Optional[str] = ..., value: _Optional[float] = ...) -> None: ...
    JOB_ID_FIELD_NUMBER: _ClassVar[int]
    SEQ_FIELD_NUMBER: _ClassVar[int]
    AT_FIELD_NUMBER: _ClassVar[int]
    PHASE_FIELD_NUMBER: _ClassVar[int]
    FOLD_FIELD_NUMBER: _ClassVar[int]
    N_FOLDS_FIELD_NUMBER: _ClassVar[int]
    STEP_FIELD_NUMBER: _ClassVar[int]
    METRICS_FIELD_NUMBER: _ClassVar[int]
    LOG_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    PROGRESS_FIELD_NUMBER: _ClassVar[int]
    job_id: str
    seq: int
    at: int
    phase: str
    fold: int
    n_folds: int
    step: int
    metrics: _containers.ScalarMap[str, float]
    log: str
    status: JobStatus
    progress: float
    def __init__(self, job_id: _Optional[str] = ..., seq: _Optional[int] = ..., at: _Optional[int] = ..., phase: _Optional[str] = ..., fold: _Optional[int] = ..., n_folds: _Optional[int] = ..., step: _Optional[int] = ..., metrics: _Optional[_Mapping[str, float]] = ..., log: _Optional[str] = ..., status: _Optional[_Union[JobStatus, str]] = ..., progress: _Optional[float] = ...) -> None: ...

class Reproducibility(_message.Message):
    __slots__ = ("code_commit", "config_hash", "env_lock_hash", "library_versions", "hardware", "seed", "runtime_seconds", "threads")
    class LibraryVersionsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    CODE_COMMIT_FIELD_NUMBER: _ClassVar[int]
    CONFIG_HASH_FIELD_NUMBER: _ClassVar[int]
    ENV_LOCK_HASH_FIELD_NUMBER: _ClassVar[int]
    LIBRARY_VERSIONS_FIELD_NUMBER: _ClassVar[int]
    HARDWARE_FIELD_NUMBER: _ClassVar[int]
    SEED_FIELD_NUMBER: _ClassVar[int]
    RUNTIME_SECONDS_FIELD_NUMBER: _ClassVar[int]
    THREADS_FIELD_NUMBER: _ClassVar[int]
    code_commit: str
    config_hash: str
    env_lock_hash: str
    library_versions: _containers.ScalarMap[str, str]
    hardware: str
    seed: int
    runtime_seconds: float
    threads: int
    def __init__(self, code_commit: _Optional[str] = ..., config_hash: _Optional[str] = ..., env_lock_hash: _Optional[str] = ..., library_versions: _Optional[_Mapping[str, str]] = ..., hardware: _Optional[str] = ..., seed: _Optional[int] = ..., runtime_seconds: _Optional[float] = ..., threads: _Optional[int] = ...) -> None: ...

class DatasetManifest(_message.Message):
    __slots__ = ("snapshot_id", "feature_set_id", "feature_set_hash", "label_spec_hash", "timeframe", "period", "rows", "instruments", "eras", "rows_dropped", "label_summary", "features", "data_source", "created_at", "avg_uniqueness")
    class RowsDroppedEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: int
        def __init__(self, key: _Optional[str] = ..., value: _Optional[int] = ...) -> None: ...
    class LabelSummaryEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: float
        def __init__(self, key: _Optional[str] = ..., value: _Optional[float] = ...) -> None: ...
    SNAPSHOT_ID_FIELD_NUMBER: _ClassVar[int]
    FEATURE_SET_ID_FIELD_NUMBER: _ClassVar[int]
    FEATURE_SET_HASH_FIELD_NUMBER: _ClassVar[int]
    LABEL_SPEC_HASH_FIELD_NUMBER: _ClassVar[int]
    TIMEFRAME_FIELD_NUMBER: _ClassVar[int]
    PERIOD_FIELD_NUMBER: _ClassVar[int]
    ROWS_FIELD_NUMBER: _ClassVar[int]
    INSTRUMENTS_FIELD_NUMBER: _ClassVar[int]
    ERAS_FIELD_NUMBER: _ClassVar[int]
    ROWS_DROPPED_FIELD_NUMBER: _ClassVar[int]
    LABEL_SUMMARY_FIELD_NUMBER: _ClassVar[int]
    FEATURES_FIELD_NUMBER: _ClassVar[int]
    DATA_SOURCE_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_FIELD_NUMBER: _ClassVar[int]
    AVG_UNIQUENESS_FIELD_NUMBER: _ClassVar[int]
    snapshot_id: str
    feature_set_id: str
    feature_set_hash: str
    label_spec_hash: str
    timeframe: str
    period: Period
    rows: int
    instruments: int
    eras: int
    rows_dropped: _containers.ScalarMap[str, int]
    label_summary: _containers.ScalarMap[str, float]
    features: _containers.RepeatedScalarFieldContainer[str]
    data_source: str
    created_at: int
    avg_uniqueness: float
    def __init__(self, snapshot_id: _Optional[str] = ..., feature_set_id: _Optional[str] = ..., feature_set_hash: _Optional[str] = ..., label_spec_hash: _Optional[str] = ..., timeframe: _Optional[str] = ..., period: _Optional[_Union[Period, _Mapping]] = ..., rows: _Optional[int] = ..., instruments: _Optional[int] = ..., eras: _Optional[int] = ..., rows_dropped: _Optional[_Mapping[str, int]] = ..., label_summary: _Optional[_Mapping[str, float]] = ..., features: _Optional[_Iterable[str]] = ..., data_source: _Optional[str] = ..., created_at: _Optional[int] = ..., avg_uniqueness: _Optional[float] = ...) -> None: ...

class FoldMetrics(_message.Message):
    __slots__ = ("fold", "train_rows", "test_rows", "train", "test", "metrics", "backtest", "best_iteration")
    class MetricsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: float
        def __init__(self, key: _Optional[str] = ..., value: _Optional[float] = ...) -> None: ...
    class BacktestEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: float
        def __init__(self, key: _Optional[str] = ..., value: _Optional[float] = ...) -> None: ...
    FOLD_FIELD_NUMBER: _ClassVar[int]
    TRAIN_ROWS_FIELD_NUMBER: _ClassVar[int]
    TEST_ROWS_FIELD_NUMBER: _ClassVar[int]
    TRAIN_FIELD_NUMBER: _ClassVar[int]
    TEST_FIELD_NUMBER: _ClassVar[int]
    METRICS_FIELD_NUMBER: _ClassVar[int]
    BACKTEST_FIELD_NUMBER: _ClassVar[int]
    BEST_ITERATION_FIELD_NUMBER: _ClassVar[int]
    fold: int
    train_rows: int
    test_rows: int
    train: Period
    test: Period
    metrics: _containers.ScalarMap[str, float]
    backtest: _containers.ScalarMap[str, float]
    best_iteration: int
    def __init__(self, fold: _Optional[int] = ..., train_rows: _Optional[int] = ..., test_rows: _Optional[int] = ..., train: _Optional[_Union[Period, _Mapping]] = ..., test: _Optional[_Union[Period, _Mapping]] = ..., metrics: _Optional[_Mapping[str, float]] = ..., backtest: _Optional[_Mapping[str, float]] = ..., best_iteration: _Optional[int] = ...) -> None: ...

class TrainingRun(_message.Message):
    __slots__ = ("run_id", "job_id", "config", "dataset", "folds", "oos_metrics", "artifact_uri", "model_card_uri", "tearsheet_uri", "diagnostics_report_id", "mlflow_run_id", "repro", "status", "base_run_id", "lineage_id", "trial_id", "launched_by", "created_at", "notes")
    class OosMetricsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: float
        def __init__(self, key: _Optional[str] = ..., value: _Optional[float] = ...) -> None: ...
    RUN_ID_FIELD_NUMBER: _ClassVar[int]
    JOB_ID_FIELD_NUMBER: _ClassVar[int]
    CONFIG_FIELD_NUMBER: _ClassVar[int]
    DATASET_FIELD_NUMBER: _ClassVar[int]
    FOLDS_FIELD_NUMBER: _ClassVar[int]
    OOS_METRICS_FIELD_NUMBER: _ClassVar[int]
    ARTIFACT_URI_FIELD_NUMBER: _ClassVar[int]
    MODEL_CARD_URI_FIELD_NUMBER: _ClassVar[int]
    TEARSHEET_URI_FIELD_NUMBER: _ClassVar[int]
    DIAGNOSTICS_REPORT_ID_FIELD_NUMBER: _ClassVar[int]
    MLFLOW_RUN_ID_FIELD_NUMBER: _ClassVar[int]
    REPRO_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    BASE_RUN_ID_FIELD_NUMBER: _ClassVar[int]
    LINEAGE_ID_FIELD_NUMBER: _ClassVar[int]
    TRIAL_ID_FIELD_NUMBER: _ClassVar[int]
    LAUNCHED_BY_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_FIELD_NUMBER: _ClassVar[int]
    NOTES_FIELD_NUMBER: _ClassVar[int]
    run_id: str
    job_id: str
    config: TrainingConfig
    dataset: DatasetManifest
    folds: _containers.RepeatedCompositeFieldContainer[FoldMetrics]
    oos_metrics: _containers.ScalarMap[str, float]
    artifact_uri: str
    model_card_uri: str
    tearsheet_uri: str
    diagnostics_report_id: str
    mlflow_run_id: str
    repro: Reproducibility
    status: JobStatus
    base_run_id: str
    lineage_id: str
    trial_id: str
    launched_by: CallerContext
    created_at: int
    notes: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, run_id: _Optional[str] = ..., job_id: _Optional[str] = ..., config: _Optional[_Union[TrainingConfig, _Mapping]] = ..., dataset: _Optional[_Union[DatasetManifest, _Mapping]] = ..., folds: _Optional[_Iterable[_Union[FoldMetrics, _Mapping]]] = ..., oos_metrics: _Optional[_Mapping[str, float]] = ..., artifact_uri: _Optional[str] = ..., model_card_uri: _Optional[str] = ..., tearsheet_uri: _Optional[str] = ..., diagnostics_report_id: _Optional[str] = ..., mlflow_run_id: _Optional[str] = ..., repro: _Optional[_Union[Reproducibility, _Mapping]] = ..., status: _Optional[_Union[JobStatus, str]] = ..., base_run_id: _Optional[str] = ..., lineage_id: _Optional[str] = ..., trial_id: _Optional[str] = ..., launched_by: _Optional[_Union[CallerContext, _Mapping]] = ..., created_at: _Optional[int] = ..., notes: _Optional[_Iterable[str]] = ...) -> None: ...

class ModelFamily(_message.Message):
    __slots__ = ("family", "tier", "task", "description", "hyperparam_schema_json", "cpu_threads_hint", "memory_mb_hint", "supports_gpu", "devices", "horizon", "experimental")
    FAMILY_FIELD_NUMBER: _ClassVar[int]
    TIER_FIELD_NUMBER: _ClassVar[int]
    TASK_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    HYPERPARAM_SCHEMA_JSON_FIELD_NUMBER: _ClassVar[int]
    CPU_THREADS_HINT_FIELD_NUMBER: _ClassVar[int]
    MEMORY_MB_HINT_FIELD_NUMBER: _ClassVar[int]
    SUPPORTS_GPU_FIELD_NUMBER: _ClassVar[int]
    DEVICES_FIELD_NUMBER: _ClassVar[int]
    HORIZON_FIELD_NUMBER: _ClassVar[int]
    EXPERIMENTAL_FIELD_NUMBER: _ClassVar[int]
    family: str
    tier: int
    task: str
    description: str
    hyperparam_schema_json: str
    cpu_threads_hint: int
    memory_mb_hint: int
    supports_gpu: bool
    devices: _containers.RepeatedScalarFieldContainer[str]
    horizon: str
    experimental: bool
    def __init__(self, family: _Optional[str] = ..., tier: _Optional[int] = ..., task: _Optional[str] = ..., description: _Optional[str] = ..., hyperparam_schema_json: _Optional[str] = ..., cpu_threads_hint: _Optional[int] = ..., memory_mb_hint: _Optional[int] = ..., supports_gpu: _Optional[bool] = ..., devices: _Optional[_Iterable[str]] = ..., horizon: _Optional[str] = ..., experimental: _Optional[bool] = ...) -> None: ...

class FeatureSetInfo(_message.Message):
    __slots__ = ("name", "version", "hash", "features", "description", "max_lookback")
    NAME_FIELD_NUMBER: _ClassVar[int]
    VERSION_FIELD_NUMBER: _ClassVar[int]
    HASH_FIELD_NUMBER: _ClassVar[int]
    FEATURES_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    MAX_LOOKBACK_FIELD_NUMBER: _ClassVar[int]
    name: str
    version: int
    hash: str
    features: _containers.RepeatedScalarFieldContainer[str]
    description: str
    max_lookback: int
    def __init__(self, name: _Optional[str] = ..., version: _Optional[int] = ..., hash: _Optional[str] = ..., features: _Optional[_Iterable[str]] = ..., description: _Optional[str] = ..., max_lookback: _Optional[int] = ...) -> None: ...

class LabelPreset(_message.Message):
    __slots__ = ("name", "spec", "timeframe", "description")
    NAME_FIELD_NUMBER: _ClassVar[int]
    SPEC_FIELD_NUMBER: _ClassVar[int]
    TIMEFRAME_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    name: str
    spec: LabelSpec
    timeframe: str
    description: str
    def __init__(self, name: _Optional[str] = ..., spec: _Optional[_Union[LabelSpec, _Mapping]] = ..., timeframe: _Optional[str] = ..., description: _Optional[str] = ...) -> None: ...

class ModelCard(_message.Message):
    __slots__ = ("run_id", "markdown", "fields")
    class FieldsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    RUN_ID_FIELD_NUMBER: _ClassVar[int]
    MARKDOWN_FIELD_NUMBER: _ClassVar[int]
    FIELDS_FIELD_NUMBER: _ClassVar[int]
    run_id: str
    markdown: str
    fields: _containers.ScalarMap[str, str]
    def __init__(self, run_id: _Optional[str] = ..., markdown: _Optional[str] = ..., fields: _Optional[_Mapping[str, str]] = ...) -> None: ...

class FeatureStat(_message.Message):
    __slots__ = ("feature", "missing_rate", "mean", "std", "p01", "p99", "outlier_rate")
    FEATURE_FIELD_NUMBER: _ClassVar[int]
    MISSING_RATE_FIELD_NUMBER: _ClassVar[int]
    MEAN_FIELD_NUMBER: _ClassVar[int]
    STD_FIELD_NUMBER: _ClassVar[int]
    P01_FIELD_NUMBER: _ClassVar[int]
    P99_FIELD_NUMBER: _ClassVar[int]
    OUTLIER_RATE_FIELD_NUMBER: _ClassVar[int]
    feature: str
    missing_rate: float
    mean: float
    std: float
    p01: float
    p99: float
    outlier_rate: float
    def __init__(self, feature: _Optional[str] = ..., missing_rate: _Optional[float] = ..., mean: _Optional[float] = ..., std: _Optional[float] = ..., p01: _Optional[float] = ..., p99: _Optional[float] = ..., outlier_rate: _Optional[float] = ...) -> None: ...

class LabelFoldStat(_message.Message):
    __slots__ = ("fold", "mean", "std", "skew", "positive_rate", "n")
    FOLD_FIELD_NUMBER: _ClassVar[int]
    MEAN_FIELD_NUMBER: _ClassVar[int]
    STD_FIELD_NUMBER: _ClassVar[int]
    SKEW_FIELD_NUMBER: _ClassVar[int]
    POSITIVE_RATE_FIELD_NUMBER: _ClassVar[int]
    N_FIELD_NUMBER: _ClassVar[int]
    fold: int
    mean: float
    std: float
    skew: float
    positive_rate: float
    n: int
    def __init__(self, fold: _Optional[int] = ..., mean: _Optional[float] = ..., std: _Optional[float] = ..., skew: _Optional[float] = ..., positive_rate: _Optional[float] = ..., n: _Optional[int] = ...) -> None: ...

class DataQuality(_message.Message):
    __slots__ = ("rows_per_fold", "features", "labels", "avg_uniqueness", "rows_dropped")
    class RowsDroppedEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: int
        def __init__(self, key: _Optional[str] = ..., value: _Optional[int] = ...) -> None: ...
    ROWS_PER_FOLD_FIELD_NUMBER: _ClassVar[int]
    FEATURES_FIELD_NUMBER: _ClassVar[int]
    LABELS_FIELD_NUMBER: _ClassVar[int]
    AVG_UNIQUENESS_FIELD_NUMBER: _ClassVar[int]
    ROWS_DROPPED_FIELD_NUMBER: _ClassVar[int]
    rows_per_fold: _containers.RepeatedCompositeFieldContainer[FoldMetrics]
    features: _containers.RepeatedCompositeFieldContainer[FeatureStat]
    labels: _containers.RepeatedCompositeFieldContainer[LabelFoldStat]
    avg_uniqueness: float
    rows_dropped: _containers.ScalarMap[str, int]
    def __init__(self, rows_per_fold: _Optional[_Iterable[_Union[FoldMetrics, _Mapping]]] = ..., features: _Optional[_Iterable[_Union[FeatureStat, _Mapping]]] = ..., labels: _Optional[_Iterable[_Union[LabelFoldStat, _Mapping]]] = ..., avg_uniqueness: _Optional[float] = ..., rows_dropped: _Optional[_Mapping[str, int]] = ...) -> None: ...

class ShiftStat(_message.Message):
    __slots__ = ("feature", "fold", "psi", "ks")
    FEATURE_FIELD_NUMBER: _ClassVar[int]
    FOLD_FIELD_NUMBER: _ClassVar[int]
    PSI_FIELD_NUMBER: _ClassVar[int]
    KS_FIELD_NUMBER: _ClassVar[int]
    feature: str
    fold: int
    psi: float
    ks: float
    def __init__(self, feature: _Optional[str] = ..., fold: _Optional[int] = ..., psi: _Optional[float] = ..., ks: _Optional[float] = ...) -> None: ...

class FeatureCluster(_message.Message):
    __slots__ = ("features", "max_abs_corr")
    FEATURES_FIELD_NUMBER: _ClassVar[int]
    MAX_ABS_CORR_FIELD_NUMBER: _ClassVar[int]
    features: _containers.RepeatedScalarFieldContainer[str]
    max_abs_corr: float
    def __init__(self, features: _Optional[_Iterable[str]] = ..., max_abs_corr: _Optional[float] = ...) -> None: ...

class StationarityResult(_message.Message):
    __slots__ = ("feature", "adf_stat", "critical_5pct", "stationary")
    FEATURE_FIELD_NUMBER: _ClassVar[int]
    ADF_STAT_FIELD_NUMBER: _ClassVar[int]
    CRITICAL_5PCT_FIELD_NUMBER: _ClassVar[int]
    STATIONARY_FIELD_NUMBER: _ClassVar[int]
    feature: str
    adf_stat: float
    critical_5pct: float
    stationary: bool
    def __init__(self, feature: _Optional[str] = ..., adf_stat: _Optional[float] = ..., critical_5pct: _Optional[float] = ..., stationary: _Optional[bool] = ...) -> None: ...

class LeakageFlag(_message.Message):
    __slots__ = ("feature", "reason", "value", "threshold")
    FEATURE_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    VALUE_FIELD_NUMBER: _ClassVar[int]
    THRESHOLD_FIELD_NUMBER: _ClassVar[int]
    feature: str
    reason: str
    value: float
    threshold: float
    def __init__(self, feature: _Optional[str] = ..., reason: _Optional[str] = ..., value: _Optional[float] = ..., threshold: _Optional[float] = ...) -> None: ...

class FeatureDiagnostics(_message.Message):
    __slots__ = ("shift", "clusters", "stationarity", "leakage_flags")
    SHIFT_FIELD_NUMBER: _ClassVar[int]
    CLUSTERS_FIELD_NUMBER: _ClassVar[int]
    STATIONARITY_FIELD_NUMBER: _ClassVar[int]
    LEAKAGE_FLAGS_FIELD_NUMBER: _ClassVar[int]
    shift: _containers.RepeatedCompositeFieldContainer[ShiftStat]
    clusters: _containers.RepeatedCompositeFieldContainer[FeatureCluster]
    stationarity: _containers.RepeatedCompositeFieldContainer[StationarityResult]
    leakage_flags: _containers.RepeatedCompositeFieldContainer[LeakageFlag]
    def __init__(self, shift: _Optional[_Iterable[_Union[ShiftStat, _Mapping]]] = ..., clusters: _Optional[_Iterable[_Union[FeatureCluster, _Mapping]]] = ..., stationarity: _Optional[_Iterable[_Union[StationarityResult, _Mapping]]] = ..., leakage_flags: _Optional[_Iterable[_Union[LeakageFlag, _Mapping]]] = ...) -> None: ...

class CurvePoint(_message.Message):
    __slots__ = ("step", "train_metric", "val_metric")
    STEP_FIELD_NUMBER: _ClassVar[int]
    TRAIN_METRIC_FIELD_NUMBER: _ClassVar[int]
    VAL_METRIC_FIELD_NUMBER: _ClassVar[int]
    step: int
    train_metric: float
    val_metric: float
    def __init__(self, step: _Optional[int] = ..., train_metric: _Optional[float] = ..., val_metric: _Optional[float] = ...) -> None: ...

class FoldCurve(_message.Message):
    __slots__ = ("fold", "metric", "points", "best_step")
    FOLD_FIELD_NUMBER: _ClassVar[int]
    METRIC_FIELD_NUMBER: _ClassVar[int]
    POINTS_FIELD_NUMBER: _ClassVar[int]
    BEST_STEP_FIELD_NUMBER: _ClassVar[int]
    fold: int
    metric: str
    points: _containers.RepeatedCompositeFieldContainer[CurvePoint]
    best_step: int
    def __init__(self, fold: _Optional[int] = ..., metric: _Optional[str] = ..., points: _Optional[_Iterable[_Union[CurvePoint, _Mapping]]] = ..., best_step: _Optional[int] = ...) -> None: ...

class SizePoint(_message.Message):
    __slots__ = ("train_rows", "train_metric", "val_metric")
    TRAIN_ROWS_FIELD_NUMBER: _ClassVar[int]
    TRAIN_METRIC_FIELD_NUMBER: _ClassVar[int]
    VAL_METRIC_FIELD_NUMBER: _ClassVar[int]
    train_rows: int
    train_metric: float
    val_metric: float
    def __init__(self, train_rows: _Optional[int] = ..., train_metric: _Optional[float] = ..., val_metric: _Optional[float] = ...) -> None: ...

class LearningCurves(_message.Message):
    __slots__ = ("per_fold", "size_curve", "overfitting_gap")
    PER_FOLD_FIELD_NUMBER: _ClassVar[int]
    SIZE_CURVE_FIELD_NUMBER: _ClassVar[int]
    OVERFITTING_GAP_FIELD_NUMBER: _ClassVar[int]
    per_fold: _containers.RepeatedCompositeFieldContainer[FoldCurve]
    size_curve: _containers.RepeatedCompositeFieldContainer[SizePoint]
    overfitting_gap: float
    def __init__(self, per_fold: _Optional[_Iterable[_Union[FoldCurve, _Mapping]]] = ..., size_curve: _Optional[_Iterable[_Union[SizePoint, _Mapping]]] = ..., overfitting_gap: _Optional[float] = ...) -> None: ...

class FoldImportance(_message.Message):
    __slots__ = ("fold", "values")
    class ValuesEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: float
        def __init__(self, key: _Optional[str] = ..., value: _Optional[float] = ...) -> None: ...
    FOLD_FIELD_NUMBER: _ClassVar[int]
    VALUES_FIELD_NUMBER: _ClassVar[int]
    fold: int
    values: _containers.ScalarMap[str, float]
    def __init__(self, fold: _Optional[int] = ..., values: _Optional[_Mapping[str, float]] = ...) -> None: ...

class ImportanceStability(_message.Message):
    __slots__ = ("feature", "mean", "std", "sign_flip_rate", "rank_std")
    FEATURE_FIELD_NUMBER: _ClassVar[int]
    MEAN_FIELD_NUMBER: _ClassVar[int]
    STD_FIELD_NUMBER: _ClassVar[int]
    SIGN_FLIP_RATE_FIELD_NUMBER: _ClassVar[int]
    RANK_STD_FIELD_NUMBER: _ClassVar[int]
    feature: str
    mean: float
    std: float
    sign_flip_rate: float
    rank_std: float
    def __init__(self, feature: _Optional[str] = ..., mean: _Optional[float] = ..., std: _Optional[float] = ..., sign_flip_rate: _Optional[float] = ..., rank_std: _Optional[float] = ...) -> None: ...

class Interaction(_message.Message):
    __slots__ = ("feature_a", "feature_b", "value")
    FEATURE_A_FIELD_NUMBER: _ClassVar[int]
    FEATURE_B_FIELD_NUMBER: _ClassVar[int]
    VALUE_FIELD_NUMBER: _ClassVar[int]
    feature_a: str
    feature_b: str
    value: float
    def __init__(self, feature_a: _Optional[str] = ..., feature_b: _Optional[str] = ..., value: _Optional[float] = ...) -> None: ...

class FeatureImportance(_message.Message):
    __slots__ = ("method", "per_fold", "stability", "top_interactions")
    METHOD_FIELD_NUMBER: _ClassVar[int]
    PER_FOLD_FIELD_NUMBER: _ClassVar[int]
    STABILITY_FIELD_NUMBER: _ClassVar[int]
    TOP_INTERACTIONS_FIELD_NUMBER: _ClassVar[int]
    method: str
    per_fold: _containers.RepeatedCompositeFieldContainer[FoldImportance]
    stability: _containers.RepeatedCompositeFieldContainer[ImportanceStability]
    top_interactions: _containers.RepeatedCompositeFieldContainer[Interaction]
    def __init__(self, method: _Optional[str] = ..., per_fold: _Optional[_Iterable[_Union[FoldImportance, _Mapping]]] = ..., stability: _Optional[_Iterable[_Union[ImportanceStability, _Mapping]]] = ..., top_interactions: _Optional[_Iterable[_Union[Interaction, _Mapping]]] = ...) -> None: ...

class ErrorSlice(_message.Message):
    __slots__ = ("slice_by", "slice", "n", "metric", "calibration_error", "vs_overall", "metric_name")
    SLICE_BY_FIELD_NUMBER: _ClassVar[int]
    SLICE_FIELD_NUMBER: _ClassVar[int]
    N_FIELD_NUMBER: _ClassVar[int]
    METRIC_FIELD_NUMBER: _ClassVar[int]
    CALIBRATION_ERROR_FIELD_NUMBER: _ClassVar[int]
    VS_OVERALL_FIELD_NUMBER: _ClassVar[int]
    METRIC_NAME_FIELD_NUMBER: _ClassVar[int]
    slice_by: str
    slice: str
    n: int
    metric: float
    calibration_error: float
    vs_overall: float
    metric_name: str
    def __init__(self, slice_by: _Optional[str] = ..., slice: _Optional[str] = ..., n: _Optional[int] = ..., metric: _Optional[float] = ..., calibration_error: _Optional[float] = ..., vs_overall: _Optional[float] = ..., metric_name: _Optional[str] = ...) -> None: ...

class WorstSample(_message.Message):
    __slots__ = ("instrument_id", "decision_time", "label", "prediction", "regime")
    INSTRUMENT_ID_FIELD_NUMBER: _ClassVar[int]
    DECISION_TIME_FIELD_NUMBER: _ClassVar[int]
    LABEL_FIELD_NUMBER: _ClassVar[int]
    PREDICTION_FIELD_NUMBER: _ClassVar[int]
    REGIME_FIELD_NUMBER: _ClassVar[int]
    instrument_id: str
    decision_time: int
    label: float
    prediction: float
    regime: str
    def __init__(self, instrument_id: _Optional[str] = ..., decision_time: _Optional[int] = ..., label: _Optional[float] = ..., prediction: _Optional[float] = ..., regime: _Optional[str] = ...) -> None: ...

class Evidence(_message.Message):
    __slots__ = ("name", "value", "threshold", "ref")
    NAME_FIELD_NUMBER: _ClassVar[int]
    VALUE_FIELD_NUMBER: _ClassVar[int]
    THRESHOLD_FIELD_NUMBER: _ClassVar[int]
    REF_FIELD_NUMBER: _ClassVar[int]
    name: str
    value: float
    threshold: float
    ref: str
    def __init__(self, name: _Optional[str] = ..., value: _Optional[float] = ..., threshold: _Optional[float] = ..., ref: _Optional[str] = ...) -> None: ...

class Finding(_message.Message):
    __slots__ = ("code", "severity", "message", "evidence")
    CODE_FIELD_NUMBER: _ClassVar[int]
    SEVERITY_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    EVIDENCE_FIELD_NUMBER: _ClassVar[int]
    code: str
    severity: Severity
    message: str
    evidence: _containers.RepeatedCompositeFieldContainer[Evidence]
    def __init__(self, code: _Optional[str] = ..., severity: _Optional[_Union[Severity, str]] = ..., message: _Optional[str] = ..., evidence: _Optional[_Iterable[_Union[Evidence, _Mapping]]] = ...) -> None: ...

class DiagnosticsReport(_message.Message):
    __slots__ = ("report_id", "run_id", "created_at", "data_quality", "feature_diagnostics", "learning_curves", "fold_metrics", "importance", "error_slices", "worst_samples", "findings", "thresholds", "holdout_excluded", "truncated", "family", "task")
    class ThresholdsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: float
        def __init__(self, key: _Optional[str] = ..., value: _Optional[float] = ...) -> None: ...
    REPORT_ID_FIELD_NUMBER: _ClassVar[int]
    RUN_ID_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_FIELD_NUMBER: _ClassVar[int]
    DATA_QUALITY_FIELD_NUMBER: _ClassVar[int]
    FEATURE_DIAGNOSTICS_FIELD_NUMBER: _ClassVar[int]
    LEARNING_CURVES_FIELD_NUMBER: _ClassVar[int]
    FOLD_METRICS_FIELD_NUMBER: _ClassVar[int]
    IMPORTANCE_FIELD_NUMBER: _ClassVar[int]
    ERROR_SLICES_FIELD_NUMBER: _ClassVar[int]
    WORST_SAMPLES_FIELD_NUMBER: _ClassVar[int]
    FINDINGS_FIELD_NUMBER: _ClassVar[int]
    THRESHOLDS_FIELD_NUMBER: _ClassVar[int]
    HOLDOUT_EXCLUDED_FIELD_NUMBER: _ClassVar[int]
    TRUNCATED_FIELD_NUMBER: _ClassVar[int]
    FAMILY_FIELD_NUMBER: _ClassVar[int]
    TASK_FIELD_NUMBER: _ClassVar[int]
    report_id: str
    run_id: str
    created_at: int
    data_quality: DataQuality
    feature_diagnostics: FeatureDiagnostics
    learning_curves: LearningCurves
    fold_metrics: _containers.RepeatedCompositeFieldContainer[FoldMetrics]
    importance: FeatureImportance
    error_slices: _containers.RepeatedCompositeFieldContainer[ErrorSlice]
    worst_samples: _containers.RepeatedCompositeFieldContainer[WorstSample]
    findings: _containers.RepeatedCompositeFieldContainer[Finding]
    thresholds: _containers.ScalarMap[str, float]
    holdout_excluded: bool
    truncated: bool
    family: str
    task: str
    def __init__(self, report_id: _Optional[str] = ..., run_id: _Optional[str] = ..., created_at: _Optional[int] = ..., data_quality: _Optional[_Union[DataQuality, _Mapping]] = ..., feature_diagnostics: _Optional[_Union[FeatureDiagnostics, _Mapping]] = ..., learning_curves: _Optional[_Union[LearningCurves, _Mapping]] = ..., fold_metrics: _Optional[_Iterable[_Union[FoldMetrics, _Mapping]]] = ..., importance: _Optional[_Union[FeatureImportance, _Mapping]] = ..., error_slices: _Optional[_Iterable[_Union[ErrorSlice, _Mapping]]] = ..., worst_samples: _Optional[_Iterable[_Union[WorstSample, _Mapping]]] = ..., findings: _Optional[_Iterable[_Union[Finding, _Mapping]]] = ..., thresholds: _Optional[_Mapping[str, float]] = ..., holdout_excluded: _Optional[bool] = ..., truncated: _Optional[bool] = ..., family: _Optional[str] = ..., task: _Optional[str] = ...) -> None: ...

class DatasetProfile(_message.Message):
    __slots__ = ("snapshot_id", "rows", "instruments", "eras", "period", "features", "label_summary", "avg_uniqueness", "rows_dropped")
    class LabelSummaryEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: float
        def __init__(self, key: _Optional[str] = ..., value: _Optional[float] = ...) -> None: ...
    class RowsDroppedEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: int
        def __init__(self, key: _Optional[str] = ..., value: _Optional[int] = ...) -> None: ...
    SNAPSHOT_ID_FIELD_NUMBER: _ClassVar[int]
    ROWS_FIELD_NUMBER: _ClassVar[int]
    INSTRUMENTS_FIELD_NUMBER: _ClassVar[int]
    ERAS_FIELD_NUMBER: _ClassVar[int]
    PERIOD_FIELD_NUMBER: _ClassVar[int]
    FEATURES_FIELD_NUMBER: _ClassVar[int]
    LABEL_SUMMARY_FIELD_NUMBER: _ClassVar[int]
    AVG_UNIQUENESS_FIELD_NUMBER: _ClassVar[int]
    ROWS_DROPPED_FIELD_NUMBER: _ClassVar[int]
    snapshot_id: str
    rows: int
    instruments: int
    eras: int
    period: Period
    features: _containers.RepeatedCompositeFieldContainer[FeatureStat]
    label_summary: _containers.ScalarMap[str, float]
    avg_uniqueness: float
    rows_dropped: _containers.ScalarMap[str, int]
    def __init__(self, snapshot_id: _Optional[str] = ..., rows: _Optional[int] = ..., instruments: _Optional[int] = ..., eras: _Optional[int] = ..., period: _Optional[_Union[Period, _Mapping]] = ..., features: _Optional[_Iterable[_Union[FeatureStat, _Mapping]]] = ..., label_summary: _Optional[_Mapping[str, float]] = ..., avg_uniqueness: _Optional[float] = ..., rows_dropped: _Optional[_Mapping[str, int]] = ...) -> None: ...

class AblationVariant(_message.Message):
    __slots__ = ("name", "drop_feature_groups", "add_feature_groups", "label", "hyperparams")
    NAME_FIELD_NUMBER: _ClassVar[int]
    DROP_FEATURE_GROUPS_FIELD_NUMBER: _ClassVar[int]
    ADD_FEATURE_GROUPS_FIELD_NUMBER: _ClassVar[int]
    LABEL_FIELD_NUMBER: _ClassVar[int]
    HYPERPARAMS_FIELD_NUMBER: _ClassVar[int]
    name: str
    drop_feature_groups: _containers.RepeatedScalarFieldContainer[str]
    add_feature_groups: _containers.RepeatedScalarFieldContainer[str]
    label: LabelSpec
    hyperparams: _struct_pb2.Struct
    def __init__(self, name: _Optional[str] = ..., drop_feature_groups: _Optional[_Iterable[str]] = ..., add_feature_groups: _Optional[_Iterable[str]] = ..., label: _Optional[_Union[LabelSpec, _Mapping]] = ..., hyperparams: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ...) -> None: ...

class MLRunCompleted(_message.Message):
    __slots__ = ("run_id", "job_id", "report_id", "lineage_id", "trial_id", "launched_by", "analyst_round_id", "status", "completed_at", "correlation_id", "base_run_id", "kind")
    RUN_ID_FIELD_NUMBER: _ClassVar[int]
    JOB_ID_FIELD_NUMBER: _ClassVar[int]
    REPORT_ID_FIELD_NUMBER: _ClassVar[int]
    LINEAGE_ID_FIELD_NUMBER: _ClassVar[int]
    TRIAL_ID_FIELD_NUMBER: _ClassVar[int]
    LAUNCHED_BY_FIELD_NUMBER: _ClassVar[int]
    ANALYST_ROUND_ID_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    COMPLETED_AT_FIELD_NUMBER: _ClassVar[int]
    CORRELATION_ID_FIELD_NUMBER: _ClassVar[int]
    BASE_RUN_ID_FIELD_NUMBER: _ClassVar[int]
    KIND_FIELD_NUMBER: _ClassVar[int]
    run_id: str
    job_id: str
    report_id: str
    lineage_id: str
    trial_id: str
    launched_by: Actor
    analyst_round_id: str
    status: JobStatus
    completed_at: int
    correlation_id: str
    base_run_id: str
    kind: str
    def __init__(self, run_id: _Optional[str] = ..., job_id: _Optional[str] = ..., report_id: _Optional[str] = ..., lineage_id: _Optional[str] = ..., trial_id: _Optional[str] = ..., launched_by: _Optional[_Union[Actor, str]] = ..., analyst_round_id: _Optional[str] = ..., status: _Optional[_Union[JobStatus, str]] = ..., completed_at: _Optional[int] = ..., correlation_id: _Optional[str] = ..., base_run_id: _Optional[str] = ..., kind: _Optional[str] = ...) -> None: ...

class BacktestCompleted(_message.Message):
    __slots__ = ("backtest_id", "run_id", "candidate_id", "report_uri", "tearsheet_uri", "completed_at", "correlation_id")
    BACKTEST_ID_FIELD_NUMBER: _ClassVar[int]
    RUN_ID_FIELD_NUMBER: _ClassVar[int]
    CANDIDATE_ID_FIELD_NUMBER: _ClassVar[int]
    REPORT_URI_FIELD_NUMBER: _ClassVar[int]
    TEARSHEET_URI_FIELD_NUMBER: _ClassVar[int]
    COMPLETED_AT_FIELD_NUMBER: _ClassVar[int]
    CORRELATION_ID_FIELD_NUMBER: _ClassVar[int]
    backtest_id: str
    run_id: str
    candidate_id: str
    report_uri: str
    tearsheet_uri: str
    completed_at: int
    correlation_id: str
    def __init__(self, backtest_id: _Optional[str] = ..., run_id: _Optional[str] = ..., candidate_id: _Optional[str] = ..., report_uri: _Optional[str] = ..., tearsheet_uri: _Optional[str] = ..., completed_at: _Optional[int] = ..., correlation_id: _Optional[str] = ...) -> None: ...
