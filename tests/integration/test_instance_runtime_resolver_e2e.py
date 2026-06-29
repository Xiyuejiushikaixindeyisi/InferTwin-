import csv
import json
from pathlib import Path

from infertwin.config.instance_runtime import build_instance_runtime_resolver
from infertwin.latency.fitted_ttft import FittedTTFTLatencyBackend
from infertwin.latency.instance_resolver import build_instance_latency_backend_resolver
from infertwin.latency.profile import ServingLatencyProfile
from infertwin.trace.reader import read_trace_csv


def test_instance_runtime_and_latency_resolvers_bind_synthetic_trace_instances(
    tmp_path: Path,
) -> None:
    trace_path = _write_trace(tmp_path)
    instance_profile_path = _write_instance_profile(tmp_path)
    registry_path = _write_model_registry(tmp_path)
    config = _config(
        trace_path=trace_path,
        instance_profile_path=instance_profile_path,
        registry_path=registry_path,
    )

    runtime_resolver = build_instance_runtime_resolver(config)
    latency_resolver = build_instance_latency_backend_resolver(config)

    resolved = {}
    for record in read_trace_csv(trace_path):
        runtime_profile = runtime_resolver.runtime_profile_for(record.instance_uuid)
        latency_backend = latency_resolver.backend_for(record.instance_uuid)
        assert isinstance(latency_backend, ServingLatencyProfile)
        assert isinstance(latency_backend.ttft_backend, FittedTTFTLatencyBackend)
        resolved[record.instance_uuid] = {
            "model_name": runtime_profile.model_name,
            "hbm_capacity_blocks": runtime_profile.default_cache.hbm_capacity_blocks,
            "block_size_tokens": runtime_profile.default_cache.block_size_tokens,
            "scheduler_max_num_seqs": runtime_profile.deployment_profile.scheduler.max_num_seqs,
            "latency_profile": latency_backend.profile,
            "ms_per_uncached_token": latency_backend.ttft_backend.ms_per_uncached_token,
        }

    assert resolved == {
        "instance-a": {
            "model_name": "glm-v5.1",
            "hbm_capacity_blocks": 4096,
            "block_size_tokens": 128,
            "scheduler_max_num_seqs": 32,
            "latency_profile": "instance-a-ttft",
            "ms_per_uncached_token": 1.0,
        },
        "instance-b": {
            "model_name": "glm-v5.1",
            "hbm_capacity_blocks": 4096,
            "block_size_tokens": 128,
            "scheduler_max_num_seqs": 32,
            "latency_profile": "glm-v5.1__default_latency",
            "ms_per_uncached_token": 3.0,
        },
        "instance-c": {
            "model_name": "glm-v5.1-alt",
            "hbm_capacity_blocks": 8192,
            "block_size_tokens": 64,
            "scheduler_max_num_seqs": 16,
            "latency_profile": "glm-v5.1-alt__default_latency",
            "ms_per_uncached_token": 5.0,
        },
    }


def _config(
    *,
    trace_path: Path,
    instance_profile_path: Path,
    registry_path: Path,
) -> dict[str, object]:
    return {
        "trace": {"path": str(trace_path)},
        "model_registry": {"profile_path": str(registry_path)},
        "instance_runtime": {"profile_path": str(instance_profile_path)},
        "instance_latency": {
            "profile_path": str(instance_profile_path),
            "require_all_trace_instances": True,
        },
        "latency": {
            "backend": "fitted_ttft",
            "model_name": "global",
            "hardware_name": "global-hardware",
            "fitted_ttft": {
                "profile": "global-fallback",
                "function": "token_linear_v1",
                "intercept_ms": 0.0,
                "ms_per_uncached_token": 99.0,
                "calibrated_from": "should-not-be-used",
            },
        },
    }


def _write_trace(tmp_path: Path) -> Path:
    path = tmp_path / "trace.csv"
    rows = [
        ("req-a", "tenant-a", "instance-a", "glm-v5.1", "2026-06-05 09:01:23"),
        ("req-b", "tenant-a", "instance-b", "glm-v5.1", "2026-06-05 09:01:24"),
        ("req-c", "tenant-a", "instance-c", "glm-v5.1-alt", "2026-06-05 09:01:25"),
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
        for request_id, tenant_id, instance_uuid, model_name, timestamp in rows:
            writer.writerow(
                {
                    "request_id": request_id,
                    "tenant_id": tenant_id,
                    "instance_uuid": instance_uuid,
                    "request_params": json.dumps(
                        {
                            "model": model_name,
                            "messages": [{"role": "user", "content": "same prompt"}],
                            "tools": [],
                        }
                    ),
                    "service_start_time": timestamp,
                }
            )
    return path


def _write_instance_profile(tmp_path: Path) -> Path:
    path = tmp_path / "instances.yaml"
    path.write_text(
        """
instances:
  name: synthetic-cluster
  latency_profiles:
    instance-a-ttft:
      backend: fitted_ttft
      model_name: glm-v5.1
      hardware_name: hardware-a
      fitted_ttft:
        profile: instance-a-ttft
        function: token_linear_v1
        intercept_ms: 0.0
        ms_per_uncached_token: 1.0
        calibrated_from: e2e
  items:
    instance-a:
      model_name: glm-v5.1
      deployment: glm-v5.1-vllm-ascend-prefill
      latency_profile: instance-a-ttft
    instance-b:
      model_name: glm-v5.1
      deployment: glm-v5.1-vllm-ascend-prefill
    instance-c:
      model_name: glm-v5.1-alt
      deployment: glm-v5.1-alt-vllm-ascend-prefill
""",
        encoding="utf-8",
    )
    return path


def _write_model_registry(tmp_path: Path) -> Path:
    _write_model_profile(tmp_path / "glm-v5.1.yaml", model_name="glm-v5.1")
    _write_model_profile(tmp_path / "glm-v5.1-alt.yaml", model_name="glm-v5.1-alt")
    _write_deployment_profile(
        tmp_path / "glm-v5.1-deployment.yaml",
        deployment_name="glm-v5.1-vllm-ascend-prefill",
        block_size=128,
        max_num_seqs=32,
    )
    _write_deployment_profile(
        tmp_path / "glm-v5.1-alt-deployment.yaml",
        deployment_name="glm-v5.1-alt-vllm-ascend-prefill",
        block_size=64,
        max_num_seqs=16,
    )
    path = tmp_path / "registry.yaml"
    path.write_text(
        """
models:
  glm-v5.1:
    model_profile_path: glm-v5.1.yaml
    deployment_profile_path: glm-v5.1-deployment.yaml
    tokenizer_profile: glm-v5
    default_cache:
      hbm_capacity_blocks: 4096
      block_size_tokens: 128
      eviction_policy: lru
    default_latency:
      backend: fitted_ttft
      model_name: glm-v5.1
      hardware_name: hardware-default-a
      fitted_ttft:
        profile: glm-v5.1-default-ttft
        function: token_linear_v1
        intercept_ms: 0.0
        ms_per_uncached_token: 3.0
        calibrated_from: model-default
  glm-v5.1-alt:
    model_profile_path: glm-v5.1-alt.yaml
    deployment_profile_path: glm-v5.1-alt-deployment.yaml
    tokenizer_profile: glm-v5
    default_cache:
      hbm_capacity_blocks: 8192
      block_size_tokens: 64
      eviction_policy: lru
    default_latency:
      backend: fitted_ttft
      model_name: glm-v5.1-alt
      hardware_name: hardware-default-b
      fitted_ttft:
        profile: glm-v5.1-alt-default-ttft
        function: token_linear_v1
        intercept_ms: 0.0
        ms_per_uncached_token: 5.0
        calibrated_from: model-default
""",
        encoding="utf-8",
    )
    return path


def _write_model_profile(path: Path, *, model_name: str) -> None:
    path.write_text(
        f"""
model:
  name: {model_name}
  tokenizer_profile: glm-v5
  cache_family: full_attention
""",
        encoding="utf-8",
    )


def _write_deployment_profile(
    path: Path,
    *,
    deployment_name: str,
    block_size: int,
    max_num_seqs: int,
) -> None:
    path.write_text(
        f"""
deployment:
  name: {deployment_name}
  engine: vllm-ascend
  scheduler:
    max_num_seqs: {max_num_seqs}
    max_num_batched_tokens: 8192
    enable_chunked_prefill: true
  cache_features:
    prefix_caching: true
    runtime_block_size: {block_size}
""",
        encoding="utf-8",
    )
