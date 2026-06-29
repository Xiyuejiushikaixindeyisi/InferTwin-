from pathlib import Path

import pytest

from infertwin.config.model_registry import ModelRegistry
from infertwin.config.validation import load_model_registry


def test_model_registry_parses_default_latency_profile() -> None:
    registry = ModelRegistry.from_mapping(_registry_mapping())

    entry = registry.entry_for("glm-v5.1")

    assert entry.name == "glm-v5.1"
    assert entry.model_profile_path == Path("configs/models/glm-v5.1.yaml")
    assert entry.deployment_profile_path == Path(
        "configs/deployments/glm-v5.1-vllm-ascend-prefill.yaml"
    )
    assert entry.tokenizer_profile == "glm-v5"
    assert entry.chat_template_profile == "glm-v5"
    assert entry.default_cache.hbm_capacity_blocks == 4096
    assert entry.default_cache.ddr_capacity_blocks is None
    assert entry.default_cache.block_size_tokens == 128
    assert entry.default_cache.eviction_policy == "lru"
    assert entry.default_cache.pooling.enabled is False
    assert entry.default_latency.name == "glm-v5.1__default_latency"
    assert entry.default_latency.model_name == "glm-v5.1"
    assert entry.default_latency.hardware_name == "ascend-a3-example"
    assert entry.default_latency.fitted_ttft.ms_per_uncached_token == 0.01
    assert entry.default_latency.fitted_ttft.calibration_window_requests == 500
    assert entry.default_latency.kv_load.ddr_ms_per_cached_token == 0.0
    assert registry.entry_by_name == {"glm-v5.1": entry}


def test_model_registry_parses_step7_pooling_defaults() -> None:
    data = _registry_mapping()
    data["models"]["glm-v5.1"]["default_cache"]["ddr_capacity_blocks"] = 65536
    data["models"]["glm-v5.1"]["default_cache"]["pooling"] = {
        "enabled": True,
        "single_instance": True,
        "multi_instance": False,
        "ddr_enabled": True,
        "remote_enabled": False,
        "ssd_enabled": False,
    }

    registry = ModelRegistry.from_mapping(data)
    cache = registry.entry_for("glm-v5.1").default_cache

    assert cache.ddr_capacity_blocks == 65536
    assert cache.pooling.enabled is True
    assert cache.pooling.single_instance is True
    assert cache.pooling.multi_instance is False
    assert cache.pooling.ddr_enabled is True
    assert cache.pooling.remote_enabled is False
    assert cache.pooling.ssd_enabled is False


def test_model_registry_loads_from_yaml_file(tmp_path: Path) -> None:
    registry_path = tmp_path / "registry.yaml"
    registry_path.write_text(
        """
models:
  glm-v5.1:
    model_profile_path: configs/models/glm-v5.1.yaml
    deployment_profile_path: configs/deployments/glm-v5.1-vllm-ascend-prefill.yaml
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
        ms_per_uncached_token: 0.01
        calibrated_from: test
""",
        encoding="utf-8",
    )

    registry = load_model_registry(registry_path)

    assert registry.entry_for("glm-v5.1").tokenizer_profile == "glm-v5"
    assert registry.entry_for("glm-v5.1").default_latency.kv_load.remote_ms_per_cached_token == 0.0


def test_model_registry_rejects_missing_model_profile_path() -> None:
    data = _registry_mapping()
    del data["models"]["glm-v5.1"]["model_profile_path"]

    with pytest.raises(ValueError, match="model_profile_path is required"):
        ModelRegistry.from_mapping(data)


def test_model_registry_rejects_missing_default_latency() -> None:
    data = _registry_mapping()
    del data["models"]["glm-v5.1"]["default_latency"]

    with pytest.raises(ValueError, match="default_latency is required"):
        ModelRegistry.from_mapping(data)


def test_model_registry_rejects_missing_deployment_profile_path() -> None:
    data = _registry_mapping()
    del data["models"]["glm-v5.1"]["deployment_profile_path"]

    with pytest.raises(ValueError, match="deployment_profile_path is required"):
        ModelRegistry.from_mapping(data)


def test_model_registry_rejects_unsupported_default_cache_policy() -> None:
    data = _registry_mapping()
    data["models"]["glm-v5.1"]["default_cache"]["eviction_policy"] = "fifo"

    with pytest.raises(ValueError, match="default_cache.eviction_policy only supports lru"):
        ModelRegistry.from_mapping(data)


def test_model_registry_rejects_non_positive_ddr_capacity() -> None:
    data = _registry_mapping()
    data["models"]["glm-v5.1"]["default_cache"]["ddr_capacity_blocks"] = 0

    with pytest.raises(ValueError, match="ddr_capacity_blocks must be a positive integer"):
        ModelRegistry.from_mapping(data)


def test_model_registry_rejects_non_boolean_pooling_flag() -> None:
    data = _registry_mapping()
    data["models"]["glm-v5.1"]["default_cache"]["pooling"] = {
        "enabled": "true",
    }

    with pytest.raises(ValueError, match="pooling.enabled must be a boolean"):
        ModelRegistry.from_mapping(data)


def test_model_registry_rejects_unsupported_default_latency_backend() -> None:
    data = _registry_mapping()
    data["models"]["glm-v5.1"]["default_latency"]["backend"] = "formula"

    with pytest.raises(ValueError, match="backend only supports fitted_ttft"):
        ModelRegistry.from_mapping(data)


def test_model_registry_entry_for_rejects_unknown_model() -> None:
    registry = ModelRegistry.from_mapping(_registry_mapping())

    with pytest.raises(ValueError, match="model registry missing model"):
        registry.entry_for("missing-model")


def _registry_mapping() -> dict[str, object]:
    return {
        "models": {
            "glm-v5.1": {
                "model_profile_path": "configs/models/glm-v5.1.yaml",
                "deployment_profile_path": (
                    "configs/deployments/glm-v5.1-vllm-ascend-prefill.yaml"
                ),
                "tokenizer_profile": "glm-v5",
                "chat_template_profile": "glm-v5",
                "default_cache": {
                    "hbm_capacity_blocks": 4096,
                    "block_size_tokens": 128,
                    "eviction_policy": "lru",
                },
                "default_latency": {
                    "backend": "fitted_ttft",
                    "model_name": "glm-v5.1",
                    "hardware_name": "ascend-a3-example",
                    "fitted_ttft": {
                        "profile": "glm-v5.1_default_ttft",
                        "function": "token_linear_v1",
                        "intercept_ms": 0.0,
                        "ms_per_uncached_token": 0.01,
                        "calibrated_from": "test",
                        "calibration_window_requests": 500,
                    },
                    "kv_load": {
                        "ddr_ms_per_cached_token": 0.0,
                        "remote_ms_per_cached_token": 0.0,
                    },
                },
            }
        }
    }
