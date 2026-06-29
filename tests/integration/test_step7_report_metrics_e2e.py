import csv
import json
from pathlib import Path

import pytest
import yaml

from infertwin.cache.events import CACHE_TIER_DDR, LOOKUP_HIT, STORE
from infertwin.cli.main import run_streaming_capacity_sweep
from infertwin.experiment.sweep import CapacitySweepRow
from infertwin.report.sweep import write_capacity_sweep_summary
from infertwin.streaming.cache_factory import CACHE_MODE_HBM_DDR_LRU
from infertwin.streaming.sweep import STREAMING_CAPACITY_SWEEP_MODE


def test_step7_streaming_report_metrics_event_dump_and_cli_e2e(tmp_path: Path) -> None:
    tokenizer_root = tmp_path / "tokenizers"
    _write_simple_tokenizer_profile(tokenizer_root, profile="runtime-tokenizer")
    trace_path = _write_trace(
        tmp_path,
        rows=[
            ("req-a1", "instance-a", "one two three four five six seven eight", 23),
            ("req-b1", "instance-b", "one two three four five six seven eight", 24),
            ("req-a2", "instance-a", "one two three four five six seven eight", 25),
        ],
    )
    instance_profile_path = _write_instance_profile(tmp_path)
    registry_path = _write_pooling_model_registry(tmp_path)
    output_dir = tmp_path / "reports"
    config = _streaming_config(
        trace_path=trace_path,
        tokenizer_root=tokenizer_root,
        instance_profile_path=instance_profile_path,
        registry_path=registry_path,
        output_dir=output_dir,
        capacities=[1],
        cache_events=True,
        cache_event_capacities=[1],
    )
    config_path = tmp_path / "step7_ddr_streaming.yaml"
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")

    paths = run_streaming_capacity_sweep(config_path)

    assert paths.capacity_sweep_path.is_file()
    assert paths.summary_path.is_file()
    capacity_rows = list(csv.DictReader(paths.capacity_sweep_path.open(encoding="utf-8")))
    trace_row = _only_row(capacity_rows, scope="trace", instance_uuid="")
    instance_rows = [row for row in capacity_rows if row["scope"] == "instance"]
    assert trace_row["hbm_capacity_blocks"] == "1"
    assert int(trace_row["ddr_hit_tokens"]) > 0
    _assert_token_invariants(trace_row)
    for row in instance_rows:
        _assert_token_invariants(row)
    _assert_trace_equals_instance_sum(trace_row, instance_rows)
    assert {row["cache_event_count"] for row in instance_rows} == {"0"}

    event_path = output_dir / "capacity_1" / "cache_events.csv"
    assert event_path.is_file()
    event_rows = list(csv.DictReader(event_path.open(encoding="utf-8")))
    assert len(event_rows) == int(trace_row["cache_event_count"])
    ddr_rows = [row for row in event_rows if row["cache_tier"] == CACHE_TIER_DDR]
    assert any(row["event_type"] == STORE and int(row["store_tokens"]) > 0 for row in ddr_rows)
    assert any(row["event_type"] == LOOKUP_HIT for row in ddr_rows)
    assert {row["ddr_capacity_blocks"] for row in ddr_rows} == {"32"}

    summary = paths.summary_path.read_text(encoding="utf-8")
    assert CACHE_MODE_HBM_DDR_LRU in summary
    assert "DDR hit accounting is modeled" in summary
    assert "DDR KV load latency is modeled when configured by Step8" in summary
    assert "Cross-instance KV pooling is not modeled" in summary
    assert "DDR / SSD cache hits are not modeled yet; DDR fields are reserved as 0" not in summary
    assert "capacity_1/cache_events.csv" in summary


def test_hbm_only_summary_keeps_ddr_reserved_as_mode_specific_assumption(
    tmp_path: Path,
) -> None:
    summary_path = tmp_path / "summary.md"

    write_capacity_sweep_summary(
        summary_path,
        rows=(
            CapacitySweepRow(
                hbm_capacity_blocks=1,
                scope="trace",
                instance_uuid="",
                request_count=1,
                iteration_count=1,
                total_prompt_tokens=8,
                hbm_hit_tokens=0,
                ddr_hit_tokens=0,
                miss_tokens=8,
                total_hit_tokens=0,
                kv_hit_rate=0.0,
                hbm_hit_rate=0.0,
                ddr_hit_rate=0.0,
                p50_ttft_ms=8.0,
                p90_ttft_ms=8.0,
                p99_ttft_ms=8.0,
                cache_event_count=0,
            ),
        ),
        config_details={
            "latency_backend": "fitted_ttft",
            "model_name": "model-a",
            "hardware_name": "hardware-a",
            "streaming_cache_mode": "batch_aware_hbm_lru",
            "streaming_cache_eviction_policy": "lru",
            "capacities": (1,),
            "cache_event_capacities": (),
        },
    )

    summary = summary_path.read_text(encoding="utf-8")
    assert "DDR / SSD cache hits are not modeled in this mode" in summary
    assert "DDR hit accounting is modeled" not in summary
    assert "Streaming cache mode: batch_aware_hbm_lru" in summary


def _assert_token_invariants(row: dict[str, str]) -> None:
    total_prompt_tokens = int(row["total_prompt_tokens"])
    hbm_hit_tokens = int(row["hbm_hit_tokens"])
    ddr_hit_tokens = int(row["ddr_hit_tokens"])
    miss_tokens = int(row["miss_tokens"])
    total_hit_tokens = int(row["total_hit_tokens"])
    assert hbm_hit_tokens + ddr_hit_tokens + miss_tokens == total_prompt_tokens
    assert total_hit_tokens == hbm_hit_tokens + ddr_hit_tokens
    if total_prompt_tokens > 0:
        assert float(row["kv_hit_rate"]) == pytest.approx(
            total_hit_tokens / total_prompt_tokens
        )
        assert float(row["hbm_hit_rate"]) == pytest.approx(
            hbm_hit_tokens / total_prompt_tokens
        )
        assert float(row["ddr_hit_rate"]) == pytest.approx(
            ddr_hit_tokens / total_prompt_tokens
        )


def _assert_trace_equals_instance_sum(
    trace_row: dict[str, str],
    instance_rows: list[dict[str, str]],
) -> None:
    summed_fields = (
        "request_count",
        "iteration_count",
        "total_prompt_tokens",
        "hbm_hit_tokens",
        "ddr_hit_tokens",
        "miss_tokens",
        "total_hit_tokens",
    )
    for field in summed_fields:
        assert int(trace_row[field]) == sum(int(row[field]) for row in instance_rows)


def _only_row(
    rows: list[dict[str, str]],
    *,
    scope: str,
    instance_uuid: str,
) -> dict[str, str]:
    matches = [
        row for row in rows if row["scope"] == scope and row["instance_uuid"] == instance_uuid
    ]
    assert len(matches) == 1
    return matches[0]


def _streaming_config(
    *,
    trace_path: Path,
    tokenizer_root: Path,
    instance_profile_path: Path,
    registry_path: Path,
    output_dir: Path,
    capacities: list[int],
    cache_events: bool,
    cache_event_capacities: list[int],
) -> dict[str, object]:
    return {
        "simulation": {"mode": STREAMING_CAPACITY_SWEEP_MODE},
        "trace": {"path": str(trace_path)},
        "tokenizers": {
            "root": str(tokenizer_root),
            "default_profile": "runtime-tokenizer",
            "cache_scope": "tenant_isolated",
        },
        "cache": {
            "mode": CACHE_MODE_HBM_DDR_LRU,
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
        },
        "latency": {
            "backend": "fitted_ttft",
            "model_name": "global",
            "hardware_name": "global",
            "fitted_ttft": {
                "profile": "global-ttft",
                "function": "token_linear_v1",
                "intercept_ms": 0.0,
                "ms_per_uncached_token": 1.0,
                "calibrated_from": "global",
            },
        },
        "model_registry": {"profile_path": str(registry_path)},
        "instance_runtime": {"profile_path": str(instance_profile_path)},
        "instance_latency": {
            "profile_path": str(instance_profile_path),
            "require_all_trace_instances": True,
        },
        "streaming": {
            "shard_root": str(output_dir / "streaming_shards"),
            "rejected_path": str(output_dir / "rejected_requests.csv"),
            "require_sorted_trace": True,
        },
        "output": {
            "directory": str(output_dir),
            "cache_events": cache_events,
            "cache_event_capacities": cache_event_capacities,
        },
    }


def _write_trace(
    tmp_path: Path,
    *,
    rows: list[tuple[str, str, str, int]],
) -> Path:
    path = tmp_path / "trace.csv"
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=(
                "request_id",
                "tenant_id",
                "instance_uuid",
                "request_params",
                "service_start_time",
            ),
        )
        writer.writeheader()
        for request_id, instance_uuid, prompt, second in rows:
            writer.writerow(
                {
                    "request_id": request_id,
                    "tenant_id": "tenant-a",
                    "instance_uuid": instance_uuid,
                    "request_params": json.dumps(
                        {
                            "model": "model-a",
                            "messages": [{"role": "user", "content": prompt}],
                            "tools": [],
                        }
                    ),
                    "service_start_time": f"2026-06-05 09:01:{second}",
                }
            )
    return path


def _write_instance_profile(tmp_path: Path) -> Path:
    path = tmp_path / "instances.yaml"
    path.write_text(
        """
instances:
  name: step7-report-e2e
  items:
    instance-a:
      model_name: model-a
      deployment: model-a-deployment
    instance-b:
      model_name: model-a
      deployment: model-a-deployment
""",
        encoding="utf-8",
    )
    return path


def _write_pooling_model_registry(tmp_path: Path) -> Path:
    _write_model_profile(tmp_path / "model-a.yaml")
    _write_deployment_profile(tmp_path / "model-a-deployment.yaml")
    path = tmp_path / "registry.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "models": {
                    "model-a": {
                        "model_profile_path": "model-a.yaml",
                        "deployment_profile_path": "model-a-deployment.yaml",
                        "tokenizer_profile": "runtime-tokenizer",
                        "default_cache": {
                            "hbm_capacity_blocks": 99,
                            "ddr_capacity_blocks": 32,
                            "block_size_tokens": 2,
                            "eviction_policy": "lru",
                            "pooling": {
                                "enabled": True,
                                "single_instance": True,
                                "multi_instance": False,
                                "ddr_enabled": True,
                                "remote_enabled": False,
                                "ssd_enabled": False,
                            },
                        },
                        "default_latency": {
                            "backend": "fitted_ttft",
                            "model_name": "model-a",
                            "hardware_name": "runtime-hardware",
                            "fitted_ttft": {
                                "profile": "model-a-default-ttft",
                                "function": "token_linear_v1",
                                "intercept_ms": 0.0,
                                "ms_per_uncached_token": 1.0,
                                "calibrated_from": "model-default",
                            },
                            "kv_load": {
                                "ddr_ms_per_cached_token": 0.0,
                                "remote_ms_per_cached_token": 0.0,
                            },
                        },
                    }
                }
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return path


def _write_model_profile(path: Path) -> None:
    path.write_text(
        """
model:
  name: model-a
  tokenizer_profile: runtime-tokenizer
  cache_family: full_attention
""",
        encoding="utf-8",
    )


def _write_deployment_profile(path: Path) -> None:
    path.write_text(
        """
deployment:
  name: model-a-deployment
  engine: vllm-ascend
  scheduler:
    max_num_seqs: 32
    max_num_batched_tokens: 8192
    enable_chunked_prefill: true
  cache_features:
    prefix_caching: true
    pooling: true
    multi_tier_cache: true
    runtime_block_size: 2
""",
        encoding="utf-8",
    )


def _write_simple_tokenizer_profile(root: Path, *, profile: str) -> None:
    profile_dir = root / profile
    profile_dir.mkdir(parents=True)
    (profile_dir / "manifest.yaml").write_text(
        f"""
tokenizer:
  profile: {profile}
  type: simple
  include_tools: true
  model_aliases:
    - {profile}
""",
        encoding="utf-8",
    )
