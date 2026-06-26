"""Latency backend interfaces."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class PrefillEstimateInput:
    cached_prefix_tokens: int
    uncached_suffix_tokens: int
    batch_request_count: int = 1


@dataclass(frozen=True, slots=True)
class KVRestoreEstimateInput:
    hbm_hit_tokens: int
    ddr_hit_tokens: int


@dataclass(frozen=True, slots=True)
class LatencyEstimate:
    milliseconds: float
    backend: str
    details: dict[str, float | int | str]


class LatencyBackend(Protocol):
    name: str

    def estimate_prefill(self, request: PrefillEstimateInput) -> LatencyEstimate:
        """Estimate prefill compute latency."""

    def estimate_kv_restore(self, request: KVRestoreEstimateInput) -> LatencyEstimate:
        """Estimate KV restore latency."""
