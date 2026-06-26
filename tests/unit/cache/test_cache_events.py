from infertwin.cache.events import EVICT, LOOKUP_MISS, MATERIALIZE
from infertwin.cache.hbm_lru import HBMCache
from infertwin.request.block_hasher import PrefixBlock


def test_hbm_cache_events_include_required_context_and_drain() -> None:
    cache = HBMCache(capacity_blocks=1)
    first = _block("first", 0)
    second = _block("second", 1)

    cache.lookup_prefix((first,), now_ms=1.0, request_id="r1", instance_uuid="i1")
    cache.materialize((first,), now_ms=2.0, request_id="r1", instance_uuid="i1")
    cache.materialize((second,), now_ms=3.0, request_id="r2", instance_uuid="i1")

    events = cache.take_events()

    assert [event.event_type for event in events] == [
        LOOKUP_MISS,
        MATERIALIZE,
        EVICT,
        MATERIALIZE,
    ]
    assert events[0].request_id == "r1"
    assert events[0].instance_uuid == "i1"
    assert events[0].block_key == "first"
    assert events[0].cache_tier == "hbm"
    assert events[2].reason == "capacity"
    assert events[2].eviction_policy == "lru"
    assert events[2].hbm_used_blocks == 0
    assert events[2].hbm_capacity_blocks == 1
    assert cache.take_events() == ()


def _block(block_key: str, block_index: int) -> PrefixBlock:
    return PrefixBlock(
        block_key=block_key,
        content_hash=f"content-{block_key}",
        block_index=block_index,
        token_count=16,
        size_bytes=0,
    )
