"""Latency backend package."""

from infertwin.latency.fallback import (
    CalibrationFailurePolicy,
    CalibrationStatus,
    LatencyFallbackConfig,
    build_latency_fallback_config,
)
from infertwin.latency.instance_resolver import (
    InstanceLatencyBackendResolver,
    InstanceLatencyConfig,
    LatencyResolutionMetadata,
    ModelRegistryConfig,
    build_instance_latency_backend_resolver,
    build_instance_latency_config,
    build_model_registry_config,
)

__all__ = [
    "CalibrationFailurePolicy",
    "CalibrationStatus",
    "InstanceLatencyBackendResolver",
    "InstanceLatencyConfig",
    "LatencyResolutionMetadata",
    "LatencyFallbackConfig",
    "ModelRegistryConfig",
    "build_instance_latency_backend_resolver",
    "build_instance_latency_config",
    "build_latency_fallback_config",
    "build_model_registry_config",
]
