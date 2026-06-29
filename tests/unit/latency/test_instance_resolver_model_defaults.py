from pathlib import Path

import pytest

from infertwin.latency.fitted_ttft import FittedTTFTLatencyBackend
from infertwin.latency.instance_resolver import build_instance_latency_backend_resolver
from infertwin.latency.profile import ServingLatencyProfile
from infertwin.scheduler.batch_shape import BatchShape, ScheduledSlice


def test_instance_resolver_uses_model_default_when_instance_profile_is_missing(
    tmp_path: Path,
) -> None:
    instance_profile_path = _write_mixed_instance_profile(tmp_path)
    registry_path = _write_registry(tmp_path, slope=0.07)

    resolver = build_instance_latency_backend_resolver(
        _config(
            instance_latency={"profile_path": str(instance_profile_path)},
            model_registry={"profile_path": str(registry_path)},
        )
    )

    backend_a = resolver.backend_for("instance-a")
    backend_b = resolver.backend_for("instance-b")

    assert resolver.uses_instance_profiles is True
    assert resolver.uses_model_registry is True
    assert resolver.model_registry_path == registry_path
    assert isinstance(backend_a, ServingLatencyProfile)
    assert isinstance(backend_b, ServingLatencyProfile)
    assert backend_a.profile == "instance-a-ttft"
    assert isinstance(backend_a.ttft_backend, FittedTTFTLatencyBackend)
    assert backend_a.ttft_backend.ms_per_uncached_token == 0.01
    assert backend_b.profile == "glm-v5.1__default_latency"
    assert backend_b.model_name == "glm-v5.1"
    assert backend_b.hardware_name == "ascend-a3-example"
    assert isinstance(backend_b.ttft_backend, FittedTTFTLatencyBackend)
    assert backend_b.ttft_backend.profile == "glm-v5.1_default_ttft"
    assert backend_b.ttft_backend.ms_per_uncached_token == 0.07
    assert resolver.metadata_for("instance-a").source == "instance_profile"
    assert resolver.metadata_for("instance-b").source == "model_default"
    assert resolver.metadata_for("instance-b").calibration_status == "model_default"
    assert resolver.latency_source_by_instance == {
        "instance-a": "instance_profile",
        "instance-b": "model_default",
    }

    result = backend_b.estimate_iteration(_shape(kv_load_tokens=10))

    assert result.duration_ms == pytest.approx(4.56)
    assert result.details["ttft_ms"] == pytest.approx(0.56)
    assert result.details["kv_load_ms"] == pytest.approx(4.0)
    assert result.details["kv_load_mode"] == "token_linear_v1"
    assert result.details["kv_load_calibrated_from"] == "model-default-kv-load"


def test_instance_resolver_resolves_model_profile_relative_to_registry_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry_dir = tmp_path / "registry"
    registry_dir.mkdir()
    instance_profile_path = _write_mixed_instance_profile(tmp_path)
    registry_path = _write_registry(registry_dir, model_profile_path="models/glm-v5.1.yaml")
    (registry_dir / "unrelated-cwd").mkdir()
    monkeypatch.chdir(registry_dir / "unrelated-cwd")

    resolver = build_instance_latency_backend_resolver(
        _config(
            instance_latency={"profile_path": str(instance_profile_path)},
            model_registry={"profile_path": str(registry_path)},
        )
    )

    backend = resolver.backend_for("instance-b")

    assert isinstance(backend, ServingLatencyProfile)
    assert backend.profile == "glm-v5.1__default_latency"
    assert isinstance(backend.ttft_backend, FittedTTFTLatencyBackend)
    assert backend.ttft_backend.profile == "glm-v5.1_default_ttft"
    assert resolver.metadata_for("instance-b").source == "model_default"


def test_instance_resolver_keeps_failing_without_model_registry(
    tmp_path: Path,
) -> None:
    instance_profile_path = _write_mixed_instance_profile(tmp_path)
    resolver = build_instance_latency_backend_resolver(
        _config(instance_latency={"profile_path": str(instance_profile_path)})
    )

    with pytest.raises(ValueError, match="instance latency profile missing"):
        resolver.backend_for("instance-b")


def test_instance_resolver_validates_model_registry_even_without_instance_profile(
    tmp_path: Path,
) -> None:
    registry_path = _write_registry(tmp_path, model_name="qwen")

    with pytest.raises(ValueError, match="references model profile"):
        build_instance_latency_backend_resolver(
            _config(model_registry={"profile_path": str(registry_path)})
        )


def test_instance_resolver_rejects_missing_instance_model_name_when_registry_enabled(
    tmp_path: Path,
) -> None:
    instance_profile_path = _write_mixed_instance_profile(tmp_path, omit_instance_b_model=True)
    registry_path = _write_registry(tmp_path)

    with pytest.raises(ValueError, match="model_name is required"):
        build_instance_latency_backend_resolver(
            _config(
                instance_latency={"profile_path": str(instance_profile_path)},
                model_registry={"profile_path": str(registry_path)},
            )
        )


def test_instance_resolver_rejects_unknown_instance_model_when_registry_enabled(
    tmp_path: Path,
) -> None:
    instance_profile_path = _write_mixed_instance_profile(tmp_path, instance_b_model="qwen")
    registry_path = _write_registry(tmp_path)

    with pytest.raises(ValueError, match="model registry missing model"):
        build_instance_latency_backend_resolver(
            _config(
                instance_latency={"profile_path": str(instance_profile_path)},
                model_registry={"profile_path": str(registry_path)},
            )
        )


def _config(
    *,
    instance_latency: dict[str, object] | None = None,
    model_registry: dict[str, object] | None = None,
) -> dict[str, object]:
    config: dict[str, object] = {
        "latency": {
            "backend": "fitted_ttft",
            "model_name": "glm-v5",
            "hardware_name": "global-hardware",
            "fitted_ttft": {
                "profile": "global-ttft",
                "function": "token_linear_v1",
                "intercept_ms": 1.0,
                "ms_per_uncached_token": 0.5,
                "calibrated_from": "unit-test",
            },
        }
    }
    if instance_latency is not None:
        config["instance_latency"] = instance_latency
    if model_registry is not None:
        config["model_registry"] = model_registry
    return config


def _write_mixed_instance_profile(
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
  name: local-fixed-route-latency-example
  latency_profiles:
    instance-a-ttft:
      backend: fitted_ttft
      model_name: glm-v5.1
      hardware_name: ascend-a3-fast
      fitted_ttft:
        profile: instance-a-ttft
        function: token_linear_v1
        intercept_ms: 0.0
        ms_per_uncached_token: 0.010
        calibrated_from: synthetic
  items:
    instance-a:
      model_name: glm-v5.1
      deployment: glm-v5.1-vllm-ascend-prefill
      latency_profile: instance-a-ttft
    instance-b:
{instance_b_model_line}      deployment: glm-v5.1-vllm-ascend-prefill
""",
        encoding="utf-8",
    )
    return path


def _write_registry(
    tmp_path: Path,
    *,
    slope: float = 0.07,
    model_name: str = "glm-v5.1",
    model_profile_path: str | None = None,
) -> Path:
    resolved_model_profile_path = (
        tmp_path / model_profile_path if model_profile_path is not None else tmp_path / "model.yaml"
    )
    resolved_model_profile_path.parent.mkdir(parents=True, exist_ok=True)
    resolved_model_profile_path.write_text(
        f"""
model:
  name: {model_name}
  aliases:
    - glm-v5
  tokenizer_profile: glm-v5
""",
        encoding="utf-8",
    )
    deployment_profile_path = tmp_path / "deployment.yaml"
    deployment_profile_path.write_text(
        """
deployment:
  name: glm-v5.1-vllm-ascend-prefill
  engine: vllm-ascend
  scheduler:
    max_num_seqs: 32
    max_num_batched_tokens: 8192
    enable_chunked_prefill: true
  cache_features:
    prefix_caching: true
    runtime_block_size: 128
""",
        encoding="utf-8",
    )
    registry_path = tmp_path / "registry.yaml"
    registry_model_profile_path = model_profile_path or str(resolved_model_profile_path)
    registry_path.write_text(
        f"""
models:
  glm-v5.1:
    model_profile_path: {registry_model_profile_path}
    deployment_profile_path: {deployment_profile_path}
    tokenizer_profile: glm-v5
    default_cache:
      hbm_capacity_blocks: 4096
      block_size_tokens: 128
      eviction_policy: lru
    default_latency:
      backend: fitted_ttft
      model_name: glm-v5.1
      hardware_name: ascend-a3-example
      fitted_ttft:
        profile: glm-v5.1_default_ttft
        function: token_linear_v1
        intercept_ms: 0.0
        ms_per_uncached_token: {slope}
        calibrated_from: default_registry
      kv_load:
        mode: token_linear_v1
        ddr_fixed_overhead_ms: 2.0
        ddr_ms_per_cached_token: 0.2
        calibrated_from: model-default-kv-load
""",
        encoding="utf-8",
    )
    return registry_path


def _shape(
    *,
    kv_load_tokens: int = 0,
    kv_load_bytes: int = 0,
) -> BatchShape:
    return BatchShape(
        instance_uuid="instance-b",
        iteration_id=0,
        start_time_ms=0.0,
        request_slices=(
            ScheduledSlice(
                request_id="r0",
                scheduled_prefill_tokens=8,
                computed_tokens_before=0,
                computed_tokens_after=8,
                prompt_tokens=8,
                cached_prefix_tokens=0,
                previous_chunk_tokens=0,
                kv_load_tokens=kv_load_tokens,
                kv_load_bytes=kv_load_bytes,
            ),
        ),
    )
