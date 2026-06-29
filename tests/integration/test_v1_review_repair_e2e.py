import csv
import json
from pathlib import Path

from infertwin.report.sweep import write_capacity_sweep_report
from infertwin.streaming.request_codec import decode_simulation_request_line
from infertwin.streaming.sweep import STREAMING_CAPACITY_SWEEP_MODE, StreamingCapacitySweepRunner


def test_v1_review_repair_streaming_cluster_e2e(tmp_path: Path) -> None:
    tokenizer_root = tmp_path / "tokenizers"
    _write_simple_tokenizer_profile(tokenizer_root, profile="tokenizer-a")
    _write_simple_tokenizer_profile(tokenizer_root, profile="tokenizer-b")
    trace_path = _write_trace(tmp_path)
    instance_profile_path = _write_instance_profile(tmp_path)
    registry_path = _write_model_registry(tmp_path)
    output_dir = tmp_path / "reports"
    config = _streaming_config(
        trace_path=trace_path,
        tokenizer_root=tokenizer_root,
        instance_profile_path=instance_profile_path,
        registry_path=registry_path,
        output_dir=output_dir,
        capacities=[1, 2],
    )

    result = StreamingCapacitySweepRunner(config).run()
    report_paths = write_capacity_sweep_report(result, output_dir)

    assert result.config_details["request_build_accepted_count"] == 6
    assert result.config_details["request_build_rejected_count"] == 0
    assert result.config_details["latency_source_by_instance"] == {
        "instance-a": "instance_profile",
        "instance-b": "model_default",
        "instance-c": "model_default",
    }
    assert result.config_details["runtime_model_by_instance"] == {
        "instance-a": "model-a",
        "instance-b": "model-a",
        "instance-c": "model-b",
    }
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
        },
        "instance-b": {
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
        },
        "instance-c": {
            "model_name": "model-b",
            "hbm_capacity_blocks": 77,
            "ddr_capacity_blocks": None,
            "block_size_tokens": 4,
            "eviction_policy": "lru",
            "pooling_enabled": False,
            "single_instance_pooling_enabled": True,
            "multi_instance_pooling_enabled": False,
            "ddr_enabled": False,
            "remote_pooling_enabled": False,
            "ssd_pooling_enabled": False,
        },
    }

    rows_by_capacity_scope = {
        (row.hbm_capacity_blocks, row.scope, row.instance_uuid): row for row in result.rows
    }
    assert len(result.rows) == 8
    assert sorted({row.hbm_capacity_blocks for row in result.rows}) == [1, 2]
    for capacity in (1, 2):
        assert rows_by_capacity_scope[(capacity, "trace", "")].request_count == 6
        assert rows_by_capacity_scope[(capacity, "instance", "instance-a")].request_count == 2
        assert rows_by_capacity_scope[(capacity, "instance", "instance-b")].request_count == 2
        assert rows_by_capacity_scope[(capacity, "instance", "instance-c")].request_count == 2

    instance_a = rows_by_capacity_scope[(2, "instance", "instance-a")]
    instance_b = rows_by_capacity_scope[(2, "instance", "instance-b")]
    instance_c = rows_by_capacity_scope[(2, "instance", "instance-c")]
    assert instance_b.p90_ttft_ms == instance_a.p90_ttft_ms * 3
    assert instance_c.p90_ttft_ms > instance_a.p90_ttft_ms
    assert instance_a.iteration_count > instance_c.iteration_count
    assert instance_b.iteration_count > instance_c.iteration_count

    shard_requests = _read_shard_requests(output_dir / "streaming_shards")
    assert {request.instance_uuid: request.tokenizer_profile for request in shard_requests} == {
        "instance-a": "tokenizer-a",
        "instance-b": "tokenizer-a",
        "instance-c": "tokenizer-b",
    }
    assert {request.instance_uuid: request.effective_block_size for request in shard_requests} == {
        "instance-a": 2,
        "instance-b": 2,
        "instance-c": 4,
    }

    csv_rows = list(csv.DictReader(report_paths.capacity_sweep_path.open(encoding="utf-8")))
    assert len(csv_rows) == 8
    assert {row["scope"] for row in csv_rows} == {"trace", "instance"}
    assert {row["hbm_capacity_blocks"] for row in csv_rows} == {"1", "2"}
    summary = report_paths.summary_path.read_text(encoding="utf-8")
    assert "## Latency Resolution" in summary
    assert "| instance-a | instance_profile |" in summary
    assert "| instance-b | model_default |" in summary
    assert "| instance-c | model_default |" in summary


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
                "ms_per_uncached_token": 99.0,
                "calibrated_from": "should-not-be-used",
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


def _write_trace(tmp_path: Path) -> Path:
    path = tmp_path / "trace.csv"
    rows = [
        ("req-a1", "instance-a", "model-a", "one two three four five six seven eight", 23),
        ("req-b1", "instance-b", "model-a", "one two three four five six seven eight", 24),
        ("req-c1", "instance-c", "model-b", "one two three four five six seven eight", 25),
        ("req-a2", "instance-a", "model-a", "one two three four five six seven eight", 26),
        ("req-b2", "instance-b", "model-a", "one two three four five six seven eight", 27),
        ("req-c2", "instance-c", "model-b", "one two three four five six seven eight", 28),
    ]
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
  name: v1-repair-cluster
  latency_profiles:
    instance-a-ttft:
      backend: fitted_ttft
      model_name: model-a
      hardware_name: hardware-a
      fitted_ttft:
        profile: instance-a-ttft
        function: token_linear_v1
        intercept_ms: 0.0
        ms_per_uncached_token: 1.0
        calibrated_from: v1-repair-e2e
  items:
    instance-a:
      model_name: model-a
      deployment: model-a-deployment
      latency_profile: instance-a-ttft
    instance-b:
      model_name: model-a
      deployment: model-a-deployment
    instance-c:
      model_name: model-b
      deployment: model-b-deployment
""",
        encoding="utf-8",
    )
    return path


def _write_model_registry(tmp_path: Path) -> Path:
    _write_model_profile(tmp_path / "model-a.yaml", model_name="model-a", tokenizer="tokenizer-a")
    _write_model_profile(tmp_path / "model-b.yaml", model_name="model-b", tokenizer="tokenizer-b")
    _write_deployment_profile(
        tmp_path / "model-a-deployment.yaml",
        deployment_name="model-a-deployment",
        block_size=2,
        max_num_batched_tokens=2,
        max_num_seqs=1,
    )
    _write_deployment_profile(
        tmp_path / "model-b-deployment.yaml",
        deployment_name="model-b-deployment",
        block_size=4,
        max_num_batched_tokens=64,
        max_num_seqs=8,
    )
    path = tmp_path / "registry.yaml"
    path.write_text(
        """
models:
  model-a:
    model_profile_path: model-a.yaml
    deployment_profile_path: model-a-deployment.yaml
    tokenizer_profile: tokenizer-a
    default_cache:
      hbm_capacity_blocks: 99
      block_size_tokens: 2
      eviction_policy: lru
    default_latency:
      backend: fitted_ttft
      model_name: model-a
      hardware_name: hardware-a-default
      fitted_ttft:
        profile: model-a-default-ttft
        function: token_linear_v1
        intercept_ms: 0.0
        ms_per_uncached_token: 3.0
        calibrated_from: model-default
  model-b:
    model_profile_path: model-b.yaml
    deployment_profile_path: model-b-deployment.yaml
    tokenizer_profile: tokenizer-b
    default_cache:
      hbm_capacity_blocks: 77
      block_size_tokens: 4
      eviction_policy: lru
    default_latency:
      backend: fitted_ttft
      model_name: model-b
      hardware_name: hardware-b-default
      fitted_ttft:
        profile: model-b-default-ttft
        function: token_linear_v1
        intercept_ms: 0.0
        ms_per_uncached_token: 5.0
        calibrated_from: model-default
""",
        encoding="utf-8",
    )
    return path


def _write_model_profile(path: Path, *, model_name: str, tokenizer: str) -> None:
    path.write_text(
        f"""
model:
  name: {model_name}
  tokenizer_profile: {tokenizer}
  cache_family: full_attention
""",
        encoding="utf-8",
    )


def _write_deployment_profile(
    path: Path,
    *,
    deployment_name: str,
    block_size: int,
    max_num_batched_tokens: int,
    max_num_seqs: int,
) -> None:
    path.write_text(
        f"""
deployment:
  name: {deployment_name}
  engine: vllm-ascend
  scheduler:
    max_num_seqs: {max_num_seqs}
    max_num_batched_tokens: {max_num_batched_tokens}
    enable_chunked_prefill: true
    long_prefill_token_threshold: {max_num_batched_tokens}
  cache_features:
    prefix_caching: true
    runtime_block_size: {block_size}
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


def _read_shard_requests(shard_root: Path):
    return [
        decode_simulation_request_line(line)
        for shard_path in sorted(shard_root.glob("*.jsonl"))
        for line in shard_path.read_text(encoding="utf-8").splitlines()
    ]
