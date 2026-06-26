"""Factories for configured batch latency backends."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from infertwin.latency.backend import BatchLatencyBackend
from infertwin.latency.fitted_ttft import FittedTTFTLatencyBackend
from infertwin.latency.formula import FormulaLatencyBackend
from infertwin.latency.profile import ServingLatencyProfile


def build_batch_latency_backend(config: Mapping[str, Any]) -> BatchLatencyBackend:
    """Build a batch latency backend from the documented latency config."""

    latency_config = _mapping(config, "latency")
    backend_name = _required_str(latency_config, "backend")
    model_name = _required_str(latency_config, "model_name")
    hardware_name = _required_str(latency_config, "hardware_name")

    if backend_name == "fitted_ttft":
        return _build_fitted_ttft_backend(
            _mapping(latency_config, "fitted_ttft"),
            model_name=model_name,
            hardware_name=hardware_name,
        )

    if backend_name == "serving_latency_profile":
        profile_config = _mapping(latency_config, "serving_latency_profile")
        ttft_backend_name = _optional_str(
            profile_config,
            "ttft_backend",
            default="fitted_ttft",
        )
        if ttft_backend_name != "fitted_ttft":
            raise ValueError("serving_latency_profile.ttft_backend only supports fitted_ttft")
        fitted_config = _mapping(latency_config, "fitted_ttft")
        return ServingLatencyProfile(
            profile=_required_str(profile_config, "profile"),
            calibrated_from=_optional_str(
                profile_config,
                "calibrated_from",
                default="manual_default",
            ),
            calibration_window_requests=_optional_int(
                profile_config,
                "calibration_window_requests",
                default=500,
            ),
            ttft_backend=_build_fitted_ttft_backend(
                fitted_config,
                model_name=model_name,
                hardware_name=hardware_name,
            ),
        )

    if backend_name == "formula":
        formula_config = _mapping(latency_config, "formula")
        return FormulaLatencyBackend(
            iteration_fixed_overhead_ms=_required_float(
                formula_config,
                "iteration_fixed_overhead_ms",
            ),
            iteration_prefill_token_ms=_required_float(
                formula_config,
                "iteration_prefill_token_ms",
            ),
            iteration_batch_overhead_ms=_required_float(
                formula_config,
                "iteration_batch_overhead_ms",
            ),
            iteration_context_token_ms=_required_float(
                formula_config,
                "iteration_context_token_ms",
            ),
            model_name=model_name,
            hardware_name=hardware_name,
        )

    raise ValueError(f"unsupported latency backend: {backend_name}")


def _build_fitted_ttft_backend(
    fitted_config: Mapping[str, Any],
    *,
    model_name: str,
    hardware_name: str,
) -> FittedTTFTLatencyBackend:
    return FittedTTFTLatencyBackend(
        profile=_required_str(fitted_config, "profile"),
        function=_required_str(fitted_config, "function"),  # type: ignore[arg-type]
        intercept_ms=_required_float(fitted_config, "intercept_ms"),
        ms_per_uncached_token=_required_float(
            fitted_config,
            "ms_per_uncached_token",
        ),
        calibrated_from=_required_str(fitted_config, "calibrated_from"),
        model_name=model_name,
        hardware_name=hardware_name,
    )


def _mapping(parent: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = parent.get(key)
    if not isinstance(value, Mapping):
        raise ValueError(f"{key} config must be a mapping")
    return value


def _required_str(config: Mapping[str, Any], key: str) -> str:
    value = config.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be a non-empty string")
    return value


def _required_float(config: Mapping[str, Any], key: str) -> float:
    value = config.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{key} must be a number")
    return float(value)


def _optional_str(config: Mapping[str, Any], key: str, *, default: str) -> str:
    value = config.get(key, default)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be a non-empty string")
    return value


def _optional_int(config: Mapping[str, Any], key: str, *, default: int) -> int:
    value = config.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key} must be an integer")
    return value
