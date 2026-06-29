import csv
import json
from pathlib import Path

import yaml

from infertwin.cache.events import CACHE_TIER_DDR, CACHE_TIER_HBM, LOOKUP_HIT, MATERIALIZE, STORE
from infertwin.cli.main import main
from infertwin.replay.timeline import CHUNK_TTFT_GRANULARITY, PROGRESSIVE_TIMELINE_MODE
from infertwin.streaming.cache_factory import CACHE_MODE_HBM_DDR_LRU_PROGRESSIVE_TIMELINE
from infertwin.streaming.sweep import STREAMING_CAPACITY_SWEEP_MODE


def test_step9_progressive_streaming_cli_e2e(tmp_path: Path) -> None:
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
                "2026-06-05 09:01:23.050",
            ),
            (
                "req-b1",
                "instance-b",
                "one two three four five six seven eight nine ten eleven twelve",
                "2026-06-05 09:01:23.100",
            ),
        ],
    )
    output_dir = tmp_path / "reports"
    config_path = _write_config(
        tmp_path,
        trace_path=trace_path,
        tokenizer_root=tokenizer_root,
        registry_path=_write_pooling_model_registry(tmp_path),
        instance_profile_path=_write_instance_profile(tmp_path),
        output_dir=output_dir,
    )

    assert main(["sweep-streaming", "--config", str(config_path)]) == 0

    capacity_rows = list(csv.DictReader((output_dir / "capacity_sweep.csv").open()))
    rows = {
        (int(row["hbm_capacity_blocks"]), row["scope"], row["instance_uuid"]): row
        for row in capacity_rows
    }
    trace_capacity_1 = rows[(1, "trace", "")]
    instance_a_capacity_1 = rows[(1, "instance", "instance-a")]
    instance_b_capacity_1 = rows[(1, "instance", "instance-b")]

    assert trace_capacity_1["timeline_mode"] == PROGRESSIVE_TIMELINE_MODE
    assert trace_capacity_1["ttft_granularity"] == CHUNK_TTFT_GRANULARITY
    assert int(trace_capacity_1["request_count"]) == 3
    assert int(trace_capacity_1["total_chunk_count"]) > 0
    assert int(trace_capacity_1["total_scheduled_chunk_count"]) > 0
    assert int(trace_capacity_1["total_progressive_materialized_tokens"]) > 0
    assert float(trace_capacity_1["total_kv_load_ms"]) > 0.0
    assert float(trace_capacity_1["total_kv_load_wait_ms"]) > 0.0
    assert int(trace_capacity_1["total_load_event_count"]) > 0

    assert int(instance_a_capacity_1["ddr_hit_tokens"]) > 0
    assert float(instance_a_capacity_1["total_kv_load_ms"]) > 0.0
    assert float(instance_a_capacity_1["total_kv_load_wait_ms"]) > 0.0
    assert int(instance_a_capacity_1["total_progressive_materialized_tokens"]) > 0
    assert int(instance_b_capacity_1["hbm_hit_tokens"]) == 0
    assert int(instance_b_capacity_1["ddr_hit_tokens"]) == 0
    assert int(instance_b_capacity_1["miss_tokens"]) == int(
        instance_b_capacity_1["total_prompt_tokens"]
    )

    event_rows = list(csv.DictReader((output_dir / "capacity_1" / "cache_events.csv").open()))
    assert any(
        row["cache_tier"] == CACHE_TIER_HBM
        and row["event_type"] == MATERIALIZE
        and row["reason"] == "progressive_chunk_materialization"
        for row in event_rows
    )
    assert any(
        row["cache_tier"] == CACHE_TIER_DDR
        and row["event_type"] == STORE
        and row["reason"] == "progressive_chunk_store"
        for row in event_rows
    )
    assert any(
        row["cache_tier"] == CACHE_TIER_DDR
        and row["event_type"] == LOOKUP_HIT
        and row["instance_uuid"] == "instance-a"
        for row in event_rows
    )
    assert not any(
        row["cache_tier"] == CACHE_TIER_DDR
        and row["event_type"] == LOOKUP_HIT
        and row["instance_uuid"] == "instance-b"
        for row in event_rows
    )

    summary = (output_dir / "summary.md").read_text(encoding="utf-8")
    assert "Timeline Results" in summary
    assert "Progressive timeline mode is enabled" in summary
    assert "DDR KV load wait and compute wait are modeled as typed replay metrics" in summary
    assert "capacity_1/cache_events.csv" in summary


def _write_config(
    tmp_path: Path,
    *,
    trace_path: Path,
    tokenizer_root: Path,
    registry_path: Path,
    instance_profile_path: Path,
    output_dir: Path,
) -> Path:
    config = {
        "simulation": {"mode": STREAMING_CAPACITY_SWEEP_MODE},
        "trace": {"path": str(trace_path)},
        "tokenizers": {
            "root": str(tokenizer_root),
            "default_profile": "runtime-tokenizer",
            "cache_scope": "tenant_isolated",
        },
        "cache": {
            "mode": CACHE_MODE_HBM_DDR_LRU_PROGRESSIVE_TIMELINE,
            "eviction_policy": "lru",
        },
        "sweep": {
            "hbm_capacity_blocks": [1, 4],
            "parallel_instances": False,
        },
        "scheduler": {
            "policy": "fcfs",
            "max_num_batched_tokens": 4,
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
            "cache_events": True,
            "cache_event_capacities": [1],
        },
        "model_registry": {"profile_path": str(registry_path)},
        "instance_runtime": {"profile_path": str(instance_profile_path)},
        "instance_latency": {
            "profile_path": str(instance_profile_path),
            "require_all_trace_instances": True,
        },
    }
    path = tmp_path / "step9_streaming_cli_e2e.yaml"
    path.write_text(yaml.safe_dump(config, sort_keys=True), encoding="utf-8")
    return path


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
  name: step9-cli-e2e
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
                            "ddr_capacity_blocks": 64,
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
                                "mode": "token_linear_v1",
                                "ddr_fixed_overhead_ms": 1.0,
                                "ddr_ms_per_cached_token": 0.5,
                                "remote_ms_per_cached_token": 0.0,
                                "aggregation": "shared_link_sum",
                                "overlap_mode": "none_v1",
                                "transfer_path": "local_ddr_cpu",
                                "calibrated_from": "step9-e2e",
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
    max_num_batched_tokens: 4
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
    (profile_dir / "kv_meta.json").write_text(
        json.dumps({"kv_bytes_per_token": 16}),
        encoding="utf-8",
    )
