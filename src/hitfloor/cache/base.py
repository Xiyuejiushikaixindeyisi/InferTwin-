"""Shared prefix cache protocol boundaries."""

from __future__ import annotations

from typing import Protocol

from hitfloor.cache.events import CacheEvent
from hitfloor.cache.results import PrefixLookupResult
from hitfloor.request.block_hasher import PrefixBlock


class PrefixCache(Protocol):
    """Protocol consumed by replay engines.

    Implementations are responsible for prefix lookup, finish-time
    materialization, and cache events. Replay code should not depend on the
    concrete cache policy.
    """

    @property
    def resident_blocks(self) -> int: ...

    def contains(self, block_key: str) -> bool: ...

    def lookup_prefix(
        self,
        blocks: tuple[PrefixBlock, ...],
        now_ms: float,
        request_id: str = "",
        instance_uuid: str = "",
    ) -> PrefixLookupResult: ...

    def materialize(
        self,
        blocks: tuple[PrefixBlock, ...],
        now_ms: float,
        request_id: str = "",
        instance_uuid: str = "",
    ) -> None: ...

    def take_events(self) -> tuple[CacheEvent, ...]: ...
