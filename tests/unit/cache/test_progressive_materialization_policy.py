import pytest

from infertwin.cache.events import MATERIALIZE
from infertwin.cache.hbm_lru import HBMCache
from infertwin.cache.materialization import (
    FinishTimeMaterializationPolicy,
    ProgressiveFullBlockMaterializationPolicy,
)
from infertwin.request.block_hasher import PrefixBlock


def test_progressive_policy_materializes_newly_completed_full_block() -> None:
    blocks = (_block("a", 0, 4), _block("b", 1, 4))
    cache = HBMCache(capacity_blocks=4)
    policy = ProgressiveFullBlockMaterializationPolicy()

    result = policy.materialize_scheduled_chunk(
        cache=cache,
        materialization_blocks=blocks,
        prompt_blocks=blocks,
        effective_block_size=4,
        computed_tokens_before=0,
        computed_tokens_after=4,
        chunk_finish_time_ms=10.0,
        request_id="r1",
        instance_uuid="i1",
    )

    assert result.materialized_blocks == (blocks[0],)
    assert result.block_count == 1
    assert result.token_count == 4
    assert cache.lookup_prefix(blocks, now_ms=11.0).hbm_hit_blocks == (blocks[0],)
    events = cache.take_events()
    assert events[0].event_type == MATERIALIZE
    assert events[0].reason == "progressive_chunk_materialization"


def test_progressive_policy_skips_partial_block() -> None:
    blocks = (_block("a", 0, 4), _block("partial", 1, 2))
    cache = HBMCache(capacity_blocks=4)
    policy = ProgressiveFullBlockMaterializationPolicy()

    result = policy.materialize_scheduled_chunk(
        cache=cache,
        materialization_blocks=blocks,
        prompt_blocks=blocks,
        effective_block_size=4,
        computed_tokens_before=4,
        computed_tokens_after=6,
        chunk_finish_time_ms=10.0,
        request_id="r1",
        instance_uuid="i1",
    )

    assert result.materialized_blocks == ()
    assert not cache.contains("partial")


def test_progressive_policy_uses_duplicate_guard() -> None:
    blocks = (_block("a", 0, 4), _block("b", 1, 4))
    cache = HBMCache(capacity_blocks=4)
    policy = ProgressiveFullBlockMaterializationPolicy()

    result = policy.materialize_scheduled_chunk(
        cache=cache,
        materialization_blocks=blocks,
        prompt_blocks=blocks,
        effective_block_size=4,
        computed_tokens_before=0,
        computed_tokens_after=8,
        chunk_finish_time_ms=10.0,
        request_id="r1",
        instance_uuid="i1",
        already_materialized_block_keys=frozenset({"a"}),
    )

    assert result.materialized_blocks == (blocks[1],)
    assert not cache.contains("a")
    assert cache.contains("b")


def test_progressive_policy_fails_without_effective_block_size() -> None:
    block = _block("a", 0, 4)
    cache = HBMCache(capacity_blocks=4)
    policy = ProgressiveFullBlockMaterializationPolicy()

    with pytest.raises(ValueError, match="effective_block_size"):
        policy.materialize_scheduled_chunk(
            cache=cache,
            materialization_blocks=(block,),
            prompt_blocks=(block,),
            effective_block_size=0,
            computed_tokens_before=0,
            computed_tokens_after=4,
            chunk_finish_time_ms=10.0,
            request_id="r1",
            instance_uuid="i1",
        )


def test_finish_time_policy_does_not_support_progressive_chunks() -> None:
    policy = FinishTimeMaterializationPolicy()

    assert not policy.supports_progressive_chunks
    assert policy.materialize_scheduled_chunk(
        cache=HBMCache(capacity_blocks=4),
        materialization_blocks=(_block("a", 0, 4),),
        prompt_blocks=(_block("a", 0, 4),),
        effective_block_size=4,
        computed_tokens_before=0,
        computed_tokens_after=4,
        chunk_finish_time_ms=10.0,
        request_id="r1",
        instance_uuid="i1",
    ).materialized_blocks == ()


def _block(block_key: str, block_index: int, token_count: int) -> PrefixBlock:
    return PrefixBlock(
        block_key=block_key,
        content_hash=f"content-{block_key}",
        block_index=block_index,
        token_count=token_count,
        size_bytes=token_count,
    )
