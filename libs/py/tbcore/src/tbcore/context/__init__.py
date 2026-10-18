"""Point-in-time context data: availability rules, lake reads, news handling, sentiment aggregates,
regimes, event blackouts, and context features (section 09, ADR-0032, rules CX-*)."""

from tbcore.context.availability import DEFAULT, AvailabilityConfig, Timing, parse_timing, stamp
from tbcore.context.canonical import canonical_json, payload_sha256
from tbcore.context.events import (
    BlackoutStrategy,
    EventBlackout,
    EventSource,
    InMemoryEventSource,
    ScheduledEvent,
    categorize,
)
from tbcore.context.features import CONTEXT_FEATURES, ContextFeatureSpec, context_features
from tbcore.context.news import DuplicateDetector, find_duplicates, normalize_headline
from tbcore.context.regime import REGIME_VERSION, compute_regimes
from tbcore.context.sentiment import SentimentAgg, aggregate
from tbcore.context.store import ContextStore, EarningsWindow

__all__ = [
    "CONTEXT_FEATURES",
    "DEFAULT",
    "REGIME_VERSION",
    "AvailabilityConfig",
    "BlackoutStrategy",
    "ContextFeatureSpec",
    "ContextStore",
    "DuplicateDetector",
    "EarningsWindow",
    "EventBlackout",
    "EventSource",
    "InMemoryEventSource",
    "ScheduledEvent",
    "SentimentAgg",
    "Timing",
    "aggregate",
    "canonical_json",
    "categorize",
    "compute_regimes",
    "context_features",
    "find_duplicates",
    "normalize_headline",
    "parse_timing",
    "payload_sha256",
    "stamp",
]
