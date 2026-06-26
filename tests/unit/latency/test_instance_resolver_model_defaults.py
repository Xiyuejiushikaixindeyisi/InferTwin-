from pathlib import Path

import pytest

from infertwin.latency.fitted_ttft import FittedTTFTLatencyBackend
from infertwin.latency.instance_resolver import build_instance_latency_backend_resolver


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
    assert isinstance(backend_a, FittedTTFTLatencyBackend)
    assert isinstance(backend_b, FittedTTFTLatencyBackend)
    assert backend_a.profile == "instance-a-ttft"
    assert backend_a.ms_per_uncached_token == 0.01
    assert backend_b.profile == "glm-v5.1_default_ttft"
    assert backend_b.model_name == "glm-v5.1"
    assert backend_b.hardware_name == "ascend-a3-example"
    assert backend_b.ms_per_uncached_token == 0.07
    assert resolver.metadata_for("instance-a").source == "instance_profile"
    assert resolver.metadata_for("instance-b").source == "model_default"
    assert resolver.metadata_for("instance-b").calibration_status == "model_default"
    assert resolver.latency_source_by_instance == {
        "instance-a": "instance_profile",
        "instance-b": "model_default",
    }


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
) -> Path:
    model_profile_path = tmp_path / "model.yaml"
    model_profile_path.write_text(
        f"""
model:
  name: {model_name}
  aliases:
    - glm-v5
  tokenizer_profile: glm-v5
""",
        encoding="utf-8",
    )
    registry_path = tmp_path / "registry.yaml"
    registry_path.write_text(
        f"""
models:
  glm-v5.1:
    model_profile_path: {model_profile_path}
    tokenizer_profile: glm-v5
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
""",
        encoding="utf-8",
    )
    return registry_path
