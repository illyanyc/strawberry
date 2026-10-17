from tb.broker.v1 import credentials_pb2 as _credentials_pb2
from tb.common.v1 import common_pb2 as _common_pb2
from tb.ledger.v1 import ledger_pb2 as _ledger_pb2
from tb.orders.v1 import orders_pb2 as _orders_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class SessionState(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    SESSION_STATE_UNSPECIFIED: _ClassVar[SessionState]
    SESSION_STATE_DISCONNECTED: _ClassVar[SessionState]
    SESSION_STATE_CONNECTING: _ClassVar[SessionState]
    SESSION_STATE_AUTHENTICATED: _ClassVar[SessionState]
    SESSION_STATE_SYNCING: _ClassVar[SessionState]
    SESSION_STATE_READY: _ClassVar[SessionState]
    SESSION_STATE_DEGRADED: _ClassVar[SessionState]
    SESSION_STATE_STOPPED: _ClassVar[SessionState]

class OrderClass(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    ORDER_CLASS_UNSPECIFIED: _ClassVar[OrderClass]
    ORDER_CLASS_SIMPLE: _ClassVar[OrderClass]
    ORDER_CLASS_BRACKET: _ClassVar[OrderClass]
    ORDER_CLASS_OCO: _ClassVar[OrderClass]
    ORDER_CLASS_OTO: _ClassVar[OrderClass]

class SubmitOutcome(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    SUBMIT_OUTCOME_UNSPECIFIED: _ClassVar[SubmitOutcome]
    SUBMIT_OUTCOME_ACCEPTED: _ClassVar[SubmitOutcome]
    SUBMIT_OUTCOME_REJECTED: _ClassVar[SubmitOutcome]
    SUBMIT_OUTCOME_UNKNOWN: _ClassVar[SubmitOutcome]

class CancelOutcome(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    CANCEL_OUTCOME_UNSPECIFIED: _ClassVar[CancelOutcome]
    CANCEL_OUTCOME_REQUESTED: _ClassVar[CancelOutcome]
    CANCEL_OUTCOME_ALREADY_FINAL: _ClassVar[CancelOutcome]
    CANCEL_OUTCOME_NOT_FOUND: _ClassVar[CancelOutcome]
    CANCEL_OUTCOME_REJECTED: _ClassVar[CancelOutcome]
    CANCEL_OUTCOME_UNKNOWN: _ClassVar[CancelOutcome]

class ReplaceOutcome(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    REPLACE_OUTCOME_UNSPECIFIED: _ClassVar[ReplaceOutcome]
    REPLACE_OUTCOME_ACCEPTED: _ClassVar[ReplaceOutcome]
    REPLACE_OUTCOME_REJECTED: _ClassVar[ReplaceOutcome]
    REPLACE_OUTCOME_NOT_FOUND: _ClassVar[ReplaceOutcome]
    REPLACE_OUTCOME_UNKNOWN: _ClassVar[ReplaceOutcome]

class RejectCode(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    REJECT_CODE_UNSPECIFIED: _ClassVar[RejectCode]
    REJECT_CODE_INVALID_ATTRIBUTION: _ClassVar[RejectCode]
    REJECT_CODE_INVALID_ORDER: _ClassVar[RejectCode]
    REJECT_CODE_INSUFFICIENT_BUYING_POWER: _ClassVar[RejectCode]
    REJECT_CODE_SELF_CROSS: _ClassVar[RejectCode]
    REJECT_CODE_NOT_SHORTABLE: _ClassVar[RejectCode]
    REJECT_CODE_MARKET_CLOSED: _ClassVar[RejectCode]
    REJECT_CODE_RATE_LIMITED: _ClassVar[RejectCode]
    REJECT_CODE_DUPLICATE_CLIENT_ORDER_ID: _ClassVar[RejectCode]
    REJECT_CODE_UNKNOWN_INSTRUMENT: _ClassVar[RejectCode]
    REJECT_CODE_BROKER_RISK: _ClassVar[RejectCode]
    REJECT_CODE_NOT_TRADABLE: _ClassVar[RejectCode]
    REJECT_CODE_BROKER_OTHER: _ClassVar[RejectCode]
SESSION_STATE_UNSPECIFIED: SessionState
SESSION_STATE_DISCONNECTED: SessionState
SESSION_STATE_CONNECTING: SessionState
SESSION_STATE_AUTHENTICATED: SessionState
SESSION_STATE_SYNCING: SessionState
SESSION_STATE_READY: SessionState
SESSION_STATE_DEGRADED: SessionState
SESSION_STATE_STOPPED: SessionState
ORDER_CLASS_UNSPECIFIED: OrderClass
ORDER_CLASS_SIMPLE: OrderClass
ORDER_CLASS_BRACKET: OrderClass
ORDER_CLASS_OCO: OrderClass
ORDER_CLASS_OTO: OrderClass
SUBMIT_OUTCOME_UNSPECIFIED: SubmitOutcome
SUBMIT_OUTCOME_ACCEPTED: SubmitOutcome
SUBMIT_OUTCOME_REJECTED: SubmitOutcome
SUBMIT_OUTCOME_UNKNOWN: SubmitOutcome
CANCEL_OUTCOME_UNSPECIFIED: CancelOutcome
CANCEL_OUTCOME_REQUESTED: CancelOutcome
CANCEL_OUTCOME_ALREADY_FINAL: CancelOutcome
CANCEL_OUTCOME_NOT_FOUND: CancelOutcome
CANCEL_OUTCOME_REJECTED: CancelOutcome
CANCEL_OUTCOME_UNKNOWN: CancelOutcome
REPLACE_OUTCOME_UNSPECIFIED: ReplaceOutcome
REPLACE_OUTCOME_ACCEPTED: ReplaceOutcome
REPLACE_OUTCOME_REJECTED: ReplaceOutcome
REPLACE_OUTCOME_NOT_FOUND: ReplaceOutcome
REPLACE_OUTCOME_UNKNOWN: ReplaceOutcome
REJECT_CODE_UNSPECIFIED: RejectCode
REJECT_CODE_INVALID_ATTRIBUTION: RejectCode
REJECT_CODE_INVALID_ORDER: RejectCode
REJECT_CODE_INSUFFICIENT_BUYING_POWER: RejectCode
REJECT_CODE_SELF_CROSS: RejectCode
REJECT_CODE_NOT_SHORTABLE: RejectCode
REJECT_CODE_MARKET_CLOSED: RejectCode
REJECT_CODE_RATE_LIMITED: RejectCode
REJECT_CODE_DUPLICATE_CLIENT_ORDER_ID: RejectCode
REJECT_CODE_UNKNOWN_INSTRUMENT: RejectCode
REJECT_CODE_BROKER_RISK: RejectCode
REJECT_CODE_NOT_TRADABLE: RejectCode
REJECT_CODE_BROKER_OTHER: RejectCode

class Capabilities(_message.Message):
    __slots__ = ("order_types", "tifs", "supports_bracket", "supports_oco", "supports_oto", "supports_fractional", "supports_short", "supports_extended_hours", "max_client_order_id_len", "pdt_applies", "is_margin", "data_feeds", "supports_replace", "supports_trailing_stop")
    ORDER_TYPES_FIELD_NUMBER: _ClassVar[int]
    TIFS_FIELD_NUMBER: _ClassVar[int]
    SUPPORTS_BRACKET_FIELD_NUMBER: _ClassVar[int]
    SUPPORTS_OCO_FIELD_NUMBER: _ClassVar[int]
    SUPPORTS_OTO_FIELD_NUMBER: _ClassVar[int]
    SUPPORTS_FRACTIONAL_FIELD_NUMBER: _ClassVar[int]
    SUPPORTS_SHORT_FIELD_NUMBER: _ClassVar[int]
    SUPPORTS_EXTENDED_HOURS_FIELD_NUMBER: _ClassVar[int]
    MAX_CLIENT_ORDER_ID_LEN_FIELD_NUMBER: _ClassVar[int]
    PDT_APPLIES_FIELD_NUMBER: _ClassVar[int]
    IS_MARGIN_FIELD_NUMBER: _ClassVar[int]
    DATA_FEEDS_FIELD_NUMBER: _ClassVar[int]
    SUPPORTS_REPLACE_FIELD_NUMBER: _ClassVar[int]
    SUPPORTS_TRAILING_STOP_FIELD_NUMBER: _ClassVar[int]
    order_types: _containers.RepeatedScalarFieldContainer[_orders_pb2.OrderType]
    tifs: _containers.RepeatedScalarFieldContainer[_orders_pb2.TimeInForce]
    supports_bracket: bool
    supports_oco: bool
    supports_oto: bool
    supports_fractional: bool
    supports_short: bool
    supports_extended_hours: bool
    max_client_order_id_len: int
    pdt_applies: bool
    is_margin: bool
    data_feeds: _containers.RepeatedScalarFieldContainer[str]
    supports_replace: bool
    supports_trailing_stop: bool
    def __init__(self, order_types: _Optional[_Iterable[_Union[_orders_pb2.OrderType, str]]] = ..., tifs: _Optional[_Iterable[_Union[_orders_pb2.TimeInForce, str]]] = ..., supports_bracket: _Optional[bool] = ..., supports_oco: _Optional[bool] = ..., supports_oto: _Optional[bool] = ..., supports_fractional: _Optional[bool] = ..., supports_short: _Optional[bool] = ..., supports_extended_hours: _Optional[bool] = ..., max_client_order_id_len: _Optional[int] = ..., pdt_applies: _Optional[bool] = ..., is_margin: _Optional[bool] = ..., data_feeds: _Optional[_Iterable[str]] = ..., supports_replace: _Optional[bool] = ..., supports_trailing_stop: _Optional[bool] = ...) -> None: ...

class SessionStatus(_message.Message):
    __slots__ = ("broker_account_id", "state", "connected", "authenticated", "last_heartbeat", "state_since", "degraded_reasons", "last_exec_id", "last_exec_time")
    BROKER_ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    STATE_FIELD_NUMBER: _ClassVar[int]
    CONNECTED_FIELD_NUMBER: _ClassVar[int]
    AUTHENTICATED_FIELD_NUMBER: _ClassVar[int]
    LAST_HEARTBEAT_FIELD_NUMBER: _ClassVar[int]
    STATE_SINCE_FIELD_NUMBER: _ClassVar[int]
    DEGRADED_REASONS_FIELD_NUMBER: _ClassVar[int]
    LAST_EXEC_ID_FIELD_NUMBER: _ClassVar[int]
    LAST_EXEC_TIME_FIELD_NUMBER: _ClassVar[int]
    broker_account_id: str
    state: SessionState
    connected: bool
    authenticated: bool
    last_heartbeat: int
    state_since: int
    degraded_reasons: _containers.RepeatedScalarFieldContainer[str]
    last_exec_id: str
    last_exec_time: int
    def __init__(self, broker_account_id: _Optional[str] = ..., state: _Optional[_Union[SessionState, str]] = ..., connected: _Optional[bool] = ..., authenticated: _Optional[bool] = ..., last_heartbeat: _Optional[int] = ..., state_since: _Optional[int] = ..., degraded_reasons: _Optional[_Iterable[str]] = ..., last_exec_id: _Optional[str] = ..., last_exec_time: _Optional[int] = ...) -> None: ...

class BrokerAccount(_message.Message):
    __slots__ = ("broker_account_id", "broker", "environment", "capabilities", "session")
    BROKER_ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    BROKER_FIELD_NUMBER: _ClassVar[int]
    ENVIRONMENT_FIELD_NUMBER: _ClassVar[int]
    CAPABILITIES_FIELD_NUMBER: _ClassVar[int]
    SESSION_FIELD_NUMBER: _ClassVar[int]
    broker_account_id: str
    broker: _common_pb2.Broker
    environment: _credentials_pb2.Environment
    capabilities: Capabilities
    session: SessionStatus
    def __init__(self, broker_account_id: _Optional[str] = ..., broker: _Optional[_Union[_common_pb2.Broker, str]] = ..., environment: _Optional[_Union[_credentials_pb2.Environment, str]] = ..., capabilities: _Optional[_Union[Capabilities, _Mapping]] = ..., session: _Optional[_Union[SessionStatus, _Mapping]] = ...) -> None: ...

class BracketSpec(_message.Message):
    __slots__ = ("order_class", "take_profit_limit_price", "stop_loss_stop_price", "stop_loss_limit_price")
    ORDER_CLASS_FIELD_NUMBER: _ClassVar[int]
    TAKE_PROFIT_LIMIT_PRICE_FIELD_NUMBER: _ClassVar[int]
    STOP_LOSS_STOP_PRICE_FIELD_NUMBER: _ClassVar[int]
    STOP_LOSS_LIMIT_PRICE_FIELD_NUMBER: _ClassVar[int]
    order_class: OrderClass
    take_profit_limit_price: _common_pb2.Decimal
    stop_loss_stop_price: _common_pb2.Decimal
    stop_loss_limit_price: _common_pb2.Decimal
    def __init__(self, order_class: _Optional[_Union[OrderClass, str]] = ..., take_profit_limit_price: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., stop_loss_stop_price: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., stop_loss_limit_price: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ...) -> None: ...

class BrokerOrderRequest(_message.Message):
    __slots__ = ("client_order_id", "broker_account_id", "instrument", "side", "qty", "type", "limit_price", "stop_price", "tif", "extended_hours", "bracket", "trail_price", "trail_percent", "intent_id", "mode", "correlation_id")
    CLIENT_ORDER_ID_FIELD_NUMBER: _ClassVar[int]
    BROKER_ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    INSTRUMENT_FIELD_NUMBER: _ClassVar[int]
    SIDE_FIELD_NUMBER: _ClassVar[int]
    QTY_FIELD_NUMBER: _ClassVar[int]
    TYPE_FIELD_NUMBER: _ClassVar[int]
    LIMIT_PRICE_FIELD_NUMBER: _ClassVar[int]
    STOP_PRICE_FIELD_NUMBER: _ClassVar[int]
    TIF_FIELD_NUMBER: _ClassVar[int]
    EXTENDED_HOURS_FIELD_NUMBER: _ClassVar[int]
    BRACKET_FIELD_NUMBER: _ClassVar[int]
    TRAIL_PRICE_FIELD_NUMBER: _ClassVar[int]
    TRAIL_PERCENT_FIELD_NUMBER: _ClassVar[int]
    INTENT_ID_FIELD_NUMBER: _ClassVar[int]
    MODE_FIELD_NUMBER: _ClassVar[int]
    CORRELATION_ID_FIELD_NUMBER: _ClassVar[int]
    client_order_id: str
    broker_account_id: str
    instrument: _common_pb2.Instrument
    side: _common_pb2.Side
    qty: _common_pb2.Decimal
    type: _orders_pb2.OrderType
    limit_price: _common_pb2.Decimal
    stop_price: _common_pb2.Decimal
    tif: _orders_pb2.TimeInForce
    extended_hours: bool
    bracket: BracketSpec
    trail_price: _common_pb2.Decimal
    trail_percent: _common_pb2.Decimal
    intent_id: str
    mode: _common_pb2.Mode
    correlation_id: str
    def __init__(self, client_order_id: _Optional[str] = ..., broker_account_id: _Optional[str] = ..., instrument: _Optional[_Union[_common_pb2.Instrument, _Mapping]] = ..., side: _Optional[_Union[_common_pb2.Side, str]] = ..., qty: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., type: _Optional[_Union[_orders_pb2.OrderType, str]] = ..., limit_price: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., stop_price: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., tif: _Optional[_Union[_orders_pb2.TimeInForce, str]] = ..., extended_hours: _Optional[bool] = ..., bracket: _Optional[_Union[BracketSpec, _Mapping]] = ..., trail_price: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., trail_percent: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., intent_id: _Optional[str] = ..., mode: _Optional[_Union[_common_pb2.Mode, str]] = ..., correlation_id: _Optional[str] = ...) -> None: ...

class BrokerOrder(_message.Message):
    __slots__ = ("broker_account_id", "broker_order_id", "client_order_id", "perm_id", "instrument", "side", "type", "tif", "order_class", "qty", "filled_qty", "avg_fill_price", "limit_price", "stop_price", "state", "broker_status", "extended_hours", "parent_broker_order_id", "legs", "replaces_broker_order_id", "replaced_by_broker_order_id", "created_at", "submitted_at", "updated_at", "filled_at", "canceled_at", "trail_price", "trail_percent")
    BROKER_ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    BROKER_ORDER_ID_FIELD_NUMBER: _ClassVar[int]
    CLIENT_ORDER_ID_FIELD_NUMBER: _ClassVar[int]
    PERM_ID_FIELD_NUMBER: _ClassVar[int]
    INSTRUMENT_FIELD_NUMBER: _ClassVar[int]
    SIDE_FIELD_NUMBER: _ClassVar[int]
    TYPE_FIELD_NUMBER: _ClassVar[int]
    TIF_FIELD_NUMBER: _ClassVar[int]
    ORDER_CLASS_FIELD_NUMBER: _ClassVar[int]
    QTY_FIELD_NUMBER: _ClassVar[int]
    FILLED_QTY_FIELD_NUMBER: _ClassVar[int]
    AVG_FILL_PRICE_FIELD_NUMBER: _ClassVar[int]
    LIMIT_PRICE_FIELD_NUMBER: _ClassVar[int]
    STOP_PRICE_FIELD_NUMBER: _ClassVar[int]
    STATE_FIELD_NUMBER: _ClassVar[int]
    BROKER_STATUS_FIELD_NUMBER: _ClassVar[int]
    EXTENDED_HOURS_FIELD_NUMBER: _ClassVar[int]
    PARENT_BROKER_ORDER_ID_FIELD_NUMBER: _ClassVar[int]
    LEGS_FIELD_NUMBER: _ClassVar[int]
    REPLACES_BROKER_ORDER_ID_FIELD_NUMBER: _ClassVar[int]
    REPLACED_BY_BROKER_ORDER_ID_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_FIELD_NUMBER: _ClassVar[int]
    SUBMITTED_AT_FIELD_NUMBER: _ClassVar[int]
    UPDATED_AT_FIELD_NUMBER: _ClassVar[int]
    FILLED_AT_FIELD_NUMBER: _ClassVar[int]
    CANCELED_AT_FIELD_NUMBER: _ClassVar[int]
    TRAIL_PRICE_FIELD_NUMBER: _ClassVar[int]
    TRAIL_PERCENT_FIELD_NUMBER: _ClassVar[int]
    broker_account_id: str
    broker_order_id: str
    client_order_id: str
    perm_id: str
    instrument: _common_pb2.Instrument
    side: _common_pb2.Side
    type: _orders_pb2.OrderType
    tif: _orders_pb2.TimeInForce
    order_class: OrderClass
    qty: _common_pb2.Decimal
    filled_qty: _common_pb2.Decimal
    avg_fill_price: _common_pb2.Decimal
    limit_price: _common_pb2.Decimal
    stop_price: _common_pb2.Decimal
    state: _orders_pb2.OrderState
    broker_status: str
    extended_hours: bool
    parent_broker_order_id: str
    legs: _containers.RepeatedCompositeFieldContainer[BrokerOrder]
    replaces_broker_order_id: str
    replaced_by_broker_order_id: str
    created_at: int
    submitted_at: int
    updated_at: int
    filled_at: int
    canceled_at: int
    trail_price: _common_pb2.Decimal
    trail_percent: _common_pb2.Decimal
    def __init__(self, broker_account_id: _Optional[str] = ..., broker_order_id: _Optional[str] = ..., client_order_id: _Optional[str] = ..., perm_id: _Optional[str] = ..., instrument: _Optional[_Union[_common_pb2.Instrument, _Mapping]] = ..., side: _Optional[_Union[_common_pb2.Side, str]] = ..., type: _Optional[_Union[_orders_pb2.OrderType, str]] = ..., tif: _Optional[_Union[_orders_pb2.TimeInForce, str]] = ..., order_class: _Optional[_Union[OrderClass, str]] = ..., qty: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., filled_qty: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., avg_fill_price: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., limit_price: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., stop_price: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., state: _Optional[_Union[_orders_pb2.OrderState, str]] = ..., broker_status: _Optional[str] = ..., extended_hours: _Optional[bool] = ..., parent_broker_order_id: _Optional[str] = ..., legs: _Optional[_Iterable[_Union[BrokerOrder, _Mapping]]] = ..., replaces_broker_order_id: _Optional[str] = ..., replaced_by_broker_order_id: _Optional[str] = ..., created_at: _Optional[int] = ..., submitted_at: _Optional[int] = ..., updated_at: _Optional[int] = ..., filled_at: _Optional[int] = ..., canceled_at: _Optional[int] = ..., trail_price: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., trail_percent: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ...) -> None: ...

class RejectReason(_message.Message):
    __slots__ = ("code", "message", "broker_code", "local")
    CODE_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    BROKER_CODE_FIELD_NUMBER: _ClassVar[int]
    LOCAL_FIELD_NUMBER: _ClassVar[int]
    code: RejectCode
    message: str
    broker_code: str
    local: bool
    def __init__(self, code: _Optional[_Union[RejectCode, str]] = ..., message: _Optional[str] = ..., broker_code: _Optional[str] = ..., local: _Optional[bool] = ...) -> None: ...

class BrokerOrderUpdate(_message.Message):
    __slots__ = ("broker_account_id", "event", "order", "execution", "event_time", "received_at", "from_resync")
    BROKER_ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    EVENT_FIELD_NUMBER: _ClassVar[int]
    ORDER_FIELD_NUMBER: _ClassVar[int]
    EXECUTION_FIELD_NUMBER: _ClassVar[int]
    EVENT_TIME_FIELD_NUMBER: _ClassVar[int]
    RECEIVED_AT_FIELD_NUMBER: _ClassVar[int]
    FROM_RESYNC_FIELD_NUMBER: _ClassVar[int]
    broker_account_id: str
    event: str
    order: BrokerOrder
    execution: _orders_pb2.ExecutionReport
    event_time: int
    received_at: int
    from_resync: bool
    def __init__(self, broker_account_id: _Optional[str] = ..., event: _Optional[str] = ..., order: _Optional[_Union[BrokerOrder, _Mapping]] = ..., execution: _Optional[_Union[_orders_pb2.ExecutionReport, _Mapping]] = ..., event_time: _Optional[int] = ..., received_at: _Optional[int] = ..., from_resync: _Optional[bool] = ...) -> None: ...

class InstrumentInfo(_message.Message):
    __slots__ = ("instrument", "tradable", "shortable", "easy_to_borrow", "fractionable", "marginable", "min_tick", "lot_size", "status", "as_of")
    INSTRUMENT_FIELD_NUMBER: _ClassVar[int]
    TRADABLE_FIELD_NUMBER: _ClassVar[int]
    SHORTABLE_FIELD_NUMBER: _ClassVar[int]
    EASY_TO_BORROW_FIELD_NUMBER: _ClassVar[int]
    FRACTIONABLE_FIELD_NUMBER: _ClassVar[int]
    MARGINABLE_FIELD_NUMBER: _ClassVar[int]
    MIN_TICK_FIELD_NUMBER: _ClassVar[int]
    LOT_SIZE_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    AS_OF_FIELD_NUMBER: _ClassVar[int]
    instrument: _common_pb2.Instrument
    tradable: bool
    shortable: bool
    easy_to_borrow: bool
    fractionable: bool
    marginable: bool
    min_tick: _common_pb2.Decimal
    lot_size: _common_pb2.Decimal
    status: str
    as_of: int
    def __init__(self, instrument: _Optional[_Union[_common_pb2.Instrument, _Mapping]] = ..., tradable: _Optional[bool] = ..., shortable: _Optional[bool] = ..., easy_to_borrow: _Optional[bool] = ..., fractionable: _Optional[bool] = ..., marginable: _Optional[bool] = ..., min_tick: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., lot_size: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., status: _Optional[str] = ..., as_of: _Optional[int] = ...) -> None: ...

class ListAccountsRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class ListAccountsResponse(_message.Message):
    __slots__ = ("accounts",)
    ACCOUNTS_FIELD_NUMBER: _ClassVar[int]
    accounts: _containers.RepeatedCompositeFieldContainer[BrokerAccount]
    def __init__(self, accounts: _Optional[_Iterable[_Union[BrokerAccount, _Mapping]]] = ...) -> None: ...

class GetSessionStatusRequest(_message.Message):
    __slots__ = ("broker_account_id",)
    BROKER_ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    broker_account_id: str
    def __init__(self, broker_account_id: _Optional[str] = ...) -> None: ...

class GetSessionStatusResponse(_message.Message):
    __slots__ = ("status",)
    STATUS_FIELD_NUMBER: _ClassVar[int]
    status: SessionStatus
    def __init__(self, status: _Optional[_Union[SessionStatus, _Mapping]] = ...) -> None: ...

class GetAccountSnapshotRequest(_message.Message):
    __slots__ = ("broker_account_id",)
    BROKER_ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    broker_account_id: str
    def __init__(self, broker_account_id: _Optional[str] = ...) -> None: ...

class GetAccountSnapshotResponse(_message.Message):
    __slots__ = ("snapshot",)
    SNAPSHOT_FIELD_NUMBER: _ClassVar[int]
    snapshot: _ledger_pb2.AccountSnapshot
    def __init__(self, snapshot: _Optional[_Union[_ledger_pb2.AccountSnapshot, _Mapping]] = ...) -> None: ...

class StreamAccountSnapshotsRequest(_message.Message):
    __slots__ = ("broker_account_id",)
    BROKER_ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    broker_account_id: str
    def __init__(self, broker_account_id: _Optional[str] = ...) -> None: ...

class StreamAccountSnapshotsResponse(_message.Message):
    __slots__ = ("snapshot",)
    SNAPSHOT_FIELD_NUMBER: _ClassVar[int]
    snapshot: _ledger_pb2.AccountSnapshot
    def __init__(self, snapshot: _Optional[_Union[_ledger_pb2.AccountSnapshot, _Mapping]] = ...) -> None: ...

class SubmitOrderRequest(_message.Message):
    __slots__ = ("order",)
    ORDER_FIELD_NUMBER: _ClassVar[int]
    order: BrokerOrderRequest
    def __init__(self, order: _Optional[_Union[BrokerOrderRequest, _Mapping]] = ...) -> None: ...

class SubmitOrderResponse(_message.Message):
    __slots__ = ("outcome", "order", "reject", "recovered_by_lookup", "accepted_at")
    OUTCOME_FIELD_NUMBER: _ClassVar[int]
    ORDER_FIELD_NUMBER: _ClassVar[int]
    REJECT_FIELD_NUMBER: _ClassVar[int]
    RECOVERED_BY_LOOKUP_FIELD_NUMBER: _ClassVar[int]
    ACCEPTED_AT_FIELD_NUMBER: _ClassVar[int]
    outcome: SubmitOutcome
    order: BrokerOrder
    reject: RejectReason
    recovered_by_lookup: bool
    accepted_at: int
    def __init__(self, outcome: _Optional[_Union[SubmitOutcome, str]] = ..., order: _Optional[_Union[BrokerOrder, _Mapping]] = ..., reject: _Optional[_Union[RejectReason, _Mapping]] = ..., recovered_by_lookup: _Optional[bool] = ..., accepted_at: _Optional[int] = ...) -> None: ...

class CancelOrderRequest(_message.Message):
    __slots__ = ("broker_account_id", "client_order_id", "broker_order_id", "correlation_id")
    BROKER_ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    CLIENT_ORDER_ID_FIELD_NUMBER: _ClassVar[int]
    BROKER_ORDER_ID_FIELD_NUMBER: _ClassVar[int]
    CORRELATION_ID_FIELD_NUMBER: _ClassVar[int]
    broker_account_id: str
    client_order_id: str
    broker_order_id: str
    correlation_id: str
    def __init__(self, broker_account_id: _Optional[str] = ..., client_order_id: _Optional[str] = ..., broker_order_id: _Optional[str] = ..., correlation_id: _Optional[str] = ...) -> None: ...

class CancelOrderResponse(_message.Message):
    __slots__ = ("outcome", "order", "reject")
    OUTCOME_FIELD_NUMBER: _ClassVar[int]
    ORDER_FIELD_NUMBER: _ClassVar[int]
    REJECT_FIELD_NUMBER: _ClassVar[int]
    outcome: CancelOutcome
    order: BrokerOrder
    reject: RejectReason
    def __init__(self, outcome: _Optional[_Union[CancelOutcome, str]] = ..., order: _Optional[_Union[BrokerOrder, _Mapping]] = ..., reject: _Optional[_Union[RejectReason, _Mapping]] = ...) -> None: ...

class ReplaceOrderRequest(_message.Message):
    __slots__ = ("broker_account_id", "client_order_id", "new_client_order_id", "qty", "limit_price", "stop_price", "tif", "trail_price", "correlation_id")
    BROKER_ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    CLIENT_ORDER_ID_FIELD_NUMBER: _ClassVar[int]
    NEW_CLIENT_ORDER_ID_FIELD_NUMBER: _ClassVar[int]
    QTY_FIELD_NUMBER: _ClassVar[int]
    LIMIT_PRICE_FIELD_NUMBER: _ClassVar[int]
    STOP_PRICE_FIELD_NUMBER: _ClassVar[int]
    TIF_FIELD_NUMBER: _ClassVar[int]
    TRAIL_PRICE_FIELD_NUMBER: _ClassVar[int]
    CORRELATION_ID_FIELD_NUMBER: _ClassVar[int]
    broker_account_id: str
    client_order_id: str
    new_client_order_id: str
    qty: _common_pb2.Decimal
    limit_price: _common_pb2.Decimal
    stop_price: _common_pb2.Decimal
    tif: _orders_pb2.TimeInForce
    trail_price: _common_pb2.Decimal
    correlation_id: str
    def __init__(self, broker_account_id: _Optional[str] = ..., client_order_id: _Optional[str] = ..., new_client_order_id: _Optional[str] = ..., qty: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., limit_price: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., stop_price: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., tif: _Optional[_Union[_orders_pb2.TimeInForce, str]] = ..., trail_price: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., correlation_id: _Optional[str] = ...) -> None: ...

class ReplaceOrderResponse(_message.Message):
    __slots__ = ("outcome", "order", "reject")
    OUTCOME_FIELD_NUMBER: _ClassVar[int]
    ORDER_FIELD_NUMBER: _ClassVar[int]
    REJECT_FIELD_NUMBER: _ClassVar[int]
    outcome: ReplaceOutcome
    order: BrokerOrder
    reject: RejectReason
    def __init__(self, outcome: _Optional[_Union[ReplaceOutcome, str]] = ..., order: _Optional[_Union[BrokerOrder, _Mapping]] = ..., reject: _Optional[_Union[RejectReason, _Mapping]] = ...) -> None: ...

class GetOrderRequest(_message.Message):
    __slots__ = ("broker_account_id", "client_order_id", "broker_order_id")
    BROKER_ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    CLIENT_ORDER_ID_FIELD_NUMBER: _ClassVar[int]
    BROKER_ORDER_ID_FIELD_NUMBER: _ClassVar[int]
    broker_account_id: str
    client_order_id: str
    broker_order_id: str
    def __init__(self, broker_account_id: _Optional[str] = ..., client_order_id: _Optional[str] = ..., broker_order_id: _Optional[str] = ...) -> None: ...

class GetOrderResponse(_message.Message):
    __slots__ = ("found", "order")
    FOUND_FIELD_NUMBER: _ClassVar[int]
    ORDER_FIELD_NUMBER: _ClassVar[int]
    found: bool
    order: BrokerOrder
    def __init__(self, found: _Optional[bool] = ..., order: _Optional[_Union[BrokerOrder, _Mapping]] = ...) -> None: ...

class ListOpenOrdersRequest(_message.Message):
    __slots__ = ("broker_account_id",)
    BROKER_ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    broker_account_id: str
    def __init__(self, broker_account_id: _Optional[str] = ...) -> None: ...

class ListOpenOrdersResponse(_message.Message):
    __slots__ = ("orders",)
    ORDERS_FIELD_NUMBER: _ClassVar[int]
    orders: _containers.RepeatedCompositeFieldContainer[BrokerOrder]
    def __init__(self, orders: _Optional[_Iterable[_Union[BrokerOrder, _Mapping]]] = ...) -> None: ...

class ListOrdersRequest(_message.Message):
    __slots__ = ("broker_account_id", "since")
    BROKER_ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    SINCE_FIELD_NUMBER: _ClassVar[int]
    broker_account_id: str
    since: int
    def __init__(self, broker_account_id: _Optional[str] = ..., since: _Optional[int] = ...) -> None: ...

class ListOrdersResponse(_message.Message):
    __slots__ = ("orders",)
    ORDERS_FIELD_NUMBER: _ClassVar[int]
    orders: _containers.RepeatedCompositeFieldContainer[BrokerOrder]
    def __init__(self, orders: _Optional[_Iterable[_Union[BrokerOrder, _Mapping]]] = ...) -> None: ...

class ListExecutionsRequest(_message.Message):
    __slots__ = ("broker_account_id", "since")
    BROKER_ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    SINCE_FIELD_NUMBER: _ClassVar[int]
    broker_account_id: str
    since: int
    def __init__(self, broker_account_id: _Optional[str] = ..., since: _Optional[int] = ...) -> None: ...

class ListExecutionsResponse(_message.Message):
    __slots__ = ("executions",)
    EXECUTIONS_FIELD_NUMBER: _ClassVar[int]
    executions: _containers.RepeatedCompositeFieldContainer[_orders_pb2.ExecutionReport]
    def __init__(self, executions: _Optional[_Iterable[_Union[_orders_pb2.ExecutionReport, _Mapping]]] = ...) -> None: ...

class StreamOrderUpdatesRequest(_message.Message):
    __slots__ = ("broker_account_id",)
    BROKER_ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    broker_account_id: str
    def __init__(self, broker_account_id: _Optional[str] = ...) -> None: ...

class StreamOrderUpdatesResponse(_message.Message):
    __slots__ = ("update",)
    UPDATE_FIELD_NUMBER: _ClassVar[int]
    update: BrokerOrderUpdate
    def __init__(self, update: _Optional[_Union[BrokerOrderUpdate, _Mapping]] = ...) -> None: ...

class StreamExecutionsRequest(_message.Message):
    __slots__ = ("broker_account_id",)
    BROKER_ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    broker_account_id: str
    def __init__(self, broker_account_id: _Optional[str] = ...) -> None: ...

class StreamExecutionsResponse(_message.Message):
    __slots__ = ("execution",)
    EXECUTION_FIELD_NUMBER: _ClassVar[int]
    execution: _orders_pb2.ExecutionReport
    def __init__(self, execution: _Optional[_Union[_orders_pb2.ExecutionReport, _Mapping]] = ...) -> None: ...

class GetInstrumentInfoRequest(_message.Message):
    __slots__ = ("broker_account_id", "instrument", "symbol")
    BROKER_ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    INSTRUMENT_FIELD_NUMBER: _ClassVar[int]
    SYMBOL_FIELD_NUMBER: _ClassVar[int]
    broker_account_id: str
    instrument: _common_pb2.Instrument
    symbol: str
    def __init__(self, broker_account_id: _Optional[str] = ..., instrument: _Optional[_Union[_common_pb2.Instrument, _Mapping]] = ..., symbol: _Optional[str] = ...) -> None: ...

class GetInstrumentInfoResponse(_message.Message):
    __slots__ = ("info",)
    INFO_FIELD_NUMBER: _ClassVar[int]
    info: InstrumentInfo
    def __init__(self, info: _Optional[_Union[InstrumentInfo, _Mapping]] = ...) -> None: ...
