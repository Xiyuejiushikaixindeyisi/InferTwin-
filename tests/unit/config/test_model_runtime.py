from pathlib import Path

import pytest

from infertwin.config.model_binding import validate_model_registry
from infertwin.config.model_registry import ModelRegistry
from infertwin.config.model_runtime import resolve_model_runtime_table


def test_resolve_model_runtime_table_combines_registry_profiles(tmp_path: Path) -> None:
    model_path = _write_model_profile(tmp_path)
    deployment_path = _write_deployment_profile(tmp_path)
    registry = ModelRegistry.from_mapping(
        _registry_mapping(
            model_profile_path=model_path.name,
            deployment_profile_path=deployment_path.name,
        )
    )
    registry_validation = validate_model_registry(registry, base_dir=tmp_path)

    table = resolve_model_runtime_table(
        registry=registry,
        registry_validation=registry_validation,
    )
    profile = table.profile_for("glm-v5.1")

    assert profile.model_name == "glm-v5.1"
    assert profile.tokenizer_profile == "glm-v5"
    assert profile.model_profile.name == "glm-v5.1"
    assert profile.deployment_profile.name == "glm-v5.1-vllm-ascend-prefill"
    assert profile.default_cache.hbm_capacity_blocks == 4096
    assert profile.default_cache.ddr_capacity_blocks is None
    assert profile.default_cache.eviction_policy == "lru"
    assert profile.default_latency.fitted_ttft.ms_per_uncached_token == 0.01
    assert profile.runtime_block_size_tokens == 128
    assert profile.effective_block_size_tokens == 512
    assert profile.speculative_drop_blocks == 1
    assert profile.pooling_enabled is False
    assert profile.ddr_capacity_blocks is None
    assert profile.single_instance_pooling_enabled is False


def test_resolve_model_runtime_table_exposes_step7_pooling_defaults(
    tmp_path: Path,
) -> None:
    model_path = _write_model_profile(tmp_path)
    deployment_path = _write_deployment_profile(
        tmp_path,
        pooling=True,
        multi_tier_cache=True,
    )
    registry = ModelRegistry.from_mapping(
        _registry_mapping(
            model_profile_path=model_path.name,
            deployment_profile_path=deployment_path.name,
            pooling=True,
        )
    )
    registry_validation = validate_model_registry(registry, base_dir=tmp_path)

    table = resolve_model_runtime_table(
        registry=registry,
        registry_validation=registry_validation,
    )
    profile = table.profile_for("glm-v5.1")

    assert profile.pooling_enabled is True
    assert profile.single_instance_pooling_enabled is True
    assert profile.ddr_capacity_blocks == 65536
    assert profile.default_cache.pooling.ddr_enabled is True


def test_model_runtime_table_rejects_unknown_model(tmp_path: Path) -> None:
    model_path = _write_model_profile(tmp_path)
    deployment_path = _write_deployment_profile(tmp_path)
    registry = ModelRegistry.from_mapping(
        _registry_mapping(
            model_profile_path=model_path.name,
            deployment_profile_path=deployment_path.name,
        )
    )
    registry_validation = validate_model_registry(registry, base_dir=tmp_path)
    table = resolve_model_runtime_table(
        registry=registry,
        registry_validation=registry_validation,
    )

    with pytest.raises(ValueError, match="model runtime table missing model"):
        table.profile_for("qwen")


def _write_model_profile(tmp_path: Path) -> Path:
    path = tmp_path / "model.yaml"
    path.write_text(
        """
model:
  name: glm-v5.1
  aliases:
    - glm-v5
  tokenizer_profile: glm-v5
  chat_template_profile: glm-v5
  cache_family: full_attention
""",
        encoding="utf-8",
    )
    return path


def _write_deployment_profile(
    tmp_path: Path,
    *,
    pooling: bool = False,
    multi_tier_cache: bool = False,
) -> Path:
    path = tmp_path / "deployment.yaml"
    path.write_text(
        f"""
deployment:
  name: glm-v5.1-vllm-ascend-prefill
  engine: vllm-ascend
  scheduler:
    max_num_seqs: 32
    max_num_batched_tokens: 8192
    enable_chunked_prefill: true
  parallel:
    prefill_context_parallel_size: 2
    decode_context_parallel_size: 2
  speculative:
    enabled: true
    method: mtp
  cache_features:
    prefix_caching: true
    multi_tier_cache: {str(multi_tier_cache).lower()}
    pooling: {str(pooling).lower()}
    kv_transfer: false
    runtime_block_size: 128
""",
        encoding="utf-8",
    )
    return path


def _registry_mapping(
    *,
    model_profile_path: str,
    deployment_profile_path: str,
    pooling: bool = False,
) -> dict[str, object]:
    default_cache: dict[str, object] = {
        "hbm_capacity_blocks": 4096,
        "block_size_tokens": 128,
        "eviction_policy": "lru",
    }
    if pooling:
        default_cache["ddr_capacity_blocks"] = 65536
        default_cache["pooling"] = {
            "enabled": True,
            "single_instance": True,
            "multi_instance": False,
            "ddr_enabled": True,
            "remote_enabled": False,
            "ssd_enabled": False,
        }
    return {
        "models": {
            "glm-v5.1": {
                "model_profile_path": model_profile_path,
                "deployment_profile_path": deployment_profile_path,
                "tokenizer_profile": "glm-v5",
                "chat_template_profile": "glm-v5",
                "default_cache": default_cache,
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
