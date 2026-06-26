from pathlib import Path

import pytest

from infertwin.config.model_registry import ModelRegistry
from infertwin.config.validation import load_model_registry


def test_model_registry_parses_default_latency_profile() -> None:
    registry = ModelRegistry.from_mapping(_registry_mapping())

    entry = registry.entry_for("glm-v5.1")

    assert entry.name == "glm-v5.1"
    assert entry.model_profile_path == Path("configs/models/glm-v5.1.yaml")
    assert entry.tokenizer_profile == "glm-v5"
    assert entry.chat_template_profile == "glm-v5"
    assert entry.default_latency.name == "glm-v5.1__default_latency"
    assert entry.default_latency.model_name == "glm-v5.1"
    assert entry.default_latency.hardware_name == "ascend-a3-example"
    assert entry.default_latency.fitted_ttft.ms_per_uncached_token == 0.01
    assert entry.default_latency.fitted_ttft.calibration_window_requests == 500
    assert entry.default_latency.kv_load.ddr_ms_per_cached_token == 0.0
    assert registry.entry_by_name == {"glm-v5.1": entry}


def test_model_registry_loads_from_yaml_file(tmp_path: Path) -> None:
    registry_path = tmp_path / "registry.yaml"
    registry_path.write_text(
        """
models:
  glm-v5.1:
    model_profile_path: configs/models/glm-v5.1.yaml
    tokenizer_profile: glm-v5
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
                "tokenizer_profile": "glm-v5",
                "chat_template_profile": "glm-v5",
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
