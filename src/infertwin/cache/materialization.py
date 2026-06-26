"""Cache materialization policies for replay engines."""

from __future__ import annotations

from typing import Protocol

from infertwin.cache.base import PrefixCache
from infertwin.request.block_hasher import PrefixBlock


class MaterializationPolicy(Protocol):
    """Decide when computed miss blocks become visible to prefix cache lookup."""

    name: str

    def materialize_finished_request(
        self,
        *,
        cache: PrefixCache,
        blocks: tuple[PrefixBlock, ...],
        finish_time_ms: float,
        request_id: str,
        instance_uuid: str,
    ) -> None:
        """Materialize blocks for a request whose prefill has finished."""


class FinishTimeMaterializationPolicy:
    """Materialize all miss blocks only after request prefill finish time."""

    name = "finish_time"

    def materialize_finished_request(
        self,
        *,
        cache: PrefixCache,
        blocks: tuple[PrefixBlock, ...],
        finish_time_ms: float,
        request_id: str,
        instance_uuid: str,
    ) -> None:
        cache.materialize(
            blocks,
            now_ms=finish_time_ms,
            request_id=request_id,
            instance_uuid=instance_uuid,
        )
