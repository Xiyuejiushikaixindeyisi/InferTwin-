"""Cache event schemas emitted by prefix cache implementations."""

from __future__ import annotations

from dataclasses import dataclass


LOOKUP_HIT = "lookup_hit"
LOOKUP_MISS = "lookup_miss"
MATERIALIZE = "materialize"
EVICT = "evict"
CACHE_TIER_HBM = "hbm"


@dataclass(frozen=True, slots=True)
class CacheEvent:
    event_type: str
    timestamp_ms: float
    instance_uuid: str
    request_id: str
    block_key: str
    block_index: int
    token_count: int
    cache_tier: str
    reason: str
    eviction_policy: str
    hbm_used_blocks: int
    hbm_capacity_blocks: int
