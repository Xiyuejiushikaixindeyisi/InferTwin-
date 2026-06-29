"""Offline KV-load calibration helpers.

This module turns external observations into InferTwin KV-load profile
parameters. It does not run external simulators and is not wired into replay.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

_EPSILON = 1e-12


@dataclass(frozen=True, slots=True)
class KVLoadCalibrationObservation:
    """One offline KV-load latency observation."""

    source: str
    model_name: str
    hardware_name: str
    transfer_path: str
    kv_load_tokens: int
    kv_load_bytes: int
    kv_load_request_count: int
    batch_size: int
    duration_ms: float
    note: str = ""

    def __post_init__(self) -> None:
        _require_non_empty(self.source, field_name="source")
        _require_non_empty(self.model_name, field_name="model_name")
        _require_non_empty(self.hardware_name, field_name="hardware_name")
        _require_non_empty(self.transfer_path, field_name="transfer_path")
        _require_non_negative_int(self.kv_load_tokens, field_name="kv_load_tokens")
        _require_non_negative_int(self.kv_load_bytes, field_name="kv_load_bytes")
        _require_non_negative_int(
            self.kv_load_request_count,
            field_name="kv_load_request_count",
        )
        _require_positive_int(self.batch_size, field_name="batch_size")
        _require_non_negative_float(self.duration_ms, field_name="duration_ms")
        if not isinstance(self.note, str):
            raise ValueError("note must be a string")


@dataclass(frozen=True, slots=True)
class KVLoadCalibrationFit:
    """Fitted KV-load profile parameters for one Step8 v1 mode."""

    mode: Literal["token_linear_v1", "byte_linear_v1"]
    transfer_path: str
    ddr_fixed_overhead_ms: float
    calibrated_from: str
    sample_count: int
    aggregation: Literal["shared_link_sum"] = "shared_link_sum"
    overlap_mode: Literal["none_v1"] = "none_v1"
    ddr_ms_per_cached_token: float = 0.0
    ddr_ms_per_byte: float = 0.0

    def __post_init__(self) -> None:
        if self.mode not in {"token_linear_v1", "byte_linear_v1"}:
            raise ValueError("mode must be token_linear_v1 or byte_linear_v1")
        if self.aggregation != "shared_link_sum":
            raise ValueError("aggregation only supports shared_link_sum")
        if self.overlap_mode != "none_v1":
            raise ValueError("overlap_mode only supports none_v1")
        _require_non_empty(self.transfer_path, field_name="transfer_path")
        _require_non_empty(self.calibrated_from, field_name="calibrated_from")
        _require_non_negative_float(
            self.ddr_fixed_overhead_ms,
            field_name="ddr_fixed_overhead_ms",
        )
        _require_non_negative_float(
            self.ddr_ms_per_cached_token,
            field_name="ddr_ms_per_cached_token",
        )
        _require_non_negative_float(self.ddr_ms_per_byte, field_name="ddr_ms_per_byte")
        _require_positive_int(self.sample_count, field_name="sample_count")

        if self.mode == "token_linear_v1" and self.ddr_ms_per_byte != 0.0:
            raise ValueError("token_linear_v1 fit must not set ddr_ms_per_byte")
        if self.mode == "byte_linear_v1" and self.ddr_ms_per_cached_token != 0.0:
            raise ValueError("byte_linear_v1 fit must not set ddr_ms_per_cached_token")


def fit_token_linear_v1(
    observations: Sequence[KVLoadCalibrationObservation],
    *,
    calibrated_from: str,
    fit_intercept: bool = True,
) -> KVLoadCalibrationFit:
    """Fit ``duration_ms = overhead + tokens * ms_per_token``."""

    normalized = _normalize_observations(observations)
    _require_non_empty(calibrated_from, field_name="calibrated_from")
    _require_any_positive(
        [item.kv_load_tokens for item in normalized],
        field_name="kv_load_tokens",
    )
    intercept, slope = _linear_fit(
        x_values=[float(item.kv_load_tokens) for item in normalized],
        y_values=[item.duration_ms for item in normalized],
        fit_intercept=fit_intercept,
    )
    return KVLoadCalibrationFit(
        mode="token_linear_v1",
        transfer_path=normalized[0].transfer_path,
        ddr_fixed_overhead_ms=intercept,
        ddr_ms_per_cached_token=slope,
        calibrated_from=calibrated_from,
        sample_count=len(normalized),
    )


def fit_byte_linear_v1(
    observations: Sequence[KVLoadCalibrationObservation],
    *,
    calibrated_from: str,
    fit_intercept: bool = True,
) -> KVLoadCalibrationFit:
    """Fit ``duration_ms = overhead + bytes * ms_per_byte``."""

    normalized = _normalize_observations(observations)
    _require_non_empty(calibrated_from, field_name="calibrated_from")
    _require_any_positive(
        [item.kv_load_bytes for item in normalized],
        field_name="kv_load_bytes",
    )
    intercept, slope = _linear_fit(
        x_values=[float(item.kv_load_bytes) for item in normalized],
        y_values=[item.duration_ms for item in normalized],
        fit_intercept=fit_intercept,
    )
    return KVLoadCalibrationFit(
        mode="byte_linear_v1",
        transfer_path=normalized[0].transfer_path,
        ddr_fixed_overhead_ms=intercept,
        ddr_ms_per_byte=slope,
        calibrated_from=calibrated_from,
        sample_count=len(normalized),
    )


def to_kv_load_profile_mapping(fit: KVLoadCalibrationFit) -> dict[str, object]:
    """Convert a calibration fit into the existing KVLoadLatencyProfile mapping."""

    mapping: dict[str, object] = {
        "mode": fit.mode,
        "aggregation": fit.aggregation,
        "overlap_mode": fit.overlap_mode,
        "transfer_path": fit.transfer_path,
        "ddr_fixed_overhead_ms": fit.ddr_fixed_overhead_ms,
        "calibrated_from": fit.calibrated_from,
    }
    if fit.mode == "token_linear_v1":
        mapping["ddr_ms_per_cached_token"] = fit.ddr_ms_per_cached_token
    else:
        mapping["ddr_ms_per_byte"] = fit.ddr_ms_per_byte
    return mapping


def _normalize_observations(
    observations: Sequence[KVLoadCalibrationObservation],
) -> tuple[KVLoadCalibrationObservation, ...]:
    normalized = tuple(observations)
    if not normalized:
        raise ValueError("observations must be non-empty")

    first = normalized[0]
    for item in normalized:
        if item.model_name != first.model_name:
            raise ValueError("all observations must share the same model_name")
        if item.hardware_name != first.hardware_name:
            raise ValueError("all observations must share the same hardware_name")
        if item.transfer_path != first.transfer_path:
            raise ValueError("all observations must share the same transfer_path")
    return normalized


def _linear_fit(
    *,
    x_values: Sequence[float],
    y_values: Sequence[float],
    fit_intercept: bool,
) -> tuple[float, float]:
    if len(x_values) != len(y_values):
        raise ValueError("x_values and y_values must have the same length")
    if not x_values:
        raise ValueError("x_values must be non-empty")

    if fit_intercept:
        return _fit_with_intercept(x_values=x_values, y_values=y_values)
    return _fit_through_origin(x_values=x_values, y_values=y_values)


def _fit_with_intercept(
    *,
    x_values: Sequence[float],
    y_values: Sequence[float],
) -> tuple[float, float]:
    mean_x = sum(x_values) / len(x_values)
    mean_y = sum(y_values) / len(y_values)
    denominator = sum((x_value - mean_x) ** 2 for x_value in x_values)
    if denominator <= _EPSILON:
        raise ValueError("cannot fit slope when all calibration x values are equal")
    numerator = sum(
        (x_value - mean_x) * (y_value - mean_y)
        for x_value, y_value in zip(x_values, y_values, strict=True)
    )
    slope = numerator / denominator
    intercept = mean_y - slope * mean_x
    return _validate_fit_result(intercept=intercept, slope=slope)


def _fit_through_origin(
    *,
    x_values: Sequence[float],
    y_values: Sequence[float],
) -> tuple[float, float]:
    denominator = sum(x_value * x_value for x_value in x_values)
    if denominator <= _EPSILON:
        raise ValueError("cannot fit slope when all calibration x values are zero")
    numerator = sum(
        x_value * y_value
        for x_value, y_value in zip(x_values, y_values, strict=True)
    )
    slope = numerator / denominator
    return _validate_fit_result(intercept=0.0, slope=slope)


def _validate_fit_result(*, intercept: float, slope: float) -> tuple[float, float]:
    if intercept < -_EPSILON:
        raise ValueError("fitted fixed overhead is negative")
    if slope < -_EPSILON:
        raise ValueError("fitted slope is negative")
    return max(intercept, 0.0), max(slope, 0.0)


def _require_any_positive(values: Sequence[int], *, field_name: str) -> None:
    if not any(value > 0 for value in values):
        raise ValueError(f"at least one observation must have positive {field_name}")


def _require_non_empty(value: str, *, field_name: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} must be a non-empty string")


def _require_non_negative_int(value: int, *, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")


def _require_positive_int(value: int, *, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field_name} must be a positive integer")


def _require_non_negative_float(value: float, *, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int | float) or value < 0:
        raise ValueError(f"{field_name} must be a non-negative number")
