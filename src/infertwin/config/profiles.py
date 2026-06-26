"""Typed profile schemas for core simulator configuration."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(frozen=True, slots=True)
class CacheGroupProfile:
    """KV cache group metadata for unitary and hybrid cache managers."""

    name: str
    attention_type: str
    block_size: int

    @classmethod
    def from_mapping(cls, data: object, *, field_name: str) -> "CacheGroupProfile":
        mapping = _mapping(data, field_name)
        return cls(
            name=_required_str(mapping, "name", field_name=field_name),
            attention_type=_required_str(mapping, "attention_type", field_name=field_name),
            block_size=_positive_int(
                mapping.get("block_size"), field_name=f"{field_name}.block_size"
            ),
        )


@dataclass(frozen=True, slots=True)
class ModelProfile:
    """Model-owned settings used by request build and cache semantics."""

    name: str
    aliases: tuple[str, ...]
    tokenizer_profile: str
    chat_template_profile: str | None = None
    max_model_len: int | None = None
    cache_family: str = "full_attention"
    cache_groups: tuple[CacheGroupProfile, ...] = ()

    @classmethod
    def from_mapping(cls, data: object) -> "ModelProfile":
        mapping = _section(data, "model")
        cache_groups = tuple(
            CacheGroupProfile.from_mapping(item, field_name=f"model.cache_groups[{index}]")
            for index, item in enumerate(_optional_sequence(mapping.get("cache_groups", ())))
        )
        return cls(
            name=_required_str(mapping, "name", field_name="model"),
            aliases=_str_tuple(mapping.get("aliases", ()), field_name="model.aliases"),
            tokenizer_profile=_required_str(mapping, "tokenizer_profile", field_name="model"),
            chat_template_profile=_optional_str(
                mapping.get("chat_template_profile"),
                field_name="model.chat_template_profile",
            ),
            max_model_len=_optional_positive_int(
                mapping.get("max_model_len"),
                field_name="model.max_model_len",
            ),
            cache_family=_optional_str(
                mapping.get("cache_family"),
                field_name="model.cache_family",
            )
            or "full_attention",
            cache_groups=cache_groups,
        )

    @property
    def accepted_model_names(self) -> frozenset[str]:
        return frozenset((self.name, *self.aliases))


@dataclass(frozen=True, slots=True)
class HardwareProfile:
    """Hardware settings that can be shared by replay and latency profiles."""

    name: str
    accelerator_type: str | None = None
    accelerator_count: int | None = None
    hbm_gib: float | None = None
    kv_dtype_bytes: int | None = None
    communication: dict[str, object] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, data: object) -> "HardwareProfile":
        mapping = _section(data, "hardware")
        return cls(
            name=_required_str(mapping, "name", field_name="hardware"),
            accelerator_type=_optional_str(
                mapping.get("accelerator_type", mapping.get("gpu_type")),
                field_name="hardware.accelerator_type",
            ),
            accelerator_count=_optional_positive_int(
                mapping.get("accelerator_count", mapping.get("gpu_count")),
                field_name="hardware.accelerator_count",
            ),
            hbm_gib=_optional_positive_float(mapping.get("hbm_gib"), field_name="hardware.hbm_gib"),
            kv_dtype_bytes=_optional_positive_int(
                mapping.get("kv_dtype_bytes"),
                field_name="hardware.kv_dtype_bytes",
            ),
            communication=dict(_optional_mapping(mapping.get("communication", {}))),
        )


@dataclass(frozen=True, slots=True)
class SchedulerProfile:
    """vLLM-like scheduler startup parameters."""

    max_num_seqs: int
    max_num_batched_tokens: int
    enable_chunked_prefill: bool
    long_prefill_token_threshold: int | None = None

    @classmethod
    def from_mapping(cls, data: object, *, field_name: str) -> "SchedulerProfile":
        mapping = _mapping(data, field_name)
        return cls(
            max_num_seqs=_positive_int(
                mapping.get("max_num_seqs"),
                field_name=f"{field_name}.max_num_seqs",
            ),
            max_num_batched_tokens=_positive_int(
                mapping.get("max_num_batched_tokens"),
                field_name=f"{field_name}.max_num_batched_tokens",
            ),
            enable_chunked_prefill=_bool(
                mapping.get("enable_chunked_prefill"),
                field_name=f"{field_name}.enable_chunked_prefill",
            ),
            long_prefill_token_threshold=_optional_positive_int(
                mapping.get("long_prefill_token_threshold"),
                field_name=f"{field_name}.long_prefill_token_threshold",
            ),
        )


@dataclass(frozen=True, slots=True)
class ParallelProfile:
    """Parallelism settings that affect scheduler and cache-hit semantics."""

    tensor_parallel_size: int = 1
    pipeline_parallel_size: int = 1
    prefill_context_parallel_size: int = 1
    decode_context_parallel_size: int = 1

    @classmethod
    def from_mapping(cls, data: object | None, *, field_name: str) -> "ParallelProfile":
        mapping = _optional_mapping(data or {})
        return cls(
            tensor_parallel_size=_positive_int(
                mapping.get("tensor_parallel_size", 1),
                field_name=f"{field_name}.tensor_parallel_size",
            ),
            pipeline_parallel_size=_positive_int(
                mapping.get("pipeline_parallel_size", 1),
                field_name=f"{field_name}.pipeline_parallel_size",
            ),
            prefill_context_parallel_size=_positive_int(
                mapping.get("prefill_context_parallel_size", 1),
                field_name=f"{field_name}.prefill_context_parallel_size",
            ),
            decode_context_parallel_size=_positive_int(
                mapping.get("decode_context_parallel_size", 1),
                field_name=f"{field_name}.decode_context_parallel_size",
            ),
        )

    @property
    def context_parallel_factor(self) -> int:
        return self.prefill_context_parallel_size * self.decode_context_parallel_size


@dataclass(frozen=True, slots=True)
class SpeculativeProfile:
    """Speculative decoding settings that can reduce reusable cached blocks."""

    enabled: bool = False
    method: str | None = None
    speculative_drop_blocks: int = 0

    @classmethod
    def from_mapping(cls, data: object | None, *, field_name: str) -> "SpeculativeProfile":
        mapping = _optional_mapping(data or {})
        enabled = _bool(mapping.get("enabled", False), field_name=f"{field_name}.enabled")
        method = _optional_str(mapping.get("method"), field_name=f"{field_name}.method")
        default_drop_blocks = 1 if enabled and method in {"mtp", "eagle", "eagle3"} else 0
        return cls(
            enabled=enabled,
            method=method,
            speculative_drop_blocks=_non_negative_int(
                mapping.get("speculative_drop_blocks", default_drop_blocks),
                field_name=f"{field_name}.speculative_drop_blocks",
            ),
        )


@dataclass(frozen=True, slots=True)
class CacheFeatureProfile:
    """Cache-related deployment features that may change replay semantics."""

    prefix_caching: bool = True
    multi_tier_cache: bool = False
    pooling: bool = False
    kv_transfer: bool = False
    runtime_block_size: int | None = None

    @classmethod
    def from_mapping(cls, data: object | None, *, field_name: str) -> "CacheFeatureProfile":
        mapping = _optional_mapping(data or {})
        return cls(
            prefix_caching=_bool(
                mapping.get("prefix_caching", True),
                field_name=f"{field_name}.prefix_caching",
            ),
            multi_tier_cache=_bool(
                mapping.get("multi_tier_cache", False),
                field_name=f"{field_name}.multi_tier_cache",
            ),
            pooling=_bool(mapping.get("pooling", False), field_name=f"{field_name}.pooling"),
            kv_transfer=_bool(
                mapping.get("kv_transfer", False),
                field_name=f"{field_name}.kv_transfer",
            ),
            runtime_block_size=_optional_positive_int(
                mapping.get("runtime_block_size"),
                field_name=f"{field_name}.runtime_block_size",
            ),
        )


@dataclass(frozen=True, slots=True)
class DeploymentProfile:
    """Deployment startup and feature profile for one group of instances."""

    name: str
    engine: str
    scheduler: SchedulerProfile
    parallel: ParallelProfile = field(default_factory=ParallelProfile)
    speculative: SpeculativeProfile = field(default_factory=SpeculativeProfile)
    cache_features: CacheFeatureProfile = field(default_factory=CacheFeatureProfile)
    startup_args: dict[str, object] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, data: object) -> "DeploymentProfile":
        mapping = _section(data, "deployment")
        return cls(
            name=_required_str(mapping, "name", field_name="deployment"),
            engine=_required_str(mapping, "engine", field_name="deployment"),
            scheduler=SchedulerProfile.from_mapping(
                mapping.get("scheduler"),
                field_name="deployment.scheduler",
            ),
            parallel=ParallelProfile.from_mapping(
                mapping.get("parallel"),
                field_name="deployment.parallel",
            ),
            speculative=SpeculativeProfile.from_mapping(
                mapping.get("speculative"),
                field_name="deployment.speculative",
            ),
            cache_features=CacheFeatureProfile.from_mapping(
                mapping.get("cache_features"),
                field_name="deployment.cache_features",
            ),
            startup_args=dict(_optional_mapping(mapping.get("startup_args", {}))),
        )


@dataclass(frozen=True, slots=True)
class InstanceDeployment:
    """Deployment assignment for one fixed-routed instance."""

    instance_uuid: str
    deployment: str
    model_name: str | None = None
    latency_profile: str | None = None


@dataclass(frozen=True, slots=True)
class FittedTTFTProfile:
    """Token-linear fitted TTFT hyperparameters for one instance latency profile."""

    profile: str
    function: Literal["token_linear_v1"]
    intercept_ms: float
    ms_per_uncached_token: float
    calibrated_from: str
    calibration_window_requests: int = 500

    @classmethod
    def from_mapping(cls, data: object, *, field_name: str) -> "FittedTTFTProfile":
        mapping = _mapping(data, field_name)
        function = _required_str(mapping, "function", field_name=field_name)
        if function != "token_linear_v1":
            raise ValueError(f"{field_name}.function only supports token_linear_v1")
        return cls(
            profile=_required_str(mapping, "profile", field_name=field_name),
            function=function,
            intercept_ms=_non_negative_float(
                mapping.get("intercept_ms"),
                field_name=f"{field_name}.intercept_ms",
            ),
            ms_per_uncached_token=_non_negative_float(
                mapping.get("ms_per_uncached_token"),
                field_name=f"{field_name}.ms_per_uncached_token",
            ),
            calibrated_from=_required_str(mapping, "calibrated_from", field_name=field_name),
            calibration_window_requests=_positive_int(
                mapping.get("calibration_window_requests", 500),
                field_name=f"{field_name}.calibration_window_requests",
            ),
        )


@dataclass(frozen=True, slots=True)
class KVLoadLatencyProfile:
    """Per-token KV load latency hyperparameters for non-HBM cache hits."""

    ddr_ms_per_cached_token: float = 0.0
    remote_ms_per_cached_token: float = 0.0

    @classmethod
    def from_mapping(cls, data: object | None, *, field_name: str) -> "KVLoadLatencyProfile":
        mapping = _optional_mapping(data or {})
        return cls(
            ddr_ms_per_cached_token=_non_negative_float(
                mapping.get("ddr_ms_per_cached_token", 0.0),
                field_name=f"{field_name}.ddr_ms_per_cached_token",
            ),
            remote_ms_per_cached_token=_non_negative_float(
                mapping.get("remote_ms_per_cached_token", 0.0),
                field_name=f"{field_name}.remote_ms_per_cached_token",
            ),
        )


@dataclass(frozen=True, slots=True)
class InstanceLatencyProfile:
    """Latency backend configuration assigned to fixed-routed instances."""

    name: str
    backend: Literal["fitted_ttft"]
    model_name: str
    hardware_name: str
    fitted_ttft: FittedTTFTProfile
    kv_load: KVLoadLatencyProfile = field(default_factory=KVLoadLatencyProfile)

    @classmethod
    def from_mapping(
        cls,
        data: object,
        *,
        profile_name: str,
        field_name: str,
    ) -> "InstanceLatencyProfile":
        mapping = _mapping(data, field_name)
        backend = _required_str(mapping, "backend", field_name=field_name)
        if backend != "fitted_ttft":
            raise ValueError(f"{field_name}.backend only supports fitted_ttft")
        name = _optional_str(mapping.get("name"), field_name=f"{field_name}.name") or profile_name
        if name != profile_name:
            raise ValueError(f"{field_name}.name must match latency profile key {profile_name!r}")
        return cls(
            name=name,
            backend=backend,
            model_name=_required_str(mapping, "model_name", field_name=field_name),
            hardware_name=_required_str(mapping, "hardware_name", field_name=field_name),
            fitted_ttft=FittedTTFTProfile.from_mapping(
                mapping.get("fitted_ttft"),
                field_name=f"{field_name}.fitted_ttft",
            ),
            kv_load=KVLoadLatencyProfile.from_mapping(
                mapping.get("kv_load"),
                field_name=f"{field_name}.kv_load",
            ),
        )


@dataclass(frozen=True, slots=True)
class InstanceProfile:
    """Mapping from trace instance UUIDs to deployment profiles."""

    name: str
    instances: tuple[InstanceDeployment, ...]
    latency_profiles: tuple[InstanceLatencyProfile, ...] = ()

    @classmethod
    def from_mapping(cls, data: object) -> "InstanceProfile":
        mapping = _section(data, "instances")
        latency_profiles = _parse_latency_profiles(
            mapping.get("latency_profiles", {}),
            field_name="instances.latency_profiles",
        )
        latency_profile_names = {profile.name for profile in latency_profiles}
        raw_instances = _optional_mapping(mapping.get("items", mapping.get("instances", {})))
        instances = tuple(
            _parse_instance_deployment(
                instance_uuid=instance_uuid,
                value=value,
                latency_profile_names=latency_profile_names,
            )
            for instance_uuid, value in sorted(raw_instances.items())
        )
        return cls(
            name=_required_str(mapping, "name", field_name="instances"),
            instances=instances,
            latency_profiles=latency_profiles,
        )

    @property
    def deployment_by_instance(self) -> dict[str, str]:
        return {item.instance_uuid: item.deployment for item in self.instances}

    @property
    def instance_by_uuid(self) -> dict[str, InstanceDeployment]:
        return {item.instance_uuid: item for item in self.instances}

    @property
    def model_name_by_instance(self) -> dict[str, str]:
        return {
            item.instance_uuid: item.model_name
            for item in self.instances
            if item.model_name is not None
        }

    @property
    def latency_profile_by_name(self) -> dict[str, InstanceLatencyProfile]:
        return {item.name: item for item in self.latency_profiles}

    @property
    def latency_profile_by_instance(self) -> dict[str, InstanceLatencyProfile]:
        profiles = self.latency_profile_by_name
        return {
            item.instance_uuid: profiles[item.latency_profile]
            for item in self.instances
            if item.latency_profile is not None
        }


def _parse_latency_profiles(
    data: object,
    *,
    field_name: str,
) -> tuple[InstanceLatencyProfile, ...]:
    raw_profiles = _optional_mapping(data)
    return tuple(
        InstanceLatencyProfile.from_mapping(
            value,
            profile_name=_non_empty_str(profile_name, field_name=f"{field_name}.key"),
            field_name=f"{field_name}.{profile_name}",
        )
        for profile_name, value in sorted(raw_profiles.items())
    )


def _parse_instance_deployment(
    *,
    instance_uuid: object,
    value: object,
    latency_profile_names: set[str],
) -> InstanceDeployment:
    instance_id = _non_empty_str(instance_uuid, field_name="instances.items key")
    field_name = f"instances.items.{instance_id}"
    mapping = _mapping(value, field_name)
    latency_profile = _optional_str(
        mapping.get("latency_profile"),
        field_name=f"{field_name}.latency_profile",
    )
    if latency_profile is not None and latency_profile not in latency_profile_names:
        raise ValueError(
            f"{field_name}.latency_profile references unknown latency profile {latency_profile!r}"
        )
    return InstanceDeployment(
        instance_uuid=instance_id,
        deployment=_required_str(mapping, "deployment", field_name=field_name),
        model_name=_optional_str(mapping.get("model_name"), field_name=f"{field_name}.model_name"),
        latency_profile=latency_profile,
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


def _optional_mapping(data: object) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError("value must be a mapping")
    return data


def _optional_sequence(data: object) -> tuple[object, ...]:
    if isinstance(data, tuple):
        return data
    if isinstance(data, list):
        return tuple(data)
    raise ValueError("value must be a sequence")


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


def _str_tuple(value: object, *, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list | tuple):
        raise ValueError(f"{field_name} must be a sequence of strings")
    return tuple(_non_empty_str(item, field_name=f"{field_name}[]") for item in value)


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


def _non_negative_int(value: object, *, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")
    return value


def _optional_positive_float(value: object, *, field_name: str) -> float | None:
    if value is None:
        return None
    if not isinstance(value, int | float) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{field_name} must be a positive number")
    return float(value)


def _non_negative_float(value: object, *, field_name: str) -> float:
    if not isinstance(value, int | float) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{field_name} must be a non-negative number")
    return float(value)
