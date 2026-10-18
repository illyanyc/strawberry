from tb.ml.v1 import ml_pb2 as _ml_pb2
from tb.ml.v1 import training_pb2 as _training_pb2
from tb.signals.v1 import signals_pb2 as _signals_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class ListModelFamiliesRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class ListModelFamiliesResponse(_message.Message):
    __slots__ = ("families",)
    FAMILIES_FIELD_NUMBER: _ClassVar[int]
    families: _containers.RepeatedCompositeFieldContainer[_training_pb2.ModelFamily]
    def __init__(self, families: _Optional[_Iterable[_Union[_training_pb2.ModelFamily, _Mapping]]] = ...) -> None: ...

class ListFeatureSetsRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class ListFeatureSetsResponse(_message.Message):
    __slots__ = ("feature_sets",)
    FEATURE_SETS_FIELD_NUMBER: _ClassVar[int]
    feature_sets: _containers.RepeatedCompositeFieldContainer[_training_pb2.FeatureSetInfo]
    def __init__(self, feature_sets: _Optional[_Iterable[_Union[_training_pb2.FeatureSetInfo, _Mapping]]] = ...) -> None: ...

class ListLabelPresetsRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class ListLabelPresetsResponse(_message.Message):
    __slots__ = ("presets",)
    PRESETS_FIELD_NUMBER: _ClassVar[int]
    presets: _containers.RepeatedCompositeFieldContainer[_training_pb2.LabelPreset]
    def __init__(self, presets: _Optional[_Iterable[_Union[_training_pb2.LabelPreset, _Mapping]]] = ...) -> None: ...

class EstimateTrainingJobRequest(_message.Message):
    __slots__ = ("config", "caller")
    CONFIG_FIELD_NUMBER: _ClassVar[int]
    CALLER_FIELD_NUMBER: _ClassVar[int]
    config: _training_pb2.TrainingConfig
    caller: _training_pb2.CallerContext
    def __init__(self, config: _Optional[_Union[_training_pb2.TrainingConfig, _Mapping]] = ..., caller: _Optional[_Union[_training_pb2.CallerContext, _Mapping]] = ...) -> None: ...

class EstimateTrainingJobResponse(_message.Message):
    __slots__ = ("estimate", "errors")
    ESTIMATE_FIELD_NUMBER: _ClassVar[int]
    ERRORS_FIELD_NUMBER: _ClassVar[int]
    estimate: _training_pb2.JobEstimate
    errors: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, estimate: _Optional[_Union[_training_pb2.JobEstimate, _Mapping]] = ..., errors: _Optional[_Iterable[str]] = ...) -> None: ...

class SubmitTrainingJobRequest(_message.Message):
    __slots__ = ("config", "caller", "confirm_over_budget")
    CONFIG_FIELD_NUMBER: _ClassVar[int]
    CALLER_FIELD_NUMBER: _ClassVar[int]
    CONFIRM_OVER_BUDGET_FIELD_NUMBER: _ClassVar[int]
    config: _training_pb2.TrainingConfig
    caller: _training_pb2.CallerContext
    confirm_over_budget: bool
    def __init__(self, config: _Optional[_Union[_training_pb2.TrainingConfig, _Mapping]] = ..., caller: _Optional[_Union[_training_pb2.CallerContext, _Mapping]] = ..., confirm_over_budget: _Optional[bool] = ...) -> None: ...

class SubmitTrainingJobResponse(_message.Message):
    __slots__ = ("job",)
    JOB_FIELD_NUMBER: _ClassVar[int]
    job: _training_pb2.TrainingJob
    def __init__(self, job: _Optional[_Union[_training_pb2.TrainingJob, _Mapping]] = ...) -> None: ...

class GetJobRequest(_message.Message):
    __slots__ = ("job_id",)
    JOB_ID_FIELD_NUMBER: _ClassVar[int]
    job_id: str
    def __init__(self, job_id: _Optional[str] = ...) -> None: ...

class GetJobResponse(_message.Message):
    __slots__ = ("job",)
    JOB_FIELD_NUMBER: _ClassVar[int]
    job: _training_pb2.TrainingJob
    def __init__(self, job: _Optional[_Union[_training_pb2.TrainingJob, _Mapping]] = ...) -> None: ...

class ListJobsRequest(_message.Message):
    __slots__ = ("lineage_id", "limit")
    LINEAGE_ID_FIELD_NUMBER: _ClassVar[int]
    LIMIT_FIELD_NUMBER: _ClassVar[int]
    lineage_id: str
    limit: int
    def __init__(self, lineage_id: _Optional[str] = ..., limit: _Optional[int] = ...) -> None: ...

class ListJobsResponse(_message.Message):
    __slots__ = ("jobs",)
    JOBS_FIELD_NUMBER: _ClassVar[int]
    jobs: _containers.RepeatedCompositeFieldContainer[_training_pb2.TrainingJob]
    def __init__(self, jobs: _Optional[_Iterable[_Union[_training_pb2.TrainingJob, _Mapping]]] = ...) -> None: ...

class StreamJobProgressRequest(_message.Message):
    __slots__ = ("job_id", "after_seq")
    JOB_ID_FIELD_NUMBER: _ClassVar[int]
    AFTER_SEQ_FIELD_NUMBER: _ClassVar[int]
    job_id: str
    after_seq: int
    def __init__(self, job_id: _Optional[str] = ..., after_seq: _Optional[int] = ...) -> None: ...

class StreamJobProgressResponse(_message.Message):
    __slots__ = ("progress",)
    PROGRESS_FIELD_NUMBER: _ClassVar[int]
    progress: _training_pb2.JobProgress
    def __init__(self, progress: _Optional[_Union[_training_pb2.JobProgress, _Mapping]] = ...) -> None: ...

class CancelJobRequest(_message.Message):
    __slots__ = ("job_id", "caller")
    JOB_ID_FIELD_NUMBER: _ClassVar[int]
    CALLER_FIELD_NUMBER: _ClassVar[int]
    job_id: str
    caller: _training_pb2.CallerContext
    def __init__(self, job_id: _Optional[str] = ..., caller: _Optional[_Union[_training_pb2.CallerContext, _Mapping]] = ...) -> None: ...

class CancelJobResponse(_message.Message):
    __slots__ = ("job",)
    JOB_FIELD_NUMBER: _ClassVar[int]
    job: _training_pb2.TrainingJob
    def __init__(self, job: _Optional[_Union[_training_pb2.TrainingJob, _Mapping]] = ...) -> None: ...

class GetRunRequest(_message.Message):
    __slots__ = ("run_id",)
    RUN_ID_FIELD_NUMBER: _ClassVar[int]
    run_id: str
    def __init__(self, run_id: _Optional[str] = ...) -> None: ...

class GetRunResponse(_message.Message):
    __slots__ = ("run",)
    RUN_FIELD_NUMBER: _ClassVar[int]
    run: _training_pb2.TrainingRun
    def __init__(self, run: _Optional[_Union[_training_pb2.TrainingRun, _Mapping]] = ...) -> None: ...

class ListRunsRequest(_message.Message):
    __slots__ = ("lineage_id", "family", "limit")
    LINEAGE_ID_FIELD_NUMBER: _ClassVar[int]
    FAMILY_FIELD_NUMBER: _ClassVar[int]
    LIMIT_FIELD_NUMBER: _ClassVar[int]
    lineage_id: str
    family: str
    limit: int
    def __init__(self, lineage_id: _Optional[str] = ..., family: _Optional[str] = ..., limit: _Optional[int] = ...) -> None: ...

class ListRunsResponse(_message.Message):
    __slots__ = ("runs",)
    RUNS_FIELD_NUMBER: _ClassVar[int]
    runs: _containers.RepeatedCompositeFieldContainer[_training_pb2.TrainingRun]
    def __init__(self, runs: _Optional[_Iterable[_Union[_training_pb2.TrainingRun, _Mapping]]] = ...) -> None: ...

class RegisterModelRequest(_message.Message):
    __slots__ = ("run_id", "name", "caller")
    RUN_ID_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    CALLER_FIELD_NUMBER: _ClassVar[int]
    run_id: str
    name: str
    caller: _training_pb2.CallerContext
    def __init__(self, run_id: _Optional[str] = ..., name: _Optional[str] = ..., caller: _Optional[_Union[_training_pb2.CallerContext, _Mapping]] = ...) -> None: ...

class RegisterModelResponse(_message.Message):
    __slots__ = ("model_version",)
    MODEL_VERSION_FIELD_NUMBER: _ClassVar[int]
    model_version: _ml_pb2.ModelVersion
    def __init__(self, model_version: _Optional[_Union[_ml_pb2.ModelVersion, _Mapping]] = ...) -> None: ...

class PredictRequest(_message.Message):
    __slots__ = ("model_version", "instrument_ids", "as_of", "strategy_id")
    MODEL_VERSION_FIELD_NUMBER: _ClassVar[int]
    INSTRUMENT_IDS_FIELD_NUMBER: _ClassVar[int]
    AS_OF_FIELD_NUMBER: _ClassVar[int]
    STRATEGY_ID_FIELD_NUMBER: _ClassVar[int]
    model_version: str
    instrument_ids: _containers.RepeatedScalarFieldContainer[str]
    as_of: int
    strategy_id: str
    def __init__(self, model_version: _Optional[str] = ..., instrument_ids: _Optional[_Iterable[str]] = ..., as_of: _Optional[int] = ..., strategy_id: _Optional[str] = ...) -> None: ...

class PredictResponse(_message.Message):
    __slots__ = ("signals",)
    SIGNALS_FIELD_NUMBER: _ClassVar[int]
    signals: _containers.RepeatedCompositeFieldContainer[_signals_pb2.Signal]
    def __init__(self, signals: _Optional[_Iterable[_Union[_signals_pb2.Signal, _Mapping]]] = ...) -> None: ...

class GetModelCardRequest(_message.Message):
    __slots__ = ("run_id",)
    RUN_ID_FIELD_NUMBER: _ClassVar[int]
    run_id: str
    def __init__(self, run_id: _Optional[str] = ...) -> None: ...

class GetModelCardResponse(_message.Message):
    __slots__ = ("card",)
    CARD_FIELD_NUMBER: _ClassVar[int]
    card: _training_pb2.ModelCard
    def __init__(self, card: _Optional[_Union[_training_pb2.ModelCard, _Mapping]] = ...) -> None: ...

class GetDiagnosticsRequest(_message.Message):
    __slots__ = ("run_id", "caller")
    RUN_ID_FIELD_NUMBER: _ClassVar[int]
    CALLER_FIELD_NUMBER: _ClassVar[int]
    run_id: str
    caller: _training_pb2.CallerContext
    def __init__(self, run_id: _Optional[str] = ..., caller: _Optional[_Union[_training_pb2.CallerContext, _Mapping]] = ...) -> None: ...

class GetDiagnosticsResponse(_message.Message):
    __slots__ = ("report",)
    REPORT_FIELD_NUMBER: _ClassVar[int]
    report: _training_pb2.DiagnosticsReport
    def __init__(self, report: _Optional[_Union[_training_pb2.DiagnosticsReport, _Mapping]] = ...) -> None: ...

class GetLearningCurvesRequest(_message.Message):
    __slots__ = ("run_id", "caller")
    RUN_ID_FIELD_NUMBER: _ClassVar[int]
    CALLER_FIELD_NUMBER: _ClassVar[int]
    run_id: str
    caller: _training_pb2.CallerContext
    def __init__(self, run_id: _Optional[str] = ..., caller: _Optional[_Union[_training_pb2.CallerContext, _Mapping]] = ...) -> None: ...

class GetLearningCurvesResponse(_message.Message):
    __slots__ = ("curves",)
    CURVES_FIELD_NUMBER: _ClassVar[int]
    curves: _training_pb2.LearningCurves
    def __init__(self, curves: _Optional[_Union[_training_pb2.LearningCurves, _Mapping]] = ...) -> None: ...

class GetFeatureImportanceRequest(_message.Message):
    __slots__ = ("run_id", "method", "caller")
    RUN_ID_FIELD_NUMBER: _ClassVar[int]
    METHOD_FIELD_NUMBER: _ClassVar[int]
    CALLER_FIELD_NUMBER: _ClassVar[int]
    run_id: str
    method: str
    caller: _training_pb2.CallerContext
    def __init__(self, run_id: _Optional[str] = ..., method: _Optional[str] = ..., caller: _Optional[_Union[_training_pb2.CallerContext, _Mapping]] = ...) -> None: ...

class GetFeatureImportanceResponse(_message.Message):
    __slots__ = ("importance",)
    IMPORTANCE_FIELD_NUMBER: _ClassVar[int]
    importance: _training_pb2.FeatureImportance
    def __init__(self, importance: _Optional[_Union[_training_pb2.FeatureImportance, _Mapping]] = ...) -> None: ...

class GetErrorSlicesRequest(_message.Message):
    __slots__ = ("run_id", "slice_by", "caller")
    RUN_ID_FIELD_NUMBER: _ClassVar[int]
    SLICE_BY_FIELD_NUMBER: _ClassVar[int]
    CALLER_FIELD_NUMBER: _ClassVar[int]
    run_id: str
    slice_by: str
    caller: _training_pb2.CallerContext
    def __init__(self, run_id: _Optional[str] = ..., slice_by: _Optional[str] = ..., caller: _Optional[_Union[_training_pb2.CallerContext, _Mapping]] = ...) -> None: ...

class GetErrorSlicesResponse(_message.Message):
    __slots__ = ("slices",)
    SLICES_FIELD_NUMBER: _ClassVar[int]
    slices: _containers.RepeatedCompositeFieldContainer[_training_pb2.ErrorSlice]
    def __init__(self, slices: _Optional[_Iterable[_Union[_training_pb2.ErrorSlice, _Mapping]]] = ...) -> None: ...

class GetDatasetProfileRequest(_message.Message):
    __slots__ = ("snapshot_id", "caller")
    SNAPSHOT_ID_FIELD_NUMBER: _ClassVar[int]
    CALLER_FIELD_NUMBER: _ClassVar[int]
    snapshot_id: str
    caller: _training_pb2.CallerContext
    def __init__(self, snapshot_id: _Optional[str] = ..., caller: _Optional[_Union[_training_pb2.CallerContext, _Mapping]] = ...) -> None: ...

class GetDatasetProfileResponse(_message.Message):
    __slots__ = ("profile",)
    PROFILE_FIELD_NUMBER: _ClassVar[int]
    profile: _training_pb2.DatasetProfile
    def __init__(self, profile: _Optional[_Union[_training_pb2.DatasetProfile, _Mapping]] = ...) -> None: ...

class SubmitAblationRequest(_message.Message):
    __slots__ = ("run_id", "variants", "caller")
    RUN_ID_FIELD_NUMBER: _ClassVar[int]
    VARIANTS_FIELD_NUMBER: _ClassVar[int]
    CALLER_FIELD_NUMBER: _ClassVar[int]
    run_id: str
    variants: _containers.RepeatedCompositeFieldContainer[_training_pb2.AblationVariant]
    caller: _training_pb2.CallerContext
    def __init__(self, run_id: _Optional[str] = ..., variants: _Optional[_Iterable[_Union[_training_pb2.AblationVariant, _Mapping]]] = ..., caller: _Optional[_Union[_training_pb2.CallerContext, _Mapping]] = ...) -> None: ...

class SubmitAblationResponse(_message.Message):
    __slots__ = ("jobs",)
    JOBS_FIELD_NUMBER: _ClassVar[int]
    jobs: _containers.RepeatedCompositeFieldContainer[_training_pb2.TrainingJob]
    def __init__(self, jobs: _Optional[_Iterable[_Union[_training_pb2.TrainingJob, _Mapping]]] = ...) -> None: ...

class AnnotateRunRequest(_message.Message):
    __slots__ = ("run_id", "note", "caller")
    RUN_ID_FIELD_NUMBER: _ClassVar[int]
    NOTE_FIELD_NUMBER: _ClassVar[int]
    CALLER_FIELD_NUMBER: _ClassVar[int]
    run_id: str
    note: str
    caller: _training_pb2.CallerContext
    def __init__(self, run_id: _Optional[str] = ..., note: _Optional[str] = ..., caller: _Optional[_Union[_training_pb2.CallerContext, _Mapping]] = ...) -> None: ...

class AnnotateRunResponse(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...
