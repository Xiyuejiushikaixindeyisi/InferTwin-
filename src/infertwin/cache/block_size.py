"""Block-size resolution for cache-hit accounting."""

from __future__ import annotations

from dataclasses import dataclass
from math import lcm


CACHE_FAMILY_FULL_ATTENTION = "full_attention"
CACHE_FAMILY_SLIDING_WINDOW = "sliding_window"
CACHE_FAMILY_MAMBA = "mamba"
CACHE_FAMILY_HYBRID = "hybrid"

_SUPPORTED_CACHE_FAMILIES = {
    CACHE_FAMILY_FULL_ATTENTION,
    CACHE_FAMILY_SLIDING_WINDOW,
    CACHE_FAMILY_MAMBA,
    CACHE_FAMILY_HYBRID,
}
_CP_UNSUPPORTED_CACHE_FAMILIES = {
    CACHE_FAMILY_SLIDING_WINDOW,
    CACHE_FAMILY_MAMBA,
    CACHE_FAMILY_HYBRID,
}


@dataclass(frozen=True, slots=True)
class BlockSizeInput:
    """Inputs needed to resolve runtime and effective block size."""

    requested_block_size: int
    runtime_block_size: int | None = None
    prefill_context_parallel_size: int = 1
    decode_context_parallel_size: int = 1
    cache_family: str = CACHE_FAMILY_FULL_ATTENTION
    hybrid_group_block_sizes: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class BlockSizeResolution:
    """Resolved block sizes used by cache-hit conversion."""

    requested_block_size: int
    runtime_block_size: int
    effective_block_size: int
    prefill_context_parallel_size: int
    decode_context_parallel_size: int
    cache_family: str
    hybrid_group_block_sizes: tuple[int, ...] = ()
    unsupported_reason: str | None = None

    @property
    def supported(self) -> bool:
        return self.unsupported_reason is None

    @property
    def context_parallel_factor(self) -> int:
        return self.prefill_context_parallel_size * self.decode_context_parallel_size


class BlockSizeResolver:
    """Resolve InferTwin's three block-size layers."""

    def resolve(self, config: BlockSizeInput) -> BlockSizeResolution:
        _require_positive_int(config.requested_block_size, field_name="requested_block_size")
        runtime_block_size = config.runtime_block_size or config.requested_block_size
        _require_positive_int(runtime_block_size, field_name="runtime_block_size")
        _require_positive_int(
            config.prefill_context_parallel_size,
            field_name="prefill_context_parallel_size",
        )
        _require_positive_int(
            config.decode_context_parallel_size,
            field_name="decode_context_parallel_size",
        )
        cache_family = config.cache_family
        if cache_family not in _SUPPORTED_CACHE_FAMILIES:
            return _unsupported(
                config=config,
                runtime_block_size=runtime_block_size,
                reason=f"unsupported cache_family {cache_family!r}",
            )

        context_parallel_factor = (
            config.prefill_context_parallel_size * config.decode_context_parallel_size
        )
        if context_parallel_factor > 1 and cache_family in _CP_UNSUPPORTED_CACHE_FAMILIES:
            return _unsupported(
                config=config,
                runtime_block_size=runtime_block_size,
                reason=f"context parallelism is unsupported for cache_family {cache_family!r}",
            )

        if cache_family == CACHE_FAMILY_FULL_ATTENTION:
            effective_block_size = runtime_block_size * context_parallel_factor
        elif cache_family == CACHE_FAMILY_HYBRID:
            if not config.hybrid_group_block_sizes:
                return _unsupported(
                    config=config,
                    runtime_block_size=runtime_block_size,
                    reason="hybrid cache_family requires hybrid_group_block_sizes",
                )
            _require_positive_int_tuple(
                config.hybrid_group_block_sizes,
                field_name="hybrid_group_block_sizes",
            )
            effective_block_size = lcm(runtime_block_size, *config.hybrid_group_block_sizes)
        else:
            effective_block_size = runtime_block_size

        return BlockSizeResolution(
            requested_block_size=config.requested_block_size,
            runtime_block_size=runtime_block_size,
            effective_block_size=effective_block_size,
            prefill_context_parallel_size=config.prefill_context_parallel_size,
            decode_context_parallel_size=config.decode_context_parallel_size,
            cache_family=cache_family,
            hybrid_group_block_sizes=config.hybrid_group_block_sizes,
        )


def _unsupported(
    *,
    config: BlockSizeInput,
    runtime_block_size: int,
    reason: str,
) -> BlockSizeResolution:
    return BlockSizeResolution(
        requested_block_size=config.requested_block_size,
        runtime_block_size=runtime_block_size,
        effective_block_size=runtime_block_size,
        prefill_context_parallel_size=config.prefill_context_parallel_size,
        decode_context_parallel_size=config.decode_context_parallel_size,
        cache_family=config.cache_family,
        hybrid_group_block_sizes=config.hybrid_group_block_sizes,
        unsupported_reason=reason,
    )


def _require_positive_int(value: object, *, field_name: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{field_name} must be a positive integer")


def _require_positive_int_tuple(values: tuple[int, ...], *, field_name: str) -> None:
    for value in values:
        _require_positive_int(value, field_name=f"{field_name}[]")
