"""Batch latency schema for Step4 replay."""

from __future__ import annotations

from dataclasses import dataclass, field

from infertwin.scheduler.batch_shape import BatchShape


@dataclass(frozen=True, slots=True)
class ShapeKey:
    """Stable memoization key for an iteration latency query."""

    backend: str
    model_name: str
    hardware_name: str
    batch_size: int
    scheduled_prefill_tokens: int
    scheduled_decode_tokens: int
    max_query_len: int
    total_context_tokens: int

    @classmethod
    def from_shape(
        cls,
        *,
        backend: str,
        model_name: str,
        hardware_name: str,
        shape: BatchShape,
    ) -> ShapeKey:
        return cls(
            backend=backend,
            model_name=model_name,
            hardware_name=hardware_name,
            batch_size=shape.batch_size,
            scheduled_prefill_tokens=shape.scheduled_prefill_tokens,
            scheduled_decode_tokens=shape.scheduled_decode_tokens,
            max_query_len=shape.max_query_len,
            total_context_tokens=shape.total_context_tokens,
        )

    def __str__(self) -> str:
        parts = (
            self.backend,
            self.model_name,
            self.hardware_name,
            f"bs={self.batch_size}",
            f"prefill={self.scheduled_prefill_tokens}",
            f"decode={self.scheduled_decode_tokens}",
            f"maxq={self.max_query_len}",
            f"ctx={self.total_context_tokens}",
        )
        return "|".join(parts)


@dataclass(frozen=True, slots=True)
class LatencyResult:
    """Iteration latency returned by a batch latency backend."""

    duration_ms: float
    backend: str
    shape_key: ShapeKey
    memoized: bool = False
    details: dict[str, float | int | str | bool] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.duration_ms < 0:
            raise ValueError("duration_ms must be non-negative")
