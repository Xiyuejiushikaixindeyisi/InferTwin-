"""Formula-based latency backend for the first MVP."""

from __future__ import annotations

from dataclasses import dataclass

from hitfloor.latency.base import (
    KVRestoreEstimateInput,
    LatencyEstimate,
    PrefillEstimateInput,
)
from hitfloor.latency.schema import LatencyResult, ShapeKey
from hitfloor.scheduler.batch_shape import BatchShape


@dataclass(frozen=True, slots=True)
class FormulaLatencyBackend:
    prefill_base_ms: float = 2.0
    prefill_ms_per_uncached_token: float = 0.02
    hbm_restore_ms_per_token: float = 0.001
    ddr_restore_ms_per_token: float = 0.006
    iteration_fixed_overhead_ms: float = 0.2
    iteration_prefill_token_ms: float = 0.01
    iteration_batch_overhead_ms: float = 0.03
    iteration_context_token_ms: float = 0.0
    model_name: str = "unknown"
    hardware_name: str = "unknown"

    name: str = "formula"

    def __post_init__(self) -> None:
        coefficients = {
            "prefill_base_ms": self.prefill_base_ms,
            "prefill_ms_per_uncached_token": self.prefill_ms_per_uncached_token,
            "hbm_restore_ms_per_token": self.hbm_restore_ms_per_token,
            "ddr_restore_ms_per_token": self.ddr_restore_ms_per_token,
            "iteration_fixed_overhead_ms": self.iteration_fixed_overhead_ms,
            "iteration_prefill_token_ms": self.iteration_prefill_token_ms,
            "iteration_batch_overhead_ms": self.iteration_batch_overhead_ms,
            "iteration_context_token_ms": self.iteration_context_token_ms,
        }
        for name, value in coefficients.items():
            if value < 0:
                raise ValueError(f"{name} must be non-negative")

    def estimate_prefill(self, request: PrefillEstimateInput) -> LatencyEstimate:
        milliseconds = (
            self.prefill_base_ms
            + request.uncached_suffix_tokens * self.prefill_ms_per_uncached_token
        )
        return LatencyEstimate(
            milliseconds=milliseconds,
            backend=self.name,
            details={
                "cached_prefix_tokens": request.cached_prefix_tokens,
                "uncached_suffix_tokens": request.uncached_suffix_tokens,
            },
        )

    def estimate_kv_restore(self, request: KVRestoreEstimateInput) -> LatencyEstimate:
        hbm_ms = request.hbm_hit_tokens * self.hbm_restore_ms_per_token
        ddr_ms = request.ddr_hit_tokens * self.ddr_restore_ms_per_token
        return LatencyEstimate(
            milliseconds=hbm_ms + ddr_ms,
            backend=self.name,
            details={
                "hbm_hit_tokens": request.hbm_hit_tokens,
                "ddr_hit_tokens": request.ddr_hit_tokens,
                "hbm_ms": hbm_ms,
                "ddr_ms": ddr_ms,
            },
        )

    def estimate_iteration(self, shape: BatchShape) -> LatencyResult:
        duration_ms = (
            self.iteration_fixed_overhead_ms
            + shape.scheduled_prefill_tokens * self.iteration_prefill_token_ms
            + shape.batch_size * self.iteration_batch_overhead_ms
            + shape.total_context_tokens * self.iteration_context_token_ms
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
                "batch_size": shape.batch_size,
                "scheduled_prefill_tokens": shape.scheduled_prefill_tokens,
                "scheduled_decode_tokens": shape.scheduled_decode_tokens,
                "max_query_len": shape.max_query_len,
                "total_context_tokens": shape.total_context_tokens,
            },
        )
