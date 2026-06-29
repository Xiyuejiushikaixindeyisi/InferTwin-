"""Resolve model runtime defaults by fixed-routed instance UUID."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from infertwin.config.loader import load_yaml
from infertwin.config.model_binding import (
    ModelBindingValidationResult,
    ModelRegistryValidationResult,
    validate_instance_model_bindings,
    validate_model_registry,
)
from infertwin.config.model_registry import ModelRegistry
from infertwin.config.model_runtime import (
    ModelCacheDefaults,
    ModelRuntimeTable,
    ResolvedModelRuntimeProfile,
    resolve_model_runtime_table,
)
from infertwin.config.profiles import InstanceProfile


@dataclass(frozen=True, slots=True)
class InstanceRuntimeConfig:
    """Config paths required to resolve instance runtime defaults."""

    instance_profile_path: Path
    model_registry_path: Path


@dataclass(frozen=True, slots=True)
class InstanceRuntimeResolver:
    """Resolve scheduler/cache/deployment defaults for one fixed-routed instance."""

    instance_profile: InstanceProfile
    model_runtime_table: ModelRuntimeTable
    model_binding_validation: ModelBindingValidationResult
    instance_profile_path: Path
    model_registry_path: Path

    def runtime_profile_for(self, instance_uuid: str) -> ResolvedModelRuntimeProfile:
        """Return the model-owned runtime profile bound to an instance UUID."""

        if not instance_uuid:
            raise ValueError("instance_uuid must be a non-empty string")
        instance = self.instance_profile.instance_by_uuid.get(instance_uuid)
        if instance is None:
            raise ValueError(
                "instance runtime profile missing for "
                f"{instance_uuid!r}; add it to the instance profile table"
            )
        if instance.model_name is None:
            raise ValueError(
                f"instances.items.{instance_uuid}.model_name is required "
                "for instance runtime resolution"
            )
        return self.model_runtime_table.profile_for(instance.model_name)

    def default_cache_for(self, instance_uuid: str) -> ModelCacheDefaults:
        """Return model-owned cache defaults for one instance UUID."""

        return self.runtime_profile_for(instance_uuid).default_cache

    @property
    def model_name_by_instance(self) -> Mapping[str, str]:
        return self.model_binding_validation.model_name_by_instance

    @property
    def default_cache_by_instance(self) -> Mapping[str, ModelCacheDefaults]:
        return {
            instance_uuid: self.default_cache_for(instance_uuid)
            for instance_uuid in sorted(self.model_name_by_instance)
        }


def build_instance_runtime_resolver(config: Mapping[str, Any]) -> InstanceRuntimeResolver:
    """Build an instance runtime resolver from documented config sections."""

    runtime_config = build_instance_runtime_config(config)
    model_registry, registry_validation = _load_validated_model_registry(
        runtime_config.model_registry_path
    )
    instance_profile = InstanceProfile.from_mapping(load_yaml(runtime_config.instance_profile_path))
    model_binding_validation = validate_instance_model_bindings(
        instance_profile=instance_profile,
        model_registry=model_registry,
        registry_validation=registry_validation,
    )
    return InstanceRuntimeResolver(
        instance_profile=instance_profile,
        model_runtime_table=resolve_model_runtime_table(
            registry=model_registry,
            registry_validation=registry_validation,
        ),
        model_binding_validation=model_binding_validation,
        instance_profile_path=runtime_config.instance_profile_path,
        model_registry_path=runtime_config.model_registry_path,
    )


def build_instance_runtime_config(config: Mapping[str, Any]) -> InstanceRuntimeConfig:
    """Validate and normalize instance runtime resolver config paths."""

    model_registry_path = _required_profile_path(config, "model_registry")
    explicit_runtime_path = _optional_profile_path(config, "instance_runtime")
    latency_instance_path = _optional_profile_path(config, "instance_latency")
    if explicit_runtime_path is None and latency_instance_path is None:
        raise ValueError(
            "instance_runtime.profile_path is required for instance runtime resolution"
        )
    if (
        explicit_runtime_path is not None
        and latency_instance_path is not None
        and explicit_runtime_path != latency_instance_path
    ):
        raise ValueError(
            "instance_runtime.profile_path and instance_latency.profile_path must match "
            "when both are configured"
        )
    return InstanceRuntimeConfig(
        instance_profile_path=explicit_runtime_path or latency_instance_path,
        model_registry_path=model_registry_path,
    )


def _load_validated_model_registry(
    registry_path: Path,
) -> tuple[ModelRegistry, ModelRegistryValidationResult]:
    model_registry = ModelRegistry.from_mapping(load_yaml(registry_path))
    return model_registry, validate_model_registry(
        model_registry,
        base_dir=registry_path.parent,
    )


def _required_profile_path(config: Mapping[str, Any], section_name: str) -> Path:
    profile_path = _optional_profile_path(config, section_name)
    if profile_path is None:
        raise ValueError(f"{section_name}.profile_path is required")
    return profile_path


def _optional_profile_path(config: Mapping[str, Any], section_name: str) -> Path | None:
    raw_config = config.get(section_name)
    if raw_config is None:
        return None
    if not isinstance(raw_config, Mapping):
        raise ValueError(f"{section_name} config must be a mapping")
    value = raw_config.get("profile_path")
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{section_name}.profile_path must be a non-empty string")
    return Path(value)
