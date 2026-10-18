from tb.common.v1 import common_pb2 as _common_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class ReconciliationStatus(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    RECONCILIATION_STATUS_UNSPECIFIED: _ClassVar[ReconciliationStatus]
    RECONCILIATION_STATUS_OK: _ClassVar[ReconciliationStatus]
    RECONCILIATION_STATUS_BREAK: _ClassVar[ReconciliationStatus]
    RECONCILIATION_STATUS_STALE: _ClassVar[ReconciliationStatus]
RECONCILIATION_STATUS_UNSPECIFIED: ReconciliationStatus
RECONCILIATION_STATUS_OK: ReconciliationStatus
RECONCILIATION_STATUS_BREAK: ReconciliationStatus
RECONCILIATION_STATUS_STALE: ReconciliationStatus

class Position(_message.Message):
    __slots__ = ("instrument_id", "qty", "avg_cost", "symbol", "market_value", "broker_instrument_id")
    INSTRUMENT_ID_FIELD_NUMBER: _ClassVar[int]
    QTY_FIELD_NUMBER: _ClassVar[int]
    AVG_COST_FIELD_NUMBER: _ClassVar[int]
    SYMBOL_FIELD_NUMBER: _ClassVar[int]
    MARKET_VALUE_FIELD_NUMBER: _ClassVar[int]
    BROKER_INSTRUMENT_ID_FIELD_NUMBER: _ClassVar[int]
    instrument_id: str
    qty: _common_pb2.Decimal
    avg_cost: _common_pb2.Decimal
    symbol: str
    market_value: _common_pb2.Decimal
    broker_instrument_id: str
    def __init__(self, instrument_id: _Optional[str] = ..., qty: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., avg_cost: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., symbol: _Optional[str] = ..., market_value: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., broker_instrument_id: _Optional[str] = ...) -> None: ...

class AccountSnapshot(_message.Message):
    __slots__ = ("broker_account_id", "broker", "mode", "cash", "buying_power", "equity", "positions", "as_of", "intraday_margin_deficit", "margin_call", "initial_margin", "maintenance_margin", "long_market_value", "short_market_value", "currency", "day_trade_count", "trading_blocked", "account_blocked", "status")
    BROKER_ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    BROKER_FIELD_NUMBER: _ClassVar[int]
    MODE_FIELD_NUMBER: _ClassVar[int]
    CASH_FIELD_NUMBER: _ClassVar[int]
    BUYING_POWER_FIELD_NUMBER: _ClassVar[int]
    EQUITY_FIELD_NUMBER: _ClassVar[int]
    POSITIONS_FIELD_NUMBER: _ClassVar[int]
    AS_OF_FIELD_NUMBER: _ClassVar[int]
    INTRADAY_MARGIN_DEFICIT_FIELD_NUMBER: _ClassVar[int]
    MARGIN_CALL_FIELD_NUMBER: _ClassVar[int]
    INITIAL_MARGIN_FIELD_NUMBER: _ClassVar[int]
    MAINTENANCE_MARGIN_FIELD_NUMBER: _ClassVar[int]
    LONG_MARKET_VALUE_FIELD_NUMBER: _ClassVar[int]
    SHORT_MARKET_VALUE_FIELD_NUMBER: _ClassVar[int]
    CURRENCY_FIELD_NUMBER: _ClassVar[int]
    DAY_TRADE_COUNT_FIELD_NUMBER: _ClassVar[int]
    TRADING_BLOCKED_FIELD_NUMBER: _ClassVar[int]
    ACCOUNT_BLOCKED_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    broker_account_id: str
    broker: _common_pb2.Broker
    mode: _common_pb2.Mode
    cash: _common_pb2.Decimal
    buying_power: _common_pb2.Decimal
    equity: _common_pb2.Decimal
    positions: _containers.RepeatedCompositeFieldContainer[Position]
    as_of: int
    intraday_margin_deficit: bool
    margin_call: bool
    initial_margin: _common_pb2.Decimal
    maintenance_margin: _common_pb2.Decimal
    long_market_value: _common_pb2.Decimal
    short_market_value: _common_pb2.Decimal
    currency: str
    day_trade_count: int
    trading_blocked: bool
    account_blocked: bool
    status: str
    def __init__(self, broker_account_id: _Optional[str] = ..., broker: _Optional[_Union[_common_pb2.Broker, str]] = ..., mode: _Optional[_Union[_common_pb2.Mode, str]] = ..., cash: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., buying_power: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., equity: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., positions: _Optional[_Iterable[_Union[Position, _Mapping]]] = ..., as_of: _Optional[int] = ..., intraday_margin_deficit: _Optional[bool] = ..., margin_call: _Optional[bool] = ..., initial_margin: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., maintenance_margin: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., long_market_value: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., short_market_value: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., currency: _Optional[str] = ..., day_trade_count: _Optional[int] = ..., trading_blocked: _Optional[bool] = ..., account_blocked: _Optional[bool] = ..., status: _Optional[str] = ...) -> None: ...

class StrategyPosition(_message.Message):
    __slots__ = ("broker_account_id", "owner", "instrument_id", "qty", "cost_basis", "realized_pnl", "as_of")
    BROKER_ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    OWNER_FIELD_NUMBER: _ClassVar[int]
    INSTRUMENT_ID_FIELD_NUMBER: _ClassVar[int]
    QTY_FIELD_NUMBER: _ClassVar[int]
    COST_BASIS_FIELD_NUMBER: _ClassVar[int]
    REALIZED_PNL_FIELD_NUMBER: _ClassVar[int]
    AS_OF_FIELD_NUMBER: _ClassVar[int]
    broker_account_id: str
    owner: str
    instrument_id: str
    qty: _common_pb2.Decimal
    cost_basis: _common_pb2.Decimal
    realized_pnl: _common_pb2.Decimal
    as_of: int
    def __init__(self, broker_account_id: _Optional[str] = ..., owner: _Optional[str] = ..., instrument_id: _Optional[str] = ..., qty: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., cost_basis: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., realized_pnl: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., as_of: _Optional[int] = ...) -> None: ...

class LedgerEntry(_message.Message):
    __slots__ = ("entry_id", "broker_account_id", "owner", "instrument_id", "lot_id", "qty", "cost", "realized_pnl", "source_exec_id", "recorded_at")
    ENTRY_ID_FIELD_NUMBER: _ClassVar[int]
    BROKER_ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    OWNER_FIELD_NUMBER: _ClassVar[int]
    INSTRUMENT_ID_FIELD_NUMBER: _ClassVar[int]
    LOT_ID_FIELD_NUMBER: _ClassVar[int]
    QTY_FIELD_NUMBER: _ClassVar[int]
    COST_FIELD_NUMBER: _ClassVar[int]
    REALIZED_PNL_FIELD_NUMBER: _ClassVar[int]
    SOURCE_EXEC_ID_FIELD_NUMBER: _ClassVar[int]
    RECORDED_AT_FIELD_NUMBER: _ClassVar[int]
    entry_id: str
    broker_account_id: str
    owner: str
    instrument_id: str
    lot_id: str
    qty: _common_pb2.Decimal
    cost: _common_pb2.Decimal
    realized_pnl: _common_pb2.Decimal
    source_exec_id: str
    recorded_at: int
    def __init__(self, entry_id: _Optional[str] = ..., broker_account_id: _Optional[str] = ..., owner: _Optional[str] = ..., instrument_id: _Optional[str] = ..., lot_id: _Optional[str] = ..., qty: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., cost: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., realized_pnl: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., source_exec_id: _Optional[str] = ..., recorded_at: _Optional[int] = ...) -> None: ...

class OwnerQty(_message.Message):
    __slots__ = ("owner", "qty")
    OWNER_FIELD_NUMBER: _ClassVar[int]
    QTY_FIELD_NUMBER: _ClassVar[int]
    owner: str
    qty: _common_pb2.Decimal
    def __init__(self, owner: _Optional[str] = ..., qty: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ...) -> None: ...

class ReconciliationDiff(_message.Message):
    __slots__ = ("instrument_id", "broker_qty", "ledger_qty", "owner_breakdown")
    INSTRUMENT_ID_FIELD_NUMBER: _ClassVar[int]
    BROKER_QTY_FIELD_NUMBER: _ClassVar[int]
    LEDGER_QTY_FIELD_NUMBER: _ClassVar[int]
    OWNER_BREAKDOWN_FIELD_NUMBER: _ClassVar[int]
    instrument_id: str
    broker_qty: _common_pb2.Decimal
    ledger_qty: _common_pb2.Decimal
    owner_breakdown: _containers.RepeatedCompositeFieldContainer[OwnerQty]
    def __init__(self, instrument_id: _Optional[str] = ..., broker_qty: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., ledger_qty: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., owner_breakdown: _Optional[_Iterable[_Union[OwnerQty, _Mapping]]] = ...) -> None: ...

class ReconciliationRequest(_message.Message):
    __slots__ = ("broker_account_id", "reason", "requested_at", "correlation_id")
    BROKER_ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    REQUESTED_AT_FIELD_NUMBER: _ClassVar[int]
    CORRELATION_ID_FIELD_NUMBER: _ClassVar[int]
    broker_account_id: str
    reason: str
    requested_at: int
    correlation_id: str
    def __init__(self, broker_account_id: _Optional[str] = ..., reason: _Optional[str] = ..., requested_at: _Optional[int] = ..., correlation_id: _Optional[str] = ...) -> None: ...

class ReconciliationResult(_message.Message):
    __slots__ = ("broker_account_id", "as_of", "status", "diffs")
    BROKER_ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    AS_OF_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    DIFFS_FIELD_NUMBER: _ClassVar[int]
    broker_account_id: str
    as_of: int
    status: ReconciliationStatus
    diffs: _containers.RepeatedCompositeFieldContainer[ReconciliationDiff]
    def __init__(self, broker_account_id: _Optional[str] = ..., as_of: _Optional[int] = ..., status: _Optional[_Union[ReconciliationStatus, str]] = ..., diffs: _Optional[_Iterable[_Union[ReconciliationDiff, _Mapping]]] = ...) -> None: ...
