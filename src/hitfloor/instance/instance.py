"""Model instance simulation boundary."""

from __future__ import annotations

from dataclasses import dataclass

from hitfloor.cache.simulator import KVCacheSimulator


@dataclass(slots=True)
class SimulatedInstance:
    instance_uuid: str
    cache: KVCacheSimulator
