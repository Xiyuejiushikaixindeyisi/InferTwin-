from pathlib import Path

import pytest

from infertwin.latency.fitted_ttft import FittedTTFTLatencyBackend
from infertwin.latency.instance_resolver import (
    build_instance_latency_backend_resolver,
    build_instance_latency_config,
    build_model_registry_config,
)


def test_instance_latency_resolver_falls_back_to_global_backend() -> None:
    resolver = build_instance_latency_backend_resolver(_config())

    backend = resolver.backend_for("instance-a")

    assert resolver.uses_instance_profiles is False
    assert resolver.profile_path is None
    assert resolver.uses_model_registry is False
    assert resolver.model_registry_path is None
    assert resolver.profile_name_by_instance == {}
    assert resolver.instance_profile_count == 0
    assert resolver.metadata_for("instance-a").source == "global"
    assert isinstance(backend, FittedTTFTLatencyBackend)
    assert backend.profile == "global-ttft"
    assert backend.ms_per_uncached_token == 0.5


def test_instance_latency_resolver_returns_backend_by_instance_uuid(tmp_path: Path) -> None:
    profile_path = _write_instance_profile(tmp_path)
    resolver = build_instance_latency_backend_resolver(
        _config(instance_latency={"profile_path": str(profile_path)})
    )

    backend_a = resolver.backend_for("instance-a")
    backend_b = resolver.backend_for("instance-b")

    assert resolver.uses_instance_profiles is True
    assert resolver.uses_model_registry is False
    assert resolver.profile_path == profile_path
    assert resolver.profile_name_by_instance == {
        "instance-a": "instance-a-ttft",
        "instance-b": "instance-b-ttft",
    }
    assert resolver.instance_profile_count == 2
    assert isinstance(backend_a, FittedTTFTLatencyBackend)
    assert isinstance(backend_b, FittedTTFTLatencyBackend)
    assert backend_a.profile == "instance-a-ttft"
    assert backend_a.model_name == "glm-v5.1"
    assert backend_a.hardware_name == "ascend-a3-fast"
    assert backend_a.ms_per_uncached_token == 0.01
    assert backend_b.profile == "instance-b-ttft"
    assert backend_b.hardware_name == "ascend-a3-slow"
    assert backend_b.ms_per_uncached_token == 0.02
    assert resolver.metadata_for("instance-a").source == "instance_profile"
    assert resolver.metadata_for("instance-a").calibration_status == "configured"
    assert resolver.backend_for("instance-a") is backend_a


def test_instance_latency_resolver_fails_on_missing_instance(tmp_path: Path) -> None:
    profile_path = _write_instance_profile(tmp_path)
    resolver = build_instance_latency_backend_resolver(
        _config(instance_latency={"profile_path": str(profile_path)})
    )

    with pytest.raises(ValueError, match="instance latency profile missing"):
        resolver.backend_for("instance-c")


def test_instance_latency_config_requires_profile_path() -> None:
    with pytest.raises(ValueError, match="profile_path is required"):
        build_instance_latency_config(_config(instance_latency={}))


def test_instance_latency_config_rejects_disabled_require_all() -> None:
    with pytest.raises(ValueError, match="require_all_trace_instances=false"):
        build_instance_latency_config(
            _config(
                instance_latency={
                    "profile_path": "configs/instances/local-fixed-route-latency-example.yaml",
                    "require_all_trace_instances": False,
                }
            )
        )


def test_model_registry_config_requires_profile_path() -> None:
    with pytest.raises(ValueError, match="model_registry.profile_path is required"):
        build_model_registry_config({**_config(), "model_registry": {}})


def _config(
    *,
    instance_latency: dict[str, object] | None = None,
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
    return config


def _write_instance_profile(tmp_path: Path) -> Path:
    path = tmp_path / "instances.yaml"
    path.write_text(
        """
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
        calibration_window_requests: 500
    instance-b-ttft:
      backend: fitted_ttft
      model_name: glm-v5.1
      hardware_name: ascend-a3-slow
      fitted_ttft:
        profile: instance-b-ttft
        function: token_linear_v1
        intercept_ms: 0.0
        ms_per_uncached_token: 0.020
        calibrated_from: synthetic
        calibration_window_requests: 500
  items:
    instance-a:
      deployment: glm-v5.1-vllm-ascend-prefill
      latency_profile: instance-a-ttft
    instance-b:
      deployment: glm-v5.1-vllm-ascend-prefill
      latency_profile: instance-b-ttft
""",
        encoding="utf-8",
    )
    return path
