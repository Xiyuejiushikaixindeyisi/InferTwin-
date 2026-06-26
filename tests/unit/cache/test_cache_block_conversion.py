import pytest

from infertwin.cache.block_size import (
    CACHE_FAMILY_FULL_ATTENTION,
    CACHE_FAMILY_HYBRID,
    CACHE_FAMILY_SLIDING_WINDOW,
    BlockSizeInput,
    BlockSizeResolver,
)
from infertwin.cache.cache_block_conversion import (
    CacheBlockConversionInput,
    CacheBlockConversionPolicy,
)


def test_full_attention_uses_prompt_minus_one_and_full_block_floor() -> None:
    resolution = _resolve(requested_block_size=128)

    result = _calculate(prompt_tokens=129, block_size=resolution)

    assert result.max_cache_hit_length == 128
    assert result.max_matchable_blocks == 1
    assert result.matched_blocks == 1
    assert result.cached_blocks == 1
    assert result.cached_tokens == 128


def test_prompt_equal_to_one_block_still_recomputes_last_token_block() -> None:
    resolution = _resolve(requested_block_size=128)

    result = _calculate(prompt_tokens=128, block_size=resolution)

    assert result.max_cache_hit_length == 127
    assert result.max_matchable_blocks == 0
    assert result.cached_tokens == 0


def test_runtime_block_size_override_is_used_for_cached_tokens() -> None:
    resolution = _resolve(requested_block_size=128, runtime_block_size=768)

    result = _calculate(prompt_tokens=1600, block_size=resolution)

    assert result.requested_block_size == 128
    assert result.runtime_block_size == 768
    assert result.effective_block_size == 768
    assert result.max_matchable_blocks == 2
    assert result.cached_tokens == 1536


def test_context_parallelism_scales_full_attention_effective_block_size() -> None:
    resolution = _resolve(
        requested_block_size=128,
        prefill_context_parallel_size=2,
        decode_context_parallel_size=2,
    )

    result = _calculate(prompt_tokens=1000, block_size=resolution)

    assert result.effective_block_size == 512
    assert result.max_matchable_blocks == 1
    assert result.cached_tokens == 512


def test_speculative_drop_blocks_removes_last_matched_blocks() -> None:
    resolution = _resolve(requested_block_size=128)

    result = _calculate(
        prompt_tokens=1025,
        block_size=resolution,
        speculative_drop_blocks=1,
    )

    assert result.max_matchable_blocks == 8
    assert result.matched_blocks == 8
    assert result.cached_blocks == 7
    assert result.cached_tokens == 896


def test_actual_matched_blocks_are_capped_by_max_cache_hit_length() -> None:
    resolution = _resolve(requested_block_size=128)

    result = _calculate(prompt_tokens=1025, block_size=resolution, matched_blocks=20)

    assert result.max_matchable_blocks == 8
    assert result.matched_blocks == 8
    assert result.cached_tokens == 1024


def test_hybrid_cache_groups_align_to_lcm_block_size() -> None:
    resolution = _resolve(
        requested_block_size=128,
        runtime_block_size=128,
        cache_family=CACHE_FAMILY_HYBRID,
        hybrid_group_block_sizes=(128, 768),
    )

    result = _calculate(prompt_tokens=1600, block_size=resolution)

    assert result.supported is True
    assert result.effective_block_size == 768
    assert result.max_matchable_blocks == 2
    assert result.cached_tokens == 1536


def test_unsupported_context_parallel_cache_family_returns_guarded_result() -> None:
    resolution = _resolve(
        requested_block_size=128,
        cache_family=CACHE_FAMILY_SLIDING_WINDOW,
        prefill_context_parallel_size=2,
    )

    result = _calculate(prompt_tokens=1025, block_size=resolution)

    assert resolution.supported is False
    assert result.supported is False
    assert result.unsupported_reason == (
        "context parallelism is unsupported for cache_family 'sliding_window'"
    )
    assert result.cached_tokens == 0


def test_invalid_inputs_fail_explicitly() -> None:
    with pytest.raises(ValueError, match="requested_block_size"):
        _resolve(requested_block_size=0)

    resolution = _resolve(requested_block_size=128)
    with pytest.raises(ValueError, match="prompt_tokens"):
        _calculate(prompt_tokens=-1, block_size=resolution)
    with pytest.raises(ValueError, match="matched_blocks"):
        _calculate(prompt_tokens=128, block_size=resolution, matched_blocks=-1)


def _resolve(
    *,
    requested_block_size: int,
    runtime_block_size: int | None = None,
    prefill_context_parallel_size: int = 1,
    decode_context_parallel_size: int = 1,
    cache_family: str = CACHE_FAMILY_FULL_ATTENTION,
    hybrid_group_block_sizes: tuple[int, ...] = (),
):
    return BlockSizeResolver().resolve(
        BlockSizeInput(
            requested_block_size=requested_block_size,
            runtime_block_size=runtime_block_size,
            prefill_context_parallel_size=prefill_context_parallel_size,
            decode_context_parallel_size=decode_context_parallel_size,
            cache_family=cache_family,
            hybrid_group_block_sizes=hybrid_group_block_sizes,
        )
    )


def _calculate(
    *,
    prompt_tokens: int,
    block_size,
    matched_blocks: int | None = None,
    speculative_drop_blocks: int = 0,
):
    return CacheBlockConversionPolicy().calculate(
        CacheBlockConversionInput(
            prompt_tokens=prompt_tokens,
            block_size=block_size,
            matched_blocks=matched_blocks,
            speculative_drop_blocks=speculative_drop_blocks,
        )
    )
