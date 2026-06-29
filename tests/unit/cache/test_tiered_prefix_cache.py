from infertwin.cache.ddr_lru import DDRLRUCache
from infertwin.cache.events import CACHE_TIER_DDR, CACHE_TIER_HBM, LOOKUP_HIT, LOOKUP_MISS, STORE
from infertwin.cache.hbm_lru import HBMCache
from infertwin.cache.tiered import TieredPrefixCache
from infertwin.request.block_hasher import PrefixBlock


def test_tiered_lookup_prefers_hbm_over_ddr() -> None:
    block = _block("a", 0)
    cache = _cache()
    cache.materialize((block,), now_ms=1.0)
    cache.take_events()

    result = cache.lookup_prefix((block,), now_ms=2.0, request_id="r1", instance_uuid="i1")

    assert result.hbm_hit_blocks == (block,)
    assert result.ddr_hit_blocks == ()
    assert result.miss_blocks == ()
    assert cache.hbm_resident_blocks == 1
    assert cache.ddr_resident_blocks == 1
    events = cache.take_events()
    assert [event.event_type for event in events] == [LOOKUP_HIT]
    assert events[0].cache_tier == CACHE_TIER_HBM


def test_tiered_lookup_uses_ddr_after_hbm_miss_tail() -> None:
    blocks = (_block("a", 0), _block("b", 1), _block("c", 2), _block("d", 3))
    cache = _cache()
    cache.hbm.materialize((blocks[0],), now_ms=1.0)
    cache.ddr.store((blocks[1], blocks[2]), now_ms=1.0)
    cache.take_events()

    result = cache.lookup_prefix(blocks, now_ms=2.0, request_id="r1", instance_uuid="i1")

    assert result.hbm_hit_blocks == (blocks[0],)
    assert result.ddr_hit_blocks == (blocks[1], blocks[2])
    assert result.miss_blocks == (blocks[3],)
    assert result.hbm_hit_tokens + result.ddr_hit_tokens + result.miss_tokens == 64
    events = cache.take_events()
    assert [event.cache_tier for event in events] == [
        CACHE_TIER_HBM,
        CACHE_TIER_HBM,
        CACHE_TIER_HBM,
        CACHE_TIER_HBM,
        CACHE_TIER_DDR,
        CACHE_TIER_DDR,
        CACHE_TIER_DDR,
    ]
    assert [event.event_type for event in events] == [
        LOOKUP_HIT,
        LOOKUP_MISS,
        LOOKUP_MISS,
        LOOKUP_MISS,
        LOOKUP_HIT,
        LOOKUP_HIT,
        LOOKUP_MISS,
    ]
    assert events[4].source_tier == CACHE_TIER_DDR
    assert events[4].hbm_used_blocks == 1
    assert events[4].hbm_capacity_blocks == 4


def test_tiered_lookup_does_not_skip_middle_ddr_miss() -> None:
    blocks = (_block("a", 0), _block("b", 1), _block("c", 2))
    cache = _cache()
    cache.hbm.materialize((blocks[0],), now_ms=1.0)
    cache.ddr.store((blocks[2],), now_ms=1.0)
    cache.take_events()

    result = cache.lookup_prefix(blocks, now_ms=2.0)

    assert result.hbm_hit_blocks == (blocks[0],)
    assert result.ddr_hit_blocks == ()
    assert result.miss_blocks == (blocks[1], blocks[2])


def test_ddr_hit_does_not_promote_to_hbm() -> None:
    block = _block("a", 0)
    cache = _cache()
    cache.ddr.store((block,), now_ms=1.0)
    cache.take_events()

    result = cache.lookup_prefix((block,), now_ms=2.0)

    assert result.hbm_hit_blocks == ()
    assert result.ddr_hit_blocks == (block,)
    assert not cache.hbm.contains(block.block_key)
    assert cache.ddr.contains(block.block_key)
    assert cache.hbm_resident_blocks == 0
    assert cache.ddr_resident_blocks == 1


def test_tiered_materialize_writes_hbm_and_ddr_in_stable_event_order() -> None:
    blocks = (_block("a", 0), _block("b", 1))
    cache = _cache()

    cache.materialize(blocks, now_ms=1.0, request_id="r1", instance_uuid="i1")

    assert all(cache.hbm.contains(block.block_key) for block in blocks)
    assert all(cache.ddr.contains(block.block_key) for block in blocks)
    events = cache.take_events()
    assert [event.cache_tier for event in events] == [
        CACHE_TIER_HBM,
        CACHE_TIER_HBM,
        CACHE_TIER_DDR,
        CACHE_TIER_DDR,
    ]
    assert [event.event_type for event in events] == [
        "materialize",
        "materialize",
        STORE,
        STORE,
    ]
    assert events[2].target_tier == CACHE_TIER_DDR


def test_tiered_progressive_materialize_uses_progressive_store_reason() -> None:
    block = _block("a", 0)
    cache = _cache()

    cache.materialize(
        (block,),
        now_ms=1.0,
        request_id="r1",
        instance_uuid="i1",
        reason="progressive_chunk_materialization",
    )

    events = cache.take_events()
    assert [(event.cache_tier, event.reason) for event in events] == [
        (CACHE_TIER_HBM, "progressive_chunk_materialization"),
        (CACHE_TIER_DDR, "progressive_chunk_store"),
    ]


def test_tiered_materialize_over_capacity_keeps_tier_suffixes_without_oom() -> None:
    blocks = (_block("a", 0), _block("b", 1), _block("c", 2))
    cache = _cache(hbm_capacity=1, ddr_capacity=2)

    cache.materialize(blocks, now_ms=1.0)
    result = cache.lookup_prefix(blocks, now_ms=2.0)

    assert not cache.hbm.contains("a")
    assert not cache.hbm.contains("b")
    assert cache.hbm.contains("c")
    assert not cache.ddr.contains("a")
    assert cache.ddr.contains("b")
    assert cache.ddr.contains("c")
    assert result.hbm_hit_blocks == ()
    assert result.ddr_hit_blocks == ()
    assert result.miss_blocks == blocks


def test_tiered_contains_and_resident_stats_include_both_tiers() -> None:
    hbm_only = _block("hbm", 0)
    ddr_only = _block("ddr", 1)
    cache = _cache()
    cache.hbm.materialize((hbm_only,), now_ms=1.0)
    cache.ddr.store((ddr_only,), now_ms=1.0)
    cache.take_events()

    assert cache.contains(hbm_only.block_key)
    assert cache.contains(ddr_only.block_key)
    assert not cache.contains("missing")
    assert cache.resident_blocks == 2
    assert cache.hbm_resident_blocks == 1
    assert cache.ddr_resident_blocks == 1
    assert cache.stats.hbm_resident_blocks == 1
    assert cache.stats.hbm_capacity_blocks == 4
    assert cache.stats.ddr_resident_blocks == 1
    assert cache.stats.ddr_capacity_blocks == 8


def test_tiered_take_events_drains_both_tiers() -> None:
    cache = _cache()
    cache.materialize((_block("a", 0),), now_ms=1.0)

    assert len(cache.take_events()) == 2
    assert cache.take_events() == ()


def _cache(*, hbm_capacity: int = 4, ddr_capacity: int = 8) -> TieredPrefixCache:
    return TieredPrefixCache(
        hbm=HBMCache(capacity_blocks=hbm_capacity),
        ddr=DDRLRUCache(capacity_blocks=ddr_capacity),
    )


def _block(block_key: str, block_index: int) -> PrefixBlock:
    return PrefixBlock(
        block_key=block_key,
        content_hash=f"content-{block_key}",
        block_index=block_index,
        token_count=16,
        size_bytes=0,
    )
