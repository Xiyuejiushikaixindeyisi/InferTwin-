"""Consistency checks for model registry and fixed-routed instance bindings."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from infertwin.config.loader import load_yaml
from infertwin.config.model_registry import ModelRegistry, ModelRegistryEntry
from infertwin.config.profiles import DeploymentProfile, InstanceProfile, ModelProfile


@dataclass(frozen=True, slots=True)
class ModelRegistryValidationResult:
    """Validated model registry metadata."""

    model_profiles: tuple[ModelProfile, ...]
    deployment_profiles: tuple[DeploymentProfile, ...]

    @property
    def model_profile_by_name(self) -> Mapping[str, ModelProfile]:
        return {profile.name: profile for profile in self.model_profiles}

    @property
    def deployment_profile_by_name(self) -> Mapping[str, DeploymentProfile]:
        return {
            model_profile.name: deployment_profile
            for model_profile, deployment_profile in zip(
                self.model_profiles,
                self.deployment_profiles,
                strict=True,
            )
        }


@dataclass(frozen=True, slots=True)
class ModelBindingValidationResult:
    """Validated instance-to-model binding metadata."""

    instance_count: int
    model_name_by_instance: Mapping[str, str]


def validate_model_registry(
    registry: ModelRegistry,
    *,
    base_dir: str | Path | None = None,
) -> ModelRegistryValidationResult:
    """Validate registry entries against their referenced ModelProfile files."""

    model_profiles: list[ModelProfile] = []
    deployment_profiles: list[DeploymentProfile] = []
    root = Path(base_dir) if base_dir is not None else None
    for entry in registry.entries:
        model_profile = ModelProfile.from_mapping(
            load_yaml(_resolve_profile_path(entry.model_profile_path, root))
        )
        deployment_profile = DeploymentProfile.from_mapping(
            load_yaml(_resolve_profile_path(entry.deployment_profile_path, root))
        )
        if model_profile.name != entry.name:
            raise ValueError(
                "model registry entry "
                f"{entry.name!r} references model profile {model_profile.name!r}"
            )
        if model_profile.tokenizer_profile != entry.tokenizer_profile:
            raise ValueError(
                "model registry entry "
                f"{entry.name!r} tokenizer_profile {entry.tokenizer_profile!r} "
                f"does not match ModelProfile tokenizer_profile "
                f"{model_profile.tokenizer_profile!r}"
            )
        if entry.default_latency.model_name not in model_profile.accepted_model_names:
            raise ValueError(
                "model registry entry "
                f"{entry.name!r} default latency model_name "
                f"{entry.default_latency.model_name!r} is not accepted by ModelProfile"
            )
        _validate_step7_cache_features(entry, deployment_profile)
        model_profiles.append(model_profile)
        deployment_profiles.append(deployment_profile)
    return ModelRegistryValidationResult(
        model_profiles=tuple(model_profiles),
        deployment_profiles=tuple(deployment_profiles),
    )


def validate_instance_model_bindings(
    *,
    instance_profile: InstanceProfile,
    model_registry: ModelRegistry,
    registry_validation: ModelRegistryValidationResult | None = None,
) -> ModelBindingValidationResult:
    """Validate that fixed-routed instances bind to registered models."""

    model_profiles = (
        registry_validation.model_profile_by_name if registry_validation is not None else {}
    )
    model_name_by_instance: dict[str, str] = {}
    latency_profiles = instance_profile.latency_profile_by_name
    for instance in instance_profile.instances:
        if instance.model_name is None:
            raise ValueError(
                f"instances.items.{instance.instance_uuid}.model_name is required "
                "when model registry is enabled"
            )
        entry = model_registry.entry_for(instance.model_name)
        accepted_names = _accepted_model_names(entry.name, model_profiles)
        if instance.latency_profile is not None:
            latency_profile = latency_profiles[instance.latency_profile]
            if latency_profile.model_name not in accepted_names:
                raise ValueError(
                    "instance latency profile model mismatch for "
                    f"{instance.instance_uuid!r}: instance model "
                    f"{instance.model_name!r}, latency profile "
                    f"{latency_profile.name!r} model {latency_profile.model_name!r}"
                )
        model_name_by_instance[instance.instance_uuid] = instance.model_name
    return ModelBindingValidationResult(
        instance_count=len(instance_profile.instances),
        model_name_by_instance=model_name_by_instance,
    )


def _resolve_profile_path(path: Path, base_dir: Path | None) -> Path:
    if path.is_absolute() or base_dir is None:
        return path
    return base_dir / path


def _validate_step7_cache_features(
    entry: ModelRegistryEntry,
    deployment_profile: DeploymentProfile,
) -> None:
    cache = entry.default_cache
    pooling = cache.pooling
    cache_features = deployment_profile.cache_features
    field_prefix = f"models.{entry.name}.default_cache.pooling"

    if cache.eviction_policy != "lru":
        raise ValueError(
            f"models.{entry.name}.default_cache.eviction_policy only supports lru in InferTwin V1"
        )
    if cache_features.kv_transfer:
        raise ValueError(
            "model registry entry "
            f"{entry.name!r} references deployment profile {deployment_profile.name!r} "
            "with kv_transfer enabled; external KV transfer is not supported in Step7"
        )

    if not pooling.enabled:
        if cache_features.pooling:
            raise ValueError(
                "model registry entry "
                f"{entry.name!r} enables deployment pooling but "
                "default_cache.pooling.enabled is false"
            )
        if cache_features.multi_tier_cache:
            raise ValueError(
                "model registry entry "
                f"{entry.name!r} enables deployment multi_tier_cache but "
                "default_cache.pooling.enabled is false"
            )
        return

    if cache.ddr_capacity_blocks is None:
        raise ValueError(
            f"models.{entry.name}.default_cache.ddr_capacity_blocks is required "
            "when default_cache.pooling.enabled is true"
        )
    if not pooling.single_instance:
        raise ValueError(f"{field_prefix}.single_instance must be true in Step7")
    if pooling.multi_instance:
        raise ValueError(f"{field_prefix}.multi_instance is not supported in Step7")
    if not pooling.ddr_enabled:
        raise ValueError(f"{field_prefix}.ddr_enabled must be true in Step7")
    if pooling.remote_enabled:
        raise ValueError(f"{field_prefix}.remote_enabled is not supported in Step7")
    if pooling.ssd_enabled:
        raise ValueError(f"{field_prefix}.ssd_enabled is not supported in Step7")
    if not cache_features.pooling:
        raise ValueError(
            "model registry entry "
            f"{entry.name!r} enables default_cache.pooling but deployment profile "
            f"{deployment_profile.name!r} has cache_features.pooling=false"
        )


def _accepted_model_names(
    model_name: str,
    model_profiles: Mapping[str, ModelProfile],
) -> frozenset[str]:
    model_profile = model_profiles.get(model_name)
    if model_profile is None:
        return frozenset({model_name})
    return model_profile.accepted_model_names
