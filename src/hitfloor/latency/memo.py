"""Memoization for repeated latency shapes."""

from __future__ import annotations

from dataclasses import replace
from typing import Callable

from hitfloor.latency.schema import LatencyResult, ShapeKey


class ShapeMemo:
    """Cache latency results by exact shape key."""

    def __init__(self) -> None:
        self._results: dict[ShapeKey, LatencyResult] = {}

    def __len__(self) -> int:
        return len(self._results)

    def get(self, key: ShapeKey) -> LatencyResult | None:
        result = self._results.get(key)
        if result is None:
            return None
        return replace(result, memoized=True)

    def put(self, result: LatencyResult) -> None:
        self._results[result.shape_key] = replace(result, memoized=False)

    def get_or_compute(
        self,
        key: ShapeKey,
        compute: Callable[[], LatencyResult],
    ) -> LatencyResult:
        cached = self.get(key)
        if cached is not None:
            return cached

        result = compute()
        if result.shape_key != key:
            raise ValueError("computed latency result does not match requested shape key")
        self.put(result)
        return replace(result, memoized=False)
