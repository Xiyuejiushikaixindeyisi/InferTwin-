"""Metrics emitted by batch-aware replay."""

from __future__ import annotations

from dataclasses import dataclass, field

from infertwin.cache.cached_token_accounting import account_prefix_lookup
from infertwin.cache.event_sink import CacheEventStats
from infertwin.cache.events import CacheEvent
from infertwin.cache.results import PrefixLookupResult
from infertwin.instance.request import SimulationRequest
from infertwin.latency.schema import LatencyResult
from infertwin.request.block_hasher import PrefixBlock
from infertwin.replay.timeline import (
    CHUNK_TTFT_GRANULARITY,
    ITERATION_TTFT_GRANULARITY,
    LEGACY_TIMELINE_MODE,
    PROGRESSIVE_TIMELINE_MODE,
)
from infertwin.replay.ttft import RequestTTFTComposer
from infertwin.scheduler.batch_shape import BatchShape
from infertwin.scheduler.state import RequestState


@dataclass(frozen=True, slots=True)
class LookupMetrics:
    hbm_hit_tokens: int = 0
    ddr_hit_tokens: int = 0
    ddr_hit_bytes: int = 0
    miss_tokens: int = 0
    miss_blocks: tuple[PrefixBlock, ...] = ()
    materialization_blocks: tuple[PrefixBlock, ...] = ()
    raw_hbm_hit_tokens: int = 0
    raw_ddr_hit_tokens: int = 0
    raw_miss_tokens: int = 0
    cached_token_cap: int = 0

    @classmethod
    def from_result(
        cls,
        lookup: PrefixLookupResult,
        *,
        request: SimulationRequest,
    ) -> LookupMetrics:
        accounted = account_prefix_lookup(
            lookup=lookup,
            prompt_tokens=request.prompt_tokens,
            block_conversion=request.block_conversion_result,
        )
        return cls(
            hbm_hit_tokens=accounted.hbm_hit_tokens,
            ddr_hit_tokens=accounted.ddr_hit_tokens,
            ddr_hit_bytes=sum(block.size_bytes for block in accounted.ddr_hit_blocks),
            miss_tokens=accounted.miss_tokens,
            miss_blocks=accounted.materialization_blocks,
            materialization_blocks=accounted.materialization_blocks,
            raw_hbm_hit_tokens=accounted.raw_hbm_hit_tokens,
            raw_ddr_hit_tokens=accounted.raw_ddr_hit_tokens,
            raw_miss_tokens=accounted.raw_miss_tokens,
            cached_token_cap=accounted.cached_token_cap,
        )

    @property
    def effective_hit_tokens(self) -> int:
        return self.hbm_hit_tokens + self.ddr_hit_tokens


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
    kv_load_tokens: int = 0
    kv_load_bytes: int = 0
    kv_load_ms: float = 0.0
    prefill_compute_ms: float = 0.0
    queue_ms: float = 0.0
    timeline_mode: str = LEGACY_TIMELINE_MODE
    ttft_granularity: str = ITERATION_TTFT_GRANULARITY
    compute_wait_ms: float = 0.0
    kv_load_wait_ms: float = 0.0
    uncached_prefill_compute_ms: float = 0.0
    unattributed_ttft_ms: float = 0.0
    chunk_count: int = 0
    load_event_count: int = 0
    progressive_materialized_blocks: int = 0
    progressive_materialized_tokens: int = 0


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
    kv_load_tokens: int = 0
    kv_load_bytes: int = 0
    kv_load_request_count: int = 0
    kv_load_ms: float = 0.0
    prefill_compute_ms: float = 0.0
    queue_ms: float = 0.0
    timeline_mode: str = LEGACY_TIMELINE_MODE
    ttft_granularity: str = ITERATION_TTFT_GRANULARITY
    waiting_for_compute_count: int = 0
    waiting_for_kv_load_count: int = 0
    scheduled_chunk_count: int = 0
    kv_transfer_queue_depth_max: int = 0
    compute_wait_ms: float = 0.0
    kv_load_wait_ms: float = 0.0
    unattributed_ttft_ms: float = 0.0
    progressive_materialized_blocks: int = 0
    progressive_materialized_tokens: int = 0


@dataclass(frozen=True, slots=True)
class IterationLatencyBreakdown:
    """Latency components attached to one replay iteration or slice."""

    prefill_compute_ms: float = 0.0
    kv_load_ms: float = 0.0
    queue_ms: float = 0.0

    def __post_init__(self) -> None:
        if self.prefill_compute_ms < 0 or self.kv_load_ms < 0 or self.queue_ms < 0:
            raise ValueError("latency breakdown values must be non-negative")


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
    composition = RequestTTFTComposer().compose(
        request=request,
        state=state,
        finish_time_ms=finish_time_ms,
        first_scheduled_time_ms=first_scheduled_time_ms,
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
        scheduler_wait_ms=composition.scheduler_wait_ms,
        ttft_ms=composition.ttft_ms,
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
        kv_load_tokens=lookup.ddr_hit_tokens,
        kv_load_bytes=lookup.ddr_hit_bytes,
        kv_load_ms=state.kv_load_ms,
        prefill_compute_ms=state.prefill_compute_ms,
        queue_ms=state.queue_ms,
        timeline_mode=composition.timeline_mode,
        ttft_granularity=composition.ttft_granularity,
        compute_wait_ms=composition.compute_wait_ms,
        kv_load_wait_ms=composition.kv_load_wait_ms,
        uncached_prefill_compute_ms=composition.uncached_prefill_compute_ms,
        unattributed_ttft_ms=composition.unattributed_ttft_ms,
        chunk_count=composition.chunk_count,
        load_event_count=composition.load_event_count,
        progressive_materialized_blocks=state.progressive_materialized_blocks,
        progressive_materialized_tokens=state.progressive_materialized_tokens,
    )


def build_iteration_metrics(
    *,
    shape: BatchShape,
    latency: LatencyResult,
    finish_time_ms: float,
    timeline_mode: str = LEGACY_TIMELINE_MODE,
    waiting_for_compute_count: int = 0,
    waiting_for_kv_load_count: int = 0,
    compute_wait_ms: float = 0.0,
    kv_load_wait_ms: float = 0.0,
    kv_transfer_queue_depth_max: int = 0,
    progressive_materialized_blocks: int = 0,
    progressive_materialized_tokens: int = 0,
) -> IterationMetrics:
    breakdown = latency_breakdown_from_result(latency)
    scheduled_chunk_count = 0
    ttft_granularity = ITERATION_TTFT_GRANULARITY
    if timeline_mode == PROGRESSIVE_TIMELINE_MODE:
        scheduled_chunk_count = sum(
            1 for item in shape.request_slices if item.scheduled_prefill_tokens > 0
        )
        ttft_granularity = CHUNK_TTFT_GRANULARITY
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
        kv_load_tokens=shape.kv_load_tokens,
        kv_load_bytes=shape.kv_load_bytes,
        kv_load_request_count=shape.kv_load_request_count,
        kv_load_ms=breakdown.kv_load_ms,
        prefill_compute_ms=breakdown.prefill_compute_ms,
        queue_ms=breakdown.queue_ms,
        timeline_mode=timeline_mode,
        ttft_granularity=ttft_granularity,
        waiting_for_compute_count=waiting_for_compute_count,
        waiting_for_kv_load_count=waiting_for_kv_load_count,
        scheduled_chunk_count=scheduled_chunk_count,
        kv_transfer_queue_depth_max=kv_transfer_queue_depth_max,
        compute_wait_ms=compute_wait_ms,
        kv_load_wait_ms=kv_load_wait_ms,
        progressive_materialized_blocks=progressive_materialized_blocks,
        progressive_materialized_tokens=progressive_materialized_tokens,
    )


def latency_breakdown_from_result(latency: LatencyResult) -> IterationLatencyBreakdown:
    """Extract stable latency components from a backend result.

    Legacy backends expose only duration, so their full duration is treated as
    prefill compute.  ServingLatencyProfile exposes explicit component details.
    """

    return IterationLatencyBreakdown(
        prefill_compute_ms=_detail_float(
            latency,
            "ttft_ms",
            default=latency.duration_ms,
        ),
        kv_load_ms=_detail_float(latency, "kv_load_ms", default=0.0),
        queue_ms=_detail_float(latency, "queue_ms", default=0.0),
    )


def split_iteration_latency_contributions(
    *,
    shape: BatchShape,
    latency: LatencyResult,
) -> dict[str, IterationLatencyBreakdown]:
    """Split iteration latency components into request-level attributions."""

    breakdown = latency_breakdown_from_result(latency)
    total_prefill_tokens = shape.scheduled_prefill_tokens
    total_kv_load_bytes = shape.kv_load_bytes
    total_kv_load_tokens = shape.kv_load_tokens
    batch_size = shape.batch_size
    contributions: dict[str, IterationLatencyBreakdown] = {}

    for request_slice in shape.request_slices:
        prefill_ms = 0.0
        if total_prefill_tokens > 0 and request_slice.scheduled_prefill_tokens > 0:
            prefill_ms = (
                breakdown.prefill_compute_ms
                * request_slice.scheduled_prefill_tokens
                / total_prefill_tokens
            )

        kv_load_ms = 0.0
        if total_kv_load_bytes > 0 and request_slice.kv_load_bytes > 0:
            kv_load_ms = breakdown.kv_load_ms * request_slice.kv_load_bytes / total_kv_load_bytes
        elif total_kv_load_tokens > 0 and request_slice.kv_load_tokens > 0:
            kv_load_ms = breakdown.kv_load_ms * request_slice.kv_load_tokens / total_kv_load_tokens

        queue_ms = breakdown.queue_ms / batch_size if batch_size > 0 else 0.0
        contributions[request_slice.request_id] = IterationLatencyBreakdown(
            prefill_compute_ms=prefill_ms,
            kv_load_ms=kv_load_ms,
            queue_ms=queue_ms,
        )

    return contributions


def _required_time(value: float | None, *, field_name: str, request_id: str) -> float:
    if value is None:
        raise ValueError(f"{field_name} is missing for finished request {request_id}")
    return value


def _safe_rate(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return numerator / denominator


def _detail_float(latency: LatencyResult, key: str, *, default: float) -> float:
    value = latency.details.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"latency detail {key!r} must be numeric")
    if value < 0:
        raise ValueError(f"latency detail {key!r} must be non-negative")
    return float(value)
