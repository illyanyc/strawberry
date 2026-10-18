from google.protobuf import struct_pb2 as _struct_pb2
from tb.ml.v1 import ml_pb2 as _ml_pb2
from tb.ml.v1 import training_pb2 as _training_pb2
from tb.orders.v1 import execution_pb2 as _execution_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class CycleStatus(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    CYCLE_STATUS_UNSPECIFIED: _ClassVar[CycleStatus]
    CYCLE_STATUS_OPEN: _ClassVar[CycleStatus]
    CYCLE_STATUS_SPENT: _ClassVar[CycleStatus]
    CYCLE_STATUS_CLOSED: _ClassVar[CycleStatus]

class StudyStatus(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    STUDY_STATUS_UNSPECIFIED: _ClassVar[StudyStatus]
    STUDY_STATUS_CREATED: _ClassVar[StudyStatus]
    STUDY_STATUS_RUNNING: _ClassVar[StudyStatus]
    STUDY_STATUS_PAUSED: _ClassVar[StudyStatus]
    STUDY_STATUS_COMPLETED: _ClassVar[StudyStatus]
    STUDY_STATUS_STOPPED: _ClassVar[StudyStatus]
    STUDY_STATUS_FAILED: _ClassVar[StudyStatus]

class TrialStatus(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    TRIAL_STATUS_UNSPECIFIED: _ClassVar[TrialStatus]
    TRIAL_STATUS_RUNNING: _ClassVar[TrialStatus]
    TRIAL_STATUS_COMPLETE: _ClassVar[TrialStatus]
    TRIAL_STATUS_PRUNED: _ClassVar[TrialStatus]
    TRIAL_STATUS_FAILED: _ClassVar[TrialStatus]
    TRIAL_STATUS_ABANDONED: _ClassVar[TrialStatus]
CYCLE_STATUS_UNSPECIFIED: CycleStatus
CYCLE_STATUS_OPEN: CycleStatus
CYCLE_STATUS_SPENT: CycleStatus
CYCLE_STATUS_CLOSED: CycleStatus
STUDY_STATUS_UNSPECIFIED: StudyStatus
STUDY_STATUS_CREATED: StudyStatus
STUDY_STATUS_RUNNING: StudyStatus
STUDY_STATUS_PAUSED: StudyStatus
STUDY_STATUS_COMPLETED: StudyStatus
STUDY_STATUS_STOPPED: StudyStatus
STUDY_STATUS_FAILED: StudyStatus
TRIAL_STATUS_UNSPECIFIED: TrialStatus
TRIAL_STATUS_RUNNING: TrialStatus
TRIAL_STATUS_COMPLETE: TrialStatus
TRIAL_STATUS_PRUNED: TrialStatus
TRIAL_STATUS_FAILED: TrialStatus
TRIAL_STATUS_ABANDONED: TrialStatus

class Budget(_message.Message):
    __slots__ = ("max_trials", "max_wall_seconds", "max_cpu_hours", "max_gpu_hours")
    MAX_TRIALS_FIELD_NUMBER: _ClassVar[int]
    MAX_WALL_SECONDS_FIELD_NUMBER: _ClassVar[int]
    MAX_CPU_HOURS_FIELD_NUMBER: _ClassVar[int]
    MAX_GPU_HOURS_FIELD_NUMBER: _ClassVar[int]
    max_trials: int
    max_wall_seconds: int
    max_cpu_hours: float
    max_gpu_hours: float
    def __init__(self, max_trials: _Optional[int] = ..., max_wall_seconds: _Optional[int] = ..., max_cpu_hours: _Optional[float] = ..., max_gpu_hours: _Optional[float] = ...) -> None: ...

class BudgetUsage(_message.Message):
    __slots__ = ("trials", "wall_seconds", "cpu_hours", "gpu_hours")
    TRIALS_FIELD_NUMBER: _ClassVar[int]
    WALL_SECONDS_FIELD_NUMBER: _ClassVar[int]
    CPU_HOURS_FIELD_NUMBER: _ClassVar[int]
    GPU_HOURS_FIELD_NUMBER: _ClassVar[int]
    trials: int
    wall_seconds: int
    cpu_hours: float
    gpu_hours: float
    def __init__(self, trials: _Optional[int] = ..., wall_seconds: _Optional[int] = ..., cpu_hours: _Optional[float] = ..., gpu_hours: _Optional[float] = ...) -> None: ...

class PromotionLimits(_message.Message):
    __slots__ = ("max_promotions_per_window", "window_seconds", "min_shadow_sessions", "cooldown_after_demotion_seconds")
    MAX_PROMOTIONS_PER_WINDOW_FIELD_NUMBER: _ClassVar[int]
    WINDOW_SECONDS_FIELD_NUMBER: _ClassVar[int]
    MIN_SHADOW_SESSIONS_FIELD_NUMBER: _ClassVar[int]
    COOLDOWN_AFTER_DEMOTION_SECONDS_FIELD_NUMBER: _ClassVar[int]
    max_promotions_per_window: int
    window_seconds: int
    min_shadow_sessions: int
    cooldown_after_demotion_seconds: int
    def __init__(self, max_promotions_per_window: _Optional[int] = ..., window_seconds: _Optional[int] = ..., min_shadow_sessions: _Optional[int] = ..., cooldown_after_demotion_seconds: _Optional[int] = ...) -> None: ...

class Lineage(_message.Message):
    __slots__ = ("lineage_id", "name", "family", "timeframe", "universe", "autonomy_level", "analyst_budget", "analyst_used", "promotion_limits", "holdout_boundary", "created_at", "current_cycle_id", "champion_candidate_id")
    LINEAGE_ID_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    FAMILY_FIELD_NUMBER: _ClassVar[int]
    TIMEFRAME_FIELD_NUMBER: _ClassVar[int]
    UNIVERSE_FIELD_NUMBER: _ClassVar[int]
    AUTONOMY_LEVEL_FIELD_NUMBER: _ClassVar[int]
    ANALYST_BUDGET_FIELD_NUMBER: _ClassVar[int]
    ANALYST_USED_FIELD_NUMBER: _ClassVar[int]
    PROMOTION_LIMITS_FIELD_NUMBER: _ClassVar[int]
    HOLDOUT_BOUNDARY_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_FIELD_NUMBER: _ClassVar[int]
    CURRENT_CYCLE_ID_FIELD_NUMBER: _ClassVar[int]
    CHAMPION_CANDIDATE_ID_FIELD_NUMBER: _ClassVar[int]
    lineage_id: str
    name: str
    family: str
    timeframe: str
    universe: str
    autonomy_level: int
    analyst_budget: Budget
    analyst_used: BudgetUsage
    promotion_limits: PromotionLimits
    holdout_boundary: int
    created_at: int
    current_cycle_id: str
    champion_candidate_id: str
    def __init__(self, lineage_id: _Optional[str] = ..., name: _Optional[str] = ..., family: _Optional[str] = ..., timeframe: _Optional[str] = ..., universe: _Optional[str] = ..., autonomy_level: _Optional[int] = ..., analyst_budget: _Optional[_Union[Budget, _Mapping]] = ..., analyst_used: _Optional[_Union[BudgetUsage, _Mapping]] = ..., promotion_limits: _Optional[_Union[PromotionLimits, _Mapping]] = ..., holdout_boundary: _Optional[int] = ..., created_at: _Optional[int] = ..., current_cycle_id: _Optional[str] = ..., champion_candidate_id: _Optional[str] = ...) -> None: ...

class ResearchCycle(_message.Message):
    __slots__ = ("cycle_id", "lineage_id", "opened_at", "opened_by", "effective_trial_budget", "effective_trials_used", "status", "available_years", "target_sharpe")
    CYCLE_ID_FIELD_NUMBER: _ClassVar[int]
    LINEAGE_ID_FIELD_NUMBER: _ClassVar[int]
    OPENED_AT_FIELD_NUMBER: _ClassVar[int]
    OPENED_BY_FIELD_NUMBER: _ClassVar[int]
    EFFECTIVE_TRIAL_BUDGET_FIELD_NUMBER: _ClassVar[int]
    EFFECTIVE_TRIALS_USED_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    AVAILABLE_YEARS_FIELD_NUMBER: _ClassVar[int]
    TARGET_SHARPE_FIELD_NUMBER: _ClassVar[int]
    cycle_id: str
    lineage_id: str
    opened_at: int
    opened_by: _training_pb2.CallerContext
    effective_trial_budget: float
    effective_trials_used: float
    status: CycleStatus
    available_years: float
    target_sharpe: float
    def __init__(self, cycle_id: _Optional[str] = ..., lineage_id: _Optional[str] = ..., opened_at: _Optional[int] = ..., opened_by: _Optional[_Union[_training_pb2.CallerContext, _Mapping]] = ..., effective_trial_budget: _Optional[float] = ..., effective_trials_used: _Optional[float] = ..., status: _Optional[_Union[CycleStatus, str]] = ..., available_years: _Optional[float] = ..., target_sharpe: _Optional[float] = ...) -> None: ...

class PreRegistration(_message.Message):
    __slots__ = ("hypothesis", "metric", "universe", "period", "stop_rule")
    HYPOTHESIS_FIELD_NUMBER: _ClassVar[int]
    METRIC_FIELD_NUMBER: _ClassVar[int]
    UNIVERSE_FIELD_NUMBER: _ClassVar[int]
    PERIOD_FIELD_NUMBER: _ClassVar[int]
    STOP_RULE_FIELD_NUMBER: _ClassVar[int]
    hypothesis: str
    metric: str
    universe: str
    period: _training_pb2.Period
    stop_rule: str
    def __init__(self, hypothesis: _Optional[str] = ..., metric: _Optional[str] = ..., universe: _Optional[str] = ..., period: _Optional[_Union[_training_pb2.Period, _Mapping]] = ..., stop_rule: _Optional[str] = ...) -> None: ...

class ParamSpace(_message.Message):
    __slots__ = ("name", "kind", "low", "high", "choices", "step", "families")
    NAME_FIELD_NUMBER: _ClassVar[int]
    KIND_FIELD_NUMBER: _ClassVar[int]
    LOW_FIELD_NUMBER: _ClassVar[int]
    HIGH_FIELD_NUMBER: _ClassVar[int]
    CHOICES_FIELD_NUMBER: _ClassVar[int]
    STEP_FIELD_NUMBER: _ClassVar[int]
    FAMILIES_FIELD_NUMBER: _ClassVar[int]
    name: str
    kind: str
    low: float
    high: float
    choices: _containers.RepeatedScalarFieldContainer[str]
    step: float
    families: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, name: _Optional[str] = ..., kind: _Optional[str] = ..., low: _Optional[float] = ..., high: _Optional[float] = ..., choices: _Optional[_Iterable[str]] = ..., step: _Optional[float] = ..., families: _Optional[_Iterable[str]] = ...) -> None: ...

class Objective(_message.Message):
    __slots__ = ("metric", "direction", "extra_metrics")
    METRIC_FIELD_NUMBER: _ClassVar[int]
    DIRECTION_FIELD_NUMBER: _ClassVar[int]
    EXTRA_METRICS_FIELD_NUMBER: _ClassVar[int]
    metric: str
    direction: str
    extra_metrics: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, metric: _Optional[str] = ..., direction: _Optional[str] = ..., extra_metrics: _Optional[_Iterable[str]] = ...) -> None: ...

class NestedEvaluation(_message.Message):
    __slots__ = ("outer", "inner", "cost_multiplier", "event_driven_top_k")
    OUTER_FIELD_NUMBER: _ClassVar[int]
    INNER_FIELD_NUMBER: _ClassVar[int]
    COST_MULTIPLIER_FIELD_NUMBER: _ClassVar[int]
    EVENT_DRIVEN_TOP_K_FIELD_NUMBER: _ClassVar[int]
    outer: _training_pb2.SplitterSpec
    inner: _training_pb2.SplitterSpec
    cost_multiplier: float
    event_driven_top_k: int
    def __init__(self, outer: _Optional[_Union[_training_pb2.SplitterSpec, _Mapping]] = ..., inner: _Optional[_Union[_training_pb2.SplitterSpec, _Mapping]] = ..., cost_multiplier: _Optional[float] = ..., event_driven_top_k: _Optional[int] = ...) -> None: ...

class StudySpec(_message.Message):
    __slots__ = ("name", "lineage_id", "families", "search_space", "feature_sets", "label_specs", "timeframe", "universe", "period", "objective", "evaluation", "sampler", "pruner", "budget", "seed", "preregistration", "base_config", "kind")
    NAME_FIELD_NUMBER: _ClassVar[int]
    LINEAGE_ID_FIELD_NUMBER: _ClassVar[int]
    FAMILIES_FIELD_NUMBER: _ClassVar[int]
    SEARCH_SPACE_FIELD_NUMBER: _ClassVar[int]
    FEATURE_SETS_FIELD_NUMBER: _ClassVar[int]
    LABEL_SPECS_FIELD_NUMBER: _ClassVar[int]
    TIMEFRAME_FIELD_NUMBER: _ClassVar[int]
    UNIVERSE_FIELD_NUMBER: _ClassVar[int]
    PERIOD_FIELD_NUMBER: _ClassVar[int]
    OBJECTIVE_FIELD_NUMBER: _ClassVar[int]
    EVALUATION_FIELD_NUMBER: _ClassVar[int]
    SAMPLER_FIELD_NUMBER: _ClassVar[int]
    PRUNER_FIELD_NUMBER: _ClassVar[int]
    BUDGET_FIELD_NUMBER: _ClassVar[int]
    SEED_FIELD_NUMBER: _ClassVar[int]
    PREREGISTRATION_FIELD_NUMBER: _ClassVar[int]
    BASE_CONFIG_FIELD_NUMBER: _ClassVar[int]
    KIND_FIELD_NUMBER: _ClassVar[int]
    name: str
    lineage_id: str
    families: _containers.RepeatedScalarFieldContainer[str]
    search_space: _containers.RepeatedCompositeFieldContainer[ParamSpace]
    feature_sets: _containers.RepeatedCompositeFieldContainer[_training_pb2.FeatureSetRef]
    label_specs: _containers.RepeatedCompositeFieldContainer[_training_pb2.LabelSpec]
    timeframe: str
    universe: _training_pb2.UniverseSpec
    period: _training_pb2.Period
    objective: Objective
    evaluation: NestedEvaluation
    sampler: str
    pruner: str
    budget: Budget
    seed: int
    preregistration: PreRegistration
    base_config: _training_pb2.TrainingConfig
    kind: str
    def __init__(self, name: _Optional[str] = ..., lineage_id: _Optional[str] = ..., families: _Optional[_Iterable[str]] = ..., search_space: _Optional[_Iterable[_Union[ParamSpace, _Mapping]]] = ..., feature_sets: _Optional[_Iterable[_Union[_training_pb2.FeatureSetRef, _Mapping]]] = ..., label_specs: _Optional[_Iterable[_Union[_training_pb2.LabelSpec, _Mapping]]] = ..., timeframe: _Optional[str] = ..., universe: _Optional[_Union[_training_pb2.UniverseSpec, _Mapping]] = ..., period: _Optional[_Union[_training_pb2.Period, _Mapping]] = ..., objective: _Optional[_Union[Objective, _Mapping]] = ..., evaluation: _Optional[_Union[NestedEvaluation, _Mapping]] = ..., sampler: _Optional[str] = ..., pruner: _Optional[str] = ..., budget: _Optional[_Union[Budget, _Mapping]] = ..., seed: _Optional[int] = ..., preregistration: _Optional[_Union[PreRegistration, _Mapping]] = ..., base_config: _Optional[_Union[_training_pb2.TrainingConfig, _Mapping]] = ..., kind: _Optional[str] = ...) -> None: ...

class Trial(_message.Message):
    __slots__ = ("trial_id", "lineage_id", "cycle_id", "study_id", "number", "outer_fold", "spec_hash", "preregistration_hash", "params", "data_snapshot_id", "code_commit", "seed", "source", "status", "inner_metric", "sharpe", "n_obs", "oof_returns_ref", "compute_seconds", "created_at", "run_ids", "dimensions", "analyst_round_id", "config_hash")
    TRIAL_ID_FIELD_NUMBER: _ClassVar[int]
    LINEAGE_ID_FIELD_NUMBER: _ClassVar[int]
    CYCLE_ID_FIELD_NUMBER: _ClassVar[int]
    STUDY_ID_FIELD_NUMBER: _ClassVar[int]
    NUMBER_FIELD_NUMBER: _ClassVar[int]
    OUTER_FOLD_FIELD_NUMBER: _ClassVar[int]
    SPEC_HASH_FIELD_NUMBER: _ClassVar[int]
    PREREGISTRATION_HASH_FIELD_NUMBER: _ClassVar[int]
    PARAMS_FIELD_NUMBER: _ClassVar[int]
    DATA_SNAPSHOT_ID_FIELD_NUMBER: _ClassVar[int]
    CODE_COMMIT_FIELD_NUMBER: _ClassVar[int]
    SEED_FIELD_NUMBER: _ClassVar[int]
    SOURCE_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    INNER_METRIC_FIELD_NUMBER: _ClassVar[int]
    SHARPE_FIELD_NUMBER: _ClassVar[int]
    N_OBS_FIELD_NUMBER: _ClassVar[int]
    OOF_RETURNS_REF_FIELD_NUMBER: _ClassVar[int]
    COMPUTE_SECONDS_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_FIELD_NUMBER: _ClassVar[int]
    RUN_IDS_FIELD_NUMBER: _ClassVar[int]
    DIMENSIONS_FIELD_NUMBER: _ClassVar[int]
    ANALYST_ROUND_ID_FIELD_NUMBER: _ClassVar[int]
    CONFIG_HASH_FIELD_NUMBER: _ClassVar[int]
    trial_id: str
    lineage_id: str
    cycle_id: str
    study_id: str
    number: int
    outer_fold: int
    spec_hash: str
    preregistration_hash: str
    params: _struct_pb2.Struct
    data_snapshot_id: str
    code_commit: str
    seed: int
    source: _training_pb2.Actor
    status: TrialStatus
    inner_metric: float
    sharpe: float
    n_obs: int
    oof_returns_ref: str
    compute_seconds: float
    created_at: int
    run_ids: _containers.RepeatedScalarFieldContainer[str]
    dimensions: _containers.RepeatedScalarFieldContainer[str]
    analyst_round_id: str
    config_hash: str
    def __init__(self, trial_id: _Optional[str] = ..., lineage_id: _Optional[str] = ..., cycle_id: _Optional[str] = ..., study_id: _Optional[str] = ..., number: _Optional[int] = ..., outer_fold: _Optional[int] = ..., spec_hash: _Optional[str] = ..., preregistration_hash: _Optional[str] = ..., params: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ..., data_snapshot_id: _Optional[str] = ..., code_commit: _Optional[str] = ..., seed: _Optional[int] = ..., source: _Optional[_Union[_training_pb2.Actor, str]] = ..., status: _Optional[_Union[TrialStatus, str]] = ..., inner_metric: _Optional[float] = ..., sharpe: _Optional[float] = ..., n_obs: _Optional[int] = ..., oof_returns_ref: _Optional[str] = ..., compute_seconds: _Optional[float] = ..., created_at: _Optional[int] = ..., run_ids: _Optional[_Iterable[str]] = ..., dimensions: _Optional[_Iterable[str]] = ..., analyst_round_id: _Optional[str] = ..., config_hash: _Optional[str] = ...) -> None: ...

class StudyBest(_message.Message):
    __slots__ = ("trial_id", "outer_fold", "inner_metric", "params")
    TRIAL_ID_FIELD_NUMBER: _ClassVar[int]
    OUTER_FOLD_FIELD_NUMBER: _ClassVar[int]
    INNER_METRIC_FIELD_NUMBER: _ClassVar[int]
    PARAMS_FIELD_NUMBER: _ClassVar[int]
    trial_id: str
    outer_fold: int
    inner_metric: float
    params: _struct_pb2.Struct
    def __init__(self, trial_id: _Optional[str] = ..., outer_fold: _Optional[int] = ..., inner_metric: _Optional[float] = ..., params: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ...) -> None: ...

class OuterEstimate(_message.Message):
    __slots__ = ("sharpe", "sharpe_annualized", "ci_low", "ci_high", "net_return", "max_drawdown", "n_obs", "positive_fold_fraction", "trades")
    SHARPE_FIELD_NUMBER: _ClassVar[int]
    SHARPE_ANNUALIZED_FIELD_NUMBER: _ClassVar[int]
    CI_LOW_FIELD_NUMBER: _ClassVar[int]
    CI_HIGH_FIELD_NUMBER: _ClassVar[int]
    NET_RETURN_FIELD_NUMBER: _ClassVar[int]
    MAX_DRAWDOWN_FIELD_NUMBER: _ClassVar[int]
    N_OBS_FIELD_NUMBER: _ClassVar[int]
    POSITIVE_FOLD_FRACTION_FIELD_NUMBER: _ClassVar[int]
    TRADES_FIELD_NUMBER: _ClassVar[int]
    sharpe: float
    sharpe_annualized: float
    ci_low: float
    ci_high: float
    net_return: float
    max_drawdown: float
    n_obs: int
    positive_fold_fraction: float
    trades: int
    def __init__(self, sharpe: _Optional[float] = ..., sharpe_annualized: _Optional[float] = ..., ci_low: _Optional[float] = ..., ci_high: _Optional[float] = ..., net_return: _Optional[float] = ..., max_drawdown: _Optional[float] = ..., n_obs: _Optional[int] = ..., positive_fold_fraction: _Optional[float] = ..., trades: _Optional[int] = ...) -> None: ...

class Study(_message.Message):
    __slots__ = ("study_id", "spec", "spec_hash", "status", "created_at", "started_at", "finished_at", "trials_completed", "trials_pruned", "trials_failed", "trial_count_total", "budget_used", "best_trials", "outer_estimate", "gate_report_id", "candidate_id", "created_by", "cycle_id", "message", "pbo", "deflated_sharpe")
    STUDY_ID_FIELD_NUMBER: _ClassVar[int]
    SPEC_FIELD_NUMBER: _ClassVar[int]
    SPEC_HASH_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_FIELD_NUMBER: _ClassVar[int]
    STARTED_AT_FIELD_NUMBER: _ClassVar[int]
    FINISHED_AT_FIELD_NUMBER: _ClassVar[int]
    TRIALS_COMPLETED_FIELD_NUMBER: _ClassVar[int]
    TRIALS_PRUNED_FIELD_NUMBER: _ClassVar[int]
    TRIALS_FAILED_FIELD_NUMBER: _ClassVar[int]
    TRIAL_COUNT_TOTAL_FIELD_NUMBER: _ClassVar[int]
    BUDGET_USED_FIELD_NUMBER: _ClassVar[int]
    BEST_TRIALS_FIELD_NUMBER: _ClassVar[int]
    OUTER_ESTIMATE_FIELD_NUMBER: _ClassVar[int]
    GATE_REPORT_ID_FIELD_NUMBER: _ClassVar[int]
    CANDIDATE_ID_FIELD_NUMBER: _ClassVar[int]
    CREATED_BY_FIELD_NUMBER: _ClassVar[int]
    CYCLE_ID_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    PBO_FIELD_NUMBER: _ClassVar[int]
    DEFLATED_SHARPE_FIELD_NUMBER: _ClassVar[int]
    study_id: str
    spec: StudySpec
    spec_hash: str
    status: StudyStatus
    created_at: int
    started_at: int
    finished_at: int
    trials_completed: int
    trials_pruned: int
    trials_failed: int
    trial_count_total: int
    budget_used: BudgetUsage
    best_trials: _containers.RepeatedCompositeFieldContainer[StudyBest]
    outer_estimate: OuterEstimate
    gate_report_id: str
    candidate_id: str
    created_by: _training_pb2.CallerContext
    cycle_id: str
    message: str
    pbo: float
    deflated_sharpe: float
    def __init__(self, study_id: _Optional[str] = ..., spec: _Optional[_Union[StudySpec, _Mapping]] = ..., spec_hash: _Optional[str] = ..., status: _Optional[_Union[StudyStatus, str]] = ..., created_at: _Optional[int] = ..., started_at: _Optional[int] = ..., finished_at: _Optional[int] = ..., trials_completed: _Optional[int] = ..., trials_pruned: _Optional[int] = ..., trials_failed: _Optional[int] = ..., trial_count_total: _Optional[int] = ..., budget_used: _Optional[_Union[BudgetUsage, _Mapping]] = ..., best_trials: _Optional[_Iterable[_Union[StudyBest, _Mapping]]] = ..., outer_estimate: _Optional[_Union[OuterEstimate, _Mapping]] = ..., gate_report_id: _Optional[str] = ..., candidate_id: _Optional[str] = ..., created_by: _Optional[_Union[_training_pb2.CallerContext, _Mapping]] = ..., cycle_id: _Optional[str] = ..., message: _Optional[str] = ..., pbo: _Optional[float] = ..., deflated_sharpe: _Optional[float] = ...) -> None: ...

class StudyProgress(_message.Message):
    __slots__ = ("study_id", "seq", "at", "status", "trials_completed", "outer_fold", "best_inner", "message", "trial")
    STUDY_ID_FIELD_NUMBER: _ClassVar[int]
    SEQ_FIELD_NUMBER: _ClassVar[int]
    AT_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    TRIALS_COMPLETED_FIELD_NUMBER: _ClassVar[int]
    OUTER_FOLD_FIELD_NUMBER: _ClassVar[int]
    BEST_INNER_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    TRIAL_FIELD_NUMBER: _ClassVar[int]
    study_id: str
    seq: int
    at: int
    status: StudyStatus
    trials_completed: int
    outer_fold: int
    best_inner: float
    message: str
    trial: Trial
    def __init__(self, study_id: _Optional[str] = ..., seq: _Optional[int] = ..., at: _Optional[int] = ..., status: _Optional[_Union[StudyStatus, str]] = ..., trials_completed: _Optional[int] = ..., outer_fold: _Optional[int] = ..., best_inner: _Optional[float] = ..., message: _Optional[str] = ..., trial: _Optional[_Union[Trial, _Mapping]] = ...) -> None: ...

class TrialLedger(_message.Message):
    __slots__ = ("lineage_id", "trials_total", "by_source", "by_dimension", "by_status", "holdout_evaluations", "cycles")
    class BySourceEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: int
        def __init__(self, key: _Optional[str] = ..., value: _Optional[int] = ...) -> None: ...
    class ByDimensionEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: int
        def __init__(self, key: _Optional[str] = ..., value: _Optional[int] = ...) -> None: ...
    class ByStatusEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: int
        def __init__(self, key: _Optional[str] = ..., value: _Optional[int] = ...) -> None: ...
    LINEAGE_ID_FIELD_NUMBER: _ClassVar[int]
    TRIALS_TOTAL_FIELD_NUMBER: _ClassVar[int]
    BY_SOURCE_FIELD_NUMBER: _ClassVar[int]
    BY_DIMENSION_FIELD_NUMBER: _ClassVar[int]
    BY_STATUS_FIELD_NUMBER: _ClassVar[int]
    HOLDOUT_EVALUATIONS_FIELD_NUMBER: _ClassVar[int]
    CYCLES_FIELD_NUMBER: _ClassVar[int]
    lineage_id: str
    trials_total: int
    by_source: _containers.ScalarMap[str, int]
    by_dimension: _containers.ScalarMap[str, int]
    by_status: _containers.ScalarMap[str, int]
    holdout_evaluations: int
    cycles: _containers.RepeatedCompositeFieldContainer[ResearchCycle]
    def __init__(self, lineage_id: _Optional[str] = ..., trials_total: _Optional[int] = ..., by_source: _Optional[_Mapping[str, int]] = ..., by_dimension: _Optional[_Mapping[str, int]] = ..., by_status: _Optional[_Mapping[str, int]] = ..., holdout_evaluations: _Optional[int] = ..., cycles: _Optional[_Iterable[_Union[ResearchCycle, _Mapping]]] = ...) -> None: ...

class CumulativeOverfittingStats(_message.Message):
    __slots__ = ("lineage_id", "n_trials", "n_effective", "sr_variance", "best_trial_id", "best_sharpe", "deflated_sharpe_prob", "pbo", "min_track_record_length", "min_backtest_length_years", "available_years", "significance_reachable", "dsr_threshold")
    LINEAGE_ID_FIELD_NUMBER: _ClassVar[int]
    N_TRIALS_FIELD_NUMBER: _ClassVar[int]
    N_EFFECTIVE_FIELD_NUMBER: _ClassVar[int]
    SR_VARIANCE_FIELD_NUMBER: _ClassVar[int]
    BEST_TRIAL_ID_FIELD_NUMBER: _ClassVar[int]
    BEST_SHARPE_FIELD_NUMBER: _ClassVar[int]
    DEFLATED_SHARPE_PROB_FIELD_NUMBER: _ClassVar[int]
    PBO_FIELD_NUMBER: _ClassVar[int]
    MIN_TRACK_RECORD_LENGTH_FIELD_NUMBER: _ClassVar[int]
    MIN_BACKTEST_LENGTH_YEARS_FIELD_NUMBER: _ClassVar[int]
    AVAILABLE_YEARS_FIELD_NUMBER: _ClassVar[int]
    SIGNIFICANCE_REACHABLE_FIELD_NUMBER: _ClassVar[int]
    DSR_THRESHOLD_FIELD_NUMBER: _ClassVar[int]
    lineage_id: str
    n_trials: int
    n_effective: float
    sr_variance: float
    best_trial_id: str
    best_sharpe: float
    deflated_sharpe_prob: float
    pbo: float
    min_track_record_length: float
    min_backtest_length_years: float
    available_years: float
    significance_reachable: bool
    dsr_threshold: float
    def __init__(self, lineage_id: _Optional[str] = ..., n_trials: _Optional[int] = ..., n_effective: _Optional[float] = ..., sr_variance: _Optional[float] = ..., best_trial_id: _Optional[str] = ..., best_sharpe: _Optional[float] = ..., deflated_sharpe_prob: _Optional[float] = ..., pbo: _Optional[float] = ..., min_track_record_length: _Optional[float] = ..., min_backtest_length_years: _Optional[float] = ..., available_years: _Optional[float] = ..., significance_reachable: _Optional[bool] = ..., dsr_threshold: _Optional[float] = ...) -> None: ...

class ConvergencePoint(_message.Message):
    __slots__ = ("trial", "best_raw", "best_deflated")
    TRIAL_FIELD_NUMBER: _ClassVar[int]
    BEST_RAW_FIELD_NUMBER: _ClassVar[int]
    BEST_DEFLATED_FIELD_NUMBER: _ClassVar[int]
    trial: int
    best_raw: float
    best_deflated: float
    def __init__(self, trial: _Optional[int] = ..., best_raw: _Optional[float] = ..., best_deflated: _Optional[float] = ...) -> None: ...

class ParamStability(_message.Message):
    __slots__ = ("param", "best_value", "neighbor_mean", "neighbor_std")
    PARAM_FIELD_NUMBER: _ClassVar[int]
    BEST_VALUE_FIELD_NUMBER: _ClassVar[int]
    NEIGHBOR_MEAN_FIELD_NUMBER: _ClassVar[int]
    NEIGHBOR_STD_FIELD_NUMBER: _ClassVar[int]
    param: str
    best_value: float
    neighbor_mean: float
    neighbor_std: float
    def __init__(self, param: _Optional[str] = ..., best_value: _Optional[float] = ..., neighbor_mean: _Optional[float] = ..., neighbor_std: _Optional[float] = ...) -> None: ...

class ParetoPoint(_message.Message):
    __slots__ = ("trial_id", "values")
    class ValuesEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: float
        def __init__(self, key: _Optional[str] = ..., value: _Optional[float] = ...) -> None: ...
    TRIAL_ID_FIELD_NUMBER: _ClassVar[int]
    VALUES_FIELD_NUMBER: _ClassVar[int]
    trial_id: str
    values: _containers.ScalarMap[str, float]
    def __init__(self, trial_id: _Optional[str] = ..., values: _Optional[_Mapping[str, float]] = ...) -> None: ...

class StudyDiagnostics(_message.Message):
    __slots__ = ("study_id", "convergence", "plateau_estimate", "param_importance", "param_stability", "pareto_front", "pruned_rate", "failure_rate")
    class ParamImportanceEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: float
        def __init__(self, key: _Optional[str] = ..., value: _Optional[float] = ...) -> None: ...
    STUDY_ID_FIELD_NUMBER: _ClassVar[int]
    CONVERGENCE_FIELD_NUMBER: _ClassVar[int]
    PLATEAU_ESTIMATE_FIELD_NUMBER: _ClassVar[int]
    PARAM_IMPORTANCE_FIELD_NUMBER: _ClassVar[int]
    PARAM_STABILITY_FIELD_NUMBER: _ClassVar[int]
    PARETO_FRONT_FIELD_NUMBER: _ClassVar[int]
    PRUNED_RATE_FIELD_NUMBER: _ClassVar[int]
    FAILURE_RATE_FIELD_NUMBER: _ClassVar[int]
    study_id: str
    convergence: _containers.RepeatedCompositeFieldContainer[ConvergencePoint]
    plateau_estimate: int
    param_importance: _containers.ScalarMap[str, float]
    param_stability: _containers.RepeatedCompositeFieldContainer[ParamStability]
    pareto_front: _containers.RepeatedCompositeFieldContainer[ParetoPoint]
    pruned_rate: float
    failure_rate: float
    def __init__(self, study_id: _Optional[str] = ..., convergence: _Optional[_Iterable[_Union[ConvergencePoint, _Mapping]]] = ..., plateau_estimate: _Optional[int] = ..., param_importance: _Optional[_Mapping[str, float]] = ..., param_stability: _Optional[_Iterable[_Union[ParamStability, _Mapping]]] = ..., pareto_front: _Optional[_Iterable[_Union[ParetoPoint, _Mapping]]] = ..., pruned_rate: _Optional[float] = ..., failure_rate: _Optional[float] = ...) -> None: ...

class FoldResult(_message.Message):
    __slots__ = ("fold", "test", "sharpe", "net_return", "trades", "max_drawdown")
    FOLD_FIELD_NUMBER: _ClassVar[int]
    TEST_FIELD_NUMBER: _ClassVar[int]
    SHARPE_FIELD_NUMBER: _ClassVar[int]
    NET_RETURN_FIELD_NUMBER: _ClassVar[int]
    TRADES_FIELD_NUMBER: _ClassVar[int]
    MAX_DRAWDOWN_FIELD_NUMBER: _ClassVar[int]
    fold: int
    test: _training_pb2.Period
    sharpe: float
    net_return: float
    trades: int
    max_drawdown: float
    def __init__(self, fold: _Optional[int] = ..., test: _Optional[_Union[_training_pb2.Period, _Mapping]] = ..., sharpe: _Optional[float] = ..., net_return: _Optional[float] = ..., trades: _Optional[int] = ..., max_drawdown: _Optional[float] = ...) -> None: ...

class RegimeResult(_message.Message):
    __slots__ = ("regime", "n", "sharpe", "net_return", "pnl_share")
    REGIME_FIELD_NUMBER: _ClassVar[int]
    N_FIELD_NUMBER: _ClassVar[int]
    SHARPE_FIELD_NUMBER: _ClassVar[int]
    NET_RETURN_FIELD_NUMBER: _ClassVar[int]
    PNL_SHARE_FIELD_NUMBER: _ClassVar[int]
    regime: str
    n: int
    sharpe: float
    net_return: float
    pnl_share: float
    def __init__(self, regime: _Optional[str] = ..., n: _Optional[int] = ..., sharpe: _Optional[float] = ..., net_return: _Optional[float] = ..., pnl_share: _Optional[float] = ...) -> None: ...

class CostSensitivity(_message.Message):
    __slots__ = ("net_return", "sharpe")
    class NetReturnEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: float
        def __init__(self, key: _Optional[str] = ..., value: _Optional[float] = ...) -> None: ...
    class SharpeEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: float
        def __init__(self, key: _Optional[str] = ..., value: _Optional[float] = ...) -> None: ...
    NET_RETURN_FIELD_NUMBER: _ClassVar[int]
    SHARPE_FIELD_NUMBER: _ClassVar[int]
    net_return: _containers.ScalarMap[str, float]
    sharpe: _containers.ScalarMap[str, float]
    def __init__(self, net_return: _Optional[_Mapping[str, float]] = ..., sharpe: _Optional[_Mapping[str, float]] = ...) -> None: ...

class GateReport(_message.Message):
    __slots__ = ("report_id", "candidate_id", "created_at", "passed", "reasons", "outer", "dsr", "n_effective", "pbo", "spa_p", "romano_wolf_p", "walk_forward", "regimes", "cost_sensitivity", "param_stability", "leakage_passed", "survivorship_status", "warnings", "thresholds", "engine", "champion_candidate_id", "deployment_limits")
    class ThresholdsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: float
        def __init__(self, key: _Optional[str] = ..., value: _Optional[float] = ...) -> None: ...
    REPORT_ID_FIELD_NUMBER: _ClassVar[int]
    CANDIDATE_ID_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_FIELD_NUMBER: _ClassVar[int]
    PASSED_FIELD_NUMBER: _ClassVar[int]
    REASONS_FIELD_NUMBER: _ClassVar[int]
    OUTER_FIELD_NUMBER: _ClassVar[int]
    DSR_FIELD_NUMBER: _ClassVar[int]
    N_EFFECTIVE_FIELD_NUMBER: _ClassVar[int]
    PBO_FIELD_NUMBER: _ClassVar[int]
    SPA_P_FIELD_NUMBER: _ClassVar[int]
    ROMANO_WOLF_P_FIELD_NUMBER: _ClassVar[int]
    WALK_FORWARD_FIELD_NUMBER: _ClassVar[int]
    REGIMES_FIELD_NUMBER: _ClassVar[int]
    COST_SENSITIVITY_FIELD_NUMBER: _ClassVar[int]
    PARAM_STABILITY_FIELD_NUMBER: _ClassVar[int]
    LEAKAGE_PASSED_FIELD_NUMBER: _ClassVar[int]
    SURVIVORSHIP_STATUS_FIELD_NUMBER: _ClassVar[int]
    WARNINGS_FIELD_NUMBER: _ClassVar[int]
    THRESHOLDS_FIELD_NUMBER: _ClassVar[int]
    ENGINE_FIELD_NUMBER: _ClassVar[int]
    CHAMPION_CANDIDATE_ID_FIELD_NUMBER: _ClassVar[int]
    DEPLOYMENT_LIMITS_FIELD_NUMBER: _ClassVar[int]
    report_id: str
    candidate_id: str
    created_at: int
    passed: bool
    reasons: _containers.RepeatedScalarFieldContainer[str]
    outer: OuterEstimate
    dsr: float
    n_effective: float
    pbo: float
    spa_p: float
    romano_wolf_p: float
    walk_forward: _containers.RepeatedCompositeFieldContainer[FoldResult]
    regimes: _containers.RepeatedCompositeFieldContainer[RegimeResult]
    cost_sensitivity: CostSensitivity
    param_stability: _containers.RepeatedCompositeFieldContainer[ParamStability]
    leakage_passed: bool
    survivorship_status: str
    warnings: _containers.RepeatedScalarFieldContainer[str]
    thresholds: _containers.ScalarMap[str, float]
    engine: str
    champion_candidate_id: str
    deployment_limits: DeploymentLimits
    def __init__(self, report_id: _Optional[str] = ..., candidate_id: _Optional[str] = ..., created_at: _Optional[int] = ..., passed: _Optional[bool] = ..., reasons: _Optional[_Iterable[str]] = ..., outer: _Optional[_Union[OuterEstimate, _Mapping]] = ..., dsr: _Optional[float] = ..., n_effective: _Optional[float] = ..., pbo: _Optional[float] = ..., spa_p: _Optional[float] = ..., romano_wolf_p: _Optional[float] = ..., walk_forward: _Optional[_Iterable[_Union[FoldResult, _Mapping]]] = ..., regimes: _Optional[_Iterable[_Union[RegimeResult, _Mapping]]] = ..., cost_sensitivity: _Optional[_Union[CostSensitivity, _Mapping]] = ..., param_stability: _Optional[_Iterable[_Union[ParamStability, _Mapping]]] = ..., leakage_passed: _Optional[bool] = ..., survivorship_status: _Optional[str] = ..., warnings: _Optional[_Iterable[str]] = ..., thresholds: _Optional[_Mapping[str, float]] = ..., engine: _Optional[str] = ..., champion_candidate_id: _Optional[str] = ..., deployment_limits: _Optional[_Union[DeploymentLimits, _Mapping]] = ...) -> None: ...

class Candidate(_message.Message):
    __slots__ = ("candidate_id", "lineage_id", "study_id", "run_id", "model_version", "config", "stage", "created_at", "gate_report_id", "synthetic_data", "registry_entry_json", "strategy_id", "stage_changed_at", "shadow_sessions", "demoted", "origin")
    CANDIDATE_ID_FIELD_NUMBER: _ClassVar[int]
    LINEAGE_ID_FIELD_NUMBER: _ClassVar[int]
    STUDY_ID_FIELD_NUMBER: _ClassVar[int]
    RUN_ID_FIELD_NUMBER: _ClassVar[int]
    MODEL_VERSION_FIELD_NUMBER: _ClassVar[int]
    CONFIG_FIELD_NUMBER: _ClassVar[int]
    STAGE_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_FIELD_NUMBER: _ClassVar[int]
    GATE_REPORT_ID_FIELD_NUMBER: _ClassVar[int]
    SYNTHETIC_DATA_FIELD_NUMBER: _ClassVar[int]
    REGISTRY_ENTRY_JSON_FIELD_NUMBER: _ClassVar[int]
    STRATEGY_ID_FIELD_NUMBER: _ClassVar[int]
    STAGE_CHANGED_AT_FIELD_NUMBER: _ClassVar[int]
    SHADOW_SESSIONS_FIELD_NUMBER: _ClassVar[int]
    DEMOTED_FIELD_NUMBER: _ClassVar[int]
    ORIGIN_FIELD_NUMBER: _ClassVar[int]
    candidate_id: str
    lineage_id: str
    study_id: str
    run_id: str
    model_version: _ml_pb2.ModelVersion
    config: _training_pb2.TrainingConfig
    stage: _ml_pb2.RegistryStage
    created_at: int
    gate_report_id: str
    synthetic_data: bool
    registry_entry_json: str
    strategy_id: str
    stage_changed_at: int
    shadow_sessions: int
    demoted: bool
    origin: str
    def __init__(self, candidate_id: _Optional[str] = ..., lineage_id: _Optional[str] = ..., study_id: _Optional[str] = ..., run_id: _Optional[str] = ..., model_version: _Optional[_Union[_ml_pb2.ModelVersion, _Mapping]] = ..., config: _Optional[_Union[_training_pb2.TrainingConfig, _Mapping]] = ..., stage: _Optional[_Union[_ml_pb2.RegistryStage, str]] = ..., created_at: _Optional[int] = ..., gate_report_id: _Optional[str] = ..., synthetic_data: _Optional[bool] = ..., registry_entry_json: _Optional[str] = ..., strategy_id: _Optional[str] = ..., stage_changed_at: _Optional[int] = ..., shadow_sessions: _Optional[int] = ..., demoted: _Optional[bool] = ..., origin: _Optional[str] = ...) -> None: ...

class PermissionDecision(_message.Message):
    __slots__ = ("allowed", "reason", "requires_step_up", "failed_gates")
    ALLOWED_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    REQUIRES_STEP_UP_FIELD_NUMBER: _ClassVar[int]
    FAILED_GATES_FIELD_NUMBER: _ClassVar[int]
    allowed: bool
    reason: str
    requires_step_up: bool
    failed_gates: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, allowed: _Optional[bool] = ..., reason: _Optional[str] = ..., requires_step_up: _Optional[bool] = ..., failed_gates: _Optional[_Iterable[str]] = ...) -> None: ...

class EvidencePack(_message.Message):
    __slots__ = ("candidate_id", "markdown", "html", "created_at")
    CANDIDATE_ID_FIELD_NUMBER: _ClassVar[int]
    MARKDOWN_FIELD_NUMBER: _ClassVar[int]
    HTML_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_FIELD_NUMBER: _ClassVar[int]
    candidate_id: str
    markdown: str
    html: str
    created_at: int
    def __init__(self, candidate_id: _Optional[str] = ..., markdown: _Optional[str] = ..., html: _Optional[str] = ..., created_at: _Optional[int] = ...) -> None: ...

class GraphNode(_message.Message):
    __slots__ = ("id", "kind", "label")
    ID_FIELD_NUMBER: _ClassVar[int]
    KIND_FIELD_NUMBER: _ClassVar[int]
    LABEL_FIELD_NUMBER: _ClassVar[int]
    id: str
    kind: str
    label: str
    def __init__(self, id: _Optional[str] = ..., kind: _Optional[str] = ..., label: _Optional[str] = ...) -> None: ...

class GraphEdge(_message.Message):
    __slots__ = ("to", "kind")
    FROM_FIELD_NUMBER: _ClassVar[int]
    TO_FIELD_NUMBER: _ClassVar[int]
    KIND_FIELD_NUMBER: _ClassVar[int]
    to: str
    kind: str
    def __init__(self, to: _Optional[str] = ..., kind: _Optional[str] = ..., **kwargs) -> None: ...

class LineageGraph(_message.Message):
    __slots__ = ("nodes", "edges")
    NODES_FIELD_NUMBER: _ClassVar[int]
    EDGES_FIELD_NUMBER: _ClassVar[int]
    nodes: _containers.RepeatedCompositeFieldContainer[GraphNode]
    edges: _containers.RepeatedCompositeFieldContainer[GraphEdge]
    def __init__(self, nodes: _Optional[_Iterable[_Union[GraphNode, _Mapping]]] = ..., edges: _Optional[_Iterable[_Union[GraphEdge, _Mapping]]] = ...) -> None: ...

class RetrainingPolicy(_message.Message):
    __slots__ = ("lineage_id", "strategy_id", "triggers", "cadence_seconds", "psi_threshold", "ic_floor", "enabled", "extend_seconds")
    LINEAGE_ID_FIELD_NUMBER: _ClassVar[int]
    STRATEGY_ID_FIELD_NUMBER: _ClassVar[int]
    TRIGGERS_FIELD_NUMBER: _ClassVar[int]
    CADENCE_SECONDS_FIELD_NUMBER: _ClassVar[int]
    PSI_THRESHOLD_FIELD_NUMBER: _ClassVar[int]
    IC_FLOOR_FIELD_NUMBER: _ClassVar[int]
    ENABLED_FIELD_NUMBER: _ClassVar[int]
    EXTEND_SECONDS_FIELD_NUMBER: _ClassVar[int]
    lineage_id: str
    strategy_id: str
    triggers: _containers.RepeatedScalarFieldContainer[str]
    cadence_seconds: int
    psi_threshold: float
    ic_floor: float
    enabled: bool
    extend_seconds: int
    def __init__(self, lineage_id: _Optional[str] = ..., strategy_id: _Optional[str] = ..., triggers: _Optional[_Iterable[str]] = ..., cadence_seconds: _Optional[int] = ..., psi_threshold: _Optional[float] = ..., ic_floor: _Optional[float] = ..., enabled: _Optional[bool] = ..., extend_seconds: _Optional[int] = ...) -> None: ...

class StudyCompleted(_message.Message):
    __slots__ = ("study_id", "lineage_id", "status", "candidate_id", "gate_report_id", "completed_at", "launched_by", "analyst_round_id", "correlation_id")
    STUDY_ID_FIELD_NUMBER: _ClassVar[int]
    LINEAGE_ID_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    CANDIDATE_ID_FIELD_NUMBER: _ClassVar[int]
    GATE_REPORT_ID_FIELD_NUMBER: _ClassVar[int]
    COMPLETED_AT_FIELD_NUMBER: _ClassVar[int]
    LAUNCHED_BY_FIELD_NUMBER: _ClassVar[int]
    ANALYST_ROUND_ID_FIELD_NUMBER: _ClassVar[int]
    CORRELATION_ID_FIELD_NUMBER: _ClassVar[int]
    study_id: str
    lineage_id: str
    status: StudyStatus
    candidate_id: str
    gate_report_id: str
    completed_at: int
    launched_by: _training_pb2.Actor
    analyst_round_id: str
    correlation_id: str
    def __init__(self, study_id: _Optional[str] = ..., lineage_id: _Optional[str] = ..., status: _Optional[_Union[StudyStatus, str]] = ..., candidate_id: _Optional[str] = ..., gate_report_id: _Optional[str] = ..., completed_at: _Optional[int] = ..., launched_by: _Optional[_Union[_training_pb2.Actor, str]] = ..., analyst_round_id: _Optional[str] = ..., correlation_id: _Optional[str] = ...) -> None: ...

class StageChanged(_message.Message):
    __slots__ = ("candidate_id", "lineage_id", "from_stage", "to_stage", "caller", "changed_at", "reason")
    CANDIDATE_ID_FIELD_NUMBER: _ClassVar[int]
    LINEAGE_ID_FIELD_NUMBER: _ClassVar[int]
    FROM_STAGE_FIELD_NUMBER: _ClassVar[int]
    TO_STAGE_FIELD_NUMBER: _ClassVar[int]
    CALLER_FIELD_NUMBER: _ClassVar[int]
    CHANGED_AT_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    candidate_id: str
    lineage_id: str
    from_stage: _ml_pb2.RegistryStage
    to_stage: _ml_pb2.RegistryStage
    caller: _training_pb2.CallerContext
    changed_at: int
    reason: str
    def __init__(self, candidate_id: _Optional[str] = ..., lineage_id: _Optional[str] = ..., from_stage: _Optional[_Union[_ml_pb2.RegistryStage, str]] = ..., to_stage: _Optional[_Union[_ml_pb2.RegistryStage, str]] = ..., caller: _Optional[_Union[_training_pb2.CallerContext, _Mapping]] = ..., changed_at: _Optional[int] = ..., reason: _Optional[str] = ...) -> None: ...

class CreateLineageRequest(_message.Message):
    __slots__ = ("lineage", "caller")
    LINEAGE_FIELD_NUMBER: _ClassVar[int]
    CALLER_FIELD_NUMBER: _ClassVar[int]
    lineage: Lineage
    caller: _training_pb2.CallerContext
    def __init__(self, lineage: _Optional[_Union[Lineage, _Mapping]] = ..., caller: _Optional[_Union[_training_pb2.CallerContext, _Mapping]] = ...) -> None: ...

class CreateLineageResponse(_message.Message):
    __slots__ = ("lineage",)
    LINEAGE_FIELD_NUMBER: _ClassVar[int]
    lineage: Lineage
    def __init__(self, lineage: _Optional[_Union[Lineage, _Mapping]] = ...) -> None: ...

class GetLineageRequest(_message.Message):
    __slots__ = ("lineage_id",)
    LINEAGE_ID_FIELD_NUMBER: _ClassVar[int]
    lineage_id: str
    def __init__(self, lineage_id: _Optional[str] = ...) -> None: ...

class GetLineageResponse(_message.Message):
    __slots__ = ("lineage",)
    LINEAGE_FIELD_NUMBER: _ClassVar[int]
    lineage: Lineage
    def __init__(self, lineage: _Optional[_Union[Lineage, _Mapping]] = ...) -> None: ...

class ListLineagesRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class ListLineagesResponse(_message.Message):
    __slots__ = ("lineages",)
    LINEAGES_FIELD_NUMBER: _ClassVar[int]
    lineages: _containers.RepeatedCompositeFieldContainer[Lineage]
    def __init__(self, lineages: _Optional[_Iterable[_Union[Lineage, _Mapping]]] = ...) -> None: ...

class SetLineageAutonomyRequest(_message.Message):
    __slots__ = ("lineage_id", "level", "analyst_budget", "promotion_limits", "caller")
    LINEAGE_ID_FIELD_NUMBER: _ClassVar[int]
    LEVEL_FIELD_NUMBER: _ClassVar[int]
    ANALYST_BUDGET_FIELD_NUMBER: _ClassVar[int]
    PROMOTION_LIMITS_FIELD_NUMBER: _ClassVar[int]
    CALLER_FIELD_NUMBER: _ClassVar[int]
    lineage_id: str
    level: int
    analyst_budget: Budget
    promotion_limits: PromotionLimits
    caller: _training_pb2.CallerContext
    def __init__(self, lineage_id: _Optional[str] = ..., level: _Optional[int] = ..., analyst_budget: _Optional[_Union[Budget, _Mapping]] = ..., promotion_limits: _Optional[_Union[PromotionLimits, _Mapping]] = ..., caller: _Optional[_Union[_training_pb2.CallerContext, _Mapping]] = ...) -> None: ...

class SetLineageAutonomyResponse(_message.Message):
    __slots__ = ("lineage",)
    LINEAGE_FIELD_NUMBER: _ClassVar[int]
    lineage: Lineage
    def __init__(self, lineage: _Optional[_Union[Lineage, _Mapping]] = ...) -> None: ...

class OpenCycleRequest(_message.Message):
    __slots__ = ("lineage_id", "target_sharpe", "caller")
    LINEAGE_ID_FIELD_NUMBER: _ClassVar[int]
    TARGET_SHARPE_FIELD_NUMBER: _ClassVar[int]
    CALLER_FIELD_NUMBER: _ClassVar[int]
    lineage_id: str
    target_sharpe: float
    caller: _training_pb2.CallerContext
    def __init__(self, lineage_id: _Optional[str] = ..., target_sharpe: _Optional[float] = ..., caller: _Optional[_Union[_training_pb2.CallerContext, _Mapping]] = ...) -> None: ...

class OpenCycleResponse(_message.Message):
    __slots__ = ("cycle",)
    CYCLE_FIELD_NUMBER: _ClassVar[int]
    cycle: ResearchCycle
    def __init__(self, cycle: _Optional[_Union[ResearchCycle, _Mapping]] = ...) -> None: ...

class CreateStudyRequest(_message.Message):
    __slots__ = ("spec", "caller")
    SPEC_FIELD_NUMBER: _ClassVar[int]
    CALLER_FIELD_NUMBER: _ClassVar[int]
    spec: StudySpec
    caller: _training_pb2.CallerContext
    def __init__(self, spec: _Optional[_Union[StudySpec, _Mapping]] = ..., caller: _Optional[_Union[_training_pb2.CallerContext, _Mapping]] = ...) -> None: ...

class CreateStudyResponse(_message.Message):
    __slots__ = ("study",)
    STUDY_FIELD_NUMBER: _ClassVar[int]
    study: Study
    def __init__(self, study: _Optional[_Union[Study, _Mapping]] = ...) -> None: ...

class StartStudyRequest(_message.Message):
    __slots__ = ("study_id", "budget", "caller")
    STUDY_ID_FIELD_NUMBER: _ClassVar[int]
    BUDGET_FIELD_NUMBER: _ClassVar[int]
    CALLER_FIELD_NUMBER: _ClassVar[int]
    study_id: str
    budget: Budget
    caller: _training_pb2.CallerContext
    def __init__(self, study_id: _Optional[str] = ..., budget: _Optional[_Union[Budget, _Mapping]] = ..., caller: _Optional[_Union[_training_pb2.CallerContext, _Mapping]] = ...) -> None: ...

class StartStudyResponse(_message.Message):
    __slots__ = ("study",)
    STUDY_FIELD_NUMBER: _ClassVar[int]
    study: Study
    def __init__(self, study: _Optional[_Union[Study, _Mapping]] = ...) -> None: ...

class PauseStudyRequest(_message.Message):
    __slots__ = ("study_id", "caller")
    STUDY_ID_FIELD_NUMBER: _ClassVar[int]
    CALLER_FIELD_NUMBER: _ClassVar[int]
    study_id: str
    caller: _training_pb2.CallerContext
    def __init__(self, study_id: _Optional[str] = ..., caller: _Optional[_Union[_training_pb2.CallerContext, _Mapping]] = ...) -> None: ...

class PauseStudyResponse(_message.Message):
    __slots__ = ("study",)
    STUDY_FIELD_NUMBER: _ClassVar[int]
    study: Study
    def __init__(self, study: _Optional[_Union[Study, _Mapping]] = ...) -> None: ...

class ResumeStudyRequest(_message.Message):
    __slots__ = ("study_id", "caller")
    STUDY_ID_FIELD_NUMBER: _ClassVar[int]
    CALLER_FIELD_NUMBER: _ClassVar[int]
    study_id: str
    caller: _training_pb2.CallerContext
    def __init__(self, study_id: _Optional[str] = ..., caller: _Optional[_Union[_training_pb2.CallerContext, _Mapping]] = ...) -> None: ...

class ResumeStudyResponse(_message.Message):
    __slots__ = ("study",)
    STUDY_FIELD_NUMBER: _ClassVar[int]
    study: Study
    def __init__(self, study: _Optional[_Union[Study, _Mapping]] = ...) -> None: ...

class StopStudyRequest(_message.Message):
    __slots__ = ("study_id", "caller")
    STUDY_ID_FIELD_NUMBER: _ClassVar[int]
    CALLER_FIELD_NUMBER: _ClassVar[int]
    study_id: str
    caller: _training_pb2.CallerContext
    def __init__(self, study_id: _Optional[str] = ..., caller: _Optional[_Union[_training_pb2.CallerContext, _Mapping]] = ...) -> None: ...

class StopStudyResponse(_message.Message):
    __slots__ = ("study",)
    STUDY_FIELD_NUMBER: _ClassVar[int]
    study: Study
    def __init__(self, study: _Optional[_Union[Study, _Mapping]] = ...) -> None: ...

class GetStudyRequest(_message.Message):
    __slots__ = ("study_id",)
    STUDY_ID_FIELD_NUMBER: _ClassVar[int]
    study_id: str
    def __init__(self, study_id: _Optional[str] = ...) -> None: ...

class GetStudyResponse(_message.Message):
    __slots__ = ("study",)
    STUDY_FIELD_NUMBER: _ClassVar[int]
    study: Study
    def __init__(self, study: _Optional[_Union[Study, _Mapping]] = ...) -> None: ...

class ListStudiesRequest(_message.Message):
    __slots__ = ("lineage_id", "limit")
    LINEAGE_ID_FIELD_NUMBER: _ClassVar[int]
    LIMIT_FIELD_NUMBER: _ClassVar[int]
    lineage_id: str
    limit: int
    def __init__(self, lineage_id: _Optional[str] = ..., limit: _Optional[int] = ...) -> None: ...

class ListStudiesResponse(_message.Message):
    __slots__ = ("studies",)
    STUDIES_FIELD_NUMBER: _ClassVar[int]
    studies: _containers.RepeatedCompositeFieldContainer[Study]
    def __init__(self, studies: _Optional[_Iterable[_Union[Study, _Mapping]]] = ...) -> None: ...

class StreamStudyProgressRequest(_message.Message):
    __slots__ = ("study_id", "after_seq")
    STUDY_ID_FIELD_NUMBER: _ClassVar[int]
    AFTER_SEQ_FIELD_NUMBER: _ClassVar[int]
    study_id: str
    after_seq: int
    def __init__(self, study_id: _Optional[str] = ..., after_seq: _Optional[int] = ...) -> None: ...

class StreamStudyProgressResponse(_message.Message):
    __slots__ = ("progress",)
    PROGRESS_FIELD_NUMBER: _ClassVar[int]
    progress: StudyProgress
    def __init__(self, progress: _Optional[_Union[StudyProgress, _Mapping]] = ...) -> None: ...

class ListTrialsRequest(_message.Message):
    __slots__ = ("study_id", "lineage_id", "limit")
    STUDY_ID_FIELD_NUMBER: _ClassVar[int]
    LINEAGE_ID_FIELD_NUMBER: _ClassVar[int]
    LIMIT_FIELD_NUMBER: _ClassVar[int]
    study_id: str
    lineage_id: str
    limit: int
    def __init__(self, study_id: _Optional[str] = ..., lineage_id: _Optional[str] = ..., limit: _Optional[int] = ...) -> None: ...

class ListTrialsResponse(_message.Message):
    __slots__ = ("trials",)
    TRIALS_FIELD_NUMBER: _ClassVar[int]
    trials: _containers.RepeatedCompositeFieldContainer[Trial]
    def __init__(self, trials: _Optional[_Iterable[_Union[Trial, _Mapping]]] = ...) -> None: ...

class RecordTrialRequest(_message.Message):
    __slots__ = ("trial", "caller")
    TRIAL_FIELD_NUMBER: _ClassVar[int]
    CALLER_FIELD_NUMBER: _ClassVar[int]
    trial: Trial
    caller: _training_pb2.CallerContext
    def __init__(self, trial: _Optional[_Union[Trial, _Mapping]] = ..., caller: _Optional[_Union[_training_pb2.CallerContext, _Mapping]] = ...) -> None: ...

class RecordTrialResponse(_message.Message):
    __slots__ = ("trial",)
    TRIAL_FIELD_NUMBER: _ClassVar[int]
    trial: Trial
    def __init__(self, trial: _Optional[_Union[Trial, _Mapping]] = ...) -> None: ...

class GetStudyDiagnosticsRequest(_message.Message):
    __slots__ = ("study_id", "caller")
    STUDY_ID_FIELD_NUMBER: _ClassVar[int]
    CALLER_FIELD_NUMBER: _ClassVar[int]
    study_id: str
    caller: _training_pb2.CallerContext
    def __init__(self, study_id: _Optional[str] = ..., caller: _Optional[_Union[_training_pb2.CallerContext, _Mapping]] = ...) -> None: ...

class GetStudyDiagnosticsResponse(_message.Message):
    __slots__ = ("diagnostics",)
    DIAGNOSTICS_FIELD_NUMBER: _ClassVar[int]
    diagnostics: StudyDiagnostics
    def __init__(self, diagnostics: _Optional[_Union[StudyDiagnostics, _Mapping]] = ...) -> None: ...

class GetTrialLedgerRequest(_message.Message):
    __slots__ = ("lineage_id", "caller")
    LINEAGE_ID_FIELD_NUMBER: _ClassVar[int]
    CALLER_FIELD_NUMBER: _ClassVar[int]
    lineage_id: str
    caller: _training_pb2.CallerContext
    def __init__(self, lineage_id: _Optional[str] = ..., caller: _Optional[_Union[_training_pb2.CallerContext, _Mapping]] = ...) -> None: ...

class GetTrialLedgerResponse(_message.Message):
    __slots__ = ("ledger",)
    LEDGER_FIELD_NUMBER: _ClassVar[int]
    ledger: TrialLedger
    def __init__(self, ledger: _Optional[_Union[TrialLedger, _Mapping]] = ...) -> None: ...

class GetCumulativeOverfittingStatsRequest(_message.Message):
    __slots__ = ("lineage_id", "caller")
    LINEAGE_ID_FIELD_NUMBER: _ClassVar[int]
    CALLER_FIELD_NUMBER: _ClassVar[int]
    lineage_id: str
    caller: _training_pb2.CallerContext
    def __init__(self, lineage_id: _Optional[str] = ..., caller: _Optional[_Union[_training_pb2.CallerContext, _Mapping]] = ...) -> None: ...

class GetCumulativeOverfittingStatsResponse(_message.Message):
    __slots__ = ("stats",)
    STATS_FIELD_NUMBER: _ClassVar[int]
    stats: CumulativeOverfittingStats
    def __init__(self, stats: _Optional[_Union[CumulativeOverfittingStats, _Mapping]] = ...) -> None: ...

class GetGateReportRequest(_message.Message):
    __slots__ = ("candidate_id", "caller")
    CANDIDATE_ID_FIELD_NUMBER: _ClassVar[int]
    CALLER_FIELD_NUMBER: _ClassVar[int]
    candidate_id: str
    caller: _training_pb2.CallerContext
    def __init__(self, candidate_id: _Optional[str] = ..., caller: _Optional[_Union[_training_pb2.CallerContext, _Mapping]] = ...) -> None: ...

class GetGateReportResponse(_message.Message):
    __slots__ = ("report",)
    REPORT_FIELD_NUMBER: _ClassVar[int]
    report: GateReport
    def __init__(self, report: _Optional[_Union[GateReport, _Mapping]] = ...) -> None: ...

class GetWalkForwardBreakdownRequest(_message.Message):
    __slots__ = ("candidate_id", "caller")
    CANDIDATE_ID_FIELD_NUMBER: _ClassVar[int]
    CALLER_FIELD_NUMBER: _ClassVar[int]
    candidate_id: str
    caller: _training_pb2.CallerContext
    def __init__(self, candidate_id: _Optional[str] = ..., caller: _Optional[_Union[_training_pb2.CallerContext, _Mapping]] = ...) -> None: ...

class GetWalkForwardBreakdownResponse(_message.Message):
    __slots__ = ("folds",)
    FOLDS_FIELD_NUMBER: _ClassVar[int]
    folds: _containers.RepeatedCompositeFieldContainer[FoldResult]
    def __init__(self, folds: _Optional[_Iterable[_Union[FoldResult, _Mapping]]] = ...) -> None: ...

class GetRegimeBreakdownRequest(_message.Message):
    __slots__ = ("candidate_id", "caller")
    CANDIDATE_ID_FIELD_NUMBER: _ClassVar[int]
    CALLER_FIELD_NUMBER: _ClassVar[int]
    candidate_id: str
    caller: _training_pb2.CallerContext
    def __init__(self, candidate_id: _Optional[str] = ..., caller: _Optional[_Union[_training_pb2.CallerContext, _Mapping]] = ...) -> None: ...

class GetRegimeBreakdownResponse(_message.Message):
    __slots__ = ("regimes",)
    REGIMES_FIELD_NUMBER: _ClassVar[int]
    regimes: _containers.RepeatedCompositeFieldContainer[RegimeResult]
    def __init__(self, regimes: _Optional[_Iterable[_Union[RegimeResult, _Mapping]]] = ...) -> None: ...

class GetCostSensitivityRequest(_message.Message):
    __slots__ = ("candidate_id", "caller")
    CANDIDATE_ID_FIELD_NUMBER: _ClassVar[int]
    CALLER_FIELD_NUMBER: _ClassVar[int]
    candidate_id: str
    caller: _training_pb2.CallerContext
    def __init__(self, candidate_id: _Optional[str] = ..., caller: _Optional[_Union[_training_pb2.CallerContext, _Mapping]] = ...) -> None: ...

class GetCostSensitivityResponse(_message.Message):
    __slots__ = ("sensitivity",)
    SENSITIVITY_FIELD_NUMBER: _ClassVar[int]
    sensitivity: CostSensitivity
    def __init__(self, sensitivity: _Optional[_Union[CostSensitivity, _Mapping]] = ...) -> None: ...

class ListCandidatesRequest(_message.Message):
    __slots__ = ("lineage_id", "stage")
    LINEAGE_ID_FIELD_NUMBER: _ClassVar[int]
    STAGE_FIELD_NUMBER: _ClassVar[int]
    lineage_id: str
    stage: _ml_pb2.RegistryStage
    def __init__(self, lineage_id: _Optional[str] = ..., stage: _Optional[_Union[_ml_pb2.RegistryStage, str]] = ...) -> None: ...

class ListCandidatesResponse(_message.Message):
    __slots__ = ("candidates",)
    CANDIDATES_FIELD_NUMBER: _ClassVar[int]
    candidates: _containers.RepeatedCompositeFieldContainer[Candidate]
    def __init__(self, candidates: _Optional[_Iterable[_Union[Candidate, _Mapping]]] = ...) -> None: ...

class GetCandidateRequest(_message.Message):
    __slots__ = ("candidate_id",)
    CANDIDATE_ID_FIELD_NUMBER: _ClassVar[int]
    candidate_id: str
    def __init__(self, candidate_id: _Optional[str] = ...) -> None: ...

class GetCandidateResponse(_message.Message):
    __slots__ = ("candidate",)
    CANDIDATE_FIELD_NUMBER: _ClassVar[int]
    candidate: Candidate
    def __init__(self, candidate: _Optional[_Union[Candidate, _Mapping]] = ...) -> None: ...

class CheckTransitionRequest(_message.Message):
    __slots__ = ("candidate_id", "to_stage", "caller")
    CANDIDATE_ID_FIELD_NUMBER: _ClassVar[int]
    TO_STAGE_FIELD_NUMBER: _ClassVar[int]
    CALLER_FIELD_NUMBER: _ClassVar[int]
    candidate_id: str
    to_stage: _ml_pb2.RegistryStage
    caller: _training_pb2.CallerContext
    def __init__(self, candidate_id: _Optional[str] = ..., to_stage: _Optional[_Union[_ml_pb2.RegistryStage, str]] = ..., caller: _Optional[_Union[_training_pb2.CallerContext, _Mapping]] = ...) -> None: ...

class CheckTransitionResponse(_message.Message):
    __slots__ = ("decision",)
    DECISION_FIELD_NUMBER: _ClassVar[int]
    decision: PermissionDecision
    def __init__(self, decision: _Optional[_Union[PermissionDecision, _Mapping]] = ...) -> None: ...

class PromoteCandidateRequest(_message.Message):
    __slots__ = ("candidate_id", "to_stage", "caller")
    CANDIDATE_ID_FIELD_NUMBER: _ClassVar[int]
    TO_STAGE_FIELD_NUMBER: _ClassVar[int]
    CALLER_FIELD_NUMBER: _ClassVar[int]
    candidate_id: str
    to_stage: _ml_pb2.RegistryStage
    caller: _training_pb2.CallerContext
    def __init__(self, candidate_id: _Optional[str] = ..., to_stage: _Optional[_Union[_ml_pb2.RegistryStage, str]] = ..., caller: _Optional[_Union[_training_pb2.CallerContext, _Mapping]] = ...) -> None: ...

class PromoteCandidateResponse(_message.Message):
    __slots__ = ("candidate", "decision")
    CANDIDATE_FIELD_NUMBER: _ClassVar[int]
    DECISION_FIELD_NUMBER: _ClassVar[int]
    candidate: Candidate
    decision: PermissionDecision
    def __init__(self, candidate: _Optional[_Union[Candidate, _Mapping]] = ..., decision: _Optional[_Union[PermissionDecision, _Mapping]] = ...) -> None: ...

class DeployChallengerShadowRequest(_message.Message):
    __slots__ = ("candidate_id", "caller")
    CANDIDATE_ID_FIELD_NUMBER: _ClassVar[int]
    CALLER_FIELD_NUMBER: _ClassVar[int]
    candidate_id: str
    caller: _training_pb2.CallerContext
    def __init__(self, candidate_id: _Optional[str] = ..., caller: _Optional[_Union[_training_pb2.CallerContext, _Mapping]] = ...) -> None: ...

class DeployChallengerShadowResponse(_message.Message):
    __slots__ = ("candidate", "decision")
    CANDIDATE_FIELD_NUMBER: _ClassVar[int]
    DECISION_FIELD_NUMBER: _ClassVar[int]
    candidate: Candidate
    decision: PermissionDecision
    def __init__(self, candidate: _Optional[_Union[Candidate, _Mapping]] = ..., decision: _Optional[_Union[PermissionDecision, _Mapping]] = ...) -> None: ...

class NominateLiveEligibleRequest(_message.Message):
    __slots__ = ("candidate_id", "rationale", "caller")
    CANDIDATE_ID_FIELD_NUMBER: _ClassVar[int]
    RATIONALE_FIELD_NUMBER: _ClassVar[int]
    CALLER_FIELD_NUMBER: _ClassVar[int]
    candidate_id: str
    rationale: str
    caller: _training_pb2.CallerContext
    def __init__(self, candidate_id: _Optional[str] = ..., rationale: _Optional[str] = ..., caller: _Optional[_Union[_training_pb2.CallerContext, _Mapping]] = ...) -> None: ...

class NominateLiveEligibleResponse(_message.Message):
    __slots__ = ("decision",)
    DECISION_FIELD_NUMBER: _ClassVar[int]
    decision: PermissionDecision
    def __init__(self, decision: _Optional[_Union[PermissionDecision, _Mapping]] = ...) -> None: ...

class DemoteCandidateRequest(_message.Message):
    __slots__ = ("candidate_id", "to_stage", "reason", "caller")
    CANDIDATE_ID_FIELD_NUMBER: _ClassVar[int]
    TO_STAGE_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    CALLER_FIELD_NUMBER: _ClassVar[int]
    candidate_id: str
    to_stage: _ml_pb2.RegistryStage
    reason: str
    caller: _training_pb2.CallerContext
    def __init__(self, candidate_id: _Optional[str] = ..., to_stage: _Optional[_Union[_ml_pb2.RegistryStage, str]] = ..., reason: _Optional[str] = ..., caller: _Optional[_Union[_training_pb2.CallerContext, _Mapping]] = ...) -> None: ...

class DemoteCandidateResponse(_message.Message):
    __slots__ = ("candidate",)
    CANDIDATE_FIELD_NUMBER: _ClassVar[int]
    candidate: Candidate
    def __init__(self, candidate: _Optional[_Union[Candidate, _Mapping]] = ...) -> None: ...

class GetEvidencePackRequest(_message.Message):
    __slots__ = ("candidate_id",)
    CANDIDATE_ID_FIELD_NUMBER: _ClassVar[int]
    candidate_id: str
    def __init__(self, candidate_id: _Optional[str] = ...) -> None: ...

class GetEvidencePackResponse(_message.Message):
    __slots__ = ("pack",)
    PACK_FIELD_NUMBER: _ClassVar[int]
    pack: EvidencePack
    def __init__(self, pack: _Optional[_Union[EvidencePack, _Mapping]] = ...) -> None: ...

class GetLineageGraphRequest(_message.Message):
    __slots__ = ("candidate_id", "model_version")
    CANDIDATE_ID_FIELD_NUMBER: _ClassVar[int]
    MODEL_VERSION_FIELD_NUMBER: _ClassVar[int]
    candidate_id: str
    model_version: str
    def __init__(self, candidate_id: _Optional[str] = ..., model_version: _Optional[str] = ...) -> None: ...

class GetLineageGraphResponse(_message.Message):
    __slots__ = ("graph",)
    GRAPH_FIELD_NUMBER: _ClassVar[int]
    graph: LineageGraph
    def __init__(self, graph: _Optional[_Union[LineageGraph, _Mapping]] = ...) -> None: ...

class SetRetrainingPolicyRequest(_message.Message):
    __slots__ = ("policy", "caller")
    POLICY_FIELD_NUMBER: _ClassVar[int]
    CALLER_FIELD_NUMBER: _ClassVar[int]
    policy: RetrainingPolicy
    caller: _training_pb2.CallerContext
    def __init__(self, policy: _Optional[_Union[RetrainingPolicy, _Mapping]] = ..., caller: _Optional[_Union[_training_pb2.CallerContext, _Mapping]] = ...) -> None: ...

class SetRetrainingPolicyResponse(_message.Message):
    __slots__ = ("policy",)
    POLICY_FIELD_NUMBER: _ClassVar[int]
    policy: RetrainingPolicy
    def __init__(self, policy: _Optional[_Union[RetrainingPolicy, _Mapping]] = ...) -> None: ...

class TriggerRetrainRequest(_message.Message):
    __slots__ = ("lineage_id", "reason", "caller")
    LINEAGE_ID_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    CALLER_FIELD_NUMBER: _ClassVar[int]
    lineage_id: str
    reason: str
    caller: _training_pb2.CallerContext
    def __init__(self, lineage_id: _Optional[str] = ..., reason: _Optional[str] = ..., caller: _Optional[_Union[_training_pb2.CallerContext, _Mapping]] = ...) -> None: ...

class TriggerRetrainResponse(_message.Message):
    __slots__ = ("study",)
    STUDY_FIELD_NUMBER: _ClassVar[int]
    study: Study
    def __init__(self, study: _Optional[_Union[Study, _Mapping]] = ...) -> None: ...

class DeploymentLimits(_message.Message):
    __slots__ = ("lineage_id", "horizon", "limits", "allocation", "source", "updated_at", "updated_by")
    LINEAGE_ID_FIELD_NUMBER: _ClassVar[int]
    HORIZON_FIELD_NUMBER: _ClassVar[int]
    LIMITS_FIELD_NUMBER: _ClassVar[int]
    ALLOCATION_FIELD_NUMBER: _ClassVar[int]
    SOURCE_FIELD_NUMBER: _ClassVar[int]
    UPDATED_AT_FIELD_NUMBER: _ClassVar[int]
    UPDATED_BY_FIELD_NUMBER: _ClassVar[int]
    lineage_id: str
    horizon: str
    limits: _execution_pb2.RiskLimits
    allocation: str
    source: str
    updated_at: int
    updated_by: str
    def __init__(self, lineage_id: _Optional[str] = ..., horizon: _Optional[str] = ..., limits: _Optional[_Union[_execution_pb2.RiskLimits, _Mapping]] = ..., allocation: _Optional[str] = ..., source: _Optional[str] = ..., updated_at: _Optional[int] = ..., updated_by: _Optional[str] = ...) -> None: ...

class GetDeploymentLimitsRequest(_message.Message):
    __slots__ = ("lineage_id",)
    LINEAGE_ID_FIELD_NUMBER: _ClassVar[int]
    lineage_id: str
    def __init__(self, lineage_id: _Optional[str] = ...) -> None: ...

class GetDeploymentLimitsResponse(_message.Message):
    __slots__ = ("limits", "profile")
    LIMITS_FIELD_NUMBER: _ClassVar[int]
    PROFILE_FIELD_NUMBER: _ClassVar[int]
    limits: DeploymentLimits
    profile: DeploymentLimits
    def __init__(self, limits: _Optional[_Union[DeploymentLimits, _Mapping]] = ..., profile: _Optional[_Union[DeploymentLimits, _Mapping]] = ...) -> None: ...

class SetDeploymentLimitsRequest(_message.Message):
    __slots__ = ("lineage_id", "limits", "allocation", "caller")
    LINEAGE_ID_FIELD_NUMBER: _ClassVar[int]
    LIMITS_FIELD_NUMBER: _ClassVar[int]
    ALLOCATION_FIELD_NUMBER: _ClassVar[int]
    CALLER_FIELD_NUMBER: _ClassVar[int]
    lineage_id: str
    limits: _execution_pb2.RiskLimits
    allocation: str
    caller: _training_pb2.CallerContext
    def __init__(self, lineage_id: _Optional[str] = ..., limits: _Optional[_Union[_execution_pb2.RiskLimits, _Mapping]] = ..., allocation: _Optional[str] = ..., caller: _Optional[_Union[_training_pb2.CallerContext, _Mapping]] = ...) -> None: ...

class SetDeploymentLimitsResponse(_message.Message):
    __slots__ = ("limits", "raised")
    LIMITS_FIELD_NUMBER: _ClassVar[int]
    RAISED_FIELD_NUMBER: _ClassVar[int]
    limits: DeploymentLimits
    raised: bool
    def __init__(self, limits: _Optional[_Union[DeploymentLimits, _Mapping]] = ..., raised: _Optional[bool] = ...) -> None: ...

class GetGraduationStatisticsRequest(_message.Message):
    __slots__ = ("strategy_id", "paper_session_returns", "horizon")
    STRATEGY_ID_FIELD_NUMBER: _ClassVar[int]
    PAPER_SESSION_RETURNS_FIELD_NUMBER: _ClassVar[int]
    HORIZON_FIELD_NUMBER: _ClassVar[int]
    strategy_id: str
    paper_session_returns: _containers.RepeatedScalarFieldContainer[float]
    horizon: str
    def __init__(self, strategy_id: _Optional[str] = ..., paper_session_returns: _Optional[_Iterable[float]] = ..., horizon: _Optional[str] = ...) -> None: ...

class GraduationStatistics(_message.Message):
    __slots__ = ("strategy_id", "candidate_id", "registry_stage", "dsr_combined", "n_effective", "n_observations", "dsr_available", "backtest_drawdown_p95", "drawdown_available", "modeled_shortfall_bps", "shortfall_available", "divergence_match_rate", "divergence_sessions", "divergence_available", "computed_at")
    STRATEGY_ID_FIELD_NUMBER: _ClassVar[int]
    CANDIDATE_ID_FIELD_NUMBER: _ClassVar[int]
    REGISTRY_STAGE_FIELD_NUMBER: _ClassVar[int]
    DSR_COMBINED_FIELD_NUMBER: _ClassVar[int]
    N_EFFECTIVE_FIELD_NUMBER: _ClassVar[int]
    N_OBSERVATIONS_FIELD_NUMBER: _ClassVar[int]
    DSR_AVAILABLE_FIELD_NUMBER: _ClassVar[int]
    BACKTEST_DRAWDOWN_P95_FIELD_NUMBER: _ClassVar[int]
    DRAWDOWN_AVAILABLE_FIELD_NUMBER: _ClassVar[int]
    MODELED_SHORTFALL_BPS_FIELD_NUMBER: _ClassVar[int]
    SHORTFALL_AVAILABLE_FIELD_NUMBER: _ClassVar[int]
    DIVERGENCE_MATCH_RATE_FIELD_NUMBER: _ClassVar[int]
    DIVERGENCE_SESSIONS_FIELD_NUMBER: _ClassVar[int]
    DIVERGENCE_AVAILABLE_FIELD_NUMBER: _ClassVar[int]
    COMPUTED_AT_FIELD_NUMBER: _ClassVar[int]
    strategy_id: str
    candidate_id: str
    registry_stage: str
    dsr_combined: float
    n_effective: float
    n_observations: int
    dsr_available: bool
    backtest_drawdown_p95: float
    drawdown_available: bool
    modeled_shortfall_bps: float
    shortfall_available: bool
    divergence_match_rate: float
    divergence_sessions: int
    divergence_available: bool
    computed_at: int
    def __init__(self, strategy_id: _Optional[str] = ..., candidate_id: _Optional[str] = ..., registry_stage: _Optional[str] = ..., dsr_combined: _Optional[float] = ..., n_effective: _Optional[float] = ..., n_observations: _Optional[int] = ..., dsr_available: _Optional[bool] = ..., backtest_drawdown_p95: _Optional[float] = ..., drawdown_available: _Optional[bool] = ..., modeled_shortfall_bps: _Optional[float] = ..., shortfall_available: _Optional[bool] = ..., divergence_match_rate: _Optional[float] = ..., divergence_sessions: _Optional[int] = ..., divergence_available: _Optional[bool] = ..., computed_at: _Optional[int] = ...) -> None: ...

class GetGraduationStatisticsResponse(_message.Message):
    __slots__ = ("statistics",)
    STATISTICS_FIELD_NUMBER: _ClassVar[int]
    statistics: GraduationStatistics
    def __init__(self, statistics: _Optional[_Union[GraduationStatistics, _Mapping]] = ...) -> None: ...
