"""Model registry schema for simulator profile resolution."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from infertwin.config.model_runtime import ModelCacheDefaults, ModelRuntimeDefaults
from infertwin.config.profiles import InstanceLatencyProfile


@dataclass(frozen=True, slots=True)
class ModelRegistryEntry:
    """Registered model metadata and default latency profile."""

    name: str
    model_profile_path: Path
    runtime_defaults: ModelRuntimeDefaults
    tokenizer_profile: str
    chat_template_profile: str | None
    default_latency: InstanceLatencyProfile

    @property
    def deployment_profile_path(self) -> Path:
        return self.runtime_defaults.deployment_profile_path

    @property
    def default_cache(self) -> ModelCacheDefaults:
        return self.runtime_defaults.cache


@dataclass(frozen=True, slots=True)
class ModelRegistry:
    """Registry of models known to InferTwin."""

    entries: tuple[ModelRegistryEntry, ...]

    @classmethod
    def from_mapping(cls, data: object) -> "ModelRegistry":
        mapping = _section(data, "models")
        entries = tuple(
            _parse_model_registry_entry(
                model_name=_non_empty_str(model_name, field_name="models key"),
                value=value,
            )
            for model_name, value in sorted(mapping.items())
        )
        return cls(entries=entries)

    @property
    def entry_by_name(self) -> Mapping[str, ModelRegistryEntry]:
        return {entry.name: entry for entry in self.entries}

    def entry_for(self, model_name: str) -> ModelRegistryEntry:
        normalized = _non_empty_str(model_name, field_name="model_name")
        entry = self.entry_by_name.get(normalized)
        if entry is None:
            raise ValueError(f"model registry missing model {normalized!r}")
        return entry


def _parse_model_registry_entry(
    *,
    model_name: str,
    value: object,
) -> ModelRegistryEntry:
    field_name = f"models.{model_name}"
    mapping = _mapping(value, field_name)
    default_latency = InstanceLatencyProfile.from_mapping(
        _required_mapping(mapping, "default_latency", field_name=field_name),
        profile_name=f"{model_name}__default_latency",
        field_name=f"{field_name}.default_latency",
    )
    return ModelRegistryEntry(
        name=model_name,
        model_profile_path=Path(
            _required_str(mapping, "model_profile_path", field_name=field_name)
        ),
        runtime_defaults=ModelRuntimeDefaults(
            deployment_profile_path=Path(
                _required_str(mapping, "deployment_profile_path", field_name=field_name)
            ),
            cache=ModelCacheDefaults.from_mapping(
                _required_mapping(mapping, "default_cache", field_name=field_name),
                field_name=f"{field_name}.default_cache",
            ),
        ),
        tokenizer_profile=_required_str(mapping, "tokenizer_profile", field_name=field_name),
        chat_template_profile=_optional_str(
            mapping.get("chat_template_profile"),
            field_name=f"{field_name}.chat_template_profile",
        ),
        default_latency=default_latency,
    )


def _section(data: object, section_name: str) -> dict[str, Any]:
    mapping = _mapping(data, section_name)
    if section_name in mapping:
        return _mapping(mapping[section_name], section_name)
    return mapping


def _mapping(data: object, field_name: str) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError(f"{field_name} must be a mapping")
    return data


def _required_mapping(mapping: dict[str, Any], key: str, *, field_name: str) -> dict[str, Any]:
    if key not in mapping:
        raise ValueError(f"{field_name}.{key} is required")
    return _mapping(mapping[key], f"{field_name}.{key}")


def _required_str(mapping: dict[str, Any], key: str, *, field_name: str) -> str:
    if key not in mapping:
        raise ValueError(f"{field_name}.{key} is required")
    return _non_empty_str(mapping[key], field_name=f"{field_name}.{key}")


def _optional_str(value: object, *, field_name: str) -> str | None:
    if value is None:
        return None
    return _non_empty_str(value, field_name=field_name)


def _non_empty_str(value: object, *, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value
