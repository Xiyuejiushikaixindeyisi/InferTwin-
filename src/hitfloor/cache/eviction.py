"""Eviction policy interfaces and implementations."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Mapping
from typing import Protocol


class EvictableBlock(Protocol):
    block_key: str
    last_access_time_ms: float
    last_access_seq: int
    created_time_ms: float


class HBMEvictionPolicy(Protocol):
    name: str

    def on_insert(self, block: EvictableBlock) -> None: ...

    def on_access(self, block: EvictableBlock, *, reason: str) -> None: ...

    def on_remove(self, block: EvictableBlock) -> None: ...

    def select_victim(
        self,
        blocks: Mapping[str, EvictableBlock],
    ) -> EvictableBlock: ...


class LRUEvictionPolicy:
    """Stateful least-recently-used eviction policy."""

    name = "lru"

    def __init__(self) -> None:
        self._recency: OrderedDict[str, None] = OrderedDict()

    def on_insert(self, block: EvictableBlock) -> None:
        self._recency.pop(block.block_key, None)
        self._recency[block.block_key] = None

    def on_access(self, block: EvictableBlock, *, reason: str) -> None:
        if block.block_key not in self._recency:
            raise ValueError(f"cannot access non-resident block in lru policy: {block.block_key}")
        self._recency.move_to_end(block.block_key)

    def on_remove(self, block: EvictableBlock) -> None:
        if block.block_key not in self._recency:
            raise ValueError(f"cannot remove non-resident block from lru policy: {block.block_key}")
        del self._recency[block.block_key]

    def select_victim(
        self,
        blocks: Mapping[str, EvictableBlock],
    ) -> EvictableBlock:
        if not blocks:
            raise ValueError("cannot select an eviction victim from an empty cache")
        for block_key in self._recency:
            if block_key not in blocks:
                raise ValueError(f"lru policy references non-resident block: {block_key}")
            return blocks[block_key]
        raise ValueError(
            "cannot select an eviction victim because lru policy has no resident blocks"
        )


HBMEvictor = HBMEvictionPolicy
LRUEvictor = LRUEvictionPolicy
