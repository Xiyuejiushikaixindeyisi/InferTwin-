import pytest

from infertwin.cache.event_sink import CacheEventStats
from infertwin.experiment.sweep import build_capacity_rows
from infertwin.replay.metrics import BatchAwareRequestMetrics, IterationMetrics
from infertwin.replay.timeline import (
    CHUNK_TTFT_GRANULARITY,
    LEGACY_TIMELINE_MODE,
    PROGRESSIVE_TIMELINE_MODE,
)
from infertwin.streaming.metrics import CapacitySweepStreamingMetricAggregator


def test_streaming_capacity_metric_aggregator_matches_batch_rows() -> None:
    request_metrics = (
        _request_metric(
            "r1",
            "instance-b",
            prompt_tokens=10,
            hbm_hit_tokens=4,
            ttft_ms=30.0,
            kv_load_ms=3.0,
            compute_wait_ms=2.0,
            kv_load_wait_ms=1.0,
            uncached_prefill_compute_ms=20.0,
            chunk_count=2,
            progressive_materialized_tokens=4,
        ),
        _request_metric(
            "r2",
            "instance-a",
            prompt_tokens=10,
            hbm_hit_tokens=10,
            ttft_ms=10.0,
            kv_load_ms=0.0,
        ),
        _request_metric(
            "r3",
            "instance-a",
            prompt_tokens=10,
            hbm_hit_tokens=0,
            ttft_ms=20.0,
            kv_load_ms=2.0,
            compute_wait_ms=4.0,
            kv_load_wait_ms=2.0,
            uncached_prefill_compute_ms=6.0,
            chunk_count=2,
            progressive_materialized_tokens=8,
        ),
    )
    iteration_metrics = (
        _iteration_metric("instance-a", 0, waiting_for_compute_count=1),
        _iteration_metric("instance-b", 0, waiting_for_kv_load_count=1),
        _iteration_metric("instance-a", 1, kv_transfer_queue_depth_max=2),
    )
    cache_event_stats = CacheEventStats(total_events=12)

    aggregator = CapacitySweepStreamingMetricAggregator()
    for metric in iteration_metrics[:1]:
        aggregator.on_iteration(metric)
    for metric in request_metrics[:2]:
        aggregator.on_request(metric)
    for metric in iteration_metrics[1:]:
        aggregator.on_iteration(metric)
    for metric in request_metrics[2:]:
        aggregator.on_request(metric)

    streaming_rows = aggregator.build_rows(capacity=8, cache_event_stats=cache_event_stats)
    batch_rows = build_capacity_rows(
        capacity=8,
        request_metrics=request_metrics,
        iteration_metrics=iteration_metrics,
        cache_event_stats=cache_event_stats,
    )

    assert streaming_rows == batch_rows
    assert streaming_rows[0].total_kv_load_ms == 5.0
    assert streaming_rows[0].avg_kv_load_ms == pytest.approx(5.0 / 3)
    assert streaming_rows[0].p90_kv_load_ms == 3.0
    assert streaming_rows[0].timeline_mode == LEGACY_TIMELINE_MODE
    assert streaming_rows[0].total_compute_wait_ms == 6.0
    assert streaming_rows[0].p90_compute_wait_ms == 4.0
    assert streaming_rows[0].total_kv_load_wait_ms == 3.0
    assert streaming_rows[0].p90_kv_load_wait_ms == 2.0
    assert streaming_rows[0].total_uncached_prefill_compute_ms == 26.0
    assert streaming_rows[0].total_chunk_count == 4
    assert streaming_rows[0].total_progressive_materialized_tokens == 12
    assert streaming_rows[0].total_waiting_for_compute_count == 1
    assert streaming_rows[0].total_waiting_for_kv_load_count == 1
    assert streaming_rows[0].total_scheduled_chunk_count == 3
    assert streaming_rows[0].max_kv_transfer_queue_depth == 2
    assert aggregator.request_count == 3
    assert aggregator.iteration_count == 3
    assert request_metrics[0].timeline_mode == LEGACY_TIMELINE_MODE


def test_streaming_capacity_metric_aggregator_preserves_progressive_timeline() -> None:
    aggregator = CapacitySweepStreamingMetricAggregator(
        timeline_mode=PROGRESSIVE_TIMELINE_MODE,
        ttft_granularity=CHUNK_TTFT_GRANULARITY,
    )

    aggregator.on_request(
        _request_metric(
            "r1",
            "instance-a",
            prompt_tokens=10,
            hbm_hit_tokens=4,
            ttft_ms=30.0,
            timeline_mode=PROGRESSIVE_TIMELINE_MODE,
            ttft_granularity=CHUNK_TTFT_GRANULARITY,
            compute_wait_ms=3.0,
            kv_load_wait_ms=2.0,
            uncached_prefill_compute_ms=15.0,
            chunk_count=3,
            load_event_count=1,
            progressive_materialized_tokens=8,
        )
    )
    aggregator.on_iteration(
        _iteration_metric(
            "instance-a",
            0,
            timeline_mode=PROGRESSIVE_TIMELINE_MODE,
            ttft_granularity=CHUNK_TTFT_GRANULARITY,
            waiting_for_compute_count=2,
            waiting_for_kv_load_count=1,
            scheduled_chunk_count=3,
            kv_transfer_queue_depth_max=4,
        )
    )

    rows = aggregator.build_rows(capacity=4, cache_event_stats=CacheEventStats())

    trace_row = rows[0]
    assert trace_row.timeline_mode == PROGRESSIVE_TIMELINE_MODE
    assert trace_row.ttft_granularity == CHUNK_TTFT_GRANULARITY
    assert trace_row.total_compute_wait_ms == 3.0
    assert trace_row.total_kv_load_wait_ms == 2.0
    assert trace_row.total_chunk_count == 3
    assert trace_row.total_load_event_count == 1
    assert trace_row.total_progressive_materialized_tokens == 8
    assert trace_row.total_waiting_for_compute_count == 2
    assert trace_row.total_waiting_for_kv_load_count == 1
    assert trace_row.total_scheduled_chunk_count == 3
    assert trace_row.max_kv_transfer_queue_depth == 4


def test_streaming_capacity_metric_aggregator_rejects_mixed_timeline() -> None:
    aggregator = CapacitySweepStreamingMetricAggregator(
        timeline_mode=PROGRESSIVE_TIMELINE_MODE,
        ttft_granularity=CHUNK_TTFT_GRANULARITY,
    )

    with pytest.raises(ValueError, match="timeline_mode invariant"):
        aggregator.on_request(
            _request_metric(
                "r1",
                "instance-a",
                prompt_tokens=10,
                hbm_hit_tokens=4,
                ttft_ms=30.0,
            )
        )


def test_streaming_capacity_metric_aggregator_emits_empty_trace_row() -> None:
    rows = CapacitySweepStreamingMetricAggregator().build_rows(
        capacity=4,
        cache_event_stats=CacheEventStats(total_events=7),
    )

    assert len(rows) == 1
    assert rows[0].scope == "trace"
    assert rows[0].request_count == 0
    assert rows[0].iteration_count == 0
    assert rows[0].kv_hit_rate == 0.0
    assert rows[0].p90_ttft_ms == 0.0
    assert rows[0].total_kv_load_ms == 0.0
    assert rows[0].p90_kv_load_ms == 0.0
    assert rows[0].timeline_mode == LEGACY_TIMELINE_MODE
    assert rows[0].total_compute_wait_ms == 0.0
    assert rows[0].total_chunk_count == 0
    assert rows[0].cache_event_count == 7


def test_streaming_capacity_metric_aggregator_rejects_invalid_token_accounting() -> None:
    aggregator = CapacitySweepStreamingMetricAggregator()
    invalid = _request_metric(
        "r1",
        "instance-a",
        prompt_tokens=10,
        hbm_hit_tokens=4,
        ttft_ms=1.0,
    )
    invalid = BatchAwareRequestMetrics(
        request_id=invalid.request_id,
        tenant_id=invalid.tenant_id,
        instance_uuid=invalid.instance_uuid,
        model=invalid.model,
        tokenizer_profile=invalid.tokenizer_profile,
        arrival_time_ms=invalid.arrival_time_ms,
        first_scheduled_time_ms=invalid.first_scheduled_time_ms,
        finish_time_ms=invalid.finish_time_ms,
        scheduler_wait_ms=invalid.scheduler_wait_ms,
        ttft_ms=invalid.ttft_ms,
        prompt_tokens=invalid.prompt_tokens,
        prompt_blocks=invalid.prompt_blocks,
        hbm_hit_tokens=invalid.hbm_hit_tokens,
        ddr_hit_tokens=invalid.ddr_hit_tokens,
        miss_tokens=99,
        effective_hit_rate=invalid.effective_hit_rate,
        scheduled_iteration_count=invalid.scheduled_iteration_count,
    )

    with pytest.raises(ValueError, match="token invariant"):
        aggregator.on_request(invalid)


def _request_metric(
    request_id: str,
    instance_uuid: str,
    *,
    prompt_tokens: int,
    hbm_hit_tokens: int,
    ttft_ms: float,
    kv_load_ms: float = 0.0,
    timeline_mode: str = LEGACY_TIMELINE_MODE,
    ttft_granularity: str = "iteration",
    compute_wait_ms: float = 0.0,
    kv_load_wait_ms: float = 0.0,
    uncached_prefill_compute_ms: float = 0.0,
    chunk_count: int = 0,
    load_event_count: int = 0,
    progressive_materialized_tokens: int = 0,
) -> BatchAwareRequestMetrics:
    return BatchAwareRequestMetrics(
        request_id=request_id,
        tenant_id="tenant-a",
        instance_uuid=instance_uuid,
        model="glm-v5",
        tokenizer_profile="glm-v5",
        arrival_time_ms=0.0,
        first_scheduled_time_ms=0.0,
        finish_time_ms=ttft_ms,
        scheduler_wait_ms=0.0,
        ttft_ms=ttft_ms,
        prompt_tokens=prompt_tokens,
        prompt_blocks=1,
        hbm_hit_tokens=hbm_hit_tokens,
        ddr_hit_tokens=0,
        miss_tokens=prompt_tokens - hbm_hit_tokens,
        effective_hit_rate=hbm_hit_tokens / prompt_tokens,
        scheduled_iteration_count=1,
        kv_load_ms=kv_load_ms,
        timeline_mode=timeline_mode,
        ttft_granularity=ttft_granularity,
        compute_wait_ms=compute_wait_ms,
        kv_load_wait_ms=kv_load_wait_ms,
        uncached_prefill_compute_ms=uncached_prefill_compute_ms,
        chunk_count=chunk_count,
        load_event_count=load_event_count,
        progressive_materialized_blocks=1 if progressive_materialized_tokens else 0,
        progressive_materialized_tokens=progressive_materialized_tokens,
    )


def _iteration_metric(
    instance_uuid: str,
    iteration_id: int,
    *,
    timeline_mode: str = LEGACY_TIMELINE_MODE,
    ttft_granularity: str = "iteration",
    waiting_for_compute_count: int = 0,
    waiting_for_kv_load_count: int = 0,
    scheduled_chunk_count: int = 1,
    kv_transfer_queue_depth_max: int = 0,
) -> IterationMetrics:
    return IterationMetrics(
        instance_uuid=instance_uuid,
        iteration_id=iteration_id,
        start_time_ms=float(iteration_id),
        finish_time_ms=float(iteration_id + 1),
        duration_ms=1.0,
        batch_size=1,
        scheduled_prefill_tokens=1,
        scheduled_decode_tokens=0,
        max_query_len=1,
        total_context_tokens=1,
        backend="fitted_ttft",
        shape_key="shape",
        memoized=False,
        request_ids=(f"r{iteration_id}",),
        timeline_mode=timeline_mode,
        ttft_granularity=ttft_granularity,
        waiting_for_compute_count=waiting_for_compute_count,
        waiting_for_kv_load_count=waiting_for_kv_load_count,
        scheduled_chunk_count=scheduled_chunk_count,
        kv_transfer_queue_depth_max=kv_transfer_queue_depth_max,
    )
