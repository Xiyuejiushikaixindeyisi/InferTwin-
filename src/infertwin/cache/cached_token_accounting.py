"""vLLM-like cached-token accounting for raw prefix-cache lookup results."""

from __future__ import annotations

from dataclasses import dataclass

from infertwin.cache.cache_block_conversion import CacheBlockConversionResult
from infertwin.cache.results import PrefixLookupResult
from infertwin.request.block_hasher import PrefixBlock


@dataclass(frozen=True, slots=True)
class AccountedLookupResult:
    """Replay-facing lookup metrics after applying cached-token accounting.

    Raw cache lookup tells whether block metadata is resident. Replay metrics
    need vLLM-like usage accounting, where cached tokens are capped by full
    effective blocks, ``prompt_tokens - 1``, and speculative drop blocks.
    """

    hbm_hit_tokens: int
    ddr_hit_tokens: int
    miss_tokens: int
    hbm_hit_blocks: tuple[PrefixBlock, ...]
    ddr_hit_blocks: tuple[PrefixBlock, ...]
    materialization_blocks: tuple[PrefixBlock, ...]
    raw_hbm_hit_tokens: int
    raw_ddr_hit_tokens: int
    raw_miss_tokens: int
    cached_token_cap: int

    @property
    def effective_hit_tokens(self) -> int:
        return self.hbm_hit_tokens + self.ddr_hit_tokens


def account_prefix_lookup(
    *,
    lookup: PrefixLookupResult,
    prompt_tokens: int,
    block_conversion: CacheBlockConversionResult | None,
) -> AccountedLookupResult:
    """Apply vLLM-like cached-token accounting to a raw prefix lookup result."""

    if prompt_tokens < 0:
        raise ValueError("prompt_tokens must be non-negative")

    conversion = block_conversion or _legacy_conversion_result(
        lookup=lookup,
        prompt_tokens=prompt_tokens,
    )
    if not conversion.supported:
        raise ValueError(
            f"unsupported cache block conversion result: {conversion.unsupported_reason}"
        )

    raw_hit_blocks = lookup.hbm_hit_blocks + lookup.ddr_hit_blocks
    raw_matchable_blocks = _count_matchable_full_blocks(
        blocks=raw_hit_blocks,
        effective_block_size=conversion.effective_block_size,
        max_cache_hit_length=conversion.max_cache_hit_length,
    )
    matched_blocks = min(raw_matchable_blocks, conversion.max_matchable_blocks)
    cached_blocks = max(matched_blocks - conversion.speculative_drop_blocks, 0)
    cached_token_cap = cached_blocks * conversion.effective_block_size

    hbm_hit_blocks, hbm_hit_tokens = _take_accounted_blocks(
        blocks=lookup.hbm_hit_blocks,
        token_budget=cached_token_cap,
        effective_block_size=conversion.effective_block_size,
    )
    remaining_cap = cached_token_cap - hbm_hit_tokens
    ddr_hit_blocks, ddr_hit_tokens = _take_accounted_blocks(
        blocks=lookup.ddr_hit_blocks,
        token_budget=remaining_cap,
        effective_block_size=conversion.effective_block_size,
    )
    miss_tokens = prompt_tokens - hbm_hit_tokens - ddr_hit_tokens
    if miss_tokens < 0:
        raise ValueError("accounted hit tokens cannot exceed prompt_tokens")

    return AccountedLookupResult(
        hbm_hit_tokens=hbm_hit_tokens,
        ddr_hit_tokens=ddr_hit_tokens,
        miss_tokens=miss_tokens,
        hbm_hit_blocks=hbm_hit_blocks,
        ddr_hit_blocks=ddr_hit_blocks,
        materialization_blocks=lookup.miss_blocks,
        raw_hbm_hit_tokens=lookup.hbm_hit_tokens,
        raw_ddr_hit_tokens=lookup.ddr_hit_tokens,
        raw_miss_tokens=lookup.miss_tokens,
        cached_token_cap=cached_token_cap,
    )


def _count_matchable_full_blocks(
    *,
    blocks: tuple[PrefixBlock, ...],
    effective_block_size: int,
    max_cache_hit_length: int,
) -> int:
    count = 0
    tokens = 0
    for block in blocks:
        if block.token_count != effective_block_size:
            break
        if tokens + block.token_count > max_cache_hit_length:
            break
        tokens += block.token_count
        count += 1
    return count


def _take_accounted_blocks(
    *,
    blocks: tuple[PrefixBlock, ...],
    token_budget: int,
    effective_block_size: int,
) -> tuple[tuple[PrefixBlock, ...], int]:
    if token_budget <= 0:
        return (), 0

    selected: list[PrefixBlock] = []
    tokens = 0
    for block in blocks:
        if block.token_count != effective_block_size:
            break
        if tokens + block.token_count > token_budget:
            break
        selected.append(block)
        tokens += block.token_count
    return tuple(selected), tokens


def _legacy_conversion_result(
    *,
    lookup: PrefixLookupResult,
    prompt_tokens: int,
) -> CacheBlockConversionResult:
    effective_block_size = _infer_effective_block_size(lookup, prompt_tokens)
    max_cache_hit_length = max(prompt_tokens - 1, 0)
    max_matchable_blocks = max_cache_hit_length // effective_block_size
    return CacheBlockConversionResult(
        requested_block_size=effective_block_size,
        runtime_block_size=effective_block_size,
        effective_block_size=effective_block_size,
        max_cache_hit_length=max_cache_hit_length,
        max_matchable_blocks=max_matchable_blocks,
        matched_blocks=max_matchable_blocks,
        speculative_drop_blocks=0,
        cached_blocks=max_matchable_blocks,
        cached_tokens=max_matchable_blocks * effective_block_size,
    )


def _infer_effective_block_size(
    lookup: PrefixLookupResult,
    prompt_tokens: int,
) -> int:
    token_counts = [
        block.token_count
        for block in (lookup.hbm_hit_blocks + lookup.ddr_hit_blocks + lookup.miss_blocks)
        if block.token_count > 0
    ]
    if token_counts:
        return max(token_counts)
    return max(prompt_tokens, 1)
