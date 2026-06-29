from pathlib import Path

from infertwin.experiment.sweep import CapacitySweepResult, CapacitySweepRow
from infertwin.report.sweep import write_capacity_sweep_report
from infertwin.replay.timeline import CHUNK_TTFT_GRANULARITY, PROGRESSIVE_TIMELINE_MODE


def test_write_capacity_sweep_report_writes_csv_and_summary(tmp_path: Path) -> None:
    result = CapacitySweepResult(
        rows=(
            _row(capacity=8, scope="trace", instance_uuid="", cache_event_count=10),
            _row(capacity=8, scope="instance", instance_uuid="instance-a", cache_event_count=0),
        ),
        config_details={
            "latency_backend": "fitted_ttft",
            "model_name": "glm-v5",
            "hardware_name": "ascend910c",
            "capacities": (8,),
            "cache_event_capacities": (8,),
        },
        cache_event_paths={8: tmp_path / "capacity_8" / "cache_events.csv"},
    )

    paths = write_capacity_sweep_report(result, tmp_path)

    assert paths.capacity_sweep_path.is_file()
    assert paths.summary_path.is_file()
    csv_text = paths.capacity_sweep_path.read_text(encoding="utf-8")
    assert "hbm_capacity_blocks,scope,instance_uuid" in csv_text
    assert "p90_kv_load_ms" in csv_text
    assert "timeline_mode" in csv_text
    assert "total_compute_wait_ms" in csv_text
    assert "total_progressive_materialized_tokens" in csv_text
    assert "8,trace," in csv_text
    assert "8,instance,instance-a" in csv_text

    summary = paths.summary_path.read_text(encoding="utf-8")
    assert "InferTwin Capacity Sweep Summary" in summary
    assert "fitted_ttft" in summary
    assert "p90_kv_load_ms" in summary
    assert "Timeline Results" in summary
    assert "p90_compute_wait_ms" in summary
    assert "DDR / SSD cache hits are not modeled" in summary
    assert "P90 target matching / hit floor search is not performed" in summary
    assert "Instance rows set `cache_event_count` to 0" in summary
    assert "capacity_8/cache_events.csv" in summary


def test_write_capacity_sweep_report_documents_progressive_timeline(tmp_path: Path) -> None:
    result = CapacitySweepResult(
        rows=(
            _row(
                capacity=8,
                scope="trace",
                instance_uuid="",
                cache_event_count=10,
                timeline_mode=PROGRESSIVE_TIMELINE_MODE,
                ttft_granularity=CHUNK_TTFT_GRANULARITY,
                total_compute_wait_ms=3.0,
                total_kv_load_wait_ms=2.0,
                total_progressive_materialized_tokens=8,
            ),
        ),
        config_details={
            "latency_backend": "serving_latency_profile",
            "model_name": "glm-v5",
            "hardware_name": "ascend910c",
            "capacities": (8,),
            "cache_event_capacities": (),
            "streaming_cache_mode": PROGRESSIVE_TIMELINE_MODE,
        },
        cache_event_paths={},
    )

    paths = write_capacity_sweep_report(result, tmp_path)

    summary = paths.summary_path.read_text(encoding="utf-8")
    assert "Progressive timeline mode is enabled" in summary
    assert "Full miss blocks become visible after scheduled chunk finish" in summary
    assert PROGRESSIVE_TIMELINE_MODE in summary
    assert CHUNK_TTFT_GRANULARITY in summary
    assert "8 | batch_aware_hbm_ddr_lru_progressive_timeline | chunk" in summary


def _row(
    *,
    capacity: int,
    scope: str,
    instance_uuid: str,
    cache_event_count: int,
    timeline_mode: str = "legacy_iteration_v1",
    ttft_granularity: str = "iteration",
    total_compute_wait_ms: float = 0.0,
    total_kv_load_wait_ms: float = 0.0,
    total_progressive_materialized_tokens: int = 0,
):
    return CapacitySweepRow(
        hbm_capacity_blocks=capacity,
        scope=scope,
        instance_uuid=instance_uuid,
        request_count=1,
        iteration_count=1,
        total_prompt_tokens=10,
        hbm_hit_tokens=5,
        ddr_hit_tokens=0,
        miss_tokens=5,
        total_hit_tokens=5,
        kv_hit_rate=0.5,
        hbm_hit_rate=0.5,
        ddr_hit_rate=0.0,
        p50_ttft_ms=10.0,
        p90_ttft_ms=10.0,
        p99_ttft_ms=10.0,
        cache_event_count=cache_event_count,
        timeline_mode=timeline_mode,
        ttft_granularity=ttft_granularity,
        total_compute_wait_ms=total_compute_wait_ms,
        p90_compute_wait_ms=total_compute_wait_ms,
        total_kv_load_wait_ms=total_kv_load_wait_ms,
        p90_kv_load_wait_ms=total_kv_load_wait_ms,
        total_progressive_materialized_tokens=total_progressive_materialized_tokens,
    )
