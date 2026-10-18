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
    TIMEFRAME_30M: _ClassVar[Timeframe]

class AdjustmentBasis(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    ADJUSTMENT_BASIS_UNSPECIFIED: _ClassVar[AdjustmentBasis]
    ADJUSTMENT_BASIS_RAW: _ClassVar[AdjustmentBasis]
    ADJUSTMENT_BASIS_SPLIT: _ClassVar[AdjustmentBasis]
    ADJUSTMENT_BASIS_SPLIT_DIV: _ClassVar[AdjustmentBasis]

class Session(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    SESSION_UNSPECIFIED: _ClassVar[Session]
    SESSION_PRE: _ClassVar[Session]
    SESSION_REGULAR: _ClassVar[Session]
    SESSION_POST: _ClassVar[Session]

class AgreementStatus(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    AGREEMENT_STATUS_UNSPECIFIED: _ClassVar[AgreementStatus]
    AGREEMENT_STATUS_SINGLE_SOURCE: _ClassVar[AgreementStatus]
    AGREEMENT_STATUS_AGREED: _ClassVar[AgreementStatus]
    AGREEMENT_STATUS_DISAGREED: _ClassVar[AgreementStatus]

class CorporateActionType(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    CORPORATE_ACTION_TYPE_UNSPECIFIED: _ClassVar[CorporateActionType]
    CORPORATE_ACTION_TYPE_SPLIT: _ClassVar[CorporateActionType]
    CORPORATE_ACTION_TYPE_DIVIDEND: _ClassVar[CorporateActionType]
    CORPORATE_ACTION_TYPE_SYMBOL_CHANGE: _ClassVar[CorporateActionType]
    CORPORATE_ACTION_TYPE_DELISTING: _ClassVar[CorporateActionType]
    CORPORATE_ACTION_TYPE_MERGER: _ClassVar[CorporateActionType]
    CORPORATE_ACTION_TYPE_SPIN_OFF: _ClassVar[CorporateActionType]

class JobState(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    JOB_STATE_UNSPECIFIED: _ClassVar[JobState]
    JOB_STATE_QUEUED: _ClassVar[JobState]
    JOB_STATE_RUNNING: _ClassVar[JobState]
    JOB_STATE_SUCCEEDED: _ClassVar[JobState]
    JOB_STATE_FAILED: _ClassVar[JobState]
    JOB_STATE_SUCCEEDED_WITH_GAPS: _ClassVar[JobState]
TIMEFRAME_UNSPECIFIED: Timeframe
TIMEFRAME_1M: Timeframe
TIMEFRAME_5M: Timeframe
TIMEFRAME_15M: Timeframe
TIMEFRAME_1H: Timeframe
TIMEFRAME_1D: Timeframe
TIMEFRAME_30M: Timeframe
ADJUSTMENT_BASIS_UNSPECIFIED: AdjustmentBasis
ADJUSTMENT_BASIS_RAW: AdjustmentBasis
ADJUSTMENT_BASIS_SPLIT: AdjustmentBasis
ADJUSTMENT_BASIS_SPLIT_DIV: AdjustmentBasis
SESSION_UNSPECIFIED: Session
SESSION_PRE: Session
SESSION_REGULAR: Session
SESSION_POST: Session
AGREEMENT_STATUS_UNSPECIFIED: AgreementStatus
AGREEMENT_STATUS_SINGLE_SOURCE: AgreementStatus
AGREEMENT_STATUS_AGREED: AgreementStatus
AGREEMENT_STATUS_DISAGREED: AgreementStatus
CORPORATE_ACTION_TYPE_UNSPECIFIED: CorporateActionType
CORPORATE_ACTION_TYPE_SPLIT: CorporateActionType
CORPORATE_ACTION_TYPE_DIVIDEND: CorporateActionType
CORPORATE_ACTION_TYPE_SYMBOL_CHANGE: CorporateActionType
CORPORATE_ACTION_TYPE_DELISTING: CorporateActionType
CORPORATE_ACTION_TYPE_MERGER: CorporateActionType
CORPORATE_ACTION_TYPE_SPIN_OFF: CorporateActionType
JOB_STATE_UNSPECIFIED: JobState
JOB_STATE_QUEUED: JobState
JOB_STATE_RUNNING: JobState
JOB_STATE_SUCCEEDED: JobState
JOB_STATE_FAILED: JobState
JOB_STATE_SUCCEEDED_WITH_GAPS: JobState

class Bar(_message.Message):
    __slots__ = ("instrument_id", "timeframe", "ts_open", "ts_close", "open", "high", "low", "close", "volume", "vwap", "trade_count", "feed", "is_final", "available_at", "ingested_at", "source", "revision", "adjustment_basis", "session", "repaired", "symbol")
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
    SOURCE_FIELD_NUMBER: _ClassVar[int]
    REVISION_FIELD_NUMBER: _ClassVar[int]
    ADJUSTMENT_BASIS_FIELD_NUMBER: _ClassVar[int]
    SESSION_FIELD_NUMBER: _ClassVar[int]
    REPAIRED_FIELD_NUMBER: _ClassVar[int]
    SYMBOL_FIELD_NUMBER: _ClassVar[int]
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
    source: str
    revision: int
    adjustment_basis: AdjustmentBasis
    session: Session
    repaired: bool
    symbol: str
    def __init__(self, instrument_id: _Optional[str] = ..., timeframe: _Optional[_Union[Timeframe, str]] = ..., ts_open: _Optional[int] = ..., ts_close: _Optional[int] = ..., open: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., high: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., low: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., close: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., volume: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., vwap: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., trade_count: _Optional[int] = ..., feed: _Optional[str] = ..., is_final: _Optional[bool] = ..., available_at: _Optional[int] = ..., ingested_at: _Optional[int] = ..., source: _Optional[str] = ..., revision: _Optional[int] = ..., adjustment_basis: _Optional[_Union[AdjustmentBasis, str]] = ..., session: _Optional[_Union[Session, str]] = ..., repaired: _Optional[bool] = ..., symbol: _Optional[str] = ...) -> None: ...

class Quote(_message.Message):
    __slots__ = ("instrument_id", "event_time", "bid_price", "bid_size", "ask_price", "ask_size", "conditions", "feed", "available_at", "ingested_at", "symbol")
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
    SYMBOL_FIELD_NUMBER: _ClassVar[int]
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
    symbol: str
    def __init__(self, instrument_id: _Optional[str] = ..., event_time: _Optional[int] = ..., bid_price: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., bid_size: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., ask_price: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., ask_size: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., conditions: _Optional[_Iterable[str]] = ..., feed: _Optional[str] = ..., available_at: _Optional[int] = ..., ingested_at: _Optional[int] = ..., symbol: _Optional[str] = ...) -> None: ...

class Trade(_message.Message):
    __slots__ = ("instrument_id", "event_time", "price", "size", "conditions", "feed", "available_at", "ingested_at", "symbol", "trade_id", "exchange")
    INSTRUMENT_ID_FIELD_NUMBER: _ClassVar[int]
    EVENT_TIME_FIELD_NUMBER: _ClassVar[int]
    PRICE_FIELD_NUMBER: _ClassVar[int]
    SIZE_FIELD_NUMBER: _ClassVar[int]
    CONDITIONS_FIELD_NUMBER: _ClassVar[int]
    FEED_FIELD_NUMBER: _ClassVar[int]
    AVAILABLE_AT_FIELD_NUMBER: _ClassVar[int]
    INGESTED_AT_FIELD_NUMBER: _ClassVar[int]
    SYMBOL_FIELD_NUMBER: _ClassVar[int]
    TRADE_ID_FIELD_NUMBER: _ClassVar[int]
    EXCHANGE_FIELD_NUMBER: _ClassVar[int]
    instrument_id: str
    event_time: int
    price: _common_pb2.Decimal
    size: _common_pb2.Decimal
    conditions: _containers.RepeatedScalarFieldContainer[str]
    feed: str
    available_at: int
    ingested_at: int
    symbol: str
    trade_id: str
    exchange: str
    def __init__(self, instrument_id: _Optional[str] = ..., event_time: _Optional[int] = ..., price: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., size: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., conditions: _Optional[_Iterable[str]] = ..., feed: _Optional[str] = ..., available_at: _Optional[int] = ..., ingested_at: _Optional[int] = ..., symbol: _Optional[str] = ..., trade_id: _Optional[str] = ..., exchange: _Optional[str] = ...) -> None: ...

class CorporateAction(_message.Message):
    __slots__ = ("instrument_id", "type", "ex_date", "ratio", "amount", "successor_instrument_id", "source", "available_at", "ingested_at", "symbol", "new_symbol", "record_date", "payable_date", "declared_at", "source_id", "agreement", "sources")
    INSTRUMENT_ID_FIELD_NUMBER: _ClassVar[int]
    TYPE_FIELD_NUMBER: _ClassVar[int]
    EX_DATE_FIELD_NUMBER: _ClassVar[int]
    RATIO_FIELD_NUMBER: _ClassVar[int]
    AMOUNT_FIELD_NUMBER: _ClassVar[int]
    SUCCESSOR_INSTRUMENT_ID_FIELD_NUMBER: _ClassVar[int]
    SOURCE_FIELD_NUMBER: _ClassVar[int]
    AVAILABLE_AT_FIELD_NUMBER: _ClassVar[int]
    INGESTED_AT_FIELD_NUMBER: _ClassVar[int]
    SYMBOL_FIELD_NUMBER: _ClassVar[int]
    NEW_SYMBOL_FIELD_NUMBER: _ClassVar[int]
    RECORD_DATE_FIELD_NUMBER: _ClassVar[int]
    PAYABLE_DATE_FIELD_NUMBER: _ClassVar[int]
    DECLARED_AT_FIELD_NUMBER: _ClassVar[int]
    SOURCE_ID_FIELD_NUMBER: _ClassVar[int]
    AGREEMENT_FIELD_NUMBER: _ClassVar[int]
    SOURCES_FIELD_NUMBER: _ClassVar[int]
    instrument_id: str
    type: CorporateActionType
    ex_date: int
    ratio: _common_pb2.Decimal
    amount: _common_pb2.Decimal
    successor_instrument_id: str
    source: str
    available_at: int
    ingested_at: int
    symbol: str
    new_symbol: str
    record_date: int
    payable_date: int
    declared_at: int
    source_id: str
    agreement: AgreementStatus
    sources: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, instrument_id: _Optional[str] = ..., type: _Optional[_Union[CorporateActionType, str]] = ..., ex_date: _Optional[int] = ..., ratio: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., amount: _Optional[_Union[_common_pb2.Decimal, _Mapping]] = ..., successor_instrument_id: _Optional[str] = ..., source: _Optional[str] = ..., available_at: _Optional[int] = ..., ingested_at: _Optional[int] = ..., symbol: _Optional[str] = ..., new_symbol: _Optional[str] = ..., record_date: _Optional[int] = ..., payable_date: _Optional[int] = ..., declared_at: _Optional[int] = ..., source_id: _Optional[str] = ..., agreement: _Optional[_Union[AgreementStatus, str]] = ..., sources: _Optional[_Iterable[str]] = ...) -> None: ...

class ResolveSymbolsRequest(_message.Message):
    __slots__ = ("symbols",)
    SYMBOLS_FIELD_NUMBER: _ClassVar[int]
    symbols: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, symbols: _Optional[_Iterable[str]] = ...) -> None: ...

class SymbolMatch(_message.Message):
    __slots__ = ("symbol", "instrument_id")
    SYMBOL_FIELD_NUMBER: _ClassVar[int]
    INSTRUMENT_ID_FIELD_NUMBER: _ClassVar[int]
    symbol: str
    instrument_id: str
    def __init__(self, symbol: _Optional[str] = ..., instrument_id: _Optional[str] = ...) -> None: ...

class ResolveSymbolsResponse(_message.Message):
    __slots__ = ("matches", "unknown")
    MATCHES_FIELD_NUMBER: _ClassVar[int]
    UNKNOWN_FIELD_NUMBER: _ClassVar[int]
    matches: _containers.RepeatedCompositeFieldContainer[SymbolMatch]
    unknown: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, matches: _Optional[_Iterable[_Union[SymbolMatch, _Mapping]]] = ..., unknown: _Optional[_Iterable[str]] = ...) -> None: ...

class GetBarsRequest(_message.Message):
    __slots__ = ("instrument_id", "timeframe", "start", "end", "limit")
    INSTRUMENT_ID_FIELD_NUMBER: _ClassVar[int]
    TIMEFRAME_FIELD_NUMBER: _ClassVar[int]
    START_FIELD_NUMBER: _ClassVar[int]
    END_FIELD_NUMBER: _ClassVar[int]
    LIMIT_FIELD_NUMBER: _ClassVar[int]
    instrument_id: str
    timeframe: Timeframe
    start: int
    end: int
    limit: int
    def __init__(self, instrument_id: _Optional[str] = ..., timeframe: _Optional[_Union[Timeframe, str]] = ..., start: _Optional[int] = ..., end: _Optional[int] = ..., limit: _Optional[int] = ...) -> None: ...

class GetBarsResponse(_message.Message):
    __slots__ = ("bars", "source")
    BARS_FIELD_NUMBER: _ClassVar[int]
    SOURCE_FIELD_NUMBER: _ClassVar[int]
    bars: _containers.RepeatedCompositeFieldContainer[Bar]
    source: str
    def __init__(self, bars: _Optional[_Iterable[_Union[Bar, _Mapping]]] = ..., source: _Optional[str] = ...) -> None: ...

class BackfillRequest(_message.Message):
    __slots__ = ("instrument_ids", "universe", "timeframes", "start", "end", "source", "requested_by")
    INSTRUMENT_IDS_FIELD_NUMBER: _ClassVar[int]
    UNIVERSE_FIELD_NUMBER: _ClassVar[int]
    TIMEFRAMES_FIELD_NUMBER: _ClassVar[int]
    START_FIELD_NUMBER: _ClassVar[int]
    END_FIELD_NUMBER: _ClassVar[int]
    SOURCE_FIELD_NUMBER: _ClassVar[int]
    REQUESTED_BY_FIELD_NUMBER: _ClassVar[int]
    instrument_ids: _containers.RepeatedScalarFieldContainer[str]
    universe: str
    timeframes: _containers.RepeatedScalarFieldContainer[Timeframe]
    start: int
    end: int
    source: str
    requested_by: str
    def __init__(self, instrument_ids: _Optional[_Iterable[str]] = ..., universe: _Optional[str] = ..., timeframes: _Optional[_Iterable[_Union[Timeframe, str]]] = ..., start: _Optional[int] = ..., end: _Optional[int] = ..., source: _Optional[str] = ..., requested_by: _Optional[str] = ...) -> None: ...

class BackfillResponse(_message.Message):
    __slots__ = ("job_id",)
    JOB_ID_FIELD_NUMBER: _ClassVar[int]
    job_id: str
    def __init__(self, job_id: _Optional[str] = ...) -> None: ...

class GetBackfillStatusRequest(_message.Message):
    __slots__ = ("job_id",)
    JOB_ID_FIELD_NUMBER: _ClassVar[int]
    job_id: str
    def __init__(self, job_id: _Optional[str] = ...) -> None: ...

class BackfillReport(_message.Message):
    __slots__ = ("chunks_total", "chunks_done", "rows_fetched", "rows_added", "rows_revised", "gaps_found", "gaps_repaired", "gaps_confirmed_missing", "dq_findings")
    class DqFindingsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: int
        def __init__(self, key: _Optional[str] = ..., value: _Optional[int] = ...) -> None: ...
    CHUNKS_TOTAL_FIELD_NUMBER: _ClassVar[int]
    CHUNKS_DONE_FIELD_NUMBER: _ClassVar[int]
    ROWS_FETCHED_FIELD_NUMBER: _ClassVar[int]
    ROWS_ADDED_FIELD_NUMBER: _ClassVar[int]
    ROWS_REVISED_FIELD_NUMBER: _ClassVar[int]
    GAPS_FOUND_FIELD_NUMBER: _ClassVar[int]
    GAPS_REPAIRED_FIELD_NUMBER: _ClassVar[int]
    GAPS_CONFIRMED_MISSING_FIELD_NUMBER: _ClassVar[int]
    DQ_FINDINGS_FIELD_NUMBER: _ClassVar[int]
    chunks_total: int
    chunks_done: int
    rows_fetched: int
    rows_added: int
    rows_revised: int
    gaps_found: int
    gaps_repaired: int
    gaps_confirmed_missing: int
    dq_findings: _containers.ScalarMap[str, int]
    def __init__(self, chunks_total: _Optional[int] = ..., chunks_done: _Optional[int] = ..., rows_fetched: _Optional[int] = ..., rows_added: _Optional[int] = ..., rows_revised: _Optional[int] = ..., gaps_found: _Optional[int] = ..., gaps_repaired: _Optional[int] = ..., gaps_confirmed_missing: _Optional[int] = ..., dq_findings: _Optional[_Mapping[str, int]] = ...) -> None: ...

class GetBackfillStatusResponse(_message.Message):
    __slots__ = ("job_id", "state", "report", "error", "started_at", "finished_at")
    JOB_ID_FIELD_NUMBER: _ClassVar[int]
    STATE_FIELD_NUMBER: _ClassVar[int]
    REPORT_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    STARTED_AT_FIELD_NUMBER: _ClassVar[int]
    FINISHED_AT_FIELD_NUMBER: _ClassVar[int]
    job_id: str
    state: JobState
    report: BackfillReport
    error: str
    started_at: int
    finished_at: int
    def __init__(self, job_id: _Optional[str] = ..., state: _Optional[_Union[JobState, str]] = ..., report: _Optional[_Union[BackfillReport, _Mapping]] = ..., error: _Optional[str] = ..., started_at: _Optional[int] = ..., finished_at: _Optional[int] = ...) -> None: ...

class SetUniverseRequest(_message.Message):
    __slots__ = ("universe", "instrument_ids")
    UNIVERSE_FIELD_NUMBER: _ClassVar[int]
    INSTRUMENT_IDS_FIELD_NUMBER: _ClassVar[int]
    universe: str
    instrument_ids: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, universe: _Optional[str] = ..., instrument_ids: _Optional[_Iterable[str]] = ...) -> None: ...

class SetUniverseResponse(_message.Message):
    __slots__ = ("subscribed",)
    SUBSCRIBED_FIELD_NUMBER: _ClassVar[int]
    subscribed: int
    def __init__(self, subscribed: _Optional[int] = ...) -> None: ...

class FeedStatus(_message.Message):
    __slots__ = ("feed", "connected", "last_message_at", "lag_ms", "symbols_stale", "state")
    FEED_FIELD_NUMBER: _ClassVar[int]
    CONNECTED_FIELD_NUMBER: _ClassVar[int]
    LAST_MESSAGE_AT_FIELD_NUMBER: _ClassVar[int]
    LAG_MS_FIELD_NUMBER: _ClassVar[int]
    SYMBOLS_STALE_FIELD_NUMBER: _ClassVar[int]
    STATE_FIELD_NUMBER: _ClassVar[int]
    feed: str
    connected: bool
    last_message_at: int
    lag_ms: int
    symbols_stale: _containers.RepeatedScalarFieldContainer[str]
    state: str
    def __init__(self, feed: _Optional[str] = ..., connected: _Optional[bool] = ..., last_message_at: _Optional[int] = ..., lag_ms: _Optional[int] = ..., symbols_stale: _Optional[_Iterable[str]] = ..., state: _Optional[str] = ...) -> None: ...

class GetFeedStatusRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class GetFeedStatusResponse(_message.Message):
    __slots__ = ("feeds",)
    FEEDS_FIELD_NUMBER: _ClassVar[int]
    feeds: _containers.RepeatedCompositeFieldContainer[FeedStatus]
    def __init__(self, feeds: _Optional[_Iterable[_Union[FeedStatus, _Mapping]]] = ...) -> None: ...

class IsFreshRequest(_message.Message):
    __slots__ = ("instrument_id", "timeframe", "max_age")
    INSTRUMENT_ID_FIELD_NUMBER: _ClassVar[int]
    TIMEFRAME_FIELD_NUMBER: _ClassVar[int]
    MAX_AGE_FIELD_NUMBER: _ClassVar[int]
    instrument_id: str
    timeframe: Timeframe
    max_age: int
    def __init__(self, instrument_id: _Optional[str] = ..., timeframe: _Optional[_Union[Timeframe, str]] = ..., max_age: _Optional[int] = ...) -> None: ...

class IsFreshResponse(_message.Message):
    __slots__ = ("fresh", "last_bar_close", "reason")
    FRESH_FIELD_NUMBER: _ClassVar[int]
    LAST_BAR_CLOSE_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    fresh: bool
    last_bar_close: int
    reason: str
    def __init__(self, fresh: _Optional[bool] = ..., last_bar_close: _Optional[int] = ..., reason: _Optional[str] = ...) -> None: ...

class SourceHealth(_message.Message):
    __slots__ = ("source", "last_message_age_ms", "gap_count", "error_rate", "breaker_state", "reconciliation_status", "as_of")
    SOURCE_FIELD_NUMBER: _ClassVar[int]
    LAST_MESSAGE_AGE_MS_FIELD_NUMBER: _ClassVar[int]
    GAP_COUNT_FIELD_NUMBER: _ClassVar[int]
    ERROR_RATE_FIELD_NUMBER: _ClassVar[int]
    BREAKER_STATE_FIELD_NUMBER: _ClassVar[int]
    RECONCILIATION_STATUS_FIELD_NUMBER: _ClassVar[int]
    AS_OF_FIELD_NUMBER: _ClassVar[int]
    source: str
    last_message_age_ms: int
    gap_count: int
    error_rate: float
    breaker_state: str
    reconciliation_status: str
    as_of: int
    def __init__(self, source: _Optional[str] = ..., last_message_age_ms: _Optional[int] = ..., gap_count: _Optional[int] = ..., error_rate: _Optional[float] = ..., breaker_state: _Optional[str] = ..., reconciliation_status: _Optional[str] = ..., as_of: _Optional[int] = ...) -> None: ...
