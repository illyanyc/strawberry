from tb.common.v1 import common_pb2 as _common_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class Environment(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    ENVIRONMENT_UNSPECIFIED: _ClassVar[Environment]
    ENVIRONMENT_PAPER: _ClassVar[Environment]
    ENVIRONMENT_LIVE: _ClassVar[Environment]
ENVIRONMENT_UNSPECIFIED: Environment
ENVIRONMENT_PAPER: Environment
ENVIRONMENT_LIVE: Environment

class PutCredentialRequest(_message.Message):
    __slots__ = ("broker_account_id", "broker", "environment", "secret", "on_behalf_of", "correlation_id")
    class SecretEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    BROKER_ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    BROKER_FIELD_NUMBER: _ClassVar[int]
    ENVIRONMENT_FIELD_NUMBER: _ClassVar[int]
    SECRET_FIELD_NUMBER: _ClassVar[int]
    ON_BEHALF_OF_FIELD_NUMBER: _ClassVar[int]
    CORRELATION_ID_FIELD_NUMBER: _ClassVar[int]
    broker_account_id: str
    broker: _common_pb2.Broker
    environment: Environment
    secret: _containers.ScalarMap[str, str]
    on_behalf_of: str
    correlation_id: str
    def __init__(self, broker_account_id: _Optional[str] = ..., broker: _Optional[_Union[_common_pb2.Broker, str]] = ..., environment: _Optional[_Union[Environment, str]] = ..., secret: _Optional[_Mapping[str, str]] = ..., on_behalf_of: _Optional[str] = ..., correlation_id: _Optional[str] = ...) -> None: ...

class PutCredentialResponse(_message.Message):
    __slots__ = ("status",)
    STATUS_FIELD_NUMBER: _ClassVar[int]
    status: CredentialStatus
    def __init__(self, status: _Optional[_Union[CredentialStatus, _Mapping]] = ...) -> None: ...

class DeleteCredentialRequest(_message.Message):
    __slots__ = ("broker_account_id", "environment", "on_behalf_of", "correlation_id")
    BROKER_ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    ENVIRONMENT_FIELD_NUMBER: _ClassVar[int]
    ON_BEHALF_OF_FIELD_NUMBER: _ClassVar[int]
    CORRELATION_ID_FIELD_NUMBER: _ClassVar[int]
    broker_account_id: str
    environment: Environment
    on_behalf_of: str
    correlation_id: str
    def __init__(self, broker_account_id: _Optional[str] = ..., environment: _Optional[_Union[Environment, str]] = ..., on_behalf_of: _Optional[str] = ..., correlation_id: _Optional[str] = ...) -> None: ...

class DeleteCredentialResponse(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class ListCredentialStatusRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class ListCredentialStatusResponse(_message.Message):
    __slots__ = ("credentials",)
    CREDENTIALS_FIELD_NUMBER: _ClassVar[int]
    credentials: _containers.RepeatedCompositeFieldContainer[CredentialStatus]
    def __init__(self, credentials: _Optional[_Iterable[_Union[CredentialStatus, _Mapping]]] = ...) -> None: ...

class CredentialStatus(_message.Message):
    __slots__ = ("broker_account_id", "broker", "environment", "fields", "created_at", "rotated_at", "kek_id")
    BROKER_ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    BROKER_FIELD_NUMBER: _ClassVar[int]
    ENVIRONMENT_FIELD_NUMBER: _ClassVar[int]
    FIELDS_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_FIELD_NUMBER: _ClassVar[int]
    ROTATED_AT_FIELD_NUMBER: _ClassVar[int]
    KEK_ID_FIELD_NUMBER: _ClassVar[int]
    broker_account_id: str
    broker: _common_pb2.Broker
    environment: Environment
    fields: _containers.RepeatedScalarFieldContainer[str]
    created_at: int
    rotated_at: int
    kek_id: str
    def __init__(self, broker_account_id: _Optional[str] = ..., broker: _Optional[_Union[_common_pb2.Broker, str]] = ..., environment: _Optional[_Union[Environment, str]] = ..., fields: _Optional[_Iterable[str]] = ..., created_at: _Optional[int] = ..., rotated_at: _Optional[int] = ..., kek_id: _Optional[str] = ...) -> None: ...
