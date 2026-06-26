"""Calibration-failure fallback policy schema.

This module only describes what a future calibration harness may do after a
calibration-specific failure. It does not catch replay, parser, tokenizer,
scheduler, cache, or ordinary backend construction errors.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

CalibrationFailurePolicy = Literal["fail", "use_model_default"]
CalibrationStatus = Literal["configured", "model_default", "fallback_after_failure"]


@dataclass(frozen=True, slots=True)
class LatencyFallbackConfig:
    """Explicit policy for future external TTFT calibration failures."""

    on_calibration_failure: CalibrationFailurePolicy = "fail"

    @classmethod
    def from_mapping(cls, data: object | None) -> "LatencyFallbackConfig":
        """Parse the optional latency_fallback config section."""

        if data is None:
            return cls()
        if not isinstance(data, Mapping):
            raise ValueError("latency_fallback config must be a mapping")

        policy = data.get("on_calibration_failure", "fail")
        if policy not in ("fail", "use_model_default"):
            raise ValueError(
                "latency_fallback.on_calibration_failure must be one of: fail, use_model_default"
            )
        return cls(on_calibration_failure=policy)

    @property
    def uses_model_default_on_calibration_failure(self) -> bool:
        return self.on_calibration_failure == "use_model_default"


def build_latency_fallback_config(config: Mapping[str, Any]) -> LatencyFallbackConfig:
    """Build fallback policy from the root experiment config."""

    return LatencyFallbackConfig.from_mapping(config.get("latency_fallback"))
