from pathlib import Path

from infertwin.experiment.sweep import CapacitySweepResult, CapacitySweepRow
from infertwin.report.sweep import write_capacity_sweep_report


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
    assert "8,trace," in csv_text
    assert "8,instance,instance-a" in csv_text

    summary = paths.summary_path.read_text(encoding="utf-8")
    assert "InferTwin Capacity Sweep Summary" in summary
    assert "fitted_ttft" in summary
    assert "DDR / SSD cache hits are not modeled" in summary
    assert "P90 target matching / hit floor search is not performed" in summary
    assert "Instance rows set `cache_event_count` to 0" in summary
    assert "capacity_8/cache_events.csv" in summary


def _row(*, capacity: int, scope: str, instance_uuid: str, cache_event_count: int):
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
    )
