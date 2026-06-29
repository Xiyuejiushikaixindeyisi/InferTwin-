from infertwin.cache.events import EVICT, LOOKUP_HIT, LOOKUP_MISS, MATERIALIZE
from infertwin.cache.hbm_lru import HBMCache
from infertwin.request.block_hasher import PrefixBlock


def test_empty_hbm_cache_lookup_returns_all_miss() -> None:
    blocks = (_block("a", 0), _block("b", 1))
    cache = HBMCache(capacity_blocks=4)

    result = cache.lookup_prefix(blocks, now_ms=1.0, request_id="r1", instance_uuid="i1")

    assert result.hbm_hit_blocks == ()
    assert result.miss_blocks == blocks
    assert result.hbm_hit_tokens == 0
    assert result.miss_tokens == 32
    assert [event.event_type for event in cache.take_events()] == [LOOKUP_MISS, LOOKUP_MISS]


def test_materialized_blocks_are_visible_to_later_lookup() -> None:
    blocks = (_block("a", 0), _block("b", 1))
    cache = HBMCache(capacity_blocks=4)

    cache.materialize(blocks, now_ms=10.0, request_id="r1", instance_uuid="i1")
    result = cache.lookup_prefix(blocks, now_ms=11.0, request_id="r2", instance_uuid="i1")

    assert result.hbm_hit_blocks == blocks
    assert result.miss_blocks == ()
    assert result.hbm_hit_tokens == 32
    assert cache.resident_blocks == 2


def test_materialize_event_uses_requested_reason() -> None:
    block = _block("a", 0)
    cache = HBMCache(capacity_blocks=4)

    cache.materialize(
        (block,),
        now_ms=10.0,
        request_id="r1",
        instance_uuid="i1",
        reason="progressive_chunk_materialization",
    )

    (event,) = cache.take_events()
    assert event.event_type == MATERIALIZE
    assert event.reason == "progressive_chunk_materialization"


def test_lookup_only_hits_contiguous_prefix() -> None:
    requested = (_block("a", 0), _block("b", 1), _block("c", 2))
    cache = HBMCache(capacity_blocks=4)
    cache.materialize((requested[0], requested[2]), now_ms=10.0)
    cache.take_events()

    result = cache.lookup_prefix(requested, now_ms=11.0)

    assert result.hbm_hit_blocks == (requested[0],)
    assert result.miss_blocks == (requested[1], requested[2])


def test_capacity_eviction_uses_lru_and_hit_refreshes_recency() -> None:
    first = _block("a", 0)
    second = _block("b", 1)
    third = _block("c", 2)
    cache = HBMCache(capacity_blocks=2)

    cache.materialize((first, second), now_ms=1.0)
    cache.lookup_prefix((first,), now_ms=2.0)
    cache.materialize((third,), now_ms=3.0, request_id="r2", instance_uuid="i1")

    assert cache.contains(first.block_key)
    assert not cache.contains(second.block_key)
    assert cache.contains(third.block_key)
    assert cache.resident_blocks == 2
    assert EVICT in [event.event_type for event in cache.take_events()]


def test_materializing_existing_block_does_not_duplicate_capacity() -> None:
    block = _block("a", 0)
    cache = HBMCache(capacity_blocks=1)

    cache.materialize((block,), now_ms=1.0)
    cache.materialize((block,), now_ms=2.0)

    assert cache.resident_blocks == 1
    assert [event.event_type for event in cache.take_events()] == [MATERIALIZE]


def test_prompt_larger_than_capacity_keeps_suffix_and_can_break_prefix_hit() -> None:
    blocks = (_block("a", 0), _block("b", 1), _block("c", 2))
    cache = HBMCache(capacity_blocks=2)

    cache.materialize(blocks, now_ms=1.0)
    result = cache.lookup_prefix(blocks, now_ms=2.0)

    assert not cache.contains("a")
    assert cache.contains("b")
    assert cache.contains("c")
    assert cache.resident_blocks == 2
    assert result.hbm_hit_blocks == ()
    assert result.miss_blocks == blocks


def test_capacity_blocks_must_be_positive() -> None:
    try:
        HBMCache(capacity_blocks=0)
    except ValueError as exc:
        assert "capacity_blocks" in str(exc)
    else:
        raise AssertionError("expected non-positive capacity to fail")


def test_hbm_cache_calls_stateful_eviction_policy_hooks() -> None:
    first = _block("a", 0)
    second = _block("b", 1)
    policy = _RecordingPolicy()
    cache = HBMCache(capacity_blocks=1, evictor=policy)

    cache.materialize((first,), now_ms=1.0)
    cache.lookup_prefix((first,), now_ms=2.0)
    cache.materialize((first,), now_ms=3.0)
    cache.materialize((second,), now_ms=4.0)

    assert policy.calls == [
        ("insert", "a"),
        ("access:lookup_hit", "a"),
        ("access:materialize_existing", "a"),
        ("select", ""),
        ("remove", "a"),
        ("insert", "b"),
    ]


def test_event_order_for_hit_and_miss_is_stable() -> None:
    blocks = (_block("a", 0), _block("b", 1))
    cache = HBMCache(capacity_blocks=2)
    cache.materialize((blocks[0],), now_ms=1.0)
    cache.take_events()

    cache.lookup_prefix(blocks, now_ms=2.0)

    assert [event.event_type for event in cache.take_events()] == [LOOKUP_HIT, LOOKUP_MISS]


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
