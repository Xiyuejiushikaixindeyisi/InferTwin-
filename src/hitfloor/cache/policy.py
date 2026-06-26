"""Legacy cache policy helpers.

This module is not used by the Step5 `batch_aware_hbm_lru` runner. Current HBM
eviction simulation lives in `hitfloor.cache.eviction` and `hitfloor.cache.hbm_lru`.
Keep this helper only for small standalone experiments unless a future stage
reconnects it deliberately.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Generic, Iterable, TypeVar

K = TypeVar("K")
V = TypeVar("V")


class LRUCache(Generic[K, V]):
    def __init__(self, capacity: int) -> None:
        self.capacity = capacity
        self._items: OrderedDict[K, V] = OrderedDict()

    def get(self, key: K) -> V | None:
        value = self._items.get(key)
        if value is not None:
            self._items.move_to_end(key)
        return value

    def put(self, key: K, value: V) -> list[tuple[K, V]]:
        evicted: list[tuple[K, V]] = []
        if key in self._items:
            self._items.move_to_end(key)
        self._items[key] = value
        while len(self._items) > self.capacity:
            evicted.append(self._items.popitem(last=False))
        return evicted

    def values(self) -> Iterable[V]:
        return self._items.values()
