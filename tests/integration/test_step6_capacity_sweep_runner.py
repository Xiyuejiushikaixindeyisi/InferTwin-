import csv
from pathlib import Path

from infertwin.experiment.sweep import CapacitySweepRunner
from infertwin.report.sweep import write_capacity_sweep_report


def test_capacity_sweep_runner_returns_rows_without_report_files(tmp_path: Path) -> None:
    config = _config(tmp_path, capacities=[1, 4], cache_events=False)

    result = CapacitySweepRunner(config).run()

    assert len(result.rows) == 6
    assert not (tmp_path / "reports" / "capacity_sweep.csv").exists()
    assert not (tmp_path / "reports" / "summary.md").exists()

    trace_rows = [row for row in result.rows if row.scope == "trace"]
    instance_rows = [row for row in result.rows if row.scope == "instance"]
    assert [row.hbm_capacity_blocks for row in trace_rows] == [1, 4]
    assert {row.instance_uuid for row in instance_rows} == {"instance-a", "instance-b"}
    assert all(row.ddr_hit_tokens == 0 for row in result.rows)
    assert all(row.ddr_hit_rate == 0.0 for row in result.rows)
    for row in result.rows:
        assert row.hbm_hit_tokens + row.ddr_hit_tokens + row.miss_tokens == row.total_prompt_tokens
        assert row.total_hit_tokens == row.hbm_hit_tokens + row.ddr_hit_tokens
    assert all(row.cache_event_count > 0 for row in trace_rows)
    assert all(row.cache_event_count == 0 for row in instance_rows)


def test_capacity_sweep_runner_dumps_events_only_for_selected_capacity(tmp_path: Path) -> None:
    config = _config(tmp_path, capacities=[1, 4], cache_events=True, cache_event_capacities=[1])

    result = CapacitySweepRunner(config).run()

    selected_path = tmp_path / "reports" / "capacity_1" / "cache_events.csv"
    other_path = tmp_path / "reports" / "capacity_4" / "cache_events.csv"
    assert result.cache_event_paths == {1: selected_path}
    assert selected_path.is_file()
    assert not other_path.exists()
    event_rows = list(csv.DictReader(selected_path.open(encoding="utf-8")))
    assert event_rows

    paths = write_capacity_sweep_report(result, tmp_path / "reports")
    assert paths.capacity_sweep_path.is_file()
    assert paths.summary_path.is_file()
    summary = paths.summary_path.read_text(encoding="utf-8")
    assert "capacity_1/cache_events.csv" in summary


def _config(
    tmp_path: Path,
    *,
    capacities: list[int],
    cache_events: bool,
    cache_event_capacities: list[int] | None = None,
) -> dict[str, object]:
    trace_path = tmp_path / "trace.csv"
    trace_path.write_text(
        "\n".join(
            [
                "request_id,tenant_id,instance_uuid,request_params,service_start_time",
                _row("00000000000000000000000000000001", "instance-a", "same prompt", 23),
                _row("00000000000000000000000000000002", "instance-a", "same prompt", 24),
                _row("00000000000000000000000000000003", "instance-b", "same prompt", 25),
                _row("00000000000000000000000000000004", "instance-b", "same prompt", 26),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return {
        "simulation": {"mode": "capacity_sweep"},
        "trace": {"path": str(trace_path)},
        "tokenizers": {
            "root": "tokenizers",
            "default_profile": "glm-v5",
            "cache_scope": "tenant_isolated",
        },
        "cache": {
            "block_size_tokens": 4,
            "policy": "hbm",
            "eviction_policy": "lru",
        },
        "sweep": {
            "hbm_capacity_blocks": capacities,
            "parallel_instances": False,
        },
        "scheduler": {
            "policy": "fcfs",
            "max_num_batched_tokens": 8192,
            "max_num_seqs": 32,
            "enable_chunked_prefill": True,
            "long_prefill_token_threshold": 4096,
        },
        "latency": {
            "backend": "fitted_ttft",
            "model_name": "glm-v5",
            "hardware_name": "ascend910c",
            "fitted_ttft": {
                "profile": "glm-v5_ascend910c_default",
                "function": "token_linear_v1",
                "intercept_ms": 0.0,
                "ms_per_uncached_token": 1.0,
                "calibrated_from": "integration-test",
            },
        },
        "output": {
            "directory": str(tmp_path / "reports"),
            "cache_events": cache_events,
            "cache_event_capacities": cache_event_capacities or [],
        },
    }


def _row(request_id: str, instance_uuid: str, prompt: str, second: int) -> str:
    request_params = (
        "{"
        '""model"":""glm-v5"",'
        '""messages"":[{""role"":""user"",""content"":""'
        f"{prompt}"
        '""}],'
        '""tools"":[]'
        "}"
    )
    return f'{request_id},tenant-a,{instance_uuid},"{request_params}",2026-06-05 09:01:{second}'
