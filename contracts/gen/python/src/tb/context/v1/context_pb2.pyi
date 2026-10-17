from google.protobuf import struct_pb2 as _struct_pb2
from tb.common.v1 import common_pb2 as _common_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class ContextKind(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    CONTEXT_KIND_UNSPECIFIED: _ClassVar[ContextKind]
    CONTEXT_KIND_NEWS: _ClassVar[ContextKind]
    CONTEXT_KIND_MACRO: _ClassVar[ContextKind]
    CONTEXT_KIND_EARNINGS: _ClassVar[ContextKind]
    CONTEXT_KIND_CALENDAR: _ClassVar[ContextKind]
    CONTEXT_KIND_FUNDAMENTALS: _ClassVar[ContextKind]
    CONTEXT_KIND_REGIME: _ClassVar[ContextKind]
CONTEXT_KIND_UNSPECIFIED: ContextKind
CONTEXT_KIND_NEWS: ContextKind
CONTEXT_KIND_MACRO: ContextKind
CONTEXT_KIND_EARNINGS: ContextKind
CONTEXT_KIND_CALENDAR: ContextKind
CONTEXT_KIND_FUNDAMENTALS: ContextKind
CONTEXT_KIND_REGIME: ContextKind

class ContextEvent(_message.Message):
    __slots__ = ("event_id", "kind", "instrument_ids", "provenance", "payload", "source", "vintage")
    EVENT_ID_FIELD_NUMBER: _ClassVar[int]
    KIND_FIELD_NUMBER: _ClassVar[int]
    INSTRUMENT_IDS_FIELD_NUMBER: _ClassVar[int]
    PROVENANCE_FIELD_NUMBER: _ClassVar[int]
    PAYLOAD_FIELD_NUMBER: _ClassVar[int]
    SOURCE_FIELD_NUMBER: _ClassVar[int]
    VINTAGE_FIELD_NUMBER: _ClassVar[int]
    event_id: str
    kind: ContextKind
    instrument_ids: _containers.RepeatedScalarFieldContainer[str]
    provenance: _common_pb2.Provenance
    payload: _struct_pb2.Struct
    source: str
    vintage: str
    def __init__(self, event_id: _Optional[str] = ..., kind: _Optional[_Union[ContextKind, str]] = ..., instrument_ids: _Optional[_Iterable[str]] = ..., provenance: _Optional[_Union[_common_pb2.Provenance, _Mapping]] = ..., payload: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ..., source: _Optional[str] = ..., vintage: _Optional[str] = ...) -> None: ...
