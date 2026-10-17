from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class ModelStage(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    MODEL_STAGE_UNSPECIFIED: _ClassVar[ModelStage]
    MODEL_STAGE_CANDIDATE: _ClassVar[ModelStage]
    MODEL_STAGE_CHALLENGER: _ClassVar[ModelStage]
    MODEL_STAGE_CHAMPION: _ClassVar[ModelStage]
    MODEL_STAGE_ARCHIVED: _ClassVar[ModelStage]
MODEL_STAGE_UNSPECIFIED: ModelStage
MODEL_STAGE_CANDIDATE: ModelStage
MODEL_STAGE_CHALLENGER: ModelStage
MODEL_STAGE_CHAMPION: ModelStage
MODEL_STAGE_ARCHIVED: ModelStage

class ModelVersion(_message.Message):
    __slots__ = ("model_id", "version", "family", "training_run_id", "dataset_snapshot_id", "feature_set_id", "metrics", "stage", "code_commit", "config_hash", "seed", "env_lock_hash")
    class MetricsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: float
        def __init__(self, key: _Optional[str] = ..., value: _Optional[float] = ...) -> None: ...
    MODEL_ID_FIELD_NUMBER: _ClassVar[int]
    VERSION_FIELD_NUMBER: _ClassVar[int]
    FAMILY_FIELD_NUMBER: _ClassVar[int]
    TRAINING_RUN_ID_FIELD_NUMBER: _ClassVar[int]
    DATASET_SNAPSHOT_ID_FIELD_NUMBER: _ClassVar[int]
    FEATURE_SET_ID_FIELD_NUMBER: _ClassVar[int]
    METRICS_FIELD_NUMBER: _ClassVar[int]
    STAGE_FIELD_NUMBER: _ClassVar[int]
    CODE_COMMIT_FIELD_NUMBER: _ClassVar[int]
    CONFIG_HASH_FIELD_NUMBER: _ClassVar[int]
    SEED_FIELD_NUMBER: _ClassVar[int]
    ENV_LOCK_HASH_FIELD_NUMBER: _ClassVar[int]
    model_id: str
    version: str
    family: str
    training_run_id: str
    dataset_snapshot_id: str
    feature_set_id: str
    metrics: _containers.ScalarMap[str, float]
    stage: ModelStage
    code_commit: str
    config_hash: str
    seed: int
    env_lock_hash: str
    def __init__(self, model_id: _Optional[str] = ..., version: _Optional[str] = ..., family: _Optional[str] = ..., training_run_id: _Optional[str] = ..., dataset_snapshot_id: _Optional[str] = ..., feature_set_id: _Optional[str] = ..., metrics: _Optional[_Mapping[str, float]] = ..., stage: _Optional[_Union[ModelStage, str]] = ..., code_commit: _Optional[str] = ..., config_hash: _Optional[str] = ..., seed: _Optional[int] = ..., env_lock_hash: _Optional[str] = ...) -> None: ...

class BacktestRun(_message.Message):
    __slots__ = ("run_id", "strategy_config_hash", "data_snapshot_id", "cost_model", "metrics", "trades_artifact_uri", "code_commit", "seed")
    class MetricsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: float
        def __init__(self, key: _Optional[str] = ..., value: _Optional[float] = ...) -> None: ...
    RUN_ID_FIELD_NUMBER: _ClassVar[int]
    STRATEGY_CONFIG_HASH_FIELD_NUMBER: _ClassVar[int]
    DATA_SNAPSHOT_ID_FIELD_NUMBER: _ClassVar[int]
    COST_MODEL_FIELD_NUMBER: _ClassVar[int]
    METRICS_FIELD_NUMBER: _ClassVar[int]
    TRADES_ARTIFACT_URI_FIELD_NUMBER: _ClassVar[int]
    CODE_COMMIT_FIELD_NUMBER: _ClassVar[int]
    SEED_FIELD_NUMBER: _ClassVar[int]
    run_id: str
    strategy_config_hash: str
    data_snapshot_id: str
    cost_model: str
    metrics: _containers.ScalarMap[str, float]
    trades_artifact_uri: str
    code_commit: str
    seed: int
    def __init__(self, run_id: _Optional[str] = ..., strategy_config_hash: _Optional[str] = ..., data_snapshot_id: _Optional[str] = ..., cost_model: _Optional[str] = ..., metrics: _Optional[_Mapping[str, float]] = ..., trades_artifact_uri: _Optional[str] = ..., code_commit: _Optional[str] = ..., seed: _Optional[int] = ...) -> None: ...
