"""Cache materialization policies for replay engines."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from infertwin.cache.base import PrefixCache
from infertwin.request.block_hasher import PrefixBlock


@dataclass(frozen=True, slots=True)
class MaterializationResult:
    """Blocks made visible by one materialization policy call."""

    materialized_blocks: tuple[PrefixBlock, ...] = ()

    @property
    def block_count(self) -> int:
        return len(self.materialized_blocks)

    @property
    def token_count(self) -> int:
        return sum(block.token_count for block in self.materialized_blocks)


class MaterializationPolicy(Protocol):
    """Decide when computed miss blocks become visible to prefix cache lookup."""

    name: str
    supports_progressive_chunks: bool

    def materialize_scheduled_chunk(
        self,
        *,
        cache: PrefixCache,
        materialization_blocks: tuple[PrefixBlock, ...],
        prompt_blocks: tuple[PrefixBlock, ...],
        effective_block_size: int,
        computed_tokens_before: int,
        computed_tokens_after: int,
        chunk_finish_time_ms: float,
        request_id: str,
        instance_uuid: str,
        already_materialized_block_keys: frozenset[str] = frozenset(),
    ) -> MaterializationResult:
        """Materialize blocks completed by one scheduled prefill chunk."""

    def materialize_finished_request(
        self,
        *,
        cache: PrefixCache,
        blocks: tuple[PrefixBlock, ...],
        finish_time_ms: float,
        request_id: str,
        instance_uuid: str,
        prompt_blocks: tuple[PrefixBlock, ...] = (),
        effective_block_size: int = 0,
        already_materialized_block_keys: frozenset[str] = frozenset(),
    ) -> MaterializationResult:
        """Materialize blocks for a request whose prefill has finished."""


class FinishTimeMaterializationPolicy:
    """Materialize all miss blocks only after request prefill finish time."""

    name = "finish_time"
    supports_progressive_chunks = False

    def materialize_scheduled_chunk(
        self,
        *,
        cache: PrefixCache,
        materialization_blocks: tuple[PrefixBlock, ...],
        prompt_blocks: tuple[PrefixBlock, ...],
        effective_block_size: int,
        computed_tokens_before: int,
        computed_tokens_after: int,
        chunk_finish_time_ms: float,
        request_id: str,
        instance_uuid: str,
        already_materialized_block_keys: frozenset[str] = frozenset(),
    ) -> MaterializationResult:
        return MaterializationResult()

    def materialize_finished_request(
        self,
        *,
        cache: PrefixCache,
        blocks: tuple[PrefixBlock, ...],
        finish_time_ms: float,
        request_id: str,
        instance_uuid: str,
        prompt_blocks: tuple[PrefixBlock, ...] = (),
        effective_block_size: int = 0,
        already_materialized_block_keys: frozenset[str] = frozenset(),
    ) -> MaterializationResult:
        cache.materialize(
            blocks,
            now_ms=finish_time_ms,
            request_id=request_id,
            instance_uuid=instance_uuid,
        )
        return MaterializationResult(materialized_blocks=blocks)


class ProgressiveFullBlockMaterializationPolicy:
    """Materialize newly completed full miss blocks at chunk finish boundaries."""

    name = "progressive_full_block"
    supports_progressive_chunks = True

    def materialize_scheduled_chunk(
        self,
        *,
        cache: PrefixCache,
        materialization_blocks: tuple[PrefixBlock, ...],
        prompt_blocks: tuple[PrefixBlock, ...],
        effective_block_size: int,
        computed_tokens_before: int,
        computed_tokens_after: int,
        chunk_finish_time_ms: float,
        request_id: str,
        instance_uuid: str,
        already_materialized_block_keys: frozenset[str] = frozenset(),
    ) -> MaterializationResult:
        selected = _select_newly_completed_full_blocks(
            materialization_blocks=materialization_blocks,
            prompt_blocks=prompt_blocks,
            effective_block_size=effective_block_size,
            computed_tokens_before=computed_tokens_before,
            computed_tokens_after=computed_tokens_after,
            already_materialized_block_keys=already_materialized_block_keys,
        )
        cache.materialize(
            selected,
            now_ms=chunk_finish_time_ms,
            request_id=request_id,
            instance_uuid=instance_uuid,
            reason="progressive_chunk_materialization",
        )
        return MaterializationResult(materialized_blocks=selected)

    def materialize_finished_request(
        self,
        *,
        cache: PrefixCache,
        blocks: tuple[PrefixBlock, ...],
        finish_time_ms: float,
        request_id: str,
        instance_uuid: str,
        prompt_blocks: tuple[PrefixBlock, ...] = (),
        effective_block_size: int = 0,
        already_materialized_block_keys: frozenset[str] = frozenset(),
    ) -> MaterializationResult:
        if not blocks:
            return MaterializationResult()
        selected = _select_newly_completed_full_blocks(
            materialization_blocks=blocks,
            prompt_blocks=prompt_blocks,
            effective_block_size=effective_block_size,
            computed_tokens_before=-1,
            computed_tokens_after=sum(block.token_count for block in prompt_blocks),
            already_materialized_block_keys=already_materialized_block_keys,
        )
        cache.materialize(
            selected,
            now_ms=finish_time_ms,
            request_id=request_id,
            instance_uuid=instance_uuid,
            reason="progressive_chunk_materialization",
        )
        return MaterializationResult(materialized_blocks=selected)


def _select_newly_completed_full_blocks(
    *,
    materialization_blocks: tuple[PrefixBlock, ...],
    prompt_blocks: tuple[PrefixBlock, ...],
    effective_block_size: int,
    computed_tokens_before: int,
    computed_tokens_after: int,
    already_materialized_block_keys: frozenset[str],
) -> tuple[PrefixBlock, ...]:
    if not materialization_blocks:
        return ()
    if effective_block_size <= 0:
        raise ValueError("progressive materialization requires a positive effective_block_size")
    if computed_tokens_before > computed_tokens_after:
        raise ValueError("computed_tokens_before cannot exceed computed_tokens_after")

    block_end_tokens = _block_end_tokens_by_key(prompt_blocks)
    selected: list[PrefixBlock] = []
    selected_keys: set[str] = set()
    for block in materialization_blocks:
        if block.block_key in already_materialized_block_keys:
            continue
        if block.block_key in selected_keys:
            continue
        if block.token_count != effective_block_size:
            continue
        block_end_token = block_end_tokens.get(block.block_key)
        if block_end_token is None:
            raise ValueError(
                "materialization block is not present in prompt_blocks: "
                f"{block.block_key}"
            )
        if computed_tokens_before < block_end_token <= computed_tokens_after:
            selected.append(block)
            selected_keys.add(block.block_key)
    return tuple(selected)


def _block_end_tokens_by_key(blocks: tuple[PrefixBlock, ...]) -> dict[str, int]:
    end_tokens: dict[str, int] = {}
    cursor = 0
    for block in blocks:
        cursor += block.token_count
        end_tokens[block.block_key] = cursor
    return end_tokens
