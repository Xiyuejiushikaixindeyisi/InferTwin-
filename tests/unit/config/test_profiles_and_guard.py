from pathlib import Path

import pytest

from infertwin.config.guard import guard_core_profiles, guard_request_model
from infertwin.config.profiles import (
    DeploymentProfile,
    HardwareProfile,
    InstanceProfile,
    ModelProfile,
)
from infertwin.config.run_spec import RunSpec
from infertwin.config.validation import (
    load_deployment_profile,
    load_hardware_profile,
    load_instance_profile,
    load_model_profile,
    load_run_spec,
)


def test_run_spec_and_profiles_parse_typed_schema(tmp_path: Path) -> None:
    run_spec = RunSpec.from_mapping(
        {
            "run": {
                "trace_path": "data/samples/sample_trace.csv",
                "output_dir": "reports/example",
                "mode": "capacity_sweep",
                "model_name": "glm-v5.1",
                "requested_block_size": 128,
                "capacity_candidates": [16, 32],
                "model_profile": "configs/models/glm-v5.1.yaml",
            }
        }
    )
    model = ModelProfile.from_mapping(
        {
            "model": {
                "name": "glm-v5.1",
                "aliases": ["glm-v5", "glm-v5-chat"],
                "tokenizer_profile": "glm-v5",
                "chat_template_profile": "glm-v5",
                "max_model_len": 131072,
                "cache_family": "full_attention",
            }
        }
    )
    hardware = HardwareProfile.from_mapping(
        {
            "hardware": {
                "name": "ascend-a3-example",
                "accelerator_type": "Ascend910",
                "accelerator_count": 8,
                "hbm_gib": 64,
                "kv_dtype_bytes": 2,
                "communication": {"HCCL_BUFFSIZE": "1024"},
            }
        }
    )
    deployment = DeploymentProfile.from_mapping(_deployment_mapping())
    instances = InstanceProfile.from_mapping(
        {
            "instances": {
                "name": "local-fixed-route",
                "items": {
                    "instance-a": {"deployment": "glm-v5.1-vllm-ascend-prefill"},
                    "instance-b": {"deployment": "glm-v5.1-vllm-ascend-prefill"},
                },
            }
        }
    )

    assert run_spec.trace_path == Path("data/samples/sample_trace.csv")
    assert run_spec.capacity_candidates == (16, 32)
    assert model.accepted_model_names == frozenset({"glm-v5.1", "glm-v5", "glm-v5-chat"})
    assert hardware.hbm_gib == 64.0
    assert deployment.parallel.context_parallel_factor == 2
    assert deployment.speculative.enabled is False
    assert instances.deployment_by_instance == {
        "instance-a": "glm-v5.1-vllm-ascend-prefill",
        "instance-b": "glm-v5.1-vllm-ascend-prefill",
    }


def test_profiles_load_from_yaml_files(tmp_path: Path) -> None:
    run_path = tmp_path / "run.yaml"
    model_path = tmp_path / "model.yaml"
    hardware_path = tmp_path / "hardware.yaml"
    deployment_path = tmp_path / "deployment.yaml"
    instances_path = tmp_path / "instances.yaml"
    run_path.write_text(
        """
run:
  trace_path: data/samples/sample_trace.csv
  output_dir: reports/example
  mode: capacity_sweep
  model_name: glm-v5.1
  requested_block_size: 128
""",
        encoding="utf-8",
    )
    model_path.write_text(
        """
model:
  name: glm-v5.1
  tokenizer_profile: glm-v5
""",
        encoding="utf-8",
    )
    hardware_path.write_text(
        """
hardware:
  name: local-dev
""",
        encoding="utf-8",
    )
    deployment_path.write_text(
        """
deployment:
  name: local-deployment
  engine: vllm
  scheduler:
    max_num_seqs: 8
    max_num_batched_tokens: 128
    enable_chunked_prefill: true
""",
        encoding="utf-8",
    )
    instances_path.write_text(
        """
instances:
  name: local-cluster
  items:
    instance-a:
      deployment: local-deployment
""",
        encoding="utf-8",
    )

    assert load_run_spec(run_path).model_name == "glm-v5.1"
    assert load_model_profile(model_path).tokenizer_profile == "glm-v5"
    assert load_hardware_profile(hardware_path).name == "local-dev"
    assert load_deployment_profile(deployment_path).scheduler.max_num_seqs == 8
    assert load_instance_profile(instances_path).deployment_by_instance == {
        "instance-a": "local-deployment"
    }


def test_profile_schema_rejects_missing_required_fields() -> None:
    with pytest.raises(ValueError, match="model.tokenizer_profile is required"):
        ModelProfile.from_mapping({"model": {"name": "glm-v5.1"}})

    with pytest.raises(ValueError, match="run.requested_block_size"):
        RunSpec.from_mapping(
            {
                "run": {
                    "trace_path": "trace.csv",
                    "output_dir": "reports",
                    "mode": "capacity_sweep",
                    "model_name": "glm-v5.1",
                    "requested_block_size": 0,
                }
            }
        )


def test_config_guard_blocks_unsupported_semantics_before_replay() -> None:
    run_spec = _run_spec(model_name="glm-v5.1")
    model = ModelProfile.from_mapping(
        {
            "model": {
                "name": "glm-v5.1",
                "tokenizer_profile": "glm-v5",
                "cache_family": "hybrid",
            }
        }
    )
    deployment = DeploymentProfile.from_mapping(
        _deployment_mapping(
            speculative={"enabled": True, "method": "mtp"},
            parallel={"prefill_context_parallel_size": 2},
        )
    )

    result = guard_core_profiles(
        run_spec=run_spec,
        model_profile=model,
        deployment_profile=deployment,
        block_conversion_enabled=False,
    )

    assert result.blocked is True
    assert {issue.code for issue in result.issues} == {
        "SPECULATIVE_DROP_REQUIRES_BLOCK_CONVERSION",
        "UNSUPPORTED_CONTEXT_PARALLEL_CACHE_FAMILY",
        "HYBRID_CACHE_GROUPS_REQUIRED",
    }
    with pytest.raises(ValueError, match="ConfigGuard blocked replay"):
        result.raise_if_blocked()


def test_config_guard_allows_alias_request_model() -> None:
    run_spec = _run_spec(model_name="glm-v5.1")
    model = ModelProfile.from_mapping(
        {
            "model": {
                "name": "glm-v5.1",
                "aliases": ["glm-v5"],
                "tokenizer_profile": "glm-v5",
            }
        }
    )

    assert (
        guard_request_model(
            request_model="glm-v5",
            run_spec=run_spec,
            model_profile=model,
        ).blocked
        is False
    )
    assert (
        guard_request_model(
            request_model="qwen",
            run_spec=run_spec,
            model_profile=model,
        ).blocked
        is True
    )


def _run_spec(*, model_name: str) -> RunSpec:
    return RunSpec.from_mapping(
        {
            "run": {
                "trace_path": "trace.csv",
                "output_dir": "reports",
                "mode": "capacity_sweep",
                "model_name": model_name,
                "requested_block_size": 128,
            }
        }
    )


def _deployment_mapping(
    *,
    speculative: dict[str, object] | None = None,
    parallel: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "deployment": {
            "name": "glm-v5.1-vllm-ascend-prefill",
            "engine": "vllm-ascend",
            "scheduler": {
                "max_num_seqs": 32,
                "max_num_batched_tokens": 8192,
                "enable_chunked_prefill": True,
                "long_prefill_token_threshold": 4096,
            },
            "parallel": parallel or {"prefill_context_parallel_size": 2},
            "speculative": speculative or {"enabled": False},
            "cache_features": {"prefix_caching": True, "runtime_block_size": 128},
            "startup_args": {"gpu_memory_utilization": 0.9},
        }
    }
