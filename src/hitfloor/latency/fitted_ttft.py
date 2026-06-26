"""Fitted TTFT latency backend for Batch D replay."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from hitfloor.latency.schema import LatencyResult, ShapeKey
from hitfloor.scheduler.batch_shape import BatchShape


@dataclass(frozen=True, slots=True)
class FittedTTFTLatencyBackend:
    """Estimate iteration duration with a fitted token-linear TTFT function."""

    intercept_ms: float
    ms_per_uncached_token: float
    model_name: str
    hardware_name: str
    profile: str
    function: Literal["token_linear_v1"] = "token_linear_v1"
    calibrated_from: str = "manual_default"

    name: str = "fitted_ttft"

    def __post_init__(self) -> None:
        if self.function != "token_linear_v1":
            raise ValueError("FittedTTFTLatencyBackend only supports token_linear_v1")
        if self.intercept_ms < 0:
            raise ValueError("intercept_ms must be non-negative")
        if self.ms_per_uncached_token < 0:
            raise ValueError("ms_per_uncached_token must be non-negative")
        if not self.model_name:
            raise ValueError("model_name must be non-empty")
        if not self.hardware_name:
            raise ValueError("hardware_name must be non-empty")
        if not self.profile:
            raise ValueError("profile must be non-empty")

    def estimate_iteration(self, shape: BatchShape) -> LatencyResult:
        duration_ms = (
            self.intercept_ms + self.ms_per_uncached_token * shape.scheduled_prefill_tokens
        )
        shape_key = ShapeKey.from_shape(
            backend=self.name,
            model_name=self.model_name,
            hardware_name=self.hardware_name,
            shape=shape,
        )
        return LatencyResult(
            duration_ms=duration_ms,
            backend=self.name,
            shape_key=shape_key,
            details={
                "profile": self.profile,
                "function": self.function,
                "calibrated_from": self.calibrated_from,
                "intercept_ms": self.intercept_ms,
                "ms_per_uncached_token": self.ms_per_uncached_token,
                "scheduled_prefill_tokens": shape.scheduled_prefill_tokens,
            },
        )
