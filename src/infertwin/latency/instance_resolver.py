"""Resolve batch latency backends by fixed-routed instance UUID."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from infertwin.config.loader import load_yaml
from infertwin.config.model_binding import (
    ModelRegistryValidationResult,
    validate_instance_model_bindings,
    validate_model_registry,
)
from infertwin.config.model_registry import ModelRegistry
from infertwin.config.profiles import InstanceLatencyProfile, InstanceProfile
from infertwin.latency.backend import BatchLatencyBackend
from infertwin.latency.factory import build_batch_latency_backend
from infertwin.latency.fitted_ttft import FittedTTFTLatencyBackend
from infertwin.latency.kv_load import build_kv_load_component
from infertwin.latency.profile import ServingLatencyProfile


@dataclass(frozen=True, slots=True)
class InstanceLatencyConfig:
    """Configuration for optional per-instance latency backend resolution."""

    profile_path: Path | None
    require_all_trace_instances: bool = True


@dataclass(frozen=True, slots=True)
class ModelRegistryConfig:
    """Configuration for optional model registry fallback resolution."""

    profile_path: Path | None


@dataclass(frozen=True, slots=True)
class LatencyResolutionMetadata:
    """Explain where an instance latency backend came from."""

    source: Literal["global", "instance_profile", "model_default"]
    calibration_status: Literal["configured", "model_default"]
    model_name: str


class InstanceLatencyBackendResolver:
    """Resolve latency backend for one fixed-routed instance."""

    def __init__(
        self,
        *,
        global_backend: BatchLatencyBackend,
        instance_profile: InstanceProfile | None = None,
        model_registry: ModelRegistry | None = None,
        require_all_trace_instances: bool = True,
        profile_path: Path | None = None,
        model_registry_path: Path | None = None,
    ) -> None:
        self._global_backend = global_backend
        self._instance_profile = instance_profile
        self._model_registry = model_registry
        self._require_all_trace_instances = require_all_trace_instances
        self._profile_path = profile_path
        self._model_registry_path = model_registry_path
        self._backend_by_instance: dict[str, BatchLatencyBackend] = {}
        self._metadata_by_instance: dict[str, LatencyResolutionMetadata] = {}

    @property
    def uses_instance_profiles(self) -> bool:
        return self._instance_profile is not None

    @property
    def uses_model_registry(self) -> bool:
        return self._model_registry is not None

    @property
    def require_all_trace_instances(self) -> bool:
        return self._require_all_trace_instances

    @property
    def profile_path(self) -> Path | None:
        return self._profile_path

    @property
    def model_registry_path(self) -> Path | None:
        return self._model_registry_path

    @property
    def profile_name_by_instance(self) -> Mapping[str, str]:
        if self._instance_profile is None:
            return {}
        return {
            item.instance_uuid: item.latency_profile
            for item in self._instance_profile.instances
            if item.latency_profile is not None
        }

    @property
    def latency_source_by_instance(self) -> Mapping[str, str]:
        return {
            instance_uuid: metadata.source
            for instance_uuid, metadata in sorted(self._metadata_by_instance.items())
        }

    @property
    def instance_profile_count(self) -> int:
        if self._instance_profile is None:
            return 0
        return len(self._instance_profile.latency_profiles)

    def backend_for(self, instance_uuid: str) -> BatchLatencyBackend:
        """Return the backend configured for one instance UUID."""

        if not instance_uuid:
            raise ValueError("instance_uuid must be a non-empty string")
        if self._instance_profile is None:
            return self._global_backend

        backend = self._backend_by_instance.get(instance_uuid)
        if backend is not None:
            return backend

        profile, source = self._latency_profile_for_instance(instance_uuid)
        backend = _build_instance_latency_backend(profile)
        self._backend_by_instance[instance_uuid] = backend
        self._metadata_by_instance[instance_uuid] = LatencyResolutionMetadata(
            source=source,
            calibration_status="configured" if source == "instance_profile" else "model_default",
            model_name=profile.model_name,
        )
        return backend

    def metadata_for(self, instance_uuid: str) -> LatencyResolutionMetadata:
        """Return source metadata for an instance backend resolution."""

        if self._instance_profile is None:
            return LatencyResolutionMetadata(
                source="global",
                calibration_status="configured",
                model_name=self._global_backend.model_name,
            )
        metadata = self._metadata_by_instance.get(instance_uuid)
        if metadata is None:
            self.backend_for(instance_uuid)
            metadata = self._metadata_by_instance[instance_uuid]
        return metadata

    def _latency_profile_for_instance(
        self,
        instance_uuid: str,
    ) -> tuple[InstanceLatencyProfile, Literal["instance_profile", "model_default"]]:
        assert self._instance_profile is not None
        profile = self._instance_profile.latency_profile_by_instance.get(instance_uuid)
        if profile is not None:
            return profile, "instance_profile"
        instance = self._instance_profile.instance_by_uuid.get(instance_uuid)
        if instance is None:
            raise ValueError(
                "instance latency profile missing for "
                f"{instance_uuid!r}; add it to the instance profile table"
            )
        if self._model_registry is not None and instance.model_name is not None:
            return (
                self._model_registry.entry_for(instance.model_name).default_latency,
                "model_default",
            )
        if self._require_all_trace_instances:
            raise ValueError(
                "instance latency profile missing for "
                f"{instance_uuid!r}; add it to the instance profile table"
            )
        raise ValueError(
            "instance latency profile missing for "
            f"{instance_uuid!r}; require_all_trace_instances=false is reserved but "
            "fallback semantics are not implemented"
        )


def build_instance_latency_backend_resolver(
    config: Mapping[str, Any],
) -> InstanceLatencyBackendResolver:
    """Build a resolver that falls back to the global backend when unconfigured."""

    global_backend = build_batch_latency_backend(config)
    instance_latency_config = build_instance_latency_config(config)
    model_registry_config = build_model_registry_config(config)
    model_registry, registry_validation = _load_model_registry(model_registry_config.profile_path)
    if instance_latency_config.profile_path is None:
        return InstanceLatencyBackendResolver(
            global_backend=global_backend,
            model_registry=model_registry,
            model_registry_path=model_registry_config.profile_path,
        )

    instance_profile = InstanceProfile.from_mapping(load_yaml(instance_latency_config.profile_path))
    _validate_instance_bindings(
        model_registry=model_registry,
        registry_validation=registry_validation,
        instance_profile=instance_profile,
    )
    return InstanceLatencyBackendResolver(
        global_backend=global_backend,
        instance_profile=instance_profile,
        model_registry=model_registry,
        require_all_trace_instances=instance_latency_config.require_all_trace_instances,
        profile_path=instance_latency_config.profile_path,
        model_registry_path=model_registry_config.profile_path,
    )


def build_instance_latency_config(config: Mapping[str, Any]) -> InstanceLatencyConfig:
    """Validate and normalize the optional instance_latency section."""

    raw_config = config.get("instance_latency")
    if raw_config is None:
        return InstanceLatencyConfig(profile_path=None)
    if not isinstance(raw_config, Mapping):
        raise ValueError("instance_latency config must be a mapping")
    profile_path = _optional_path(raw_config.get("profile_path"), field_name="instance_latency")
    if profile_path is None:
        raise ValueError("instance_latency.profile_path is required")
    require_all_trace_instances = _optional_bool(
        raw_config,
        "require_all_trace_instances",
        default=True,
    )
    if not require_all_trace_instances:
        raise ValueError(
            "instance_latency.require_all_trace_instances=false is reserved but not implemented"
        )
    return InstanceLatencyConfig(
        profile_path=profile_path,
        require_all_trace_instances=require_all_trace_instances,
    )


def build_model_registry_config(config: Mapping[str, Any]) -> ModelRegistryConfig:
    """Validate and normalize the optional model_registry section."""

    raw_config = config.get("model_registry")
    if raw_config is None:
        return ModelRegistryConfig(profile_path=None)
    if not isinstance(raw_config, Mapping):
        raise ValueError("model_registry config must be a mapping")
    profile_path = _optional_path(raw_config.get("profile_path"), field_name="model_registry")
    if profile_path is None:
        raise ValueError("model_registry.profile_path is required")
    return ModelRegistryConfig(profile_path=profile_path)


def _load_model_registry(
    profile_path: Path | None,
) -> tuple[ModelRegistry | None, ModelRegistryValidationResult | None]:
    if profile_path is None:
        return None, None
    model_registry = ModelRegistry.from_mapping(load_yaml(profile_path))
    return model_registry, validate_model_registry(
        model_registry,
        base_dir=profile_path.parent,
    )


def _validate_instance_bindings(
    *,
    model_registry: ModelRegistry | None,
    registry_validation: ModelRegistryValidationResult | None,
    instance_profile: InstanceProfile,
) -> None:
    if model_registry is None:
        return
    assert registry_validation is not None
    validate_instance_model_bindings(
        instance_profile=instance_profile,
        model_registry=model_registry,
        registry_validation=registry_validation,
    )


def _build_instance_latency_backend(profile: InstanceLatencyProfile) -> BatchLatencyBackend:
    if profile.backend != "fitted_ttft":
        raise ValueError(f"unsupported instance latency backend: {profile.backend}")
    fitted = profile.fitted_ttft
    ttft_backend = FittedTTFTLatencyBackend(
        profile=fitted.profile,
        function=fitted.function,
        intercept_ms=fitted.intercept_ms,
        ms_per_uncached_token=fitted.ms_per_uncached_token,
        calibrated_from=fitted.calibrated_from,
        model_name=profile.model_name,
        hardware_name=profile.hardware_name,
    )
    return ServingLatencyProfile(
        profile=profile.name,
        ttft_backend=ttft_backend,
        kv_load_component=build_kv_load_component(profile.kv_load),
        calibrated_from=fitted.calibrated_from,
        calibration_window_requests=fitted.calibration_window_requests,
    )


def _optional_path(value: object, *, field_name: str) -> Path | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name}.profile_path must be a non-empty string")
    return Path(value)


def _optional_bool(config: Mapping[str, Any], key: str, *, default: bool) -> bool:
    value = config.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f"instance_latency.{key} must be a boolean")
    return value
