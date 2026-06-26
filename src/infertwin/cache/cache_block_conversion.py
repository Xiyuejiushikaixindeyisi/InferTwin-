"""Pure cached-token conversion helpers."""

from __future__ import annotations

from dataclasses import dataclass

from infertwin.cache.block_size import BlockSizeResolution


@dataclass(frozen=True, slots=True)
class CacheBlockConversionInput:
    """Input for converting matched cache blocks into cached tokens."""

    prompt_tokens: int
    block_size: BlockSizeResolution
    matched_blocks: int | None = None
    speculative_drop_blocks: int = 0


@dataclass(frozen=True, slots=True)
class CacheBlockConversionResult:
    """Stable output of cached-token accounting."""

    requested_block_size: int
    runtime_block_size: int
    effective_block_size: int
    max_cache_hit_length: int
    max_matchable_blocks: int
    matched_blocks: int
    speculative_drop_blocks: int
    cached_blocks: int
    cached_tokens: int
    unsupported_reason: str | None = None

    @property
    def supported(self) -> bool:
        return self.unsupported_reason is None


class CacheBlockConversionPolicy:
    """Convert prefix cache block matches into vLLM-like cached token counts."""

    def calculate(self, config: CacheBlockConversionInput) -> CacheBlockConversionResult:
        _require_non_negative_int(config.prompt_tokens, field_name="prompt_tokens")
        _require_non_negative_int(
            config.speculative_drop_blocks,
            field_name="speculative_drop_blocks",
        )
        if config.matched_blocks is not None:
            _require_non_negative_int(config.matched_blocks, field_name="matched_blocks")

        base_result = _base_result(config)
        if not config.block_size.supported:
            return CacheBlockConversionResult(
                **base_result,
                unsupported_reason=config.block_size.unsupported_reason,
            )

        matched_blocks = config.matched_blocks
        if matched_blocks is None:
            matched_blocks = base_result["max_matchable_blocks"]
        matched_blocks = min(matched_blocks, base_result["max_matchable_blocks"])
        cached_blocks = max(matched_blocks - config.speculative_drop_blocks, 0)
        result_values = dict(base_result)
        result_values.update(
            {
                "matched_blocks": matched_blocks,
                "cached_blocks": cached_blocks,
                "cached_tokens": cached_blocks * config.block_size.effective_block_size,
            }
        )
        return CacheBlockConversionResult(**result_values)


def _base_result(config: CacheBlockConversionInput) -> dict[str, int]:
    max_cache_hit_length = max(config.prompt_tokens - 1, 0)
    max_matchable_blocks = max_cache_hit_length // config.block_size.effective_block_size
    return {
        "requested_block_size": config.block_size.requested_block_size,
        "runtime_block_size": config.block_size.runtime_block_size,
        "effective_block_size": config.block_size.effective_block_size,
        "max_cache_hit_length": max_cache_hit_length,
        "max_matchable_blocks": max_matchable_blocks,
        "matched_blocks": 0,
        "speculative_drop_blocks": config.speculative_drop_blocks,
        "cached_blocks": 0,
        "cached_tokens": 0,
    }


def _require_non_negative_int(value: object, *, field_name: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")
