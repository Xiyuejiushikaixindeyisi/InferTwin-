"""Metrics emitted by batch-aware replay."""

from __future__ import annotations

from dataclasses import dataclass, field

from hitfloor.cache.event_sink import CacheEventStats
from hitfloor.cache.events import CacheEvent
from hitfloor.cache.results import PrefixLookupResult
from hitfloor.instance.request import SimulationRequest
from hitfloor.latency.schema import LatencyResult
from hitfloor.request.block_hasher import PrefixBlock
from hitfloor.scheduler.batch_shape import BatchShape
from hitfloor.scheduler.state import RequestState


@dataclass(frozen=True, slots=True)
class LookupMetrics:
    hbm_hit_tokens: int = 0
    ddr_hit_tokens: int = 0
    miss_tokens: int = 0
    miss_blocks: tuple[PrefixBlock, ...] = ()

    @classmethod
    def from_result(cls, lookup: PrefixLookupResult) -> LookupMetrics:
        return cls(
            hbm_hit_tokens=lookup.hbm_hit_tokens,
            ddr_hit_tokens=lookup.ddr_hit_tokens,
            miss_tokens=lookup.miss_tokens,
            miss_blocks=lookup.miss_blocks,
        )


@dataclass(frozen=True, slots=True)
class BatchAwareRequestMetrics:
    request_id: str
    tenant_id: str
    instance_uuid: str
    model: str
    tokenizer_profile: str
    arrival_time_ms: float
    first_scheduled_time_ms: float
    finish_time_ms: float
    scheduler_wait_ms: float
    ttft_ms: float
    prompt_tokens: int
    prompt_blocks: int
    hbm_hit_tokens: int
    ddr_hit_tokens: int
    miss_tokens: int
    effective_hit_rate: float
    scheduled_iteration_count: int


@dataclass(frozen=True, slots=True)
class IterationMetrics:
    instance_uuid: str
    iteration_id: int
    start_time_ms: float
    finish_time_ms: float
    duration_ms: float
    batch_size: int
    scheduled_prefill_tokens: int
    scheduled_decode_tokens: int
    max_query_len: int
    total_context_tokens: int
    backend: str
    shape_key: str
    memoized: bool
    request_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class BatchAwareReplayResult:
    request_metrics: tuple[BatchAwareRequestMetrics, ...]
    iteration_metrics: tuple[IterationMetrics, ...]
    cache_event_stats: CacheEventStats = field(default_factory=CacheEventStats)
    cache_events: tuple[CacheEvent, ...] = ()


def build_request_metrics(
    *,
    request: SimulationRequest,
    state: RequestState,
    lookup: LookupMetrics,
) -> BatchAwareRequestMetrics:
    first_scheduled_time_ms = _required_time(
        state.first_scheduled_time_ms,
        field_name="first_scheduled_time_ms",
        request_id=state.request_id,
    )
    finish_time_ms = _required_time(
        state.finish_time_ms,
        field_name="finish_time_ms",
        request_id=state.request_id,
    )
    return BatchAwareRequestMetrics(
        request_id=request.request_id,
        tenant_id=request.tenant_id,
        instance_uuid=request.instance_uuid,
        model=request.model,
        tokenizer_profile=request.tokenizer_profile,
        arrival_time_ms=request.start_time_ms,
        first_scheduled_time_ms=first_scheduled_time_ms,
        finish_time_ms=finish_time_ms,
        scheduler_wait_ms=first_scheduled_time_ms - request.start_time_ms,
        ttft_ms=finish_time_ms - request.start_time_ms,
        prompt_tokens=request.prompt_tokens,
        prompt_blocks=len(request.prompt_blocks),
        hbm_hit_tokens=lookup.hbm_hit_tokens,
        ddr_hit_tokens=lookup.ddr_hit_tokens,
        miss_tokens=lookup.miss_tokens,
        effective_hit_rate=_safe_rate(
            lookup.hbm_hit_tokens + lookup.ddr_hit_tokens,
            request.prompt_tokens,
        ),
        scheduled_iteration_count=state.scheduled_iteration_count,
    )


def build_iteration_metrics(
    *,
    shape: BatchShape,
    latency: LatencyResult,
    finish_time_ms: float,
) -> IterationMetrics:
    return IterationMetrics(
        instance_uuid=shape.instance_uuid,
        iteration_id=shape.iteration_id,
        start_time_ms=shape.start_time_ms,
        finish_time_ms=finish_time_ms,
        duration_ms=latency.duration_ms,
        batch_size=shape.batch_size,
        scheduled_prefill_tokens=shape.scheduled_prefill_tokens,
        scheduled_decode_tokens=shape.scheduled_decode_tokens,
        max_query_len=shape.max_query_len,
        total_context_tokens=shape.total_context_tokens,
        backend=latency.backend,
        shape_key=str(latency.shape_key),
        memoized=latency.memoized,
        request_ids=tuple(item.request_id for item in shape.request_slices),
    )


def _required_time(value: float | None, *, field_name: str, request_id: str) -> float:
    if value is None:
        raise ValueError(f"{field_name} is missing for finished request {request_id}")
    return value


def _safe_rate(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return numerator / denominator
