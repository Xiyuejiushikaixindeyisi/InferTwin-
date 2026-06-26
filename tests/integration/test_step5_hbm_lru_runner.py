import csv
from pathlib import Path

from hitfloor.experiment.runner import ExperimentRunner


def test_step5_hbm_lru_runner_writes_cache_events_and_summary(tmp_path: Path) -> None:
    trace_path = tmp_path / "trace.csv"
    trace_path.write_text(
        "\n".join(
            [
                "request_id,tenant_id,instance_uuid,request_params,service_start_time",
                _row(
                    "00000000000000000000000000000001",
                    "instance-a",
                    "same prompt",
                    "2026-06-05 09:01:23",
                ),
                _row(
                    "00000000000000000000000000000002",
                    "instance-a",
                    "different prompt",
                    "2026-06-05 09:01:24",
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    config = {
        "simulation": {"mode": "batch_aware_hbm_lru"},
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
            "hbm_capacity_blocks": 1,
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
        "output": {"directory": str(tmp_path / "reports")},
    }

    result = ExperimentRunner(config).run()

    assert result.metrics["phase"] == "batch_aware_hbm_lru"
    assert result.metrics["hbm_capacity_blocks"] == 1
    assert result.metrics["eviction_policy"] == "lru"
    assert result.metrics["cache_event_count"] > 0

    output_dir = tmp_path / "reports"
    request_metrics_path = output_dir / "request_metrics.csv"
    iteration_metrics_path = output_dir / "iteration_metrics.csv"
    cache_events_path = output_dir / "cache_events.csv"
    summary_path = output_dir / "summary.md"
    assert request_metrics_path.is_file()
    assert iteration_metrics_path.is_file()
    assert cache_events_path.is_file()
    assert summary_path.is_file()

    cache_event_rows = list(csv.DictReader(cache_events_path.open(encoding="utf-8")))
    assert cache_event_rows
    assert result.metrics["cache_event_count"] == len(cache_event_rows)
    assert {"event_type", "eviction_policy", "hbm_capacity_blocks"}.issubset(cache_event_rows[0])
    assert {row["eviction_policy"] for row in cache_event_rows} == {"lru"}
    assert {row["hbm_capacity_blocks"] for row in cache_event_rows} == {"1"}

    summary = summary_path.read_text(encoding="utf-8")
    assert "batch_aware_hbm_lru" in summary
    assert "hbm_capacity_blocks" in summary
    assert "eviction_policy" in summary
    assert "Cache events:" in summary
    assert f"- Cache events: {len(cache_event_rows)}" in summary
    assert "Evict events:" in summary


def _row(
    request_id: str,
    instance_uuid: str,
    prompt: str,
    timestamp: str,
) -> str:
    request_params = (
        "{"
        '""model"":""glm-v5"",'
        '""messages"":[{""role"":""user"",""content"":""'
        f"{prompt}"
        '""}],'
        '""tools"":[]'
        "}"
    )
    return f'{request_id},tenant-a,{instance_uuid},"{request_params}",{timestamp}'
