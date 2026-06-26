from pathlib import Path

import pytest

from infertwin.config.model_binding import (
    validate_instance_model_bindings,
    validate_model_registry,
)
from infertwin.config.model_registry import ModelRegistry
from infertwin.config.profiles import InstanceProfile


def test_validate_model_registry_loads_profiles_and_checks_consistency(
    tmp_path: Path,
) -> None:
    model_path = _write_model_profile(tmp_path, tokenizer_profile="glm-v5")
    registry = ModelRegistry.from_mapping(_registry_mapping(model_profile_path=model_path.name))

    result = validate_model_registry(registry, base_dir=tmp_path)

    assert result.model_profile_by_name["glm-v5.1"].tokenizer_profile == "glm-v5"


def test_validate_model_registry_rejects_profile_name_mismatch(tmp_path: Path) -> None:
    model_path = _write_model_profile(tmp_path, model_name="qwen")
    registry = ModelRegistry.from_mapping(_registry_mapping(model_profile_path=model_path.name))

    with pytest.raises(ValueError, match="references model profile"):
        validate_model_registry(registry, base_dir=tmp_path)


def test_validate_model_registry_rejects_tokenizer_mismatch(tmp_path: Path) -> None:
    model_path = _write_model_profile(tmp_path, tokenizer_profile="other-tokenizer")
    registry = ModelRegistry.from_mapping(_registry_mapping(model_profile_path=model_path.name))

    with pytest.raises(ValueError, match="tokenizer_profile"):
        validate_model_registry(registry, base_dir=tmp_path)


def test_validate_model_registry_rejects_default_latency_model_mismatch(
    tmp_path: Path,
) -> None:
    model_path = _write_model_profile(tmp_path)
    data = _registry_mapping(model_profile_path=model_path.name)
    data["models"]["glm-v5.1"]["default_latency"]["model_name"] = "qwen"
    registry = ModelRegistry.from_mapping(data)

    with pytest.raises(ValueError, match="default latency model_name"):
        validate_model_registry(registry, base_dir=tmp_path)


def test_validate_instance_model_bindings_accepts_registered_models(
    tmp_path: Path,
) -> None:
    model_path = _write_model_profile(tmp_path, aliases=["glm-v5"])
    registry = ModelRegistry.from_mapping(_registry_mapping(model_profile_path=model_path.name))
    registry_validation = validate_model_registry(registry, base_dir=tmp_path)
    instances = InstanceProfile.from_mapping(_instance_profile_mapping())

    result = validate_instance_model_bindings(
        instance_profile=instances,
        model_registry=registry,
        registry_validation=registry_validation,
    )

    assert result.instance_count == 2
    assert result.model_name_by_instance == {
        "instance-a": "glm-v5.1",
        "instance-b": "glm-v5.1",
    }


def test_validate_instance_model_bindings_requires_model_name() -> None:
    registry = ModelRegistry.from_mapping(_registry_mapping())
    data = _instance_profile_mapping()
    del data["instances"]["items"]["instance-a"]["model_name"]
    instances = InstanceProfile.from_mapping(data)

    with pytest.raises(ValueError, match="model_name is required"):
        validate_instance_model_bindings(
            instance_profile=instances,
            model_registry=registry,
        )


def test_validate_instance_model_bindings_rejects_unknown_model() -> None:
    registry = ModelRegistry.from_mapping(_registry_mapping())
    data = _instance_profile_mapping()
    data["instances"]["items"]["instance-a"]["model_name"] = "qwen"
    instances = InstanceProfile.from_mapping(data)

    with pytest.raises(ValueError, match="model registry missing model"):
        validate_instance_model_bindings(
            instance_profile=instances,
            model_registry=registry,
        )


def test_validate_instance_model_bindings_rejects_latency_model_mismatch(
    tmp_path: Path,
) -> None:
    model_path = _write_model_profile(tmp_path, aliases=["glm-v5"])
    registry = ModelRegistry.from_mapping(_registry_mapping(model_profile_path=model_path.name))
    registry_validation = validate_model_registry(registry, base_dir=tmp_path)
    data = _instance_profile_mapping()
    data["instances"]["latency_profiles"]["instance-a-ttft"]["model_name"] = "qwen"
    instances = InstanceProfile.from_mapping(data)

    with pytest.raises(ValueError, match="instance latency profile model mismatch"):
        validate_instance_model_bindings(
            instance_profile=instances,
            model_registry=registry,
            registry_validation=registry_validation,
        )


def _write_model_profile(
    tmp_path: Path,
    *,
    model_name: str = "glm-v5.1",
    aliases: list[str] | None = None,
    tokenizer_profile: str = "glm-v5",
) -> Path:
    alias_lines = "\n".join(f"    - {alias}" for alias in aliases or [])
    aliases_section = f"\n  aliases:\n{alias_lines}" if aliases else ""
    path = tmp_path / "model.yaml"
    path.write_text(
        f"""
model:
  name: {model_name}{aliases_section}
  tokenizer_profile: {tokenizer_profile}
  chat_template_profile: glm-v5
""",
        encoding="utf-8",
    )
    return path


def _registry_mapping(
    model_profile_path: str = "configs/models/glm-v5.1.yaml",
) -> dict[str, object]:
    return {
        "models": {
            "glm-v5.1": {
                "model_profile_path": model_profile_path,
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
                    },
                },
            }
        }
    }


def _instance_profile_mapping() -> dict[str, object]:
    return {
        "instances": {
            "name": "local-fixed-route-latency-example",
            "latency_profiles": {
                "instance-a-ttft": {
                    "backend": "fitted_ttft",
                    "model_name": "glm-v5",
                    "hardware_name": "ascend-a3-fast",
                    "fitted_ttft": {
                        "profile": "instance-a-ttft",
                        "function": "token_linear_v1",
                        "intercept_ms": 0.0,
                        "ms_per_uncached_token": 0.010,
                        "calibrated_from": "synthetic",
                    },
                },
            },
            "items": {
                "instance-a": {
                    "model_name": "glm-v5.1",
                    "deployment": "glm-v5.1-vllm-ascend-prefill",
                    "latency_profile": "instance-a-ttft",
                },
                "instance-b": {
                    "model_name": "glm-v5.1",
                    "deployment": "glm-v5.1-vllm-ascend-prefill",
                },
            },
        }
    }
