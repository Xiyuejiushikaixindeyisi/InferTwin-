"""Cache event sink interfaces and in-memory implementations."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

from infertwin.cache.events import EVICT, LOOKUP_HIT, LOOKUP_MISS, MATERIALIZE, CacheEvent


@dataclass(slots=True)
class CacheEventStats:
    total_events: int = 0
    lookup_hit_events: int = 0
    lookup_miss_events: int = 0
    materialize_events: int = 0
    evict_events: int = 0
    peak_hbm_used_blocks: int = 0
    final_hbm_used_blocks: int = 0

    def record(self, event: CacheEvent) -> None:
        self.total_events += 1
        if event.event_type == LOOKUP_HIT:
            self.lookup_hit_events += 1
        elif event.event_type == LOOKUP_MISS:
            self.lookup_miss_events += 1
        elif event.event_type == MATERIALIZE:
            self.materialize_events += 1
        elif event.event_type == EVICT:
            self.evict_events += 1

        self.peak_hbm_used_blocks = max(
            self.peak_hbm_used_blocks,
            event.hbm_used_blocks,
        )
        self.final_hbm_used_blocks = event.hbm_used_blocks

    def snapshot(self) -> CacheEventStats:
        return CacheEventStats(
            total_events=self.total_events,
            lookup_hit_events=self.lookup_hit_events,
            lookup_miss_events=self.lookup_miss_events,
            materialize_events=self.materialize_events,
            evict_events=self.evict_events,
            peak_hbm_used_blocks=self.peak_hbm_used_blocks,
            final_hbm_used_blocks=self.final_hbm_used_blocks,
        )


class CacheEventSink(Protocol):
    """Consumer for cache events emitted during replay."""

    @property
    def stats(self) -> CacheEventStats: ...

    def snapshot_stats(self) -> CacheEventStats: ...

    def emit_many(self, events: Iterable[CacheEvent]) -> None: ...

    def snapshot_events(self) -> tuple[CacheEvent, ...]: ...


class NullCacheEventSink:
    """Drop cache events while preserving the sink interface."""

    @property
    def stats(self) -> CacheEventStats:
        return CacheEventStats()

    def snapshot_stats(self) -> CacheEventStats:
        return self.stats.snapshot()

    def emit_many(self, events: Iterable[CacheEvent]) -> None:
        for _event in events:
            pass

    def snapshot_events(self) -> tuple[CacheEvent, ...]:
        return ()


class StatsOnlyCacheEventSink:
    """Track aggregate cache event stats without retaining event payloads."""

    def __init__(self) -> None:
        self._stats = CacheEventStats()

    @property
    def stats(self) -> CacheEventStats:
        return self._stats

    def snapshot_stats(self) -> CacheEventStats:
        return self._stats.snapshot()

    def emit_many(self, events: Iterable[CacheEvent]) -> None:
        for event in events:
            self._stats.record(event)

    def snapshot_events(self) -> tuple[CacheEvent, ...]:
        return ()


class InMemoryCacheEventSink:
    """Collect cache events for focused tests and small local experiments."""

    def __init__(self, max_events: int | None = 100_000) -> None:
        if max_events is not None and max_events <= 0:
            raise ValueError("max_events must be positive when provided")
        self.max_events = max_events
        self._events: list[CacheEvent] = []
        self._stats = CacheEventStats()

    @property
    def stats(self) -> CacheEventStats:
        return self._stats

    def snapshot_stats(self) -> CacheEventStats:
        return self._stats.snapshot()

    def emit_many(self, events: Iterable[CacheEvent]) -> None:
        for event in events:
            if self.max_events is not None and len(self._events) >= self.max_events:
                raise MemoryError(
                    "InMemoryCacheEventSink reached max_events; use "
                    "StatsOnlyCacheEventSink or CsvCacheEventWriter for large traces."
                )
            self._events.append(event)
            self._stats.record(event)

    def snapshot_events(self) -> tuple[CacheEvent, ...]:
        return tuple(self._events)
