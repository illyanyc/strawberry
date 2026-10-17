from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class Severity(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    SEVERITY_UNSPECIFIED: _ClassVar[Severity]
    SEVERITY_INFO: _ClassVar[Severity]
    SEVERITY_WARNING: _ClassVar[Severity]
    SEVERITY_CRITICAL: _ClassVar[Severity]
SEVERITY_UNSPECIFIED: Severity
SEVERITY_INFO: Severity
SEVERITY_WARNING: Severity
SEVERITY_CRITICAL: Severity

class Alert(_message.Message):
    __slots__ = ("alert_id", "severity", "source", "subject", "message", "dedupe_key", "runbook_url", "occurred_at", "correlation_id")
    ALERT_ID_FIELD_NUMBER: _ClassVar[int]
    SEVERITY_FIELD_NUMBER: _ClassVar[int]
    SOURCE_FIELD_NUMBER: _ClassVar[int]
    SUBJECT_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    DEDUPE_KEY_FIELD_NUMBER: _ClassVar[int]
    RUNBOOK_URL_FIELD_NUMBER: _ClassVar[int]
    OCCURRED_AT_FIELD_NUMBER: _ClassVar[int]
    CORRELATION_ID_FIELD_NUMBER: _ClassVar[int]
    alert_id: str
    severity: Severity
    source: str
    subject: str
    message: str
    dedupe_key: str
    runbook_url: str
    occurred_at: int
    correlation_id: str
    def __init__(self, alert_id: _Optional[str] = ..., severity: _Optional[_Union[Severity, str]] = ..., source: _Optional[str] = ..., subject: _Optional[str] = ..., message: _Optional[str] = ..., dedupe_key: _Optional[str] = ..., runbook_url: _Optional[str] = ..., occurred_at: _Optional[int] = ..., correlation_id: _Optional[str] = ...) -> None: ...
