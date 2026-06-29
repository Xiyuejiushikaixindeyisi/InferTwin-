import csv
import json
from pathlib import Path

import yaml

from infertwin.report.sweep import write_capacity_sweep_report
from infertwin.replay.timeline import (
    CHUNK_TTFT_GRANULARITY,
    LEGACY_TIMELINE_MODE,
    PROGRESSIVE_TIMELINE_MODE,
)
from infertwin.streaming.cache_factory import (
    CACHE_MODE_HBM_DDR_LRU,
    CACHE_MODE_HBM_DDR_LRU_PROGRESSIVE_TIMELINE,
)
from infertwin.streaming.sweep import STREAMING_CAPACITY_SWEEP_MODE, StreamingCapacitySweepRunner


def test_streaming_progressive_timeline_mode_exports_step9_fields(tmp_path: Path) -> None:
    tokenizer_root = tmp_path / "tokenizers"
    _write_simple_tokenizer_profile(tokenizer_root, profile="runtime-tokenizer")
    trace_path = _write_trace(
        tmp_path,
        rows=[
            (
                "req-a1",
                "instance-a",
                "one two three four five six seven eight nine ten eleven twelve",
                "2026-06-05 09:01:23.000",
            ),
            (
                "req-a2",
                "instance-a",
                "one two three four five six seven eight nine ten eleven twelve",
                "2026-06-05 09:01:23.008",
            ),
        ],
    )
    instance_profile_path = _write_instance_profile(tmp_path)
    registry_path = _write_pooling_model_registry(tmp_path)

    progressive_config = _streaming_config(
        trace_path=trace_path,
        tokenizer_root=tokenizer_root,
        instance_profile_path=instance_profile_path,
        registry_path=registry_path,
        output_dir=tmp_path / "progressive_reports",
        cache_mode=CACHE_MODE_HBM_DDR_LRU_PROGRESSIVE_TIMELINE,
    )
    legacy_config = _streaming_config(
        trace_path=trace_path,
        tokenizer_root=tokenizer_root,
        instance_profile_path=instance_profile_path,
        registry_path=registry_path,
        output_dir=tmp_path / "legacy_reports",
        cache_mode=CACHE_MODE_HBM_DDR_LRU,
    )

    progressive_result = StreamingCapacitySweepRunner(progressive_config).run()
    legacy_result = StreamingCapacitySweepRunner(legacy_config).run()

    progressive_trace = _trace_row(progressive_result.rows)
    legacy_trace = _trace_row(legacy_result.rows)
    assert progressive_result.config_details["streaming_cache_mode"] == (
        CACHE_MODE_HBM_DDR_LRU_PROGRESSIVE_TIMELINE
    )
    assert progressive_result.config_details["streaming_timeline_mode"] == (
        PROGRESSIVE_TIMELINE_MODE
    )
    assert progressive_result.config_details["streaming_ttft_granularity"] == (
        CHUNK_TTFT_GRANULARITY
    )
    assert progressive_result.config_details["progressive_materialization_enabled"] is True
    assert progressive_trace.timeline_mode == PROGRESSIVE_TIMELINE_MODE
    assert progressive_trace.ttft_granularity == CHUNK_TTFT_GRANULARITY
    assert progressive_trace.total_chunk_count > 0
    assert progressive_trace.total_scheduled_chunk_count > 0
    assert progressive_trace.total_progressive_materialized_tokens > 0

    assert legacy_result.config_details["streaming_cache_mode"] == CACHE_MODE_HBM_DDR_LRU
    assert legacy_result.config_details["streaming_timeline_mode"] == LEGACY_TIMELINE_MODE
    assert legacy_result.config_details["progressive_materialization_enabled"] is False
    assert legacy_trace.timeline_mode == LEGACY_TIMELINE_MODE
    assert legacy_trace.ttft_granularity == "iteration"
    assert legacy_trace.total_progressive_materialized_tokens == 0

    paths = write_capacity_sweep_report(progressive_result, tmp_path / "progressive_reports")
    rows = list(csv.DictReader(paths.capacity_sweep_path.open(encoding="utf-8")))
    trace_csv_row = next(row for row in rows if row["scope"] == "trace")
    assert trace_csv_row["timeline_mode"] == PROGRESSIVE_TIMELINE_MODE
    assert trace_csv_row["ttft_granularity"] == CHUNK_TTFT_GRANULARITY
    assert int(trace_csv_row["total_progressive_materialized_tokens"]) > 0
    summary = paths.summary_path.read_text(encoding="utf-8")
    assert "Timeline Results" in summary
    assert "Progressive timeline mode is enabled" in summary


def _trace_row(rows):
    return next(row for row in rows if row.scope == "trace")


def _streaming_config(
    *,
    trace_path: Path,
    tokenizer_root: Path,
    instance_profile_path: Path,
    registry_path: Path,
    output_dir: Path,
    cache_mode: str,
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
            "mode": cache_mode,
            "eviction_policy": "lru",
        },
        "sweep": {
            "hbm_capacity_blocks": [2],
            "parallel_instances": False,
        },
        "scheduler": {
            "policy": "fcfs",
            "max_num_batched_tokens": 8,
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
                "calibrated_from": "integration-test",
            },
        },
        "streaming": {
            "shard_root": str(output_dir / "streaming_shards"),
            "rejected_path": str(output_dir / "rejected_requests.csv"),
            "require_sorted_trace": True,
        },
        "output": {
            "directory": str(output_dir),
            "cache_events": False,
            "cache_event_capacities": [],
        },
        "model_registry": {"profile_path": str(registry_path)},
        "instance_runtime": {"profile_path": str(instance_profile_path)},
        "instance_latency": {
            "profile_path": str(instance_profile_path),
            "require_all_trace_instances": True,
        },
    }


def _write_trace(
    tmp_path: Path,
    *,
    rows: list[tuple[str, str, str, str]],
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
        for request_id, instance_uuid, prompt, service_start_time in rows:
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
                    "service_start_time": service_start_time,
                }
            )
    return path


def _write_instance_profile(tmp_path: Path) -> Path:
    path = tmp_path / "instances.yaml"
    path.write_text(
        """
instances:
  name: step9-streaming
  items:
    instance-a:
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
                            "block_size_tokens": 4,
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
                                "mode": "zero",
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
    max_num_batched_tokens: 8
    enable_chunked_prefill: true
  cache_features:
    prefix_caching: true
    pooling: true
    multi_tier_cache: true
    runtime_block_size: 4
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
