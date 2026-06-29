"""Finite DDR/CPU prefix cache tier with LRU eviction."""

from __future__ import annotations

from dataclasses import dataclass

from infertwin.cache.events import (
    CACHE_TIER_DDR,
    EVICT,
    LOOKUP_HIT,
    LOOKUP_MISS,
    STORE,
    CacheEvent,
)
from infertwin.cache.eviction import HBMEvictionPolicy, LRUEvictionPolicy
from infertwin.cache.results import PrefixLookupResult
from infertwin.request.block_hasher import PrefixBlock


@dataclass(slots=True)
class DDRBlockMeta:
    block_key: str
    block_index: int
    token_count: int
    size_bytes: int
    created_time_ms: float
    last_access_time_ms: float
    last_access_seq: int
    hit_count: int = 0
    stored_by_request_id: str = ""
    instance_uuid: str = ""


class DDRLRUCache:
    """Finite DDR/CPU prefix cache tier.

    Responsibilities:
    - stores hash-only prefix block metadata;
    - returns contiguous DDR prefix hits;
    - stores blocks after finish-time materialization;
    - delegates victim selection to an eviction policy;
    - emits DDR-tier cache events.

    It does not model HBM, promotion, KV load latency, async offload, or remote pooling.
    """

    def __init__(
        self,
        *,
        capacity_blocks: int,
        evictor: HBMEvictionPolicy | None = None,
    ) -> None:
        if isinstance(capacity_blocks, bool) or capacity_blocks <= 0:
            raise ValueError("capacity_blocks must be a positive integer")
        self._capacity_blocks = capacity_blocks
        self._evictor = evictor or LRUEvictionPolicy()
        self._blocks: dict[str, DDRBlockMeta] = {}
        self._events: list[CacheEvent] = []
        self._access_seq = 0

    @property
    def capacity_blocks(self) -> int:
        return self._capacity_blocks

    @property
    def resident_blocks(self) -> int:
        return len(self._blocks)

    @property
    def eviction_policy(self) -> str:
        return self._evictor.name

    def contains(self, block_key: str) -> bool:
        return block_key in self._blocks

    def lookup_prefix(
        self,
        blocks: tuple[PrefixBlock, ...],
        now_ms: float,
        request_id: str = "",
        instance_uuid: str = "",
        hbm_used_blocks: int = 0,
        hbm_capacity_blocks: int = 0,
    ) -> PrefixLookupResult:
        hit_blocks: list[PrefixBlock] = []
        for block in blocks:
            meta = self._blocks.get(block.block_key)
            if meta is None:
                break
            self._touch(meta, now_ms=now_ms, count_hit=True)
            self._evictor.on_access(meta, reason="lookup_hit")
            hit_blocks.append(block)
            self._emit(
                event_type=LOOKUP_HIT,
                timestamp_ms=now_ms,
                request_id=request_id,
                instance_uuid=instance_uuid,
                block=block,
                reason="prefix_hit",
                hbm_used_blocks=hbm_used_blocks,
                hbm_capacity_blocks=hbm_capacity_blocks,
                source_tier=CACHE_TIER_DDR,
            )

        miss_blocks = list(blocks[len(hit_blocks) :])
        for block in miss_blocks:
            self._emit(
                event_type=LOOKUP_MISS,
                timestamp_ms=now_ms,
                request_id=request_id,
                instance_uuid=instance_uuid,
                block=block,
                reason="prefix_miss",
                hbm_used_blocks=hbm_used_blocks,
                hbm_capacity_blocks=hbm_capacity_blocks,
            )

        return PrefixLookupResult(
            hbm_hit_blocks=(),
            ddr_hit_blocks=tuple(hit_blocks),
            miss_blocks=tuple(miss_blocks),
        )

    def store(
        self,
        blocks: tuple[PrefixBlock, ...],
        now_ms: float,
        request_id: str = "",
        instance_uuid: str = "",
        hbm_used_blocks: int = 0,
        hbm_capacity_blocks: int = 0,
        reason: str = "finish_time_store",
    ) -> None:
        for block in blocks:
            existing = self._blocks.get(block.block_key)
            if existing is not None:
                self._touch(existing, now_ms=now_ms, count_hit=False)
                self._evictor.on_access(existing, reason="store_existing")
                continue

            while len(self._blocks) >= self._capacity_blocks:
                self._evict_one(
                    timestamp_ms=now_ms,
                    request_id=request_id,
                    instance_uuid=instance_uuid,
                    hbm_used_blocks=hbm_used_blocks,
                    hbm_capacity_blocks=hbm_capacity_blocks,
                )

            meta = DDRBlockMeta(
                block_key=block.block_key,
                block_index=block.block_index,
                token_count=block.token_count,
                size_bytes=block.size_bytes,
                created_time_ms=now_ms,
                last_access_time_ms=now_ms,
                last_access_seq=self._next_access_seq(),
                stored_by_request_id=request_id,
                instance_uuid=instance_uuid,
            )
            self._blocks[block.block_key] = meta
            self._evictor.on_insert(meta)
            self._emit(
                event_type=STORE,
                timestamp_ms=now_ms,
                request_id=request_id,
                instance_uuid=instance_uuid,
                block=block,
                reason=reason,
                hbm_used_blocks=hbm_used_blocks,
                hbm_capacity_blocks=hbm_capacity_blocks,
                target_tier=CACHE_TIER_DDR,
                store_tokens=block.token_count,
            )

    def take_events(self) -> tuple[CacheEvent, ...]:
        events = tuple(self._events)
        self._events.clear()
        return events

    def _touch(
        self,
        meta: DDRBlockMeta,
        *,
        now_ms: float,
        count_hit: bool,
    ) -> None:
        meta.last_access_time_ms = now_ms
        meta.last_access_seq = self._next_access_seq()
        if count_hit:
            meta.hit_count += 1

    def _evict_one(
        self,
        *,
        timestamp_ms: float,
        request_id: str,
        instance_uuid: str,
        hbm_used_blocks: int,
        hbm_capacity_blocks: int,
    ) -> None:
        victim = self._evictor.select_victim(self._blocks)
        removed = self._blocks.pop(victim.block_key)
        self._evictor.on_remove(removed)
        self._events.append(
            CacheEvent(
                event_type=EVICT,
                timestamp_ms=timestamp_ms,
                instance_uuid=instance_uuid or removed.instance_uuid,
                request_id=request_id,
                block_key=removed.block_key,
                block_index=removed.block_index,
                token_count=removed.token_count,
                cache_tier=CACHE_TIER_DDR,
                reason="capacity",
                eviction_policy=self.eviction_policy,
                hbm_used_blocks=hbm_used_blocks,
                hbm_capacity_blocks=hbm_capacity_blocks,
                ddr_used_blocks=self.resident_blocks,
                ddr_capacity_blocks=self.capacity_blocks,
            )
        )

    def _emit(
        self,
        *,
        event_type: str,
        timestamp_ms: float,
        request_id: str,
        instance_uuid: str,
        block: PrefixBlock,
        reason: str,
        hbm_used_blocks: int,
        hbm_capacity_blocks: int,
        source_tier: str = "",
        target_tier: str = "",
        store_tokens: int = 0,
    ) -> None:
        self._events.append(
            CacheEvent(
                event_type=event_type,
                timestamp_ms=timestamp_ms,
                instance_uuid=instance_uuid,
                request_id=request_id,
                block_key=block.block_key,
                block_index=block.block_index,
                token_count=block.token_count,
                cache_tier=CACHE_TIER_DDR,
                reason=reason,
                eviction_policy=self.eviction_policy,
                hbm_used_blocks=hbm_used_blocks,
                hbm_capacity_blocks=hbm_capacity_blocks,
                ddr_used_blocks=self.resident_blocks,
                ddr_capacity_blocks=self.capacity_blocks,
                source_tier=source_tier,
                target_tier=target_tier,
                store_tokens=store_tokens,
            )
        )

    def _next_access_seq(self) -> int:
        self._access_seq += 1
        return self._access_seq
