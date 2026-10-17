from tb.common.v1 import common_pb2 as _common_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class Timeframe(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    TIMEFRAME_UNSPECIFIED: _ClassVar[Timeframe]
    TIMEFRAME_1M: _ClassVar[Timeframe]
    TIMEFRAME_5M: _ClassVar[Timeframe]
    TIMEFRAME_15M: _ClassVar[Timeframe]
    TIMEFRAME_1H: _ClassVar[Timeframe]
    TIMEFRAME_1D: _ClassVar[Timeframe]

class CorporateActionType(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    CORPORATE_ACTION_TYPE_UNSPECIFIED: _ClassVar[CorporateActionType]
    CORPORATE_ACTION_TYPE_SPLIT: _ClassVar[CorporateActionType]
    CORPORATE_ACTION_TYPE_DIVIDEND: _ClassVar[CorporateActionType]
    CORPORATE_ACTION_TYPE_SYMBOL_CHANGE: _ClassVar[CorporateActionType]
    CORPORATE_ACTION_TYPE_DELISTING: _ClassVar[CorporateActionType]
    CORPORATE_ACTION_TYPE_MERGER: _ClassVar[CorporateActionType]
TIMEFRAME_UNSPECIFIED: Timeframe
TIMEFRAME_1M: Timeframe
TIMEFRAME_5M: Timeframe
TIMEFRAME_15M: Timeframe
TIMEFRAME_1H: Timeframe
TIMEFRAME_1D: Timeframe
CORPORATE_ACTION_TYPE_UNSPECIFIED: CorporateActionType
CORPORATE_ACTION_TYPE_SPLIT: CorporateActionType
CORPORATE_ACTION_TYPE_DIVIDEND: CorporateActionType
CORPORATE_ACTION_TYPE_SYMBOL_CHANGE: CorporateActionType
CORPORATE_ACTION_TYPE_DELISTING: CorporateActionType
CORPORATE_ACTION_TYPE_MERGER: CorporateActionType

class Bar(_message.Message):
    __slots__ = ("instrument_id", "timeframe", "ts_open", "ts_close", "open", "high", "low", "close", "volume", "vwap", "trade_count", "feed", "is_final", "available_at", "ingested_at")
    INSTRUMENT_ID_FIELD_NUMBER: _ClassVar[int]
    TIMEFRAME_FIELD_NUMBER: _ClassVar[int]
    TS_OPEN_FIELD_NUMBER: _ClassVar[int]
    TS_CLOSE_FIELD_NUMBER: _ClassVar[int]
    OPEN_FIELD_NUMBER: _ClassVar[int]
    HIGH_FIELD_NUMBER: _ClassVar[int]
    LOW_FIELD_NUMBER: _ClassVar[int]
    CLOSE_FIELD_NUMBER: _ClassVar[int]
    VOLUME_FIELD_NUMBER: _ClassVar[int]
    VWAP_FIELD_NUMBER: _ClassVar[int]
    TRADE_COUNT_FIELD_NUMBER: _ClassVar[int]
    FEED_FIELD_NUMBER: _ClassVar[int]
    IS_FINAL_FIELD_NUMBER: _ClassVar[int]
    AVAILABLE_AT_FIELD_NUMBER: _ClassVar[int]
    INGESTED_AT_FIELD_NUMBER: _ClassVar[int]
    instrument_id: str
    timeframe: Timeframe
    ts_open: int
    ts_close: int
    open: _common_pb2.Decimal
    high: _common_pb2.Decimal
    low: _common_pb2.Decimal
    close: _common_pb2.Decimal
    volume: _common_pb2.Decimal
    vwap: _common_pb2.Decimal
    trade_count: int
    feed: str
    is_final: bool
    available_at: int
    ingested_at: int
    def __init__(self, instrument_id: _Optional[str] = ..., timeframe: _Optional[_Union[Timeframe, str]] = ..., ts_open: _Optional[int] = ..., ts_close: _Optional[int] = ..., open: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., high: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., low: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., close: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., volume: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., vwap: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., trade_count: _Optional[int] = ..., feed: _Optional[str] = ..., is_final: _Optional[bool] = ..., available_at: _Optional[int] = ..., ingested_at: _Optional[int] = ...) -> None: ...

class Quote(_message.Message):
    __slots__ = ("instrument_id", "event_time", "bid_price", "bid_size", "ask_price", "ask_size", "conditions", "feed", "available_at", "ingested_at")
    INSTRUMENT_ID_FIELD_NUMBER: _ClassVar[int]
    EVENT_TIME_FIELD_NUMBER: _ClassVar[int]
    BID_PRICE_FIELD_NUMBER: _ClassVar[int]
    BID_SIZE_FIELD_NUMBER: _ClassVar[int]
    ASK_PRICE_FIELD_NUMBER: _ClassVar[int]
    ASK_SIZE_FIELD_NUMBER: _ClassVar[int]
    CONDITIONS_FIELD_NUMBER: _ClassVar[int]
    FEED_FIELD_NUMBER: _ClassVar[int]
    AVAILABLE_AT_FIELD_NUMBER: _ClassVar[int]
    INGESTED_AT_FIELD_NUMBER: _ClassVar[int]
    instrument_id: str
    event_time: int
    bid_price: _common_pb2.Decimal
    bid_size: _common_pb2.Decimal
    ask_price: _common_pb2.Decimal
    ask_size: _common_pb2.Decimal
    conditions: _containers.RepeatedScalarFieldContainer[str]
    feed: str
    available_at: int
    ingested_at: int
    def __init__(self, instrument_id: _Optional[str] = ..., event_time: _Optional[int] = ..., bid_price: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., bid_size: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., ask_price: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., ask_size: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., conditions: _Optional[_Iterable[str]] = ..., feed: _Optional[str] = ..., available_at: _Optional[int] = ..., ingested_at: _Optional[int] = ...) -> None: ...

class Trade(_message.Message):
    __slots__ = ("instrument_id", "event_time", "price", "size", "conditions", "feed", "available_at", "ingested_at")
    INSTRUMENT_ID_FIELD_NUMBER: _ClassVar[int]
    EVENT_TIME_FIELD_NUMBER: _ClassVar[int]
    PRICE_FIELD_NUMBER: _ClassVar[int]
    SIZE_FIELD_NUMBER: _ClassVar[int]
    CONDITIONS_FIELD_NUMBER: _ClassVar[int]
    FEED_FIELD_NUMBER: _ClassVar[int]
    AVAILABLE_AT_FIELD_NUMBER: _ClassVar[int]
    INGESTED_AT_FIELD_NUMBER: _ClassVar[int]
    instrument_id: str
    event_time: int
    price: _common_pb2.Decimal
    size: _common_pb2.Decimal
    conditions: _containers.RepeatedScalarFieldContainer[str]
    feed: str
    available_at: int
    ingested_at: int
    def __init__(self, instrument_id: _Optional[str] = ..., event_time: _Optional[int] = ..., price: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., size: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., conditions: _Optional[_Iterable[str]] = ..., feed: _Optional[str] = ..., available_at: _Optional[int] = ..., ingested_at: _Optional[int] = ...) -> None: ...

class CorporateAction(_message.Message):
    __slots__ = ("instrument_id", "type", "ex_date", "ratio", "amount", "successor_instrument_id", "source", "available_at", "ingested_at")
    INSTRUMENT_ID_FIELD_NUMBER: _ClassVar[int]
    TYPE_FIELD_NUMBER: _ClassVar[int]
    EX_DATE_FIELD_NUMBER: _ClassVar[int]
    RATIO_FIELD_NUMBER: _ClassVar[int]
    AMOUNT_FIELD_NUMBER: _ClassVar[int]
    SUCCESSOR_INSTRUMENT_ID_FIELD_NUMBER: _ClassVar[int]
    SOURCE_FIELD_NUMBER: _ClassVar[int]
    AVAILABLE_AT_FIELD_NUMBER: _ClassVar[int]
    INGESTED_AT_FIELD_NUMBER: _ClassVar[int]
    instrument_id: str
    type: CorporateActionType
    ex_date: int
    ratio: _common_pb2.Decimal
    amount: _common_pb2.Decimal
    successor_instrument_id: str
    source: str
    available_at: int
    ingested_at: int
    def __init__(self, instrument_id: _Optional[str] = ..., type: _Optional[_Union[CorporateActionType, str]] = ..., ex_date: _Optional[int] = ..., ratio: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., amount: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., successor_instrument_id: _Optional[str] = ..., source: _Optional[str] = ..., available_at: _Optional[int] = ..., ingested_at: _Optional[int] = ...) -> None: ...
