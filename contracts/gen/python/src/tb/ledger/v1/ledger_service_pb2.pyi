from tb.common.v1 import common_pb2 as _common_pb2
from tb.ledger.v1 import ledger_pb2 as _ledger_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class Lot(_message.Message):
    __slots__ = ("lot_id", "broker_account_id", "owner", "instrument_id", "qty", "cost", "opened_at", "source_id")
    LOT_ID_FIELD_NUMBER: _ClassVar[int]
    BROKER_ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    OWNER_FIELD_NUMBER: _ClassVar[int]
    INSTRUMENT_ID_FIELD_NUMBER: _ClassVar[int]
    QTY_FIELD_NUMBER: _ClassVar[int]
    COST_FIELD_NUMBER: _ClassVar[int]
    OPENED_AT_FIELD_NUMBER: _ClassVar[int]
    SOURCE_ID_FIELD_NUMBER: _ClassVar[int]
    lot_id: str
    broker_account_id: str
    owner: str
    instrument_id: str
    qty: _common_pb2.Decimal
    cost: _common_pb2.Decimal
    opened_at: int
    source_id: str
    def __init__(self, lot_id: _Optional[str] = ..., broker_account_id: _Optional[str] = ..., owner: _Optional[str] = ..., instrument_id: _Optional[str] = ..., qty: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., cost: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., opened_at: _Optional[int] = ..., source_id: _Optional[str] = ...) -> None: ...

class OwnerPosition(_message.Message):
    __slots__ = ("instrument_id", "symbol", "qty", "cost_basis", "avg_cost", "mark_price", "market_value", "unrealized_pnl", "realized_pnl", "lots", "halted")
    INSTRUMENT_ID_FIELD_NUMBER: _ClassVar[int]
    SYMBOL_FIELD_NUMBER: _ClassVar[int]
    QTY_FIELD_NUMBER: _ClassVar[int]
    COST_BASIS_FIELD_NUMBER: _ClassVar[int]
    AVG_COST_FIELD_NUMBER: _ClassVar[int]
    MARK_PRICE_FIELD_NUMBER: _ClassVar[int]
    MARKET_VALUE_FIELD_NUMBER: _ClassVar[int]
    UNREALIZED_PNL_FIELD_NUMBER: _ClassVar[int]
    REALIZED_PNL_FIELD_NUMBER: _ClassVar[int]
    LOTS_FIELD_NUMBER: _ClassVar[int]
    HALTED_FIELD_NUMBER: _ClassVar[int]
    instrument_id: str
    symbol: str
    qty: _common_pb2.Decimal
    cost_basis: _common_pb2.Decimal
    avg_cost: _common_pb2.Decimal
    mark_price: _common_pb2.Decimal
    market_value: _common_pb2.Decimal
    unrealized_pnl: _common_pb2.Decimal
    realized_pnl: _common_pb2.Decimal
    lots: _containers.RepeatedCompositeFieldContainer[Lot]
    halted: bool
    def __init__(self, instrument_id: _Optional[str] = ..., symbol: _Optional[str] = ..., qty: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., cost_basis: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., avg_cost: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., mark_price: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., market_value: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., unrealized_pnl: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., realized_pnl: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., lots: _Optional[_Iterable[_Union[Lot, _Mapping]]] = ..., halted: _Optional[bool] = ...) -> None: ...

class OwnerView(_message.Message):
    __slots__ = ("owner", "short_id", "allocation", "cash_available", "reserved", "positions", "equity", "realized_pnl", "unrealized_pnl", "fees", "dividends", "gross_exposure", "net_exposure", "day_pnl", "high_water_mark", "allow_short", "dedicated_account")
    OWNER_FIELD_NUMBER: _ClassVar[int]
    SHORT_ID_FIELD_NUMBER: _ClassVar[int]
    ALLOCATION_FIELD_NUMBER: _ClassVar[int]
    CASH_AVAILABLE_FIELD_NUMBER: _ClassVar[int]
    RESERVED_FIELD_NUMBER: _ClassVar[int]
    POSITIONS_FIELD_NUMBER: _ClassVar[int]
    EQUITY_FIELD_NUMBER: _ClassVar[int]
    REALIZED_PNL_FIELD_NUMBER: _ClassVar[int]
    UNREALIZED_PNL_FIELD_NUMBER: _ClassVar[int]
    FEES_FIELD_NUMBER: _ClassVar[int]
    DIVIDENDS_FIELD_NUMBER: _ClassVar[int]
    GROSS_EXPOSURE_FIELD_NUMBER: _ClassVar[int]
    NET_EXPOSURE_FIELD_NUMBER: _ClassVar[int]
    DAY_PNL_FIELD_NUMBER: _ClassVar[int]
    HIGH_WATER_MARK_FIELD_NUMBER: _ClassVar[int]
    ALLOW_SHORT_FIELD_NUMBER: _ClassVar[int]
    DEDICATED_ACCOUNT_FIELD_NUMBER: _ClassVar[int]
    owner: str
    short_id: str
    allocation: _common_pb2.Decimal
    cash_available: _common_pb2.Decimal
    reserved: _common_pb2.Decimal
    positions: _containers.RepeatedCompositeFieldContainer[OwnerPosition]
    equity: _common_pb2.Decimal
    realized_pnl: _common_pb2.Decimal
    unrealized_pnl: _common_pb2.Decimal
    fees: _common_pb2.Decimal
    dividends: _common_pb2.Decimal
    gross_exposure: _common_pb2.Decimal
    net_exposure: _common_pb2.Decimal
    day_pnl: _common_pb2.Decimal
    high_water_mark: _common_pb2.Decimal
    allow_short: bool
    dedicated_account: bool
    def __init__(self, owner: _Optional[str] = ..., short_id: _Optional[str] = ..., allocation: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., cash_available: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., reserved: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., positions: _Optional[_Iterable[_Union[OwnerPosition, _Mapping]]] = ..., equity: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., realized_pnl: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., unrealized_pnl: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., fees: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., dividends: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., gross_exposure: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., net_exposure: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., day_pnl: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., high_water_mark: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., allow_short: _Optional[bool] = ..., dedicated_account: _Optional[bool] = ...) -> None: ...

class InstrumentBreakdown(_message.Message):
    __slots__ = ("instrument_id", "symbol", "broker_qty", "ledger_qty", "owners", "broker_lots")
    INSTRUMENT_ID_FIELD_NUMBER: _ClassVar[int]
    SYMBOL_FIELD_NUMBER: _ClassVar[int]
    BROKER_QTY_FIELD_NUMBER: _ClassVar[int]
    LEDGER_QTY_FIELD_NUMBER: _ClassVar[int]
    OWNERS_FIELD_NUMBER: _ClassVar[int]
    BROKER_LOTS_FIELD_NUMBER: _ClassVar[int]
    instrument_id: str
    symbol: str
    broker_qty: _common_pb2.Decimal
    ledger_qty: _common_pb2.Decimal
    owners: _containers.RepeatedCompositeFieldContainer[_ledger_pb2.OwnerQty]
    broker_lots: _containers.RepeatedCompositeFieldContainer[Lot]
    def __init__(self, instrument_id: _Optional[str] = ..., symbol: _Optional[str] = ..., broker_qty: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., ledger_qty: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., owners: _Optional[_Iterable[_Union[_ledger_pb2.OwnerQty, _Mapping]]] = ..., broker_lots: _Optional[_Iterable[_Union[Lot, _Mapping]]] = ...) -> None: ...

class AccountView(_message.Message):
    __slots__ = ("broker_account_id", "broker_snapshot", "owners", "manual", "unallocated_cash", "reserve", "allocatable_capital", "recon_status", "recon_as_of", "instruments", "open_breaks", "ledger_cash")
    BROKER_ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    BROKER_SNAPSHOT_FIELD_NUMBER: _ClassVar[int]
    OWNERS_FIELD_NUMBER: _ClassVar[int]
    MANUAL_FIELD_NUMBER: _ClassVar[int]
    UNALLOCATED_CASH_FIELD_NUMBER: _ClassVar[int]
    RESERVE_FIELD_NUMBER: _ClassVar[int]
    ALLOCATABLE_CAPITAL_FIELD_NUMBER: _ClassVar[int]
    RECON_STATUS_FIELD_NUMBER: _ClassVar[int]
    RECON_AS_OF_FIELD_NUMBER: _ClassVar[int]
    INSTRUMENTS_FIELD_NUMBER: _ClassVar[int]
    OPEN_BREAKS_FIELD_NUMBER: _ClassVar[int]
    LEDGER_CASH_FIELD_NUMBER: _ClassVar[int]
    broker_account_id: str
    broker_snapshot: _ledger_pb2.AccountSnapshot
    owners: _containers.RepeatedCompositeFieldContainer[OwnerView]
    manual: OwnerView
    unallocated_cash: _common_pb2.Decimal
    reserve: _common_pb2.Decimal
    allocatable_capital: _common_pb2.Decimal
    recon_status: _ledger_pb2.ReconciliationStatus
    recon_as_of: int
    instruments: _containers.RepeatedCompositeFieldContainer[InstrumentBreakdown]
    open_breaks: _containers.RepeatedCompositeFieldContainer[_ledger_pb2.Break]
    ledger_cash: _common_pb2.Decimal
    def __init__(self, broker_account_id: _Optional[str] = ..., broker_snapshot: _Optional[_Union[_ledger_pb2.AccountSnapshot, _Mapping]] = ..., owners: _Optional[_Iterable[_Union[OwnerView, _Mapping]]] = ..., manual: _Optional[_Union[OwnerView, _Mapping]] = ..., unallocated_cash: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., reserve: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., allocatable_capital: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., recon_status: _Optional[_Union[_ledger_pb2.ReconciliationStatus, str]] = ..., recon_as_of: _Optional[int] = ..., instruments: _Optional[_Iterable[_Union[InstrumentBreakdown, _Mapping]]] = ..., open_breaks: _Optional[_Iterable[_Union[_ledger_pb2.Break, _Mapping]]] = ..., ledger_cash: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ...) -> None: ...

class OwnershipConstraints(_message.Message):
    __slots__ = ("strategy_qty", "other_owners_qty", "account_qty", "max_sellable_by_strategy", "short_allowed", "reason", "manual_qty", "halted")
    STRATEGY_QTY_FIELD_NUMBER: _ClassVar[int]
    OTHER_OWNERS_QTY_FIELD_NUMBER: _ClassVar[int]
    ACCOUNT_QTY_FIELD_NUMBER: _ClassVar[int]
    MAX_SELLABLE_BY_STRATEGY_FIELD_NUMBER: _ClassVar[int]
    SHORT_ALLOWED_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    MANUAL_QTY_FIELD_NUMBER: _ClassVar[int]
    HALTED_FIELD_NUMBER: _ClassVar[int]
    strategy_qty: _common_pb2.Decimal
    other_owners_qty: _common_pb2.Decimal
    account_qty: _common_pb2.Decimal
    max_sellable_by_strategy: _common_pb2.Decimal
    short_allowed: bool
    reason: str
    manual_qty: _common_pb2.Decimal
    halted: bool
    def __init__(self, strategy_qty: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., other_owners_qty: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., account_qty: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., max_sellable_by_strategy: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., short_allowed: _Optional[bool] = ..., reason: _Optional[str] = ..., manual_qty: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., halted: _Optional[bool] = ...) -> None: ...

class CapitalAvailability(_message.Message):
    __slots__ = ("allocation", "cash_available", "buying_power_available", "reserved_for_open_orders")
    ALLOCATION_FIELD_NUMBER: _ClassVar[int]
    CASH_AVAILABLE_FIELD_NUMBER: _ClassVar[int]
    BUYING_POWER_AVAILABLE_FIELD_NUMBER: _ClassVar[int]
    RESERVED_FOR_OPEN_ORDERS_FIELD_NUMBER: _ClassVar[int]
    allocation: _common_pb2.Decimal
    cash_available: _common_pb2.Decimal
    buying_power_available: _common_pb2.Decimal
    reserved_for_open_orders: _common_pb2.Decimal
    def __init__(self, allocation: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., cash_available: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., buying_power_available: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., reserved_for_open_orders: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ...) -> None: ...

class EquityPoint(_message.Message):
    __slots__ = ("ts", "equity", "cash", "market_value", "realized_pnl", "unrealized_pnl", "gross_exposure")
    TS_FIELD_NUMBER: _ClassVar[int]
    EQUITY_FIELD_NUMBER: _ClassVar[int]
    CASH_FIELD_NUMBER: _ClassVar[int]
    MARKET_VALUE_FIELD_NUMBER: _ClassVar[int]
    REALIZED_PNL_FIELD_NUMBER: _ClassVar[int]
    UNREALIZED_PNL_FIELD_NUMBER: _ClassVar[int]
    GROSS_EXPOSURE_FIELD_NUMBER: _ClassVar[int]
    ts: int
    equity: _common_pb2.Decimal
    cash: _common_pb2.Decimal
    market_value: _common_pb2.Decimal
    realized_pnl: _common_pb2.Decimal
    unrealized_pnl: _common_pb2.Decimal
    gross_exposure: _common_pb2.Decimal
    def __init__(self, ts: _Optional[int] = ..., equity: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., cash: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., market_value: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., realized_pnl: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., unrealized_pnl: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., gross_exposure: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ...) -> None: ...

class WashSaleFlag(_message.Message):
    __slots__ = ("flag_id", "instrument_id", "sell_owner", "sell_account", "sell_source_id", "loss", "buy_owner", "buy_account", "buy_source_id", "sell_time", "buy_time")
    FLAG_ID_FIELD_NUMBER: _ClassVar[int]
    INSTRUMENT_ID_FIELD_NUMBER: _ClassVar[int]
    SELL_OWNER_FIELD_NUMBER: _ClassVar[int]
    SELL_ACCOUNT_FIELD_NUMBER: _ClassVar[int]
    SELL_SOURCE_ID_FIELD_NUMBER: _ClassVar[int]
    LOSS_FIELD_NUMBER: _ClassVar[int]
    BUY_OWNER_FIELD_NUMBER: _ClassVar[int]
    BUY_ACCOUNT_FIELD_NUMBER: _ClassVar[int]
    BUY_SOURCE_ID_FIELD_NUMBER: _ClassVar[int]
    SELL_TIME_FIELD_NUMBER: _ClassVar[int]
    BUY_TIME_FIELD_NUMBER: _ClassVar[int]
    flag_id: str
    instrument_id: str
    sell_owner: str
    sell_account: str
    sell_source_id: str
    loss: _common_pb2.Decimal
    buy_owner: str
    buy_account: str
    buy_source_id: str
    sell_time: int
    buy_time: int
    def __init__(self, flag_id: _Optional[str] = ..., instrument_id: _Optional[str] = ..., sell_owner: _Optional[str] = ..., sell_account: _Optional[str] = ..., sell_source_id: _Optional[str] = ..., loss: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., buy_owner: _Optional[str] = ..., buy_account: _Optional[str] = ..., buy_source_id: _Optional[str] = ..., sell_time: _Optional[int] = ..., buy_time: _Optional[int] = ...) -> None: ...

class ListAccountsRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class ListAccountsResponse(_message.Message):
    __slots__ = ("accounts",)
    ACCOUNTS_FIELD_NUMBER: _ClassVar[int]
    accounts: _containers.RepeatedCompositeFieldContainer[AccountView]
    def __init__(self, accounts: _Optional[_Iterable[_Union[AccountView, _Mapping]]] = ...) -> None: ...

class GetAccountViewRequest(_message.Message):
    __slots__ = ("broker_account_id",)
    BROKER_ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    broker_account_id: str
    def __init__(self, broker_account_id: _Optional[str] = ...) -> None: ...

class GetAccountViewResponse(_message.Message):
    __slots__ = ("view",)
    VIEW_FIELD_NUMBER: _ClassVar[int]
    view: AccountView
    def __init__(self, view: _Optional[_Union[AccountView, _Mapping]] = ...) -> None: ...

class GetStrategyPortfolioRequest(_message.Message):
    __slots__ = ("strategy_id",)
    STRATEGY_ID_FIELD_NUMBER: _ClassVar[int]
    strategy_id: str
    def __init__(self, strategy_id: _Optional[str] = ...) -> None: ...

class GetStrategyPortfolioResponse(_message.Message):
    __slots__ = ("broker_account_id", "portfolio")
    BROKER_ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    PORTFOLIO_FIELD_NUMBER: _ClassVar[int]
    broker_account_id: str
    portfolio: OwnerView
    def __init__(self, broker_account_id: _Optional[str] = ..., portfolio: _Optional[_Union[OwnerView, _Mapping]] = ...) -> None: ...

class GetOwnershipConstraintsRequest(_message.Message):
    __slots__ = ("broker_account_id", "instrument_id", "owner")
    BROKER_ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    INSTRUMENT_ID_FIELD_NUMBER: _ClassVar[int]
    OWNER_FIELD_NUMBER: _ClassVar[int]
    broker_account_id: str
    instrument_id: str
    owner: str
    def __init__(self, broker_account_id: _Optional[str] = ..., instrument_id: _Optional[str] = ..., owner: _Optional[str] = ...) -> None: ...

class GetOwnershipConstraintsResponse(_message.Message):
    __slots__ = ("constraints",)
    CONSTRAINTS_FIELD_NUMBER: _ClassVar[int]
    constraints: OwnershipConstraints
    def __init__(self, constraints: _Optional[_Union[OwnershipConstraints, _Mapping]] = ...) -> None: ...

class GetCapitalAvailabilityRequest(_message.Message):
    __slots__ = ("owner", "broker_account_id")
    OWNER_FIELD_NUMBER: _ClassVar[int]
    BROKER_ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    owner: str
    broker_account_id: str
    def __init__(self, owner: _Optional[str] = ..., broker_account_id: _Optional[str] = ...) -> None: ...

class GetCapitalAvailabilityResponse(_message.Message):
    __slots__ = ("capital",)
    CAPITAL_FIELD_NUMBER: _ClassVar[int]
    capital: CapitalAvailability
    def __init__(self, capital: _Optional[_Union[CapitalAvailability, _Mapping]] = ...) -> None: ...

class ReserveForOrderRequest(_message.Message):
    __slots__ = ("intent_id", "owner", "broker_account_id", "instrument_id", "side", "qty", "est_price", "correlation_id")
    INTENT_ID_FIELD_NUMBER: _ClassVar[int]
    OWNER_FIELD_NUMBER: _ClassVar[int]
    BROKER_ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    INSTRUMENT_ID_FIELD_NUMBER: _ClassVar[int]
    SIDE_FIELD_NUMBER: _ClassVar[int]
    QTY_FIELD_NUMBER: _ClassVar[int]
    EST_PRICE_FIELD_NUMBER: _ClassVar[int]
    CORRELATION_ID_FIELD_NUMBER: _ClassVar[int]
    intent_id: str
    owner: str
    broker_account_id: str
    instrument_id: str
    side: _common_pb2.Side
    qty: _common_pb2.Decimal
    est_price: _common_pb2.Decimal
    correlation_id: str
    def __init__(self, intent_id: _Optional[str] = ..., owner: _Optional[str] = ..., broker_account_id: _Optional[str] = ..., instrument_id: _Optional[str] = ..., side: _Optional[_Union[_common_pb2.Side, str]] = ..., qty: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., est_price: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., correlation_id: _Optional[str] = ...) -> None: ...

class ReserveForOrderResponse(_message.Message):
    __slots__ = ("reserved", "reservation_id", "reject_reason", "capital")
    RESERVED_FIELD_NUMBER: _ClassVar[int]
    RESERVATION_ID_FIELD_NUMBER: _ClassVar[int]
    REJECT_REASON_FIELD_NUMBER: _ClassVar[int]
    CAPITAL_FIELD_NUMBER: _ClassVar[int]
    reserved: bool
    reservation_id: str
    reject_reason: str
    capital: CapitalAvailability
    def __init__(self, reserved: _Optional[bool] = ..., reservation_id: _Optional[str] = ..., reject_reason: _Optional[str] = ..., capital: _Optional[_Union[CapitalAvailability, _Mapping]] = ...) -> None: ...

class ReleaseReservationRequest(_message.Message):
    __slots__ = ("reservation_id", "intent_id")
    RESERVATION_ID_FIELD_NUMBER: _ClassVar[int]
    INTENT_ID_FIELD_NUMBER: _ClassVar[int]
    reservation_id: str
    intent_id: str
    def __init__(self, reservation_id: _Optional[str] = ..., intent_id: _Optional[str] = ...) -> None: ...

class ReleaseReservationResponse(_message.Message):
    __slots__ = ("released",)
    RELEASED_FIELD_NUMBER: _ClassVar[int]
    released: bool
    def __init__(self, released: _Optional[bool] = ...) -> None: ...

class RegisterOwnerRequest(_message.Message):
    __slots__ = ("owner", "short_id", "broker_account_id", "allow_short", "dedicated_account")
    OWNER_FIELD_NUMBER: _ClassVar[int]
    SHORT_ID_FIELD_NUMBER: _ClassVar[int]
    BROKER_ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    ALLOW_SHORT_FIELD_NUMBER: _ClassVar[int]
    DEDICATED_ACCOUNT_FIELD_NUMBER: _ClassVar[int]
    owner: str
    short_id: str
    broker_account_id: str
    allow_short: bool
    dedicated_account: bool
    def __init__(self, owner: _Optional[str] = ..., short_id: _Optional[str] = ..., broker_account_id: _Optional[str] = ..., allow_short: _Optional[bool] = ..., dedicated_account: _Optional[bool] = ...) -> None: ...

class RegisterOwnerResponse(_message.Message):
    __slots__ = ("owner",)
    OWNER_FIELD_NUMBER: _ClassVar[int]
    owner: OwnerView
    def __init__(self, owner: _Optional[_Union[OwnerView, _Mapping]] = ...) -> None: ...

class SetAllocationRequest(_message.Message):
    __slots__ = ("owner", "broker_account_id", "amount", "actor", "correlation_id")
    OWNER_FIELD_NUMBER: _ClassVar[int]
    BROKER_ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    AMOUNT_FIELD_NUMBER: _ClassVar[int]
    ACTOR_FIELD_NUMBER: _ClassVar[int]
    CORRELATION_ID_FIELD_NUMBER: _ClassVar[int]
    owner: str
    broker_account_id: str
    amount: _common_pb2.Decimal
    actor: str
    correlation_id: str
    def __init__(self, owner: _Optional[str] = ..., broker_account_id: _Optional[str] = ..., amount: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., actor: _Optional[str] = ..., correlation_id: _Optional[str] = ...) -> None: ...

class SetAllocationResponse(_message.Message):
    __slots__ = ("allocation", "previous", "unallocated")
    ALLOCATION_FIELD_NUMBER: _ClassVar[int]
    PREVIOUS_FIELD_NUMBER: _ClassVar[int]
    UNALLOCATED_FIELD_NUMBER: _ClassVar[int]
    allocation: _common_pb2.Decimal
    previous: _common_pb2.Decimal
    unallocated: _common_pb2.Decimal
    def __init__(self, allocation: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., previous: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., unallocated: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ...) -> None: ...

class ReconcileRequest(_message.Message):
    __slots__ = ("broker_account_id", "trigger")
    BROKER_ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    TRIGGER_FIELD_NUMBER: _ClassVar[int]
    broker_account_id: str
    trigger: str
    def __init__(self, broker_account_id: _Optional[str] = ..., trigger: _Optional[str] = ...) -> None: ...

class ReconcileResponse(_message.Message):
    __slots__ = ("result",)
    RESULT_FIELD_NUMBER: _ClassVar[int]
    result: _ledger_pb2.ReconciliationResult
    def __init__(self, result: _Optional[_Union[_ledger_pb2.ReconciliationResult, _Mapping]] = ...) -> None: ...

class ListBreaksRequest(_message.Message):
    __slots__ = ("broker_account_id", "open_only")
    BROKER_ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    OPEN_ONLY_FIELD_NUMBER: _ClassVar[int]
    broker_account_id: str
    open_only: bool
    def __init__(self, broker_account_id: _Optional[str] = ..., open_only: _Optional[bool] = ...) -> None: ...

class ListBreaksResponse(_message.Message):
    __slots__ = ("breaks",)
    BREAKS_FIELD_NUMBER: _ClassVar[int]
    breaks: _containers.RepeatedCompositeFieldContainer[_ledger_pb2.Break]
    def __init__(self, breaks: _Optional[_Iterable[_Union[_ledger_pb2.Break, _Mapping]]] = ...) -> None: ...

class ResolveBreakRequest(_message.Message):
    __slots__ = ("break_id", "resolution", "from_owner", "qty", "actor", "note", "correlation_id")
    BREAK_ID_FIELD_NUMBER: _ClassVar[int]
    RESOLUTION_FIELD_NUMBER: _ClassVar[int]
    FROM_OWNER_FIELD_NUMBER: _ClassVar[int]
    QTY_FIELD_NUMBER: _ClassVar[int]
    ACTOR_FIELD_NUMBER: _ClassVar[int]
    NOTE_FIELD_NUMBER: _ClassVar[int]
    CORRELATION_ID_FIELD_NUMBER: _ClassVar[int]
    break_id: str
    resolution: str
    from_owner: str
    qty: _common_pb2.Decimal
    actor: str
    note: str
    correlation_id: str
    def __init__(self, break_id: _Optional[str] = ..., resolution: _Optional[str] = ..., from_owner: _Optional[str] = ..., qty: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., actor: _Optional[str] = ..., note: _Optional[str] = ..., correlation_id: _Optional[str] = ...) -> None: ...

class ResolveBreakResponse(_message.Message):
    __slots__ = ("resolved",)
    RESOLVED_FIELD_NUMBER: _ClassVar[int]
    resolved: _ledger_pb2.Break
    def __init__(self, resolved: _Optional[_Union[_ledger_pb2.Break, _Mapping]] = ...) -> None: ...

class GetEquityCurveRequest(_message.Message):
    __slots__ = ("owner", "broker_account_id", "start", "end")
    OWNER_FIELD_NUMBER: _ClassVar[int]
    BROKER_ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    START_FIELD_NUMBER: _ClassVar[int]
    END_FIELD_NUMBER: _ClassVar[int]
    owner: str
    broker_account_id: str
    start: int
    end: int
    def __init__(self, owner: _Optional[str] = ..., broker_account_id: _Optional[str] = ..., start: _Optional[int] = ..., end: _Optional[int] = ...) -> None: ...

class GetEquityCurveResponse(_message.Message):
    __slots__ = ("points",)
    POINTS_FIELD_NUMBER: _ClassVar[int]
    points: _containers.RepeatedCompositeFieldContainer[EquityPoint]
    def __init__(self, points: _Optional[_Iterable[_Union[EquityPoint, _Mapping]]] = ...) -> None: ...

class ListLotsRequest(_message.Message):
    __slots__ = ("broker_account_id", "owner", "instrument_id")
    BROKER_ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    OWNER_FIELD_NUMBER: _ClassVar[int]
    INSTRUMENT_ID_FIELD_NUMBER: _ClassVar[int]
    broker_account_id: str
    owner: str
    instrument_id: str
    def __init__(self, broker_account_id: _Optional[str] = ..., owner: _Optional[str] = ..., instrument_id: _Optional[str] = ...) -> None: ...

class ListLotsResponse(_message.Message):
    __slots__ = ("lots",)
    LOTS_FIELD_NUMBER: _ClassVar[int]
    lots: _containers.RepeatedCompositeFieldContainer[Lot]
    def __init__(self, lots: _Optional[_Iterable[_Union[Lot, _Mapping]]] = ...) -> None: ...

class ListWashSaleFlagsRequest(_message.Message):
    __slots__ = ("since",)
    SINCE_FIELD_NUMBER: _ClassVar[int]
    since: int
    def __init__(self, since: _Optional[int] = ...) -> None: ...

class ListWashSaleFlagsResponse(_message.Message):
    __slots__ = ("flags",)
    FLAGS_FIELD_NUMBER: _ClassVar[int]
    flags: _containers.RepeatedCompositeFieldContainer[WashSaleFlag]
    def __init__(self, flags: _Optional[_Iterable[_Union[WashSaleFlag, _Mapping]]] = ...) -> None: ...

class ListReconciliationReportsRequest(_message.Message):
    __slots__ = ("broker_account_id", "since", "limit")
    BROKER_ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    SINCE_FIELD_NUMBER: _ClassVar[int]
    LIMIT_FIELD_NUMBER: _ClassVar[int]
    broker_account_id: str
    since: int
    limit: int
    def __init__(self, broker_account_id: _Optional[str] = ..., since: _Optional[int] = ..., limit: _Optional[int] = ...) -> None: ...

class ListReconciliationReportsResponse(_message.Message):
    __slots__ = ("reports",)
    REPORTS_FIELD_NUMBER: _ClassVar[int]
    reports: _containers.RepeatedCompositeFieldContainer[_ledger_pb2.ReconciliationResult]
    def __init__(self, reports: _Optional[_Iterable[_Union[_ledger_pb2.ReconciliationResult, _Mapping]]] = ...) -> None: ...
