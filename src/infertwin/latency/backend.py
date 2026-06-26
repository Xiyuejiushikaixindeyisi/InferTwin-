"""Batch latency backend protocol."""

from __future__ import annotations

from typing import Protocol

from infertwin.latency.schema import LatencyResult
from infertwin.scheduler.batch_shape import BatchShape


class BatchLatencyBackend(Protocol):
    """Backend that estimates one scheduler iteration duration."""

    name: str
    model_name: str
    hardware_name: str

    def estimate_iteration(self, shape: BatchShape) -> LatencyResult:
        """Estimate duration for one scheduled batch shape."""
