"""KV cache simulation package."""

from hitfloor.cache.events import (
    CACHE_TIER_HBM,
    EVICT,
    LOOKUP_HIT,
    LOOKUP_MISS,
    MATERIALIZE,
    CacheEvent,
)
from hitfloor.cache.event_sink import (
    CacheEventSink,
    CacheEventStats,
    InMemoryCacheEventSink,
    NullCacheEventSink,
    StatsOnlyCacheEventSink,
)
from hitfloor.cache.eviction import (
    HBMEvictionPolicy,
    HBMEvictor,
    LRUEvictionPolicy,
    LRUEvictor,
)
from hitfloor.cache.hbm_lru import HBMBlockMeta, HBMCache
from hitfloor.cache.infinite_hbm import InfiniteHBMCache
from hitfloor.cache.results import PrefixLookupResult

__all__ = [
    "CACHE_TIER_HBM",
    "EVICT",
    "LOOKUP_HIT",
    "LOOKUP_MISS",
    "MATERIALIZE",
    "CacheEvent",
    "CacheEventSink",
    "CacheEventStats",
    "HBMBlockMeta",
    "HBMCache",
    "HBMEvictionPolicy",
    "HBMEvictor",
    "InMemoryCacheEventSink",
    "InfiniteHBMCache",
    "LRUEvictionPolicy",
    "LRUEvictor",
    "NullCacheEventSink",
    "PrefixLookupResult",
    "StatsOnlyCacheEventSink",
]
