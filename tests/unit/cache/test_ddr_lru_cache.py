from infertwin.cache.ddr_lru import DDRLRUCache
from infertwin.cache.events import CACHE_TIER_DDR, EVICT, LOOKUP_HIT, LOOKUP_MISS, STORE
from infertwin.request.block_hasher import PrefixBlock


def test_empty_ddr_cache_lookup_returns_all_miss() -> None:
    blocks = (_block("a", 0), _block("b", 1))
    cache = DDRLRUCache(capacity_blocks=4)

    result = cache.lookup_prefix(blocks, now_ms=1.0, request_id="r1", instance_uuid="i1")

    assert result.hbm_hit_blocks == ()
    assert result.ddr_hit_blocks == ()
    assert result.miss_blocks == blocks
    assert result.ddr_hit_tokens == 0
    assert result.miss_tokens == 32
    events = cache.take_events()
    assert [event.event_type for event in events] == [LOOKUP_MISS, LOOKUP_MISS]
    assert {event.cache_tier for event in events} == {CACHE_TIER_DDR}
    assert {event.ddr_used_blocks for event in events} == {0}
    assert {event.ddr_capacity_blocks for event in events} == {4}


def test_stored_blocks_are_visible_to_later_ddr_lookup() -> None:
    blocks = (_block("a", 0), _block("b", 1))
    cache = DDRLRUCache(capacity_blocks=4)

    cache.store(blocks, now_ms=10.0, request_id="r1", instance_uuid="i1")
    result = cache.lookup_prefix(
        blocks,
        now_ms=11.0,
        request_id="r2",
        instance_uuid="i1",
        hbm_used_blocks=3,
        hbm_capacity_blocks=8,
    )

    assert result.hbm_hit_blocks == ()
    assert result.ddr_hit_blocks == blocks
    assert result.miss_blocks == ()
    assert result.ddr_hit_tokens == 32
    assert cache.resident_blocks == 2
    events = cache.take_events()
    assert [event.event_type for event in events] == [STORE, STORE, LOOKUP_HIT, LOOKUP_HIT]
    assert events[-1].source_tier == CACHE_TIER_DDR
    assert events[-1].hbm_used_blocks == 3
    assert events[-1].hbm_capacity_blocks == 8


def test_lookup_only_hits_contiguous_ddr_prefix() -> None:
    requested = (_block("a", 0), _block("b", 1), _block("c", 2))
    cache = DDRLRUCache(capacity_blocks=4)
    cache.store((requested[0], requested[2]), now_ms=10.0)
    cache.take_events()

    result = cache.lookup_prefix(requested, now_ms=11.0)

    assert result.ddr_hit_blocks == (requested[0],)
    assert result.miss_blocks == (requested[1], requested[2])
    assert [event.event_type for event in cache.take_events()] == [
        LOOKUP_HIT,
        LOOKUP_MISS,
        LOOKUP_MISS,
    ]


def test_capacity_eviction_uses_lru_and_hit_refreshes_recency() -> None:
    first = _block("a", 0)
    second = _block("b", 1)
    third = _block("c", 2)
    cache = DDRLRUCache(capacity_blocks=2)

    cache.store((first, second), now_ms=1.0)
    cache.lookup_prefix((first,), now_ms=2.0)
    cache.store((third,), now_ms=3.0, request_id="r2", instance_uuid="i1")

    assert cache.contains(first.block_key)
    assert not cache.contains(second.block_key)
    assert cache.contains(third.block_key)
    assert cache.resident_blocks == 2
    events = cache.take_events()
    assert EVICT in [event.event_type for event in events]
    evict_event = next(event for event in events if event.event_type == EVICT)
    assert evict_event.block_key == second.block_key
    assert evict_event.cache_tier == CACHE_TIER_DDR
    assert evict_event.ddr_used_blocks == 1
    assert evict_event.ddr_capacity_blocks == 2


def test_storing_existing_block_does_not_duplicate_capacity_or_emit_store() -> None:
    block = _block("a", 0)
    cache = DDRLRUCache(capacity_blocks=1)

    cache.store((block,), now_ms=1.0)
    cache.store((block,), now_ms=2.0)

    assert cache.resident_blocks == 1
    assert [event.event_type for event in cache.take_events()] == [STORE]


def test_prompt_larger_than_capacity_keeps_suffix_and_can_break_prefix_hit() -> None:
    blocks = (_block("a", 0), _block("b", 1), _block("c", 2))
    cache = DDRLRUCache(capacity_blocks=2)

    cache.store(blocks, now_ms=1.0)
    result = cache.lookup_prefix(blocks, now_ms=2.0)

    assert not cache.contains("a")
    assert cache.contains("b")
    assert cache.contains("c")
    assert cache.resident_blocks == 2
    assert result.ddr_hit_blocks == ()
    assert result.miss_blocks == blocks


def test_capacity_blocks_must_be_positive() -> None:
    try:
        DDRLRUCache(capacity_blocks=0)
    except ValueError as exc:
        assert "capacity_blocks" in str(exc)
    else:
        raise AssertionError("expected non-positive capacity to fail")


def test_ddr_cache_calls_stateful_eviction_policy_hooks() -> None:
    first = _block("a", 0)
    second = _block("b", 1)
    policy = _RecordingPolicy()
    cache = DDRLRUCache(capacity_blocks=1, evictor=policy)

    cache.store((first,), now_ms=1.0)
    cache.lookup_prefix((first,), now_ms=2.0)
    cache.store((first,), now_ms=3.0)
    cache.store((second,), now_ms=4.0)

    assert policy.calls == [
        ("insert", "a"),
        ("access:lookup_hit", "a"),
        ("access:store_existing", "a"),
        ("select", ""),
        ("remove", "a"),
        ("insert", "b"),
    ]


def test_ddr_store_events_include_tier_context() -> None:
    block = _block("a", 0)
    cache = DDRLRUCache(capacity_blocks=4)

    cache.store(
        (block,),
        now_ms=1.0,
        request_id="r1",
        instance_uuid="i1",
        hbm_used_blocks=2,
        hbm_capacity_blocks=8,
    )

    event = cache.take_events()[0]
    assert event.event_type == STORE
    assert event.cache_tier == CACHE_TIER_DDR
    assert event.reason == "finish_time_store"
    assert event.target_tier == CACHE_TIER_DDR
    assert event.store_tokens == block.token_count
    assert event.ddr_used_blocks == 1
    assert event.ddr_capacity_blocks == 4
    assert event.hbm_used_blocks == 2
    assert event.hbm_capacity_blocks == 8


def test_ddr_store_event_uses_requested_reason() -> None:
    block = _block("a", 0)
    cache = DDRLRUCache(capacity_blocks=4)

    cache.store(
        (block,),
        now_ms=1.0,
        request_id="r1",
        instance_uuid="i1",
        reason="progressive_chunk_store",
    )

    event = cache.take_events()[0]
    assert event.event_type == STORE
    assert event.reason == "progressive_chunk_store"


def test_take_events_drains_ddr_events() -> None:
    cache = DDRLRUCache(capacity_blocks=1)
    cache.store((_block("a", 0),), now_ms=1.0)

    assert len(cache.take_events()) == 1
    assert cache.take_events() == ()


class _RecordingPolicy:
    name = "recording"

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def on_insert(self, block) -> None:
        self.calls.append(("insert", block.block_key))

    def on_access(self, block, *, reason: str) -> None:
        self.calls.append((f"access:{reason}", block.block_key))

    def on_remove(self, block) -> None:
        self.calls.append(("remove", block.block_key))

    def select_victim(self, blocks):
        self.calls.append(("select", ""))
        return next(iter(blocks.values()))


def _block(block_key: str, block_index: int) -> PrefixBlock:
    return PrefixBlock(
        block_key=block_key,
        content_hash=f"content-{block_key}",
        block_index=block_index,
        token_count=16,
        size_bytes=0,
    )
