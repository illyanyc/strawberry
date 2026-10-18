from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class Mode(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    MODE_UNSPECIFIED: _ClassVar[Mode]
    MODE_BACKTEST: _ClassVar[Mode]
    MODE_PAPER: _ClassVar[Mode]
    MODE_LIVE: _ClassVar[Mode]

class AssetClass(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    ASSET_CLASS_UNSPECIFIED: _ClassVar[AssetClass]
    ASSET_CLASS_US_EQUITY: _ClassVar[AssetClass]
    ASSET_CLASS_ETF: _ClassVar[AssetClass]
    ASSET_CLASS_OPTION: _ClassVar[AssetClass]
    ASSET_CLASS_FUTURE: _ClassVar[AssetClass]
    ASSET_CLASS_CRYPTO: _ClassVar[AssetClass]

class Side(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    SIDE_UNSPECIFIED: _ClassVar[Side]
    SIDE_BUY: _ClassVar[Side]
    SIDE_SELL: _ClassVar[Side]
    SIDE_SELL_SHORT: _ClassVar[Side]

class Horizon(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    HORIZON_UNSPECIFIED: _ClassVar[Horizon]
    HORIZON_INTRADAY: _ClassVar[Horizon]
    HORIZON_SWING: _ClassVar[Horizon]

class Broker(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    BROKER_UNSPECIFIED: _ClassVar[Broker]
    BROKER_ALPACA: _ClassVar[Broker]
    BROKER_IBKR: _ClassVar[Broker]
    BROKER_SIMULATOR: _ClassVar[Broker]
MODE_UNSPECIFIED: Mode
MODE_BACKTEST: Mode
MODE_PAPER: Mode
MODE_LIVE: Mode
ASSET_CLASS_UNSPECIFIED: AssetClass
ASSET_CLASS_US_EQUITY: AssetClass
ASSET_CLASS_ETF: AssetClass
ASSET_CLASS_OPTION: AssetClass
ASSET_CLASS_FUTURE: AssetClass
ASSET_CLASS_CRYPTO: AssetClass
SIDE_UNSPECIFIED: Side
SIDE_BUY: Side
SIDE_SELL: Side
SIDE_SELL_SHORT: Side
HORIZON_UNSPECIFIED: Horizon
HORIZON_INTRADAY: Horizon
HORIZON_SWING: Horizon
BROKER_UNSPECIFIED: Broker
BROKER_ALPACA: Broker
BROKER_IBKR: Broker
BROKER_SIMULATOR: Broker

class Decimal(_message.Message):
    __slots__ = ("value",)
    VALUE_FIELD_NUMBER: _ClassVar[int]
    value: str
    def __init__(self, value: _Optional[str] = ...) -> None: ...

class Instrument(_message.Message):
    __slots__ = ("instrument_id", "symbol", "exchange", "asset_class", "currency", "alpaca_asset_id", "ibkr_con_id", "delisted")
    INSTRUMENT_ID_FIELD_NUMBER: _ClassVar[int]
    SYMBOL_FIELD_NUMBER: _ClassVar[int]
    EXCHANGE_FIELD_NUMBER: _ClassVar[int]
    ASSET_CLASS_FIELD_NUMBER: _ClassVar[int]
    CURRENCY_FIELD_NUMBER: _ClassVar[int]
    ALPACA_ASSET_ID_FIELD_NUMBER: _ClassVar[int]
    IBKR_CON_ID_FIELD_NUMBER: _ClassVar[int]
    DELISTED_FIELD_NUMBER: _ClassVar[int]
    instrument_id: str
    symbol: str
    exchange: str
    asset_class: AssetClass
    currency: str
    alpaca_asset_id: str
    ibkr_con_id: str
    delisted: bool
    def __init__(self, instrument_id: _Optional[str] = ..., symbol: _Optional[str] = ..., exchange: _Optional[str] = ..., asset_class: _Optional[_Union[AssetClass, str]] = ..., currency: _Optional[str] = ..., alpaca_asset_id: _Optional[str] = ..., ibkr_con_id: _Optional[str] = ..., delisted: _Optional[bool] = ...) -> None: ...

class Provenance(_message.Message):
    __slots__ = ("event_time", "available_at", "ingested_at")
    EVENT_TIME_FIELD_NUMBER: _ClassVar[int]
    AVAILABLE_AT_FIELD_NUMBER: _ClassVar[int]
    INGESTED_AT_FIELD_NUMBER: _ClassVar[int]
    event_time: int
    available_at: int
    ingested_at: int
    def __init__(self, event_time: _Optional[int] = ..., available_at: _Optional[int] = ..., ingested_at: _Optional[int] = ...) -> None: ...
