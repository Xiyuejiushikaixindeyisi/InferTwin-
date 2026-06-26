"""Serving latency profile composition.

This module keeps replay-facing latency estimation behind the existing
BatchLatencyBackend contract.  The default profile composes fitted TTFT with
zero-valued queue and KV-load components; TPOT/decode remains explicit metadata
until InferTwin grows a decode-aware replay mode.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from infertwin.latency.backend import BatchLatencyBackend
from infertwin.latency.schema import LatencyResult, ShapeKey
from infertwin.scheduler.batch_shape import BatchShape

DetailValue = float | int | str | bool


@dataclass(frozen=True, slots=True)
class LatencyComponentResult:
    """Duration returned by one optional serving-latency component."""

    name: str
    duration_ms: float
    modeled: bool
    details: dict[str, DetailValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("component name must be non-empty")
        if self.duration_ms < 0:
            raise ValueError("component duration_ms must be non-negative")


class IterationLatencyComponent(Protocol):
    """Optional component that can contribute to one replay iteration duration."""

    name: str

    def estimate_iteration(self, shape: BatchShape) -> LatencyComponentResult:
        """Estimate component duration for one scheduled batch shape."""


@dataclass(frozen=True, slots=True)
class ZeroLatencyComponent:
    """Component placeholder for latency dimensions not modeled in this replay mode."""

    name: str
    reason: str

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("component name must be non-empty")
        if not self.reason:
            raise ValueError("component reason must be non-empty")

    def estimate_iteration(self, shape: BatchShape) -> LatencyComponentResult:
        return LatencyComponentResult(
            name=self.name,
            duration_ms=0.0,
            modeled=False,
            details={"reason": self.reason},
        )


@dataclass(frozen=True, slots=True)
class StaticLatencyComponent:
    """Deterministic component for tests and fixed calibration constants."""

    name: str
    duration_ms: float
    source: str = "static"

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("component name must be non-empty")
        if self.duration_ms < 0:
            raise ValueError("duration_ms must be non-negative")
        if not self.source:
            raise ValueError("source must be non-empty")

    def estimate_iteration(self, shape: BatchShape) -> LatencyComponentResult:
        return LatencyComponentResult(
            name=self.name,
            duration_ms=self.duration_ms,
            modeled=True,
            details={"source": self.source},
        )


def _zero_kv_load_component() -> ZeroLatencyComponent:
    return ZeroLatencyComponent(
        name="kv_load",
        reason="kv_load_latency_not_modeled_in_current_hbm_only_replay",
    )


def _zero_queue_component() -> ZeroLatencyComponent:
    return ZeroLatencyComponent(
        name="queue",
        reason="machine_side_queue_not_modeled_in_current_replay",
    )


@dataclass(frozen=True, slots=True)
class ServingLatencyProfile:
    """Compose serving latency dimensions for batch-aware replay.

    Current replay duration is:

    queue_ms + ttft_ms + kv_load_ms

    Decode and TPOT are recorded as ``not_modeled`` metadata because the current
    scheduler only replays prefill iterations.
    """

    profile: str
    ttft_backend: BatchLatencyBackend
    kv_load_component: IterationLatencyComponent = field(default_factory=_zero_kv_load_component)
    queue_component: IterationLatencyComponent = field(default_factory=_zero_queue_component)
    calibrated_from: str = "manual_default"
    calibration_window_requests: int = 500
    decode_mode: str = "not_modeled_in_current_replay"

    name: str = "serving_latency_profile"

    def __post_init__(self) -> None:
        if not self.profile:
            raise ValueError("profile must be non-empty")
        if not self.calibrated_from:
            raise ValueError("calibrated_from must be non-empty")
        if self.calibration_window_requests <= 0:
            raise ValueError("calibration_window_requests must be positive")
        if self.decode_mode != "not_modeled_in_current_replay":
            raise ValueError("ServingLatencyProfile only supports not-modeled decode mode")

    @property
    def model_name(self) -> str:
        return self.ttft_backend.model_name

    @property
    def hardware_name(self) -> str:
        return self.ttft_backend.hardware_name

    def estimate_iteration(self, shape: BatchShape) -> LatencyResult:
        ttft = self.ttft_backend.estimate_iteration(shape)
        queue = self.queue_component.estimate_iteration(shape)
        kv_load = self.kv_load_component.estimate_iteration(shape)
        duration_ms = queue.duration_ms + ttft.duration_ms + kv_load.duration_ms
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
                "calibrated_from": self.calibrated_from,
                "calibration_window_requests": self.calibration_window_requests,
                "ttft_backend": ttft.backend,
                "ttft_ms": ttft.duration_ms,
                "queue_component": queue.name,
                "queue_ms": queue.duration_ms,
                "queue_modeled": queue.modeled,
                "kv_load_component": kv_load.name,
                "kv_load_ms": kv_load.duration_ms,
                "kv_load_modeled": kv_load.modeled,
                "decode_mode": self.decode_mode,
                "tpot_mode": "not_modeled_in_current_replay",
                **_prefixed_details("ttft", ttft.details),
                **_prefixed_details("queue", queue.details),
                **_prefixed_details("kv_load", kv_load.details),
            },
        )


def _prefixed_details(prefix: str, details: dict[str, DetailValue]) -> dict[str, DetailValue]:
    return {f"{prefix}_{key}": value for key, value in details.items()}
