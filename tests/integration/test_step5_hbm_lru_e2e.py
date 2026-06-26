import csv
import json
from pathlib import Path

from hitfloor.experiment.runner import ExperimentRunner


def test_step5_e2e_capacity_controls_eviction_and_repeat_hits(tmp_path: Path) -> None:
    trace_path = tmp_path / "interleaved_trace.csv"
    _write_trace(
        trace_path,
        [
            ("r1", "instance-a", "alpha prompt for e2e cache replay", "2026-06-05 09:01:23"),
            ("r2", "instance-a", "beta prompt for e2e cache replay", "2026-06-05 09:01:24"),
            ("r3", "instance-a", "alpha prompt for e2e cache replay", "2026-06-05 09:01:25"),
        ],
    )

    enough_output = tmp_path / "capacity_enough"
    enough_result = ExperimentRunner(
        _config(trace_path=trace_path, output_dir=enough_output, hbm_capacity_blocks=128)
    ).run()
    enough_rows = _request_rows(enough_output)
    assert enough_result.metrics["phase"] == "batch_aware_hbm_lru"
    assert int(enough_rows["r3"]["hbm_hit_tokens"]) == int(enough_rows["r1"]["prompt_tokens"])
    assert int(enough_rows["r3"]["miss_tokens"]) == 0

    constrained_output = tmp_path / "capacity_constrained"
    constrained_result = ExperimentRunner(
        _config(trace_path=trace_path, output_dir=constrained_output, hbm_capacity_blocks=1)
    ).run()
    constrained_rows = _request_rows(constrained_output)
    constrained_events = _cache_event_rows(constrained_output)

    assert constrained_result.metrics["cache_event_count"] == len(constrained_events)
    assert _event_count(constrained_events, "evict") > 0
    assert int(constrained_rows["r3"]["hbm_hit_tokens"]) == 0
    assert int(constrained_rows["r3"]["miss_tokens"]) == int(
        constrained_rows["r3"]["prompt_tokens"]
    )
    assert int(constrained_rows["r3"]["hbm_hit_tokens"]) < int(enough_rows["r3"]["hbm_hit_tokens"])


def test_step5_e2e_multi_instance_cache_isolation(tmp_path: Path) -> None:
    trace_path = tmp_path / "multi_instance_trace.csv"
    _write_trace(
        trace_path,
        [
            ("r1", "instance-a", "shared prompt should not cross instance", "2026-06-05 09:01:23"),
            ("r2", "instance-b", "shared prompt should not cross instance", "2026-06-05 09:01:24"),
            ("r3", "instance-a", "shared prompt should not cross instance", "2026-06-05 09:01:25"),
        ],
    )
    output_dir = tmp_path / "multi_instance"

    ExperimentRunner(
        _config(trace_path=trace_path, output_dir=output_dir, hbm_capacity_blocks=128)
    ).run()

    rows = _request_rows(output_dir)
    assert int(rows["r1"]["miss_tokens"]) == int(rows["r1"]["prompt_tokens"])
    assert int(rows["r2"]["miss_tokens"]) == int(rows["r2"]["prompt_tokens"])
    assert int(rows["r2"]["hbm_hit_tokens"]) == 0
    assert int(rows["r3"]["hbm_hit_tokens"]) == int(rows["r3"]["prompt_tokens"])


def _config(
    *,
    trace_path: Path,
    output_dir: Path,
    hbm_capacity_blocks: int,
) -> dict:
    return {
        "simulation": {"mode": "batch_aware_hbm_lru"},
        "trace": {"path": str(trace_path)},
        "tokenizers": {
            "root": "tokenizers",
            "default_profile": "glm-v5",
            "cache_scope": "tenant_isolated",
        },
        "cache": {
            "block_size_tokens": 256,
            "policy": "hbm",
            "eviction_policy": "lru",
            "hbm_capacity_blocks": hbm_capacity_blocks,
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
                "ms_per_uncached_token": 0.001,
                "calibrated_from": "integration-test",
            },
        },
        "output": {"directory": str(output_dir)},
    }


def _write_trace(path: Path, rows: list[tuple[str, str, str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                "request_id",
                "tenant_id",
                "instance_uuid",
                "request_params",
                "service_start_time",
            ],
        )
        writer.writeheader()
        for request_id, instance_uuid, prompt, timestamp in rows:
            writer.writerow(
                {
                    "request_id": request_id,
                    "tenant_id": "tenant-a",
                    "instance_uuid": instance_uuid,
                    "request_params": json.dumps(
                        {
                            "model": "glm-v5",
                            "messages": [{"role": "user", "content": prompt}],
                            "tools": [],
                        },
                        ensure_ascii=True,
                    ),
                    "service_start_time": timestamp,
                }
            )


def _request_rows(output_dir: Path) -> dict[str, dict[str, str]]:
    rows = csv.DictReader((output_dir / "request_metrics.csv").open(encoding="utf-8"))
    return {row["request_id"]: row for row in rows}


def _cache_event_rows(output_dir: Path) -> list[dict[str, str]]:
    return list(csv.DictReader((output_dir / "cache_events.csv").open(encoding="utf-8")))


def _event_count(rows: list[dict[str, str]], event_type: str) -> int:
    return sum(row["event_type"] == event_type for row in rows)
