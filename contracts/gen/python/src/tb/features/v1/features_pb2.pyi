from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional

DESCRIPTOR: _descriptor.FileDescriptor

class FeatureVector(_message.Message):
    __slots__ = ("feature_set_id", "feature_set_version", "instrument_id", "as_of", "values", "snapshot_id")
    class ValuesEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: float
        def __init__(self, key: _Optional[str] = ..., value: _Optional[float] = ...) -> None: ...
    FEATURE_SET_ID_FIELD_NUMBER: _ClassVar[int]
    FEATURE_SET_VERSION_FIELD_NUMBER: _ClassVar[int]
    INSTRUMENT_ID_FIELD_NUMBER: _ClassVar[int]
    AS_OF_FIELD_NUMBER: _ClassVar[int]
    VALUES_FIELD_NUMBER: _ClassVar[int]
    SNAPSHOT_ID_FIELD_NUMBER: _ClassVar[int]
    feature_set_id: str
    feature_set_version: str
    instrument_id: str
    as_of: int
    values: _containers.ScalarMap[str, float]
    snapshot_id: str
    def __init__(self, feature_set_id: _Optional[str] = ..., feature_set_version: _Optional[str] = ..., instrument_id: _Optional[str] = ..., as_of: _Optional[int] = ..., values: _Optional[_Mapping[str, float]] = ..., snapshot_id: _Optional[str] = ...) -> None: ...
