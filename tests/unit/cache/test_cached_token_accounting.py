from infertwin.cache.block_size import BlockSizeInput, BlockSizeResolver
from infertwin.cache.cache_block_conversion import (
    CacheBlockConversionInput,
    CacheBlockConversionPolicy,
)
from infertwin.cache.cached_token_accounting import account_prefix_lookup
from infertwin.cache.results import PrefixLookupResult
from infertwin.request.block_hasher import PrefixBlock, build_prefix_blocks


def test_prompt_equal_to_block_size_has_raw_hit_but_zero_accounted_tokens() -> None:
    blocks = _blocks([1, 2, 3, 4], block_size=4)
    lookup = PrefixLookupResult(hbm_hit_blocks=blocks, ddr_hit_blocks=(), miss_blocks=())

    accounted = account_prefix_lookup(
        lookup=lookup,
        prompt_tokens=4,
        block_conversion=_conversion(prompt_tokens=4, block_size=4),
    )

    assert accounted.raw_hbm_hit_tokens == 4
    assert accounted.hbm_hit_tokens == 0
    assert accounted.miss_tokens == 4
    assert accounted.materialization_blocks == ()
    assert accounted.cached_token_cap == 0


def test_partial_block_is_not_counted_as_cached_tokens() -> None:
    blocks = _blocks([1, 2, 3, 4, 5, 6], block_size=4)
    lookup = PrefixLookupResult(hbm_hit_blocks=blocks, ddr_hit_blocks=(), miss_blocks=())

    accounted = account_prefix_lookup(
        lookup=lookup,
        prompt_tokens=6,
        block_conversion=_conversion(prompt_tokens=6, block_size=4),
    )

    assert [block.token_count for block in accounted.hbm_hit_blocks] == [4]
    assert accounted.raw_hbm_hit_tokens == 6
    assert accounted.hbm_hit_tokens == 4
    assert accounted.miss_tokens == 2
    assert accounted.materialization_blocks == ()


def test_speculative_drop_uses_actual_raw_matched_blocks() -> None:
    blocks = _blocks(list(range(13)), block_size=4)
    lookup = PrefixLookupResult(
        hbm_hit_blocks=blocks[:1],
        ddr_hit_blocks=(),
        miss_blocks=blocks[1:],
    )

    accounted = account_prefix_lookup(
        lookup=lookup,
        prompt_tokens=13,
        block_conversion=_conversion(
            prompt_tokens=13,
            block_size=4,
            speculative_drop_blocks=1,
        ),
    )

    assert accounted.raw_hbm_hit_tokens == 4
    assert accounted.cached_token_cap == 0
    assert accounted.hbm_hit_tokens == 0
    assert accounted.miss_tokens == 13
    assert accounted.materialization_blocks == blocks[1:]


def test_context_parallel_effective_block_size_controls_accounting() -> None:
    blocks = _blocks(list(range(17)), block_size=8)
    lookup = PrefixLookupResult(hbm_hit_blocks=blocks, ddr_hit_blocks=(), miss_blocks=())

    accounted = account_prefix_lookup(
        lookup=lookup,
        prompt_tokens=17,
        block_conversion=_conversion(
            prompt_tokens=17,
            block_size=4,
            prefill_context_parallel_size=2,
        ),
    )

    assert [block.token_count for block in accounted.hbm_hit_blocks] == [8, 8]
    assert accounted.hbm_hit_tokens == 16
    assert accounted.miss_tokens == 1


def test_evicted_suffix_limits_hit_tokens_by_actual_raw_match() -> None:
    blocks = _blocks(list(range(13)), block_size=4)
    lookup = PrefixLookupResult(
        hbm_hit_blocks=blocks[:2],
        ddr_hit_blocks=(),
        miss_blocks=blocks[2:],
    )

    accounted = account_prefix_lookup(
        lookup=lookup,
        prompt_tokens=13,
        block_conversion=_conversion(prompt_tokens=13, block_size=4),
    )

    assert accounted.cached_token_cap == 8
    assert accounted.hbm_hit_tokens == 8
    assert accounted.miss_tokens == 5
    assert accounted.materialization_blocks == blocks[2:]


def _blocks(token_ids: list[int], *, block_size: int) -> tuple[PrefixBlock, ...]:
    return tuple(
        build_prefix_blocks(
            token_ids=token_ids,
            block_size_tokens=block_size,
            model="glm-v5",
            tenant_id="tenant-a",
            kv_bytes_per_token=1,
        )
    )


def _conversion(
    *,
    prompt_tokens: int,
    block_size: int,
    prefill_context_parallel_size: int = 1,
    speculative_drop_blocks: int = 0,
):
    resolution = BlockSizeResolver().resolve(
        BlockSizeInput(
            requested_block_size=block_size,
            prefill_context_parallel_size=prefill_context_parallel_size,
        )
    )
    return CacheBlockConversionPolicy().calculate(
        CacheBlockConversionInput(
            prompt_tokens=prompt_tokens,
            block_size=resolution,
            speculative_drop_blocks=speculative_drop_blocks,
        )
    )
