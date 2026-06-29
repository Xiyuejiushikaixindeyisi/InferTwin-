"""Model runtime defaults resolved from registry profiles."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from infertwin.config.profiles import (
    DeploymentProfile,
    InstanceLatencyProfile,
    ModelProfile,
)

if TYPE_CHECKING:
    from infertwin.config.model_binding import ModelRegistryValidationResult
    from infertwin.config.model_registry import ModelRegistry


@dataclass(frozen=True, slots=True)
class ModelCachePoolingDefaults:
    """Model-owned pooling flags for Step7 cache runtime defaults."""

    enabled: bool = False
    single_instance: bool = True
    multi_instance: bool = False
    ddr_enabled: bool = False
    remote_enabled: bool = False
    ssd_enabled: bool = False

    @classmethod
    def from_mapping(
        cls,
        data: object | None,
        *,
        field_name: str,
    ) -> "ModelCachePoolingDefaults":
        mapping = _optional_mapping(data, field_name)
        return cls(
            enabled=_bool(mapping.get("enabled", False), field_name=f"{field_name}.enabled"),
            single_instance=_bool(
                mapping.get("single_instance", True),
                field_name=f"{field_name}.single_instance",
            ),
            multi_instance=_bool(
                mapping.get("multi_instance", False),
                field_name=f"{field_name}.multi_instance",
            ),
            ddr_enabled=_bool(
                mapping.get("ddr_enabled", False),
                field_name=f"{field_name}.ddr_enabled",
            ),
            remote_enabled=_bool(
                mapping.get("remote_enabled", False),
                field_name=f"{field_name}.remote_enabled",
            ),
            ssd_enabled=_bool(
                mapping.get("ssd_enabled", False),
                field_name=f"{field_name}.ssd_enabled",
            ),
        )


@dataclass(frozen=True, slots=True)
class ModelCacheDefaults:
    """Default cache configuration owned by one registered model."""

    hbm_capacity_blocks: int
    block_size_tokens: int
    eviction_policy: Literal["lru"] = "lru"
    ddr_capacity_blocks: int | None = None
    pooling: ModelCachePoolingDefaults = field(default_factory=ModelCachePoolingDefaults)

    @classmethod
    def from_mapping(cls, data: object, *, field_name: str) -> "ModelCacheDefaults":
        mapping = _mapping(data, field_name)
        eviction_policy = _required_str(mapping, "eviction_policy", field_name=field_name)
        if eviction_policy != "lru":
            raise ValueError(f"{field_name}.eviction_policy only supports lru in InferTwin V1")
        return cls(
            hbm_capacity_blocks=_positive_int(
                mapping.get("hbm_capacity_blocks"),
                field_name=f"{field_name}.hbm_capacity_blocks",
            ),
            block_size_tokens=_positive_int(
                mapping.get("block_size_tokens"),
                field_name=f"{field_name}.block_size_tokens",
            ),
            eviction_policy=eviction_policy,
            ddr_capacity_blocks=_optional_positive_int(
                mapping.get("ddr_capacity_blocks"),
                field_name=f"{field_name}.ddr_capacity_blocks",
            ),
            pooling=ModelCachePoolingDefaults.from_mapping(
                mapping.get("pooling"),
                field_name=f"{field_name}.pooling",
            ),
        )


@dataclass(frozen=True, slots=True)
class ModelRuntimeDefaults:
    """Registry-owned runtime defaults for one model."""

    deployment_profile_path: Path
    cache: ModelCacheDefaults


@dataclass(frozen=True, slots=True)
class ResolvedModelRuntimeProfile:
    """Resolved runtime profile assembled from model, deployment, cache, and latency data."""

    model_name: str
    tokenizer_profile: str
    chat_template_profile: str | None
    model_profile_path: Path
    deployment_profile_path: Path
    model_profile: ModelProfile
    deployment_profile: DeploymentProfile
    default_cache: ModelCacheDefaults
    default_latency: InstanceLatencyProfile

    @property
    def runtime_block_size_tokens(self) -> int:
        return self.deployment_profile.cache_features.runtime_block_size or (
            self.default_cache.block_size_tokens
        )

    @property
    def effective_block_size_tokens(self) -> int:
        return (
            self.runtime_block_size_tokens
            * self.deployment_profile.parallel.context_parallel_factor
        )

    @property
    def speculative_drop_blocks(self) -> int:
        return self.deployment_profile.speculative.speculative_drop_blocks

    @property
    def pooling_enabled(self) -> bool:
        return self.default_cache.pooling.enabled

    @property
    def ddr_capacity_blocks(self) -> int | None:
        return self.default_cache.ddr_capacity_blocks

    @property
    def single_instance_pooling_enabled(self) -> bool:
        return self.default_cache.pooling.enabled and self.default_cache.pooling.single_instance


@dataclass(frozen=True, slots=True)
class ModelRuntimeTable:
    """Lookup table for resolved model runtime profiles."""

    profiles: tuple[ResolvedModelRuntimeProfile, ...]

    @property
    def profile_by_model_name(self) -> Mapping[str, ResolvedModelRuntimeProfile]:
        return {profile.model_name: profile for profile in self.profiles}

    def profile_for(self, model_name: str) -> ResolvedModelRuntimeProfile:
        profile = self.profile_by_model_name.get(model_name)
        if profile is None:
            raise ValueError(f"model runtime table missing model {model_name!r}")
        return profile


def resolve_model_runtime_table(
    *,
    registry: "ModelRegistry",
    registry_validation: "ModelRegistryValidationResult",
) -> ModelRuntimeTable:
    """Build a deterministic runtime profile table from a validated model registry."""

    model_profiles = registry_validation.model_profile_by_name
    deployment_profiles = registry_validation.deployment_profile_by_name
    profiles = tuple(
        ResolvedModelRuntimeProfile(
            model_name=entry.name,
            tokenizer_profile=entry.tokenizer_profile,
            chat_template_profile=entry.chat_template_profile,
            model_profile_path=entry.model_profile_path,
            deployment_profile_path=entry.runtime_defaults.deployment_profile_path,
            model_profile=model_profiles[entry.name],
            deployment_profile=deployment_profiles[entry.name],
            default_cache=entry.runtime_defaults.cache,
            default_latency=entry.default_latency,
        )
        for entry in registry.entries
    )
    return ModelRuntimeTable(profiles=profiles)


def _mapping(data: object, field_name: str) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError(f"{field_name} must be a mapping")
    return data


def _optional_mapping(data: object | None, field_name: str) -> dict[str, Any]:
    if data is None:
        return {}
    return _mapping(data, field_name)


def _required_str(mapping: dict[str, Any], key: str, *, field_name: str) -> str:
    if key not in mapping:
        raise ValueError(f"{field_name}.{key} is required")
    value = mapping[key]
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name}.{key} must be a non-empty string")
    return value


def _bool(value: object, *, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field_name} must be a boolean")
    return value


def _positive_int(value: object, *, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{field_name} must be a positive integer")
    return value


def _optional_positive_int(value: object, *, field_name: str) -> int | None:
    if value is None:
        return None
    return _positive_int(value, field_name=field_name)
