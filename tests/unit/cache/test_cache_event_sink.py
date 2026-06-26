from infertwin.cache.event_sink import (
    InMemoryCacheEventSink,
    NullCacheEventSink,
    StatsOnlyCacheEventSink,
)
from infertwin.cache.events import EVICT, LOOKUP_HIT, LOOKUP_MISS, MATERIALIZE, CacheEvent


def test_in_memory_cache_event_sink_collects_events_and_stats() -> None:
    sink = InMemoryCacheEventSink()
    events = (
        _event(LOOKUP_MISS, hbm_used_blocks=0),
        _event(MATERIALIZE, hbm_used_blocks=1),
        _event(LOOKUP_HIT, hbm_used_blocks=1),
        _event(EVICT, hbm_used_blocks=0),
    )

    sink.emit_many(events)

    assert sink.snapshot_events() == events
    assert sink.stats.total_events == 4
    assert sink.stats.lookup_miss_events == 1
    assert sink.stats.materialize_events == 1
    assert sink.stats.lookup_hit_events == 1
    assert sink.stats.evict_events == 1
    assert sink.stats.peak_hbm_used_blocks == 1
    assert sink.stats.final_hbm_used_blocks == 0


def test_in_memory_cache_event_sink_fails_when_event_cap_is_reached() -> None:
    sink = InMemoryCacheEventSink(max_events=1)
    sink.emit_many((_event(LOOKUP_MISS, hbm_used_blocks=0),))

    try:
        sink.emit_many((_event(MATERIALIZE, hbm_used_blocks=1),))
    except MemoryError as exc:
        assert "StatsOnlyCacheEventSink" in str(exc)
    else:
        raise AssertionError("expected MemoryError")

    assert sink.stats.total_events == 1
    assert len(sink.snapshot_events()) == 1


def test_null_cache_event_sink_drops_events_without_tracking_stats() -> None:
    sink = NullCacheEventSink()

    sink.emit_many((_event(LOOKUP_MISS, hbm_used_blocks=3),))

    assert sink.snapshot_events() == ()
    assert sink.stats.total_events == 0
    assert sink.stats.peak_hbm_used_blocks == 0


def test_stats_only_cache_event_sink_tracks_stats_without_payloads() -> None:
    sink = StatsOnlyCacheEventSink()
    events = (
        _event(LOOKUP_MISS, hbm_used_blocks=0),
        _event(MATERIALIZE, hbm_used_blocks=2),
        _event(EVICT, hbm_used_blocks=1),
    )

    sink.emit_many(events)

    assert sink.snapshot_events() == ()
    assert sink.stats.total_events == 3
    assert sink.stats.lookup_miss_events == 1
    assert sink.stats.materialize_events == 1
    assert sink.stats.evict_events == 1
    assert sink.stats.peak_hbm_used_blocks == 2
    assert sink.stats.final_hbm_used_blocks == 1


def test_cache_event_stats_snapshot_is_not_mutated_by_later_events() -> None:
    sink = InMemoryCacheEventSink()
    sink.emit_many((_event(LOOKUP_MISS, hbm_used_blocks=0),))
    snapshot = sink.snapshot_stats()

    sink.emit_many((_event(MATERIALIZE, hbm_used_blocks=1),))

    assert snapshot.total_events == 1
    assert snapshot.lookup_miss_events == 1
    assert snapshot.materialize_events == 0
    assert sink.stats.total_events == 2


def _event(event_type: str, *, hbm_used_blocks: int) -> CacheEvent:
    return CacheEvent(
        event_type=event_type,
        timestamp_ms=1.0,
        instance_uuid="instance-a",
        request_id="request-a",
        block_key=f"block-{event_type}",
        block_index=0,
        token_count=16,
        cache_tier="hbm",
        reason="test",
        eviction_policy="lru",
        hbm_used_blocks=hbm_used_blocks,
        hbm_capacity_blocks=4,
    )
