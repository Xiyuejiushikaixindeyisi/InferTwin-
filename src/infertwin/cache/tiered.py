"""Tiered prefix cache composed from HBM and DDR/CPU tiers."""

from __future__ import annotations

from dataclasses import dataclass

from infertwin.cache.ddr_lru import DDRLRUCache
from infertwin.cache.events import CacheEvent
from infertwin.cache.hbm_lru import HBMCache
from infertwin.cache.results import PrefixLookupResult
from infertwin.request.block_hasher import PrefixBlock


@dataclass(frozen=True, slots=True)
class TieredCacheStats:
    hbm_resident_blocks: int
    hbm_capacity_blocks: int
    ddr_resident_blocks: int
    ddr_capacity_blocks: int


class TieredPrefixCache:
    """Prefix cache that looks up HBM first, then the same instance's DDR tier.

    Responsibilities:
    - keep replay-facing cache API unchanged;
    - combine HBM contiguous hits and DDR contiguous hits into one lookup result;
    - materialize finished miss blocks into both HBM and DDR;
    - preserve deterministic tier-scoped event order.

    It does not model DDR-to-HBM promotion, KV load latency, async store completion,
    or cross-instance pooling.
    """

    def __init__(self, *, hbm: HBMCache, ddr: DDRLRUCache) -> None:
        self.hbm = hbm
        self.ddr = ddr
        self._events: list[CacheEvent] = []

    @property
    def resident_blocks(self) -> int:
        return self.hbm.resident_blocks + self.ddr.resident_blocks

    @property
    def hbm_resident_blocks(self) -> int:
        return self.hbm.resident_blocks

    @property
    def ddr_resident_blocks(self) -> int:
        return self.ddr.resident_blocks

    @property
    def stats(self) -> TieredCacheStats:
        return TieredCacheStats(
            hbm_resident_blocks=self.hbm.resident_blocks,
            hbm_capacity_blocks=self.hbm.capacity_blocks,
            ddr_resident_blocks=self.ddr.resident_blocks,
            ddr_capacity_blocks=self.ddr.capacity_blocks,
        )

    def contains(self, block_key: str) -> bool:
        return self.hbm.contains(block_key) or self.ddr.contains(block_key)

    def lookup_prefix(
        self,
        blocks: tuple[PrefixBlock, ...],
        now_ms: float,
        request_id: str = "",
        instance_uuid: str = "",
    ) -> PrefixLookupResult:
        hbm_lookup = self.hbm.lookup_prefix(
            blocks,
            now_ms=now_ms,
            request_id=request_id,
            instance_uuid=instance_uuid,
        )
        self._drain_hbm_events()

        ddr_lookup = self.ddr.lookup_prefix(
            hbm_lookup.miss_blocks,
            now_ms=now_ms,
            request_id=request_id,
            instance_uuid=instance_uuid,
            hbm_used_blocks=self.hbm.resident_blocks,
            hbm_capacity_blocks=self.hbm.capacity_blocks,
        )
        self._drain_ddr_events()

        return PrefixLookupResult(
            hbm_hit_blocks=hbm_lookup.hbm_hit_blocks,
            ddr_hit_blocks=ddr_lookup.ddr_hit_blocks,
            miss_blocks=ddr_lookup.miss_blocks,
        )

    def materialize(
        self,
        blocks: tuple[PrefixBlock, ...],
        now_ms: float,
        request_id: str = "",
        instance_uuid: str = "",
        reason: str = "finish_time_materialization",
    ) -> None:
        self.hbm.materialize(
            blocks,
            now_ms=now_ms,
            request_id=request_id,
            instance_uuid=instance_uuid,
            reason=reason,
        )
        self._drain_hbm_events()

        self.ddr.store(
            blocks,
            now_ms=now_ms,
            request_id=request_id,
            instance_uuid=instance_uuid,
            hbm_used_blocks=self.hbm.resident_blocks,
            hbm_capacity_blocks=self.hbm.capacity_blocks,
            reason=_ddr_store_reason(reason),
        )
        self._drain_ddr_events()

    def take_events(self) -> tuple[CacheEvent, ...]:
        self._drain_hbm_events()
        self._drain_ddr_events()
        events = tuple(self._events)
        self._events.clear()
        return events

    def _drain_hbm_events(self) -> None:
        self._events.extend(self.hbm.take_events())

    def _drain_ddr_events(self) -> None:
        self._events.extend(self.ddr.take_events())


def _ddr_store_reason(hbm_materialize_reason: str) -> str:
    if hbm_materialize_reason == "progressive_chunk_materialization":
        return "progressive_chunk_store"
    return "finish_time_store"
