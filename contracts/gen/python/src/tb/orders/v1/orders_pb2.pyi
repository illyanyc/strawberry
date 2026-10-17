from tb.common.v1 import common_pb2 as _common_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class OrderType(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    ORDER_TYPE_UNSPECIFIED: _ClassVar[OrderType]
    ORDER_TYPE_MARKET: _ClassVar[OrderType]
    ORDER_TYPE_LIMIT: _ClassVar[OrderType]
    ORDER_TYPE_STOP: _ClassVar[OrderType]
    ORDER_TYPE_STOP_LIMIT: _ClassVar[OrderType]
    ORDER_TYPE_TRAILING_STOP: _ClassVar[OrderType]

class TimeInForce(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    TIME_IN_FORCE_UNSPECIFIED: _ClassVar[TimeInForce]
    TIME_IN_FORCE_DAY: _ClassVar[TimeInForce]
    TIME_IN_FORCE_GTC: _ClassVar[TimeInForce]
    TIME_IN_FORCE_IOC: _ClassVar[TimeInForce]
    TIME_IN_FORCE_FOK: _ClassVar[TimeInForce]
    TIME_IN_FORCE_OPG: _ClassVar[TimeInForce]
    TIME_IN_FORCE_CLS: _ClassVar[TimeInForce]

class OrderState(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    ORDER_STATE_UNSPECIFIED: _ClassVar[OrderState]
    ORDER_STATE_PENDING_SUBMIT: _ClassVar[OrderState]
    ORDER_STATE_SUBMITTED: _ClassVar[OrderState]
    ORDER_STATE_ACCEPTED: _ClassVar[OrderState]
    ORDER_STATE_PARTIALLY_FILLED: _ClassVar[OrderState]
    ORDER_STATE_FILLED: _ClassVar[OrderState]
    ORDER_STATE_PENDING_CANCEL: _ClassVar[OrderState]
    ORDER_STATE_CANCELED: _ClassVar[OrderState]
    ORDER_STATE_REJECTED: _ClassVar[OrderState]
    ORDER_STATE_EXPIRED: _ClassVar[OrderState]
    ORDER_STATE_UNKNOWN: _ClassVar[OrderState]
    ORDER_STATE_REPLACED: _ClassVar[OrderState]
    ORDER_STATE_PENDING_REPLACE: _ClassVar[OrderState]

class KillSwitchScope(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    KILL_SWITCH_SCOPE_UNSPECIFIED: _ClassVar[KillSwitchScope]
    KILL_SWITCH_SCOPE_GLOBAL: _ClassVar[KillSwitchScope]
    KILL_SWITCH_SCOPE_BROKER_ACCOUNT: _ClassVar[KillSwitchScope]
    KILL_SWITCH_SCOPE_STRATEGY: _ClassVar[KillSwitchScope]
    KILL_SWITCH_SCOPE_INSTRUMENT: _ClassVar[KillSwitchScope]

class KillSwitchAction(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    KILL_SWITCH_ACTION_UNSPECIFIED: _ClassVar[KillSwitchAction]
    KILL_SWITCH_ACTION_FREEZE: _ClassVar[KillSwitchAction]
    KILL_SWITCH_ACTION_CANCEL_OPEN: _ClassVar[KillSwitchAction]
    KILL_SWITCH_ACTION_FLATTEN_STRATEGY_OWNED: _ClassVar[KillSwitchAction]
    KILL_SWITCH_ACTION_RELEASE: _ClassVar[KillSwitchAction]
ORDER_TYPE_UNSPECIFIED: OrderType
ORDER_TYPE_MARKET: OrderType
ORDER_TYPE_LIMIT: OrderType
ORDER_TYPE_STOP: OrderType
ORDER_TYPE_STOP_LIMIT: OrderType
ORDER_TYPE_TRAILING_STOP: OrderType
TIME_IN_FORCE_UNSPECIFIED: TimeInForce
TIME_IN_FORCE_DAY: TimeInForce
TIME_IN_FORCE_GTC: TimeInForce
TIME_IN_FORCE_IOC: TimeInForce
TIME_IN_FORCE_FOK: TimeInForce
TIME_IN_FORCE_OPG: TimeInForce
TIME_IN_FORCE_CLS: TimeInForce
ORDER_STATE_UNSPECIFIED: OrderState
ORDER_STATE_PENDING_SUBMIT: OrderState
ORDER_STATE_SUBMITTED: OrderState
ORDER_STATE_ACCEPTED: OrderState
ORDER_STATE_PARTIALLY_FILLED: OrderState
ORDER_STATE_FILLED: OrderState
ORDER_STATE_PENDING_CANCEL: OrderState
ORDER_STATE_CANCELED: OrderState
ORDER_STATE_REJECTED: OrderState
ORDER_STATE_EXPIRED: OrderState
ORDER_STATE_UNKNOWN: OrderState
ORDER_STATE_REPLACED: OrderState
ORDER_STATE_PENDING_REPLACE: OrderState
KILL_SWITCH_SCOPE_UNSPECIFIED: KillSwitchScope
KILL_SWITCH_SCOPE_GLOBAL: KillSwitchScope
KILL_SWITCH_SCOPE_BROKER_ACCOUNT: KillSwitchScope
KILL_SWITCH_SCOPE_STRATEGY: KillSwitchScope
KILL_SWITCH_SCOPE_INSTRUMENT: KillSwitchScope
KILL_SWITCH_ACTION_UNSPECIFIED: KillSwitchAction
KILL_SWITCH_ACTION_FREEZE: KillSwitchAction
KILL_SWITCH_ACTION_CANCEL_OPEN: KillSwitchAction
KILL_SWITCH_ACTION_FLATTEN_STRATEGY_OWNED: KillSwitchAction
KILL_SWITCH_ACTION_RELEASE: KillSwitchAction

class OrderIntent(_message.Message):
    __slots__ = ("intent_id", "strategy_id", "broker_account_id", "instrument_id", "side", "quantity", "target_position", "order_type", "limit_price", "stop_price", "tif", "reason", "mode", "correlation_id", "created_at")
    INTENT_ID_FIELD_NUMBER: _ClassVar[int]
    STRATEGY_ID_FIELD_NUMBER: _ClassVar[int]
    BROKER_ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    INSTRUMENT_ID_FIELD_NUMBER: _ClassVar[int]
    SIDE_FIELD_NUMBER: _ClassVar[int]
    QUANTITY_FIELD_NUMBER: _ClassVar[int]
    TARGET_POSITION_FIELD_NUMBER: _ClassVar[int]
    ORDER_TYPE_FIELD_NUMBER: _ClassVar[int]
    LIMIT_PRICE_FIELD_NUMBER: _ClassVar[int]
    STOP_PRICE_FIELD_NUMBER: _ClassVar[int]
    TIF_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    MODE_FIELD_NUMBER: _ClassVar[int]
    CORRELATION_ID_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_FIELD_NUMBER: _ClassVar[int]
    intent_id: str
    strategy_id: str
    broker_account_id: str
    instrument_id: str
    side: _common_pb2.Side
    quantity: _common_pb2.Decimal
    target_position: _common_pb2.Decimal
    order_type: OrderType
    limit_price: _common_pb2.Decimal
    stop_price: _common_pb2.Decimal
    tif: TimeInForce
    reason: str
    mode: _common_pb2.Mode
    correlation_id: str
    created_at: int
    def __init__(self, intent_id: _Optional[str] = ..., strategy_id: _Optional[str] = ..., broker_account_id: _Optional[str] = ..., instrument_id: _Optional[str] = ..., side: _Optional[_Union[_common_pb2.Side, str]] = ..., quantity: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., target_position: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., order_type: _Optional[_Union[OrderType, str]] = ..., limit_price: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., stop_price: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., tif: _Optional[_Union[TimeInForce, str]] = ..., reason: _Optional[str] = ..., mode: _Optional[_Union[_common_pb2.Mode, str]] = ..., correlation_id: _Optional[str] = ..., created_at: _Optional[int] = ...) -> None: ...

class RiskCheck(_message.Message):
    __slots__ = ("name", "passed", "value", "limit", "detail")
    NAME_FIELD_NUMBER: _ClassVar[int]
    PASSED_FIELD_NUMBER: _ClassVar[int]
    VALUE_FIELD_NUMBER: _ClassVar[int]
    LIMIT_FIELD_NUMBER: _ClassVar[int]
    DETAIL_FIELD_NUMBER: _ClassVar[int]
    name: str
    passed: bool
    value: str
    limit: str
    detail: str
    def __init__(self, name: _Optional[str] = ..., passed: _Optional[bool] = ..., value: _Optional[str] = ..., limit: _Optional[str] = ..., detail: _Optional[str] = ...) -> None: ...

class RiskDecision(_message.Message):
    __slots__ = ("intent_id", "approved", "checks", "decided_at", "correlation_id")
    INTENT_ID_FIELD_NUMBER: _ClassVar[int]
    APPROVED_FIELD_NUMBER: _ClassVar[int]
    CHECKS_FIELD_NUMBER: _ClassVar[int]
    DECIDED_AT_FIELD_NUMBER: _ClassVar[int]
    CORRELATION_ID_FIELD_NUMBER: _ClassVar[int]
    intent_id: str
    approved: bool
    checks: _containers.RepeatedCompositeFieldContainer[RiskCheck]
    decided_at: int
    correlation_id: str
    def __init__(self, intent_id: _Optional[str] = ..., approved: _Optional[bool] = ..., checks: _Optional[_Iterable[_Union[RiskCheck, _Mapping]]] = ..., decided_at: _Optional[int] = ..., correlation_id: _Optional[str] = ...) -> None: ...

class Order(_message.Message):
    __slots__ = ("order_id", "intent_id", "client_order_id", "broker_order_id", "broker_account_id", "strategy_id", "instrument_id", "side", "quantity", "state", "filled_qty", "avg_price", "mode", "created_at", "submitted_at", "updated_at", "correlation_id", "perm_id")
    ORDER_ID_FIELD_NUMBER: _ClassVar[int]
    INTENT_ID_FIELD_NUMBER: _ClassVar[int]
    CLIENT_ORDER_ID_FIELD_NUMBER: _ClassVar[int]
    BROKER_ORDER_ID_FIELD_NUMBER: _ClassVar[int]
    BROKER_ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    STRATEGY_ID_FIELD_NUMBER: _ClassVar[int]
    INSTRUMENT_ID_FIELD_NUMBER: _ClassVar[int]
    SIDE_FIELD_NUMBER: _ClassVar[int]
    QUANTITY_FIELD_NUMBER: _ClassVar[int]
    STATE_FIELD_NUMBER: _ClassVar[int]
    FILLED_QTY_FIELD_NUMBER: _ClassVar[int]
    AVG_PRICE_FIELD_NUMBER: _ClassVar[int]
    MODE_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_FIELD_NUMBER: _ClassVar[int]
    SUBMITTED_AT_FIELD_NUMBER: _ClassVar[int]
    UPDATED_AT_FIELD_NUMBER: _ClassVar[int]
    CORRELATION_ID_FIELD_NUMBER: _ClassVar[int]
    PERM_ID_FIELD_NUMBER: _ClassVar[int]
    order_id: str
    intent_id: str
    client_order_id: str
    broker_order_id: str
    broker_account_id: str
    strategy_id: str
    instrument_id: str
    side: _common_pb2.Side
    quantity: _common_pb2.Decimal
    state: OrderState
    filled_qty: _common_pb2.Decimal
    avg_price: _common_pb2.Decimal
    mode: _common_pb2.Mode
    created_at: int
    submitted_at: int
    updated_at: int
    correlation_id: str
    perm_id: str
    def __init__(self, order_id: _Optional[str] = ..., intent_id: _Optional[str] = ..., client_order_id: _Optional[str] = ..., broker_order_id: _Optional[str] = ..., broker_account_id: _Optional[str] = ..., strategy_id: _Optional[str] = ..., instrument_id: _Optional[str] = ..., side: _Optional[_Union[_common_pb2.Side, str]] = ..., quantity: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., state: _Optional[_Union[OrderState, str]] = ..., filled_qty: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., avg_price: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., mode: _Optional[_Union[_common_pb2.Mode, str]] = ..., created_at: _Optional[int] = ..., submitted_at: _Optional[int] = ..., updated_at: _Optional[int] = ..., correlation_id: _Optional[str] = ..., perm_id: _Optional[str] = ...) -> None: ...

class ExecutionReport(_message.Message):
    __slots__ = ("broker_account_id", "broker_exec_id", "client_order_id", "broker_order_id", "instrument_id", "side", "qty", "price", "fees", "event_time", "mode", "ingested_at", "perm_id", "symbol", "broker", "sequence", "fee_currency", "correlation_id")
    BROKER_ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    BROKER_EXEC_ID_FIELD_NUMBER: _ClassVar[int]
    CLIENT_ORDER_ID_FIELD_NUMBER: _ClassVar[int]
    BROKER_ORDER_ID_FIELD_NUMBER: _ClassVar[int]
    INSTRUMENT_ID_FIELD_NUMBER: _ClassVar[int]
    SIDE_FIELD_NUMBER: _ClassVar[int]
    QTY_FIELD_NUMBER: _ClassVar[int]
    PRICE_FIELD_NUMBER: _ClassVar[int]
    FEES_FIELD_NUMBER: _ClassVar[int]
    EVENT_TIME_FIELD_NUMBER: _ClassVar[int]
    MODE_FIELD_NUMBER: _ClassVar[int]
    INGESTED_AT_FIELD_NUMBER: _ClassVar[int]
    PERM_ID_FIELD_NUMBER: _ClassVar[int]
    SYMBOL_FIELD_NUMBER: _ClassVar[int]
    BROKER_FIELD_NUMBER: _ClassVar[int]
    SEQUENCE_FIELD_NUMBER: _ClassVar[int]
    FEE_CURRENCY_FIELD_NUMBER: _ClassVar[int]
    CORRELATION_ID_FIELD_NUMBER: _ClassVar[int]
    broker_account_id: str
    broker_exec_id: str
    client_order_id: str
    broker_order_id: str
    instrument_id: str
    side: _common_pb2.Side
    qty: _common_pb2.Decimal
    price: _common_pb2.Decimal
    fees: _common_pb2.Decimal
    event_time: int
    mode: _common_pb2.Mode
    ingested_at: int
    perm_id: str
    symbol: str
    broker: _common_pb2.Broker
    sequence: int
    fee_currency: str
    correlation_id: str
    def __init__(self, broker_account_id: _Optional[str] = ..., broker_exec_id: _Optional[str] = ..., client_order_id: _Optional[str] = ..., broker_order_id: _Optional[str] = ..., instrument_id: _Optional[str] = ..., side: _Optional[_Union[_common_pb2.Side, str]] = ..., qty: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., price: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., fees: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., event_time: _Optional[int] = ..., mode: _Optional[_Union[_common_pb2.Mode, str]] = ..., ingested_at: _Optional[int] = ..., perm_id: _Optional[str] = ..., symbol: _Optional[str] = ..., broker: _Optional[_Union[_common_pb2.Broker, str]] = ..., sequence: _Optional[int] = ..., fee_currency: _Optional[str] = ..., correlation_id: _Optional[str] = ...) -> None: ...

class KillSwitchEvent(_message.Message):
    __slots__ = ("event_id", "scope", "target", "action", "actor", "reason", "occurred_at", "correlation_id")
    EVENT_ID_FIELD_NUMBER: _ClassVar[int]
    SCOPE_FIELD_NUMBER: _ClassVar[int]
    TARGET_FIELD_NUMBER: _ClassVar[int]
    ACTION_FIELD_NUMBER: _ClassVar[int]
    ACTOR_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    OCCURRED_AT_FIELD_NUMBER: _ClassVar[int]
    CORRELATION_ID_FIELD_NUMBER: _ClassVar[int]
    event_id: str
    scope: KillSwitchScope
    target: str
    action: KillSwitchAction
    actor: str
    reason: str
    occurred_at: int
    correlation_id: str
    def __init__(self, event_id: _Optional[str] = ..., scope: _Optional[_Union[KillSwitchScope, str]] = ..., target: _Optional[str] = ..., action: _Optional[_Union[KillSwitchAction, str]] = ..., actor: _Optional[str] = ..., reason: _Optional[str] = ..., occurred_at: _Optional[int] = ..., correlation_id: _Optional[str] = ...) -> None: ...
