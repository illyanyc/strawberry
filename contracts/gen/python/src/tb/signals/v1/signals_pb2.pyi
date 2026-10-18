from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class Direction(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    DIRECTION_UNSPECIFIED: _ClassVar[Direction]
    DIRECTION_LONG: _ClassVar[Direction]
    DIRECTION_SHORT: _ClassVar[Direction]
    DIRECTION_FLAT: _ClassVar[Direction]
DIRECTION_UNSPECIFIED: Direction
DIRECTION_LONG: Direction
DIRECTION_SHORT: Direction
DIRECTION_FLAT: Direction

class Signal(_message.Message):
    __slots__ = ("signal_id", "strategy_id", "model_version", "instrument_id", "as_of", "direction", "score", "probability", "horizon", "feature_snapshot_id", "correlation_id")
    SIGNAL_ID_FIELD_NUMBER: _ClassVar[int]
    STRATEGY_ID_FIELD_NUMBER: _ClassVar[int]
    MODEL_VERSION_FIELD_NUMBER: _ClassVar[int]
    INSTRUMENT_ID_FIELD_NUMBER: _ClassVar[int]
    AS_OF_FIELD_NUMBER: _ClassVar[int]
    DIRECTION_FIELD_NUMBER: _ClassVar[int]
    SCORE_FIELD_NUMBER: _ClassVar[int]
    PROBABILITY_FIELD_NUMBER: _ClassVar[int]
    HORIZON_FIELD_NUMBER: _ClassVar[int]
    FEATURE_SNAPSHOT_ID_FIELD_NUMBER: _ClassVar[int]
    CORRELATION_ID_FIELD_NUMBER: _ClassVar[int]
    signal_id: str
    strategy_id: str
    model_version: str
    instrument_id: str
    as_of: int
    direction: Direction
    score: float
    probability: float
    horizon: int
    feature_snapshot_id: str
    correlation_id: str
    def __init__(self, signal_id: _Optional[str] = ..., strategy_id: _Optional[str] = ..., model_version: _Optional[str] = ..., instrument_id: _Optional[str] = ..., as_of: _Optional[int] = ..., direction: _Optional[_Union[Direction, str]] = ..., score: _Optional[float] = ..., probability: _Optional[float] = ..., horizon: _Optional[int] = ..., feature_snapshot_id: _Optional[str] = ..., correlation_id: _Optional[str] = ...) -> None: ...

class RuntimeHeartbeat(_message.Message):
    __slots__ = ("runtime_id", "strategy_ids", "last_bar_available_at", "bars_processed", "intents_published", "sent_at", "version")
    RUNTIME_ID_FIELD_NUMBER: _ClassVar[int]
    STRATEGY_IDS_FIELD_NUMBER: _ClassVar[int]
    LAST_BAR_AVAILABLE_AT_FIELD_NUMBER: _ClassVar[int]
    BARS_PROCESSED_FIELD_NUMBER: _ClassVar[int]
    INTENTS_PUBLISHED_FIELD_NUMBER: _ClassVar[int]
    SENT_AT_FIELD_NUMBER: _ClassVar[int]
    VERSION_FIELD_NUMBER: _ClassVar[int]
    runtime_id: str
    strategy_ids: _containers.RepeatedScalarFieldContainer[str]
    last_bar_available_at: int
    bars_processed: int
    intents_published: int
    sent_at: int
    version: str
    def __init__(self, runtime_id: _Optional[str] = ..., strategy_ids: _Optional[_Iterable[str]] = ..., last_bar_available_at: _Optional[int] = ..., bars_processed: _Optional[int] = ..., intents_published: _Optional[int] = ..., sent_at: _Optional[int] = ..., version: _Optional[str] = ...) -> None: ...
