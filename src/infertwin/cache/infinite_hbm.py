"""Infinite HBM prefix cache used for ideal hit simulation."""

from __future__ import annotations

from dataclasses import dataclass

from infertwin.cache.events import CacheEvent
from infertwin.cache.results import PrefixLookupResult
from infertwin.request.block_hasher import PrefixBlock


@dataclass(slots=True)
class CacheBlockMeta:
    block_key: str
    block_index: int
    token_count: int
    size_bytes: int
    created_time_ms: float
    last_access_time_ms: float
    hit_count: int = 0
    refcount: int = 0


class InfiniteHBMCache:
    def __init__(self) -> None:
        self._blocks: dict[str, CacheBlockMeta] = {}

    @property
    def resident_blocks(self) -> int:
        return len(self._blocks)

    def contains(self, block_key: str) -> bool:
        return block_key in self._blocks

    def lookup_prefix(
        self,
        blocks: tuple[PrefixBlock, ...],
        now_ms: float,
        request_id: str = "",
        instance_uuid: str = "",
    ) -> PrefixLookupResult:
        hit_blocks: list[PrefixBlock] = []
        for block in blocks:
            meta = self._blocks.get(block.block_key)
            if meta is None:
                break
            meta.hit_count += 1
            meta.last_access_time_ms = now_ms
            hit_blocks.append(block)

        miss_blocks = list(blocks[len(hit_blocks) :])
        return PrefixLookupResult(
            hbm_hit_blocks=tuple(hit_blocks),
            ddr_hit_blocks=(),
            miss_blocks=tuple(miss_blocks),
        )

    def materialize(
        self,
        blocks: tuple[PrefixBlock, ...],
        now_ms: float,
        request_id: str = "",
        instance_uuid: str = "",
        reason: str = "finish_time_materialization",
    ) -> None:
        for block in blocks:
            self._blocks.setdefault(
                block.block_key,
                CacheBlockMeta(
                    block_key=block.block_key,
                    block_index=block.block_index,
                    token_count=block.token_count,
                    size_bytes=block.size_bytes,
                    created_time_ms=now_ms,
                    last_access_time_ms=now_ms,
                ),
            )

    def take_events(self) -> tuple[CacheEvent, ...]:
        return ()
