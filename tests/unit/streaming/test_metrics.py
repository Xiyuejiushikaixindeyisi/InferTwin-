import pytest

from infertwin.cache.event_sink import CacheEventStats
from infertwin.experiment.sweep import build_capacity_rows
from infertwin.replay.metrics import BatchAwareRequestMetrics, IterationMetrics
from infertwin.replay.timeline import LEGACY_TIMELINE_MODE
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
        ),
    )
    iteration_metrics = (
        _iteration_metric("instance-a", 0),
        _iteration_metric("instance-b", 0),
        _iteration_metric("instance-a", 1),
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
    assert aggregator.request_count == 3
    assert aggregator.iteration_count == 3
    assert request_metrics[0].timeline_mode == LEGACY_TIMELINE_MODE


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
    )


def _iteration_metric(instance_uuid: str, iteration_id: int) -> IterationMetrics:
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
    )
