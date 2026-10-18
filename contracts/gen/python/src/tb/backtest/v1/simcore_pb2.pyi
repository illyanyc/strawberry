from tb.broker.v1 import gateway_pb2 as _gateway_pb2
from tb.common.v1 import common_pb2 as _common_pb2
from tb.ledger.v1 import ledger_pb2 as _ledger_pb2
from tb.ledger.v1 import ledger_service_pb2 as _ledger_service_pb2
from tb.orders.v1 import execution_pb2 as _execution_pb2
from tb.orders.v1 import orders_pb2 as _orders_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class SimAccount(_message.Message):
    __slots__ = ("broker_account_id", "broker", "cash", "supports_bracket", "supports_fractional", "margin_multiple", "margin_util_cap", "pdt_guard", "pdt_max", "segregation", "opening_positions")
    BROKER_ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    BROKER_FIELD_NUMBER: _ClassVar[int]
    CASH_FIELD_NUMBER: _ClassVar[int]
    SUPPORTS_BRACKET_FIELD_NUMBER: _ClassVar[int]
    SUPPORTS_FRACTIONAL_FIELD_NUMBER: _ClassVar[int]
    MARGIN_MULTIPLE_FIELD_NUMBER: _ClassVar[int]
    MARGIN_UTIL_CAP_FIELD_NUMBER: _ClassVar[int]
    PDT_GUARD_FIELD_NUMBER: _ClassVar[int]
    PDT_MAX_FIELD_NUMBER: _ClassVar[int]
    SEGREGATION_FIELD_NUMBER: _ClassVar[int]
    OPENING_POSITIONS_FIELD_NUMBER: _ClassVar[int]
    broker_account_id: str
    broker: _common_pb2.Broker
    cash: _common_pb2.Decimal
    supports_bracket: bool
    supports_fractional: bool
    margin_multiple: _common_pb2.Decimal
    margin_util_cap: _common_pb2.Decimal
    pdt_guard: str
    pdt_max: int
    segregation: bool
    opening_positions: _containers.RepeatedCompositeFieldContainer[_ledger_pb2.Position]
    def __init__(self, broker_account_id: _Optional[str] = ..., broker: _Optional[_Union[_common_pb2.Broker, str]] = ..., cash: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., supports_bracket: _Optional[bool] = ..., supports_fractional: _Optional[bool] = ..., margin_multiple: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., margin_util_cap: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., pdt_guard: _Optional[str] = ..., pdt_max: _Optional[int] = ..., segregation: _Optional[bool] = ..., opening_positions: _Optional[_Iterable[_Union[_ledger_pb2.Position, _Mapping]]] = ...) -> None: ...

class SimRoute(_message.Message):
    __slots__ = ("horizon", "broker_account_ids")
    HORIZON_FIELD_NUMBER: _ClassVar[int]
    BROKER_ACCOUNT_IDS_FIELD_NUMBER: _ClassVar[int]
    horizon: _common_pb2.Horizon
    broker_account_ids: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, horizon: _Optional[_Union[_common_pb2.Horizon, str]] = ..., broker_account_ids: _Optional[_Iterable[str]] = ...) -> None: ...

class SimInstrument(_message.Message):
    __slots__ = ("instrument_id", "symbol", "tradable", "shortable")
    INSTRUMENT_ID_FIELD_NUMBER: _ClassVar[int]
    SYMBOL_FIELD_NUMBER: _ClassVar[int]
    TRADABLE_FIELD_NUMBER: _ClassVar[int]
    SHORTABLE_FIELD_NUMBER: _ClassVar[int]
    instrument_id: str
    symbol: str
    tradable: bool
    shortable: bool
    def __init__(self, instrument_id: _Optional[str] = ..., symbol: _Optional[str] = ..., tradable: _Optional[bool] = ..., shortable: _Optional[bool] = ...) -> None: ...

class SimSetup(_message.Message):
    __slots__ = ("accounts", "registry_json", "routing", "global_limits", "instruments", "entry_collar_bps", "exit_collar_bps", "flatten_before_close_ns", "start")
    ACCOUNTS_FIELD_NUMBER: _ClassVar[int]
    REGISTRY_JSON_FIELD_NUMBER: _ClassVar[int]
    ROUTING_FIELD_NUMBER: _ClassVar[int]
    GLOBAL_LIMITS_FIELD_NUMBER: _ClassVar[int]
    INSTRUMENTS_FIELD_NUMBER: _ClassVar[int]
    ENTRY_COLLAR_BPS_FIELD_NUMBER: _ClassVar[int]
    EXIT_COLLAR_BPS_FIELD_NUMBER: _ClassVar[int]
    FLATTEN_BEFORE_CLOSE_NS_FIELD_NUMBER: _ClassVar[int]
    START_FIELD_NUMBER: _ClassVar[int]
    accounts: _containers.RepeatedCompositeFieldContainer[SimAccount]
    registry_json: bytes
    routing: _containers.RepeatedCompositeFieldContainer[SimRoute]
    global_limits: _execution_pb2.RiskLimits
    instruments: _containers.RepeatedCompositeFieldContainer[SimInstrument]
    entry_collar_bps: int
    exit_collar_bps: int
    flatten_before_close_ns: int
    start: int
    def __init__(self, accounts: _Optional[_Iterable[_Union[SimAccount, _Mapping]]] = ..., registry_json: _Optional[bytes] = ..., routing: _Optional[_Iterable[_Union[SimRoute, _Mapping]]] = ..., global_limits: _Optional[_Union[_execution_pb2.RiskLimits, _Mapping]] = ..., instruments: _Optional[_Iterable[_Union[SimInstrument, _Mapping]]] = ..., entry_collar_bps: _Optional[int] = ..., exit_collar_bps: _Optional[int] = ..., flatten_before_close_ns: _Optional[int] = ..., start: _Optional[int] = ...) -> None: ...

class SimBar(_message.Message):
    __slots__ = ("instrument_id", "close", "ts_close", "interval_ns")
    INSTRUMENT_ID_FIELD_NUMBER: _ClassVar[int]
    CLOSE_FIELD_NUMBER: _ClassVar[int]
    TS_CLOSE_FIELD_NUMBER: _ClassVar[int]
    INTERVAL_NS_FIELD_NUMBER: _ClassVar[int]
    instrument_id: str
    close: _common_pb2.Decimal
    ts_close: int
    interval_ns: int
    def __init__(self, instrument_id: _Optional[str] = ..., close: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., ts_close: _Optional[int] = ..., interval_ns: _Optional[int] = ...) -> None: ...

class SimAdvance(_message.Message):
    __slots__ = ("now", "bars")
    NOW_FIELD_NUMBER: _ClassVar[int]
    BARS_FIELD_NUMBER: _ClassVar[int]
    now: int
    bars: _containers.RepeatedCompositeFieldContainer[SimBar]
    def __init__(self, now: _Optional[int] = ..., bars: _Optional[_Iterable[_Union[SimBar, _Mapping]]] = ...) -> None: ...

class SimSubmit(_message.Message):
    __slots__ = ("now", "intent", "bars")
    NOW_FIELD_NUMBER: _ClassVar[int]
    INTENT_FIELD_NUMBER: _ClassVar[int]
    BARS_FIELD_NUMBER: _ClassVar[int]
    now: int
    intent: _orders_pb2.OrderIntent
    bars: _containers.RepeatedCompositeFieldContainer[SimBar]
    def __init__(self, now: _Optional[int] = ..., intent: _Optional[_Union[_orders_pb2.OrderIntent, _Mapping]] = ..., bars: _Optional[_Iterable[_Union[SimBar, _Mapping]]] = ...) -> None: ...

class SimFill(_message.Message):
    __slots__ = ("now", "broker_account_id", "broker_order_id", "qty", "price", "fees", "exec_id")
    NOW_FIELD_NUMBER: _ClassVar[int]
    BROKER_ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    BROKER_ORDER_ID_FIELD_NUMBER: _ClassVar[int]
    QTY_FIELD_NUMBER: _ClassVar[int]
    PRICE_FIELD_NUMBER: _ClassVar[int]
    FEES_FIELD_NUMBER: _ClassVar[int]
    EXEC_ID_FIELD_NUMBER: _ClassVar[int]
    now: int
    broker_account_id: str
    broker_order_id: str
    qty: _common_pb2.Decimal
    price: _common_pb2.Decimal
    fees: _common_pb2.Decimal
    exec_id: str
    def __init__(self, now: _Optional[int] = ..., broker_account_id: _Optional[str] = ..., broker_order_id: _Optional[str] = ..., qty: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., price: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., fees: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., exec_id: _Optional[str] = ...) -> None: ...

class SimOrderEnd(_message.Message):
    __slots__ = ("now", "broker_account_id", "broker_order_id", "state")
    NOW_FIELD_NUMBER: _ClassVar[int]
    BROKER_ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    BROKER_ORDER_ID_FIELD_NUMBER: _ClassVar[int]
    STATE_FIELD_NUMBER: _ClassVar[int]
    now: int
    broker_account_id: str
    broker_order_id: str
    state: _orders_pb2.OrderState
    def __init__(self, now: _Optional[int] = ..., broker_account_id: _Optional[str] = ..., broker_order_id: _Optional[str] = ..., state: _Optional[_Union[_orders_pb2.OrderState, str]] = ...) -> None: ...

class SimManualFill(_message.Message):
    __slots__ = ("now", "broker_account_id", "instrument_id", "side", "qty", "price", "fees", "exec_id")
    NOW_FIELD_NUMBER: _ClassVar[int]
    BROKER_ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    INSTRUMENT_ID_FIELD_NUMBER: _ClassVar[int]
    SIDE_FIELD_NUMBER: _ClassVar[int]
    QTY_FIELD_NUMBER: _ClassVar[int]
    PRICE_FIELD_NUMBER: _ClassVar[int]
    FEES_FIELD_NUMBER: _ClassVar[int]
    EXEC_ID_FIELD_NUMBER: _ClassVar[int]
    now: int
    broker_account_id: str
    instrument_id: str
    side: _common_pb2.Side
    qty: _common_pb2.Decimal
    price: _common_pb2.Decimal
    fees: _common_pb2.Decimal
    exec_id: str
    def __init__(self, now: _Optional[int] = ..., broker_account_id: _Optional[str] = ..., instrument_id: _Optional[str] = ..., side: _Optional[_Union[_common_pb2.Side, str]] = ..., qty: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., price: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., fees: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., exec_id: _Optional[str] = ...) -> None: ...

class SimCorporateAction(_message.Message):
    __slots__ = ("now", "instrument_id", "kind", "value", "source_id")
    NOW_FIELD_NUMBER: _ClassVar[int]
    INSTRUMENT_ID_FIELD_NUMBER: _ClassVar[int]
    KIND_FIELD_NUMBER: _ClassVar[int]
    VALUE_FIELD_NUMBER: _ClassVar[int]
    SOURCE_ID_FIELD_NUMBER: _ClassVar[int]
    now: int
    instrument_id: str
    kind: str
    value: _common_pb2.Decimal
    source_id: str
    def __init__(self, now: _Optional[int] = ..., instrument_id: _Optional[str] = ..., kind: _Optional[str] = ..., value: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., source_id: _Optional[str] = ...) -> None: ...

class SimQuery(_message.Message):
    __slots__ = ("owner", "broker_account_id")
    OWNER_FIELD_NUMBER: _ClassVar[int]
    BROKER_ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    owner: str
    broker_account_id: str
    def __init__(self, owner: _Optional[str] = ..., broker_account_id: _Optional[str] = ...) -> None: ...

class SimRequest(_message.Message):
    __slots__ = ("setup", "advance", "submit", "fill", "order_end", "manual_fill", "corporate_action", "query")
    SETUP_FIELD_NUMBER: _ClassVar[int]
    ADVANCE_FIELD_NUMBER: _ClassVar[int]
    SUBMIT_FIELD_NUMBER: _ClassVar[int]
    FILL_FIELD_NUMBER: _ClassVar[int]
    ORDER_END_FIELD_NUMBER: _ClassVar[int]
    MANUAL_FILL_FIELD_NUMBER: _ClassVar[int]
    CORPORATE_ACTION_FIELD_NUMBER: _ClassVar[int]
    QUERY_FIELD_NUMBER: _ClassVar[int]
    setup: SimSetup
    advance: SimAdvance
    submit: SimSubmit
    fill: SimFill
    order_end: SimOrderEnd
    manual_fill: SimManualFill
    corporate_action: SimCorporateAction
    query: SimQuery
    def __init__(self, setup: _Optional[_Union[SimSetup, _Mapping]] = ..., advance: _Optional[_Union[SimAdvance, _Mapping]] = ..., submit: _Optional[_Union[SimSubmit, _Mapping]] = ..., fill: _Optional[_Union[SimFill, _Mapping]] = ..., order_end: _Optional[_Union[SimOrderEnd, _Mapping]] = ..., manual_fill: _Optional[_Union[SimManualFill, _Mapping]] = ..., corporate_action: _Optional[_Union[SimCorporateAction, _Mapping]] = ..., query: _Optional[_Union[SimQuery, _Mapping]] = ...) -> None: ...

class SimResponse(_message.Message):
    __slots__ = ("error", "orders", "decision", "ledger_updates", "portfolio", "intents")
    ERROR_FIELD_NUMBER: _ClassVar[int]
    ORDERS_FIELD_NUMBER: _ClassVar[int]
    DECISION_FIELD_NUMBER: _ClassVar[int]
    LEDGER_UPDATES_FIELD_NUMBER: _ClassVar[int]
    PORTFOLIO_FIELD_NUMBER: _ClassVar[int]
    INTENTS_FIELD_NUMBER: _ClassVar[int]
    error: str
    orders: _containers.RepeatedCompositeFieldContainer[_gateway_pb2.BrokerOrder]
    decision: _orders_pb2.RiskDecision
    ledger_updates: _containers.RepeatedCompositeFieldContainer[_ledger_pb2.LedgerUpdate]
    portfolio: _ledger_service_pb2.OwnerView
    intents: _containers.RepeatedCompositeFieldContainer[_execution_pb2.IntentRecord]
    def __init__(self, error: _Optional[str] = ..., orders: _Optional[_Iterable[_Union[_gateway_pb2.BrokerOrder, _Mapping]]] = ..., decision: _Optional[_Union[_orders_pb2.RiskDecision, _Mapping]] = ..., ledger_updates: _Optional[_Iterable[_Union[_ledger_pb2.LedgerUpdate, _Mapping]]] = ..., portfolio: _Optional[_Union[_ledger_service_pb2.OwnerView, _Mapping]] = ..., intents: _Optional[_Iterable[_Union[_execution_pb2.IntentRecord, _Mapping]]] = ...) -> None: ...

class RecordedMessage(_message.Message):
    __slots__ = ("subject", "data", "msg_id", "recorded_at", "stream_sequence")
    SUBJECT_FIELD_NUMBER: _ClassVar[int]
    DATA_FIELD_NUMBER: _ClassVar[int]
    MSG_ID_FIELD_NUMBER: _ClassVar[int]
    RECORDED_AT_FIELD_NUMBER: _ClassVar[int]
    STREAM_SEQUENCE_FIELD_NUMBER: _ClassVar[int]
    subject: str
    data: bytes
    msg_id: str
    recorded_at: int
    stream_sequence: int
    def __init__(self, subject: _Optional[str] = ..., data: _Optional[bytes] = ..., msg_id: _Optional[str] = ..., recorded_at: _Optional[int] = ..., stream_sequence: _Optional[int] = ...) -> None: ...
