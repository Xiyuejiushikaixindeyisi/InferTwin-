import csv
from pathlib import Path

from infertwin.cache.events import CACHE_TIER_DDR, LOOKUP_MISS, MATERIALIZE, STORE, CacheEvent
from infertwin.report.cache_events import CACHE_EVENT_FIELDNAMES, CsvCacheEventWriter


def test_csv_cache_event_writer_writes_header_for_empty_events(tmp_path: Path) -> None:
    output_path = tmp_path / "cache_events.csv"

    with CsvCacheEventWriter(output_path) as writer:
        assert writer.stats.total_events == 0

    assert output_path.read_text(encoding="utf-8").splitlines() == [
        ",".join(CACHE_EVENT_FIELDNAMES)
    ]


def test_csv_cache_event_writer_streams_rows_and_tracks_stats(tmp_path: Path) -> None:
    output_path = tmp_path / "cache_events.csv"

    with CsvCacheEventWriter(output_path) as writer:
        writer.emit_many(
            (
                _event(LOOKUP_MISS, block_key="a", hbm_used_blocks=0),
                _event(MATERIALIZE, block_key="a", hbm_used_blocks=1),
            )
        )
        assert writer.snapshot_events() == ()
        assert writer.stats.total_events == 2
        assert writer.stats.lookup_miss_events == 1
        assert writer.stats.materialize_events == 1
        assert writer.stats.peak_hbm_used_blocks == 1
        assert writer.stats.final_hbm_used_blocks == 1

    rows = list(csv.DictReader(output_path.open(encoding="utf-8")))
    assert [row["event_type"] for row in rows] == [LOOKUP_MISS, MATERIALIZE]
    assert [row["block_key"] for row in rows] == ["a", "a"]
    assert [row["hbm_used_blocks"] for row in rows] == ["0", "1"]
    assert [row["ddr_used_blocks"] for row in rows] == ["0", "0"]
    assert [row["ddr_capacity_blocks"] for row in rows] == ["0", "0"]
    assert [row["source_tier"] for row in rows] == ["", ""]
    assert [row["target_tier"] for row in rows] == ["", ""]
    assert [row["load_tokens"] for row in rows] == ["0", "0"]
    assert [row["store_tokens"] for row in rows] == ["0", "0"]
    assert set(rows[0]) == set(CACHE_EVENT_FIELDNAMES)


def test_csv_cache_event_writer_writes_ddr_store_event_fields(tmp_path: Path) -> None:
    output_path = tmp_path / "cache_events.csv"

    with CsvCacheEventWriter(output_path) as writer:
        writer.emit_many((_ddr_store_event(),))
        assert writer.stats.total_events == 1
        assert writer.stats.store_events == 1
        assert writer.stats.peak_ddr_used_blocks == 7
        assert writer.stats.final_ddr_used_blocks == 7

    rows = list(csv.DictReader(output_path.open(encoding="utf-8")))
    assert len(rows) == 1
    row = rows[0]
    assert row["event_type"] == STORE
    assert row["cache_tier"] == CACHE_TIER_DDR
    assert row["ddr_used_blocks"] == "7"
    assert row["ddr_capacity_blocks"] == "64"
    assert row["source_tier"] == ""
    assert row["target_tier"] == CACHE_TIER_DDR
    assert row["load_tokens"] == "0"
    assert row["store_tokens"] == "16"


def test_csv_cache_event_writer_requires_context_manager(tmp_path: Path) -> None:
    writer = CsvCacheEventWriter(tmp_path / "cache_events.csv")

    try:
        writer.emit_many((_event(LOOKUP_MISS, block_key="a", hbm_used_blocks=0),))
    except ValueError as exc:
        assert "opened before writing" in str(exc)
    else:
        raise AssertionError("expected unopened writer to fail")


def _event(event_type: str, *, block_key: str, hbm_used_blocks: int) -> CacheEvent:
    return CacheEvent(
        event_type=event_type,
        timestamp_ms=1.0,
        instance_uuid="instance-a",
        request_id="request-a",
        block_key=block_key,
        block_index=0,
        token_count=16,
        cache_tier="hbm",
        reason="test",
        eviction_policy="lru",
        hbm_used_blocks=hbm_used_blocks,
        hbm_capacity_blocks=4,
    )


def _ddr_store_event() -> CacheEvent:
    return CacheEvent(
        event_type=STORE,
        timestamp_ms=1.0,
        instance_uuid="instance-a",
        request_id="request-a",
        block_key="ddr-block",
        block_index=0,
        token_count=16,
        cache_tier=CACHE_TIER_DDR,
        reason="test_store",
        eviction_policy="lru",
        hbm_used_blocks=2,
        hbm_capacity_blocks=4,
        ddr_used_blocks=7,
        ddr_capacity_blocks=64,
        target_tier=CACHE_TIER_DDR,
        store_tokens=16,
    )
