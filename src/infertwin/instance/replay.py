"""Replay simulation requests against per-instance caches."""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field

from infertwin.cache.cached_token_accounting import account_prefix_lookup
from infertwin.cache.infinite_hbm import InfiniteHBMCache
from infertwin.request.block_hasher import PrefixBlock
from infertwin.instance.request import SimulationRequest


@dataclass(frozen=True, slots=True)
class RequestMetrics:
    request_id: str
    tenant_id: str
    instance_uuid: str
    model: str
    tokenizer_profile: str
    prompt_tokens: int
    prompt_blocks: int
    hbm_hit_tokens: int
    ddr_hit_tokens: int
    miss_tokens: int
    effective_hit_rate: float
    ttft_ms: float
    finish_time_ms: float


@dataclass(order=True, slots=True)
class _MaterializeEvent:
    finish_time_ms: float
    sequence: int
    instance_uuid: str = field(compare=False)
    blocks: tuple[PrefixBlock, ...] = field(compare=False)


class InfiniteHBMReplayEngine:
    def __init__(self, default_ttft_ms: float = 0.0) -> None:
        self.default_ttft_ms = default_ttft_ms
        self.instances: dict[str, InfiniteHBMCache] = {}
        self._events: list[_MaterializeEvent] = []
        self._sequence = 0

    def run(self, requests: list[SimulationRequest]) -> list[RequestMetrics]:
        metrics: list[RequestMetrics] = []
        for request in sorted(requests, key=lambda item: item.service_start_time):
            self._flush_events_until(request.start_time_ms)
            cache = self._cache_for(request.instance_uuid)
            lookup = cache.lookup_prefix(request.prompt_blocks, now_ms=request.start_time_ms)
            accounted = account_prefix_lookup(
                lookup=lookup,
                prompt_tokens=request.prompt_tokens,
                block_conversion=request.block_conversion_result,
            )
            finish_time_ms = request.start_time_ms + self.default_ttft_ms
            self._schedule_materialize(
                instance_uuid=request.instance_uuid,
                blocks=accounted.materialization_blocks,
                finish_time_ms=finish_time_ms,
            )
            metrics.append(
                RequestMetrics(
                    request_id=request.request_id,
                    tenant_id=request.tenant_id,
                    instance_uuid=request.instance_uuid,
                    model=request.model,
                    tokenizer_profile=request.tokenizer_profile,
                    prompt_tokens=request.prompt_tokens,
                    prompt_blocks=len(request.prompt_blocks),
                    hbm_hit_tokens=accounted.hbm_hit_tokens,
                    ddr_hit_tokens=accounted.ddr_hit_tokens,
                    miss_tokens=accounted.miss_tokens,
                    effective_hit_rate=_safe_rate(
                        accounted.effective_hit_tokens,
                        request.prompt_tokens,
                    ),
                    ttft_ms=self.default_ttft_ms,
                    finish_time_ms=finish_time_ms,
                )
            )

        self._flush_events_until(float("inf"))
        return metrics

    def _cache_for(self, instance_uuid: str) -> InfiniteHBMCache:
        cache = self.instances.get(instance_uuid)
        if cache is None:
            cache = InfiniteHBMCache()
            self.instances[instance_uuid] = cache
        return cache

    def _schedule_materialize(
        self,
        instance_uuid: str,
        blocks: tuple[PrefixBlock, ...],
        finish_time_ms: float,
    ) -> None:
        if not blocks:
            return
        self._sequence += 1
        heapq.heappush(
            self._events,
            _MaterializeEvent(
                finish_time_ms=finish_time_ms,
                sequence=self._sequence,
                instance_uuid=instance_uuid,
                blocks=blocks,
            ),
        )

    def _flush_events_until(self, now_ms: float) -> None:
        while self._events and self._events[0].finish_time_ms <= now_ms:
            event = heapq.heappop(self._events)
            self._cache_for(event.instance_uuid).materialize(
                event.blocks,
                now_ms=event.finish_time_ms,
            )


def _safe_rate(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return numerator / denominator
