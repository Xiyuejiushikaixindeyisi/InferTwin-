import csv
import json
from pathlib import Path

import pytest

from infertwin.streaming.request_codec import decode_simulation_request_line
from infertwin.streaming.sweep import STREAMING_CAPACITY_SWEEP_MODE, StreamingCapacitySweepRunner


def test_streaming_runner_uses_instance_runtime_for_request_build_scheduler_and_cache(
    tmp_path: Path,
) -> None:
    tokenizer_root = tmp_path / "tokenizers"
    _write_simple_tokenizer_profile(tokenizer_root, profile="runtime-tokenizer")
    trace_path = _write_trace(
        tmp_path,
        rows=[
            ("req-a", "instance-a", "model-a", "one two three four five six", 23),
            ("req-b", "instance-a", "model-a", "one two three four five six", 24),
        ],
    )
    instance_profile_path = _write_instance_profile(tmp_path)
    registry_path = _write_model_registry(
        tmp_path,
        scheduler_max_num_batched_tokens=2,
        scheduler_max_num_seqs=1,
    )
    config = _streaming_config(
        trace_path=trace_path,
        tokenizer_root=tokenizer_root,
        instance_profile_path=instance_profile_path,
        registry_path=registry_path,
        output_dir=tmp_path / "reports",
        capacities=[1],
    )

    result = StreamingCapacitySweepRunner(config).run()

    trace_row = next(row for row in result.rows if row.scope == "trace")
    assert trace_row.hbm_capacity_blocks == 1
    assert trace_row.request_count == 2
    assert trace_row.iteration_count > trace_row.request_count
    assert result.config_details["instance_runtime_enabled"] is True
    assert result.config_details["runtime_model_by_instance"] == {"instance-a": "model-a"}
    assert result.config_details["model_default_cache_by_instance"] == {
        "instance-a": {
            "model_name": "model-a",
            "hbm_capacity_blocks": 99,
            "ddr_capacity_blocks": None,
            "block_size_tokens": 2,
            "eviction_policy": "lru",
            "pooling_enabled": False,
            "single_instance_pooling_enabled": True,
            "multi_instance_pooling_enabled": False,
            "ddr_enabled": False,
            "remote_pooling_enabled": False,
            "ssd_pooling_enabled": False,
        }
    }

    shard_path = next((tmp_path / "reports" / "streaming_shards").glob("*.jsonl"))
    decoded = [
        decode_simulation_request_line(line)
        for line in shard_path.read_text(encoding="utf-8").splitlines()
    ]
    assert {request.tokenizer_profile for request in decoded} == {"runtime-tokenizer"}
    assert {request.requested_block_size for request in decoded} == {2}
    assert {request.runtime_block_size for request in decoded} == {2}
    assert {request.effective_block_size for request in decoded} == {2}


def test_streaming_runner_rejects_request_model_not_bound_to_instance_model(
    tmp_path: Path,
) -> None:
    tokenizer_root = tmp_path / "tokenizers"
    _write_simple_tokenizer_profile(tokenizer_root, profile="runtime-tokenizer")
    trace_path = _write_trace(
        tmp_path,
        rows=[
            ("req-a", "instance-a", "model-b", "one two three", 23),
        ],
    )
    instance_profile_path = _write_instance_profile(tmp_path)
    registry_path = _write_model_registry(
        tmp_path,
        scheduler_max_num_batched_tokens=2,
        scheduler_max_num_seqs=1,
    )
    config = _streaming_config(
        trace_path=trace_path,
        tokenizer_root=tokenizer_root,
        instance_profile_path=instance_profile_path,
        registry_path=registry_path,
        output_dir=tmp_path / "reports",
        capacities=[1],
    )

    with pytest.raises(ValueError, match="REQUEST_MODEL_MISMATCH"):
        StreamingCapacitySweepRunner(config).run()


def _streaming_config(
    *,
    trace_path: Path,
    tokenizer_root: Path,
    instance_profile_path: Path,
    registry_path: Path,
    output_dir: Path,
    capacities: list[int],
) -> dict[str, object]:
    return {
        "simulation": {"mode": STREAMING_CAPACITY_SWEEP_MODE},
        "trace": {"path": str(trace_path)},
        "tokenizers": {
            "root": str(tokenizer_root),
            "cache_scope": "tenant_isolated",
        },
        "cache": {
            "block_size_tokens": 64,
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
        },
        "latency": {
            "backend": "fitted_ttft",
            "model_name": "global",
            "hardware_name": "global",
            "fitted_ttft": {
                "profile": "global-ttft",
                "function": "token_linear_v1",
                "intercept_ms": 0.0,
                "ms_per_uncached_token": 0.1,
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
            "cache_events": False,
            "cache_event_capacities": [],
        },
    }


def _write_trace(tmp_path: Path, *, rows: list[tuple[str, str, str, str, int]]) -> Path:
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
        for request_id, instance_uuid, model_name, prompt, second in rows:
            writer.writerow(
                {
                    "request_id": request_id,
                    "tenant_id": "tenant-a",
                    "instance_uuid": instance_uuid,
                    "request_params": json.dumps(
                        {
                            "model": model_name,
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
  name: runtime-integration
  items:
    instance-a:
      model_name: model-a
      deployment: model-a-deployment
""",
        encoding="utf-8",
    )
    return path


def _write_model_registry(
    tmp_path: Path,
    *,
    scheduler_max_num_batched_tokens: int,
    scheduler_max_num_seqs: int,
) -> Path:
    _write_model_profile(tmp_path / "model-a.yaml")
    _write_deployment_profile(
        tmp_path / "model-a-deployment.yaml",
        scheduler_max_num_batched_tokens=scheduler_max_num_batched_tokens,
        scheduler_max_num_seqs=scheduler_max_num_seqs,
    )
    path = tmp_path / "registry.yaml"
    path.write_text(
        """
models:
  model-a:
    model_profile_path: model-a.yaml
    deployment_profile_path: model-a-deployment.yaml
    tokenizer_profile: runtime-tokenizer
    default_cache:
      hbm_capacity_blocks: 99
      block_size_tokens: 2
      eviction_policy: lru
    default_latency:
      backend: fitted_ttft
      model_name: model-a
      hardware_name: runtime-hardware
      fitted_ttft:
        profile: model-a-default-ttft
        function: token_linear_v1
        intercept_ms: 0.0
        ms_per_uncached_token: 0.1
        calibrated_from: model-default
""",
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


def _write_deployment_profile(
    path: Path,
    *,
    scheduler_max_num_batched_tokens: int,
    scheduler_max_num_seqs: int,
) -> None:
    path.write_text(
        f"""
deployment:
  name: model-a-deployment
  engine: vllm-ascend
  scheduler:
    max_num_seqs: {scheduler_max_num_seqs}
    max_num_batched_tokens: {scheduler_max_num_batched_tokens}
    enable_chunked_prefill: true
    long_prefill_token_threshold: {scheduler_max_num_batched_tokens}
  cache_features:
    prefix_caching: true
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
