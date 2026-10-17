from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Optional as _Optional

DESCRIPTOR: _descriptor.FileDescriptor

class AuditEvent(_message.Message):
    __slots__ = ("seq", "event_id", "occurred_at", "actor", "action", "target", "before_hash", "after_hash", "ip", "device", "correlation_id", "details_json", "prev_hash", "hash")
    SEQ_FIELD_NUMBER: _ClassVar[int]
    EVENT_ID_FIELD_NUMBER: _ClassVar[int]
    OCCURRED_AT_FIELD_NUMBER: _ClassVar[int]
    ACTOR_FIELD_NUMBER: _ClassVar[int]
    ACTION_FIELD_NUMBER: _ClassVar[int]
    TARGET_FIELD_NUMBER: _ClassVar[int]
    BEFORE_HASH_FIELD_NUMBER: _ClassVar[int]
    AFTER_HASH_FIELD_NUMBER: _ClassVar[int]
    IP_FIELD_NUMBER: _ClassVar[int]
    DEVICE_FIELD_NUMBER: _ClassVar[int]
    CORRELATION_ID_FIELD_NUMBER: _ClassVar[int]
    DETAILS_JSON_FIELD_NUMBER: _ClassVar[int]
    PREV_HASH_FIELD_NUMBER: _ClassVar[int]
    HASH_FIELD_NUMBER: _ClassVar[int]
    seq: int
    event_id: str
    occurred_at: int
    actor: str
    action: str
    target: str
    before_hash: str
    after_hash: str
    ip: str
    device: str
    correlation_id: str
    details_json: str
    prev_hash: str
    hash: str
    def __init__(self, seq: _Optional[int] = ..., event_id: _Optional[str] = ..., occurred_at: _Optional[int] = ..., actor: _Optional[str] = ..., action: _Optional[str] = ..., target: _Optional[str] = ..., before_hash: _Optional[str] = ..., after_hash: _Optional[str] = ..., ip: _Optional[str] = ..., device: _Optional[str] = ..., correlation_id: _Optional[str] = ..., details_json: _Optional[str] = ..., prev_hash: _Optional[str] = ..., hash: _Optional[str] = ...) -> None: ...
