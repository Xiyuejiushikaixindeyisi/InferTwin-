from pathlib import Path

import pytest

from infertwin.config.instance_runtime import (
    build_instance_runtime_config,
    build_instance_runtime_resolver,
)


def test_instance_runtime_resolver_binds_cache_defaults_to_model(
    tmp_path: Path,
) -> None:
    instance_profile_path = _write_instance_profile(tmp_path)
    registry_path = _write_model_registry(tmp_path)

    resolver = build_instance_runtime_resolver(
        _config(
            instance_runtime_path=instance_profile_path,
            model_registry_path=registry_path,
        )
    )

    runtime_a = resolver.runtime_profile_for("instance-a")
    runtime_b = resolver.runtime_profile_for("instance-b")
    runtime_c = resolver.runtime_profile_for("instance-c")

    assert runtime_a is runtime_b
    assert runtime_a.model_name == "glm-v5.1"
    assert runtime_a.default_cache.hbm_capacity_blocks == 4096
    assert runtime_a.default_cache.block_size_tokens == 128
    assert runtime_a.deployment_profile.scheduler.max_num_seqs == 32
    assert runtime_c.model_name == "glm-v5.1-alt"
    assert runtime_c.default_cache.hbm_capacity_blocks == 8192
    assert runtime_c.default_cache.block_size_tokens == 64
    assert resolver.default_cache_for("instance-a") is resolver.default_cache_for("instance-b")
    assert resolver.default_cache_for("instance-a") is not resolver.default_cache_for("instance-c")
    assert resolver.model_name_by_instance == {
        "instance-a": "glm-v5.1",
        "instance-b": "glm-v5.1",
        "instance-c": "glm-v5.1-alt",
    }


def test_instance_runtime_resolver_accepts_shared_instance_latency_profile_path(
    tmp_path: Path,
) -> None:
    instance_profile_path = _write_instance_profile(tmp_path)
    registry_path = _write_model_registry(tmp_path)

    resolver = build_instance_runtime_resolver(
        _config(
            instance_latency_path=instance_profile_path,
            model_registry_path=registry_path,
        )
    )

    assert resolver.instance_profile_path == instance_profile_path
    assert resolver.runtime_profile_for("instance-a").model_name == "glm-v5.1"


def test_instance_runtime_resolver_exposes_step7_pooling_defaults(
    tmp_path: Path,
) -> None:
    instance_profile_path = _write_instance_profile(tmp_path)
    registry_path = _write_model_registry(tmp_path, pooling_model_a=True)

    resolver = build_instance_runtime_resolver(
        _config(
            instance_runtime_path=instance_profile_path,
            model_registry_path=registry_path,
        )
    )

    runtime = resolver.runtime_profile_for("instance-a")

    assert runtime.pooling_enabled is True
    assert runtime.single_instance_pooling_enabled is True
    assert runtime.ddr_capacity_blocks == 65536
    assert runtime.default_cache.pooling.ddr_enabled is True


def test_instance_runtime_config_rejects_mismatched_instance_profile_paths(
    tmp_path: Path,
) -> None:
    instance_profile_path = _write_instance_profile(tmp_path)
    other_instance_profile_path = tmp_path / "other_instances.yaml"
    other_instance_profile_path.write_text(
        instance_profile_path.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    registry_path = _write_model_registry(tmp_path)

    with pytest.raises(ValueError, match="instance_runtime.profile_path and instance_latency"):
        build_instance_runtime_config(
            _config(
                instance_runtime_path=instance_profile_path,
                instance_latency_path=other_instance_profile_path,
                model_registry_path=registry_path,
            )
        )


def test_instance_runtime_config_requires_model_registry_path(tmp_path: Path) -> None:
    instance_profile_path = _write_instance_profile(tmp_path)

    with pytest.raises(ValueError, match="model_registry.profile_path is required"):
        build_instance_runtime_config(_config(instance_runtime_path=instance_profile_path))


def test_instance_runtime_config_requires_instance_profile_path(tmp_path: Path) -> None:
    registry_path = _write_model_registry(tmp_path)

    with pytest.raises(ValueError, match="instance_runtime.profile_path is required"):
        build_instance_runtime_config(_config(model_registry_path=registry_path))


def test_instance_runtime_resolver_rejects_instance_without_model_name(
    tmp_path: Path,
) -> None:
    instance_profile_path = _write_instance_profile(tmp_path, omit_instance_b_model=True)
    registry_path = _write_model_registry(tmp_path)

    with pytest.raises(ValueError, match="model_name is required"):
        build_instance_runtime_resolver(
            _config(
                instance_runtime_path=instance_profile_path,
                model_registry_path=registry_path,
            )
        )


def test_instance_runtime_resolver_rejects_unknown_instance_model(
    tmp_path: Path,
) -> None:
    instance_profile_path = _write_instance_profile(tmp_path, instance_b_model="missing-model")
    registry_path = _write_model_registry(tmp_path)

    with pytest.raises(ValueError, match="model registry missing model"):
        build_instance_runtime_resolver(
            _config(
                instance_runtime_path=instance_profile_path,
                model_registry_path=registry_path,
            )
        )


def test_instance_runtime_resolver_rejects_missing_trace_instance(
    tmp_path: Path,
) -> None:
    instance_profile_path = _write_instance_profile(tmp_path)
    registry_path = _write_model_registry(tmp_path)
    resolver = build_instance_runtime_resolver(
        _config(
            instance_runtime_path=instance_profile_path,
            model_registry_path=registry_path,
        )
    )

    with pytest.raises(ValueError, match="instance runtime profile missing"):
        resolver.runtime_profile_for("instance-z")


def _config(
    *,
    instance_runtime_path: Path | None = None,
    instance_latency_path: Path | None = None,
    model_registry_path: Path | None = None,
) -> dict[str, object]:
    config: dict[str, object] = {}
    if instance_runtime_path is not None:
        config["instance_runtime"] = {"profile_path": str(instance_runtime_path)}
    if instance_latency_path is not None:
        config["instance_latency"] = {"profile_path": str(instance_latency_path)}
    if model_registry_path is not None:
        config["model_registry"] = {"profile_path": str(model_registry_path)}
    return config


def _write_instance_profile(
    tmp_path: Path,
    *,
    instance_b_model: str = "glm-v5.1",
    omit_instance_b_model: bool = False,
) -> Path:
    instance_b_model_line = (
        "" if omit_instance_b_model else f"      model_name: {instance_b_model}\n"
    )
    path = tmp_path / "instances.yaml"
    path.write_text(
        f"""
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
        calibrated_from: unit-test
  items:
    instance-a:
      model_name: glm-v5.1
      deployment: glm-v5.1-vllm-ascend-prefill
      latency_profile: instance-a-ttft
    instance-b:
{instance_b_model_line}      deployment: glm-v5.1-vllm-ascend-prefill
    instance-c:
      model_name: glm-v5.1-alt
      deployment: glm-v5.1-alt-vllm-ascend-prefill
""",
        encoding="utf-8",
    )
    return path


def _write_model_registry(tmp_path: Path, *, pooling_model_a: bool = False) -> Path:
    _write_model_profile(tmp_path / "glm-v5.1.yaml", model_name="glm-v5.1")
    _write_model_profile(tmp_path / "glm-v5.1-alt.yaml", model_name="glm-v5.1-alt")
    _write_deployment_profile(
        tmp_path / "glm-v5.1-deployment.yaml",
        deployment_name="glm-v5.1-vllm-ascend-prefill",
        block_size=128,
        max_num_seqs=32,
        pooling=pooling_model_a,
        multi_tier_cache=pooling_model_a,
    )
    _write_deployment_profile(
        tmp_path / "glm-v5.1-alt-deployment.yaml",
        deployment_name="glm-v5.1-alt-vllm-ascend-prefill",
        block_size=64,
        max_num_seqs=16,
    )
    model_a_pooling_cache = (
        """
      ddr_capacity_blocks: 65536
      pooling:
        enabled: true
        single_instance: true
        multi_instance: false
        ddr_enabled: true
        remote_enabled: false
        ssd_enabled: false"""
        if pooling_model_a
        else ""
    )
    path = tmp_path / "registry.yaml"
    path.write_text(
        f"""
models:
  glm-v5.1:
    model_profile_path: glm-v5.1.yaml
    deployment_profile_path: glm-v5.1-deployment.yaml
    tokenizer_profile: glm-v5
    default_cache:
      hbm_capacity_blocks: 4096
      block_size_tokens: 128
      eviction_policy: lru{model_a_pooling_cache}
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
    pooling: bool = False,
    multi_tier_cache: bool = False,
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
    multi_tier_cache: {str(multi_tier_cache).lower()}
    pooling: {str(pooling).lower()}
    kv_transfer: false
    runtime_block_size: {block_size}
""",
        encoding="utf-8",
    )
