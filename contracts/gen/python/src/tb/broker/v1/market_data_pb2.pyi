from tb.marketdata.v1 import marketdata_pb2 as _marketdata_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class FeedState(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    FEED_STATE_UNSPECIFIED: _ClassVar[FeedState]
    FEED_STATE_CONNECTING: _ClassVar[FeedState]
    FEED_STATE_CONNECTED: _ClassVar[FeedState]
    FEED_STATE_DISCONNECTED: _ClassVar[FeedState]
    FEED_STATE_CONNECTION_LIMIT: _ClassVar[FeedState]
    FEED_STATE_AUTH_FAILED: _ClassVar[FeedState]
FEED_STATE_UNSPECIFIED: FeedState
FEED_STATE_CONNECTING: FeedState
FEED_STATE_CONNECTED: FeedState
FEED_STATE_DISCONNECTED: FeedState
FEED_STATE_CONNECTION_LIMIT: FeedState
FEED_STATE_AUTH_FAILED: FeedState

class GetNewsRequest(_message.Message):
    __slots__ = ("symbols", "start", "end", "page_token", "limit", "include_content")
    SYMBOLS_FIELD_NUMBER: _ClassVar[int]
    START_FIELD_NUMBER: _ClassVar[int]
    END_FIELD_NUMBER: _ClassVar[int]
    PAGE_TOKEN_FIELD_NUMBER: _ClassVar[int]
    LIMIT_FIELD_NUMBER: _ClassVar[int]
    INCLUDE_CONTENT_FIELD_NUMBER: _ClassVar[int]
    symbols: _containers.RepeatedScalarFieldContainer[str]
    start: int
    end: int
    page_token: str
    limit: int
    include_content: bool
    def __init__(self, symbols: _Optional[_Iterable[str]] = ..., start: _Optional[int] = ..., end: _Optional[int] = ..., page_token: _Optional[str] = ..., limit: _Optional[int] = ..., include_content: _Optional[bool] = ...) -> None: ...

class NewsArticle(_message.Message):
    __slots__ = ("id", "headline", "summary", "content", "author", "source", "url", "symbols", "created_at", "updated_at")
    ID_FIELD_NUMBER: _ClassVar[int]
    HEADLINE_FIELD_NUMBER: _ClassVar[int]
    SUMMARY_FIELD_NUMBER: _ClassVar[int]
    CONTENT_FIELD_NUMBER: _ClassVar[int]
    AUTHOR_FIELD_NUMBER: _ClassVar[int]
    SOURCE_FIELD_NUMBER: _ClassVar[int]
    URL_FIELD_NUMBER: _ClassVar[int]
    SYMBOLS_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_FIELD_NUMBER: _ClassVar[int]
    UPDATED_AT_FIELD_NUMBER: _ClassVar[int]
    id: str
    headline: str
    summary: str
    content: str
    author: str
    source: str
    url: str
    symbols: _containers.RepeatedScalarFieldContainer[str]
    created_at: int
    updated_at: int
    def __init__(self, id: _Optional[str] = ..., headline: _Optional[str] = ..., summary: _Optional[str] = ..., content: _Optional[str] = ..., author: _Optional[str] = ..., source: _Optional[str] = ..., url: _Optional[str] = ..., symbols: _Optional[_Iterable[str]] = ..., created_at: _Optional[int] = ..., updated_at: _Optional[int] = ...) -> None: ...

class GetNewsResponse(_message.Message):
    __slots__ = ("articles", "next_page_token")
    ARTICLES_FIELD_NUMBER: _ClassVar[int]
    NEXT_PAGE_TOKEN_FIELD_NUMBER: _ClassVar[int]
    articles: _containers.RepeatedCompositeFieldContainer[NewsArticle]
    next_page_token: str
    def __init__(self, articles: _Optional[_Iterable[_Union[NewsArticle, _Mapping]]] = ..., next_page_token: _Optional[str] = ...) -> None: ...

class GetHistoricalBarsRequest(_message.Message):
    __slots__ = ("symbols", "timeframe", "start", "end", "feed", "page_token", "limit")
    SYMBOLS_FIELD_NUMBER: _ClassVar[int]
    TIMEFRAME_FIELD_NUMBER: _ClassVar[int]
    START_FIELD_NUMBER: _ClassVar[int]
    END_FIELD_NUMBER: _ClassVar[int]
    FEED_FIELD_NUMBER: _ClassVar[int]
    PAGE_TOKEN_FIELD_NUMBER: _ClassVar[int]
    LIMIT_FIELD_NUMBER: _ClassVar[int]
    symbols: _containers.RepeatedScalarFieldContainer[str]
    timeframe: _marketdata_pb2.Timeframe
    start: int
    end: int
    feed: str
    page_token: str
    limit: int
    def __init__(self, symbols: _Optional[_Iterable[str]] = ..., timeframe: _Optional[_Union[_marketdata_pb2.Timeframe, str]] = ..., start: _Optional[int] = ..., end: _Optional[int] = ..., feed: _Optional[str] = ..., page_token: _Optional[str] = ..., limit: _Optional[int] = ...) -> None: ...

class GetHistoricalBarsResponse(_message.Message):
    __slots__ = ("bars", "next_page_token")
    BARS_FIELD_NUMBER: _ClassVar[int]
    NEXT_PAGE_TOKEN_FIELD_NUMBER: _ClassVar[int]
    bars: _containers.RepeatedCompositeFieldContainer[_marketdata_pb2.Bar]
    next_page_token: str
    def __init__(self, bars: _Optional[_Iterable[_Union[_marketdata_pb2.Bar, _Mapping]]] = ..., next_page_token: _Optional[str] = ...) -> None: ...

class Subscription(_message.Message):
    __slots__ = ("bars", "updated_bars", "quotes", "trades")
    BARS_FIELD_NUMBER: _ClassVar[int]
    UPDATED_BARS_FIELD_NUMBER: _ClassVar[int]
    QUOTES_FIELD_NUMBER: _ClassVar[int]
    TRADES_FIELD_NUMBER: _ClassVar[int]
    bars: _containers.RepeatedScalarFieldContainer[str]
    updated_bars: _containers.RepeatedScalarFieldContainer[str]
    quotes: _containers.RepeatedScalarFieldContainer[str]
    trades: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, bars: _Optional[_Iterable[str]] = ..., updated_bars: _Optional[_Iterable[str]] = ..., quotes: _Optional[_Iterable[str]] = ..., trades: _Optional[_Iterable[str]] = ...) -> None: ...

class StreamMarketDataRequest(_message.Message):
    __slots__ = ("feed", "subscribe", "unsubscribe")
    FEED_FIELD_NUMBER: _ClassVar[int]
    SUBSCRIBE_FIELD_NUMBER: _ClassVar[int]
    UNSUBSCRIBE_FIELD_NUMBER: _ClassVar[int]
    feed: str
    subscribe: Subscription
    unsubscribe: Subscription
    def __init__(self, feed: _Optional[str] = ..., subscribe: _Optional[_Union[Subscription, _Mapping]] = ..., unsubscribe: _Optional[_Union[Subscription, _Mapping]] = ...) -> None: ...

class FeedStatus(_message.Message):
    __slots__ = ("feed", "state", "last_message_at", "connected_since", "reconnects", "subscribed_symbols", "detail")
    FEED_FIELD_NUMBER: _ClassVar[int]
    STATE_FIELD_NUMBER: _ClassVar[int]
    LAST_MESSAGE_AT_FIELD_NUMBER: _ClassVar[int]
    CONNECTED_SINCE_FIELD_NUMBER: _ClassVar[int]
    RECONNECTS_FIELD_NUMBER: _ClassVar[int]
    SUBSCRIBED_SYMBOLS_FIELD_NUMBER: _ClassVar[int]
    DETAIL_FIELD_NUMBER: _ClassVar[int]
    feed: str
    state: FeedState
    last_message_at: int
    connected_since: int
    reconnects: int
    subscribed_symbols: int
    detail: str
    def __init__(self, feed: _Optional[str] = ..., state: _Optional[_Union[FeedState, str]] = ..., last_message_at: _Optional[int] = ..., connected_since: _Optional[int] = ..., reconnects: _Optional[int] = ..., subscribed_symbols: _Optional[int] = ..., detail: _Optional[str] = ...) -> None: ...

class StreamMarketDataResponse(_message.Message):
    __slots__ = ("bar", "updated_bar", "quote", "trade", "status")
    BAR_FIELD_NUMBER: _ClassVar[int]
    UPDATED_BAR_FIELD_NUMBER: _ClassVar[int]
    QUOTE_FIELD_NUMBER: _ClassVar[int]
    TRADE_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    bar: _marketdata_pb2.Bar
    updated_bar: _marketdata_pb2.Bar
    quote: _marketdata_pb2.Quote
    trade: _marketdata_pb2.Trade
    status: FeedStatus
    def __init__(self, bar: _Optional[_Union[_marketdata_pb2.Bar, _Mapping]] = ..., updated_bar: _Optional[_Union[_marketdata_pb2.Bar, _Mapping]] = ..., quote: _Optional[_Union[_marketdata_pb2.Quote, _Mapping]] = ..., trade: _Optional[_Union[_marketdata_pb2.Trade, _Mapping]] = ..., status: _Optional[_Union[FeedStatus, _Mapping]] = ...) -> None: ...

class Asset(_message.Message):
    __slots__ = ("symbol", "broker_asset_id", "exchange", "asset_class", "name", "status", "tradable", "shortable", "easy_to_borrow", "fractionable", "marginable")
    SYMBOL_FIELD_NUMBER: _ClassVar[int]
    BROKER_ASSET_ID_FIELD_NUMBER: _ClassVar[int]
    EXCHANGE_FIELD_NUMBER: _ClassVar[int]
    ASSET_CLASS_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    TRADABLE_FIELD_NUMBER: _ClassVar[int]
    SHORTABLE_FIELD_NUMBER: _ClassVar[int]
    EASY_TO_BORROW_FIELD_NUMBER: _ClassVar[int]
    FRACTIONABLE_FIELD_NUMBER: _ClassVar[int]
    MARGINABLE_FIELD_NUMBER: _ClassVar[int]
    symbol: str
    broker_asset_id: str
    exchange: str
    asset_class: str
    name: str
    status: str
    tradable: bool
    shortable: bool
    easy_to_borrow: bool
    fractionable: bool
    marginable: bool
    def __init__(self, symbol: _Optional[str] = ..., broker_asset_id: _Optional[str] = ..., exchange: _Optional[str] = ..., asset_class: _Optional[str] = ..., name: _Optional[str] = ..., status: _Optional[str] = ..., tradable: _Optional[bool] = ..., shortable: _Optional[bool] = ..., easy_to_borrow: _Optional[bool] = ..., fractionable: _Optional[bool] = ..., marginable: _Optional[bool] = ...) -> None: ...

class ListAssetsRequest(_message.Message):
    __slots__ = ("include_inactive",)
    INCLUDE_INACTIVE_FIELD_NUMBER: _ClassVar[int]
    include_inactive: bool
    def __init__(self, include_inactive: _Optional[bool] = ...) -> None: ...

class ListAssetsResponse(_message.Message):
    __slots__ = ("assets", "as_of")
    ASSETS_FIELD_NUMBER: _ClassVar[int]
    AS_OF_FIELD_NUMBER: _ClassVar[int]
    assets: _containers.RepeatedCompositeFieldContainer[Asset]
    as_of: int
    def __init__(self, assets: _Optional[_Iterable[_Union[Asset, _Mapping]]] = ..., as_of: _Optional[int] = ...) -> None: ...

class GetCorporateActionsRequest(_message.Message):
    __slots__ = ("symbols", "start", "end")
    SYMBOLS_FIELD_NUMBER: _ClassVar[int]
    START_FIELD_NUMBER: _ClassVar[int]
    END_FIELD_NUMBER: _ClassVar[int]
    symbols: _containers.RepeatedScalarFieldContainer[str]
    start: int
    end: int
    def __init__(self, symbols: _Optional[_Iterable[str]] = ..., start: _Optional[int] = ..., end: _Optional[int] = ...) -> None: ...

class GetCorporateActionsResponse(_message.Message):
    __slots__ = ("actions",)
    ACTIONS_FIELD_NUMBER: _ClassVar[int]
    actions: _containers.RepeatedCompositeFieldContainer[_marketdata_pb2.CorporateAction]
    def __init__(self, actions: _Optional[_Iterable[_Union[_marketdata_pb2.CorporateAction, _Mapping]]] = ...) -> None: ...

class GetFeedStatusRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class GetFeedStatusResponse(_message.Message):
    __slots__ = ("feeds",)
    FEEDS_FIELD_NUMBER: _ClassVar[int]
    feeds: _containers.RepeatedCompositeFieldContainer[FeedStatus]
    def __init__(self, feeds: _Optional[_Iterable[_Union[FeedStatus, _Mapping]]] = ...) -> None: ...
