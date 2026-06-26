"""Cache capacity search helpers."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CacheCapacityPoint:
    hbm_gb: float
    ddr_gb: float


def grid_capacity_points(
    hbm_values: list[float], ddr_values: list[float]
) -> list[CacheCapacityPoint]:
    return [CacheCapacityPoint(hbm, ddr) for hbm in hbm_values for ddr in ddr_values]
