"""Cache event schemas emitted by prefix cache implementations."""

from __future__ import annotations

from dataclasses import dataclass


LOOKUP_HIT = "lookup_hit"
LOOKUP_MISS = "lookup_miss"
MATERIALIZE = "materialize"
EVICT = "evict"
STORE = "store"
CACHE_TIER_HBM = "hbm"
CACHE_TIER_DDR = "ddr"


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
    ddr_used_blocks: int = 0
    ddr_capacity_blocks: int = 0
    source_tier: str = ""
    target_tier: str = ""
    load_tokens: int = 0
    store_tokens: int = 0
