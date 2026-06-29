from datetime import datetime, timedelta

import pytest

from infertwin.cache.event_sink import InMemoryCacheEventSink
from infertwin.cache.hbm_lru import HBMCache
from infertwin.instance.request import SimulationRequest
from infertwin.latency.formula import FormulaLatencyBackend
from infertwin.replay.event_loop import BatchAwareReplayEngine
from infertwin.replay.timeline import LEGACY_TIMELINE_MODE, PROGRESSIVE_TIMELINE_MODE
from infertwin.request.block_hasher import build_prefix_blocks
from infertwin.scheduler.config import SchedulerConfig
from infertwin.scheduler.vllm_like import VllmLikeBatchScheduler
from infertwin.streaming.metrics import InMemoryReplayMetricSink
from infertwin.streaming.replay import StreamingBatchAwareReplayEngine
from infertwin.streaming.source import ListRequestSource


def test_streaming_replay_matches_list_replay_for_one_instance() -> None:
    requests = [
        _request("r1", start_time_ms=0.0, token_ids=[1, 2, 3, 4]),
        _request("r2", start_time_ms=0.0, token_ids=[5, 6, 7, 8]),
        _request("r3", start_time_ms=20.0, token_ids=[1, 2, 3, 4]),
    ]
    list_engine = _list_engine(max_num_batched_tokens=8, prefill_token_ms=1.0)
    streaming_engine = _streaming_engine(max_num_batched_tokens=8, prefill_token_ms=1.0)

    list_result = list_engine.run(requests)
    sink = InMemoryReplayMetricSink()
    stats = streaming_engine.run_instance_stream(
        instance_uuid="instance-a",
        request_source=ListRequestSource(requests),
        cache=HBMCache(capacity_blocks=1024),
        metric_sink=sink,
    )

    assert sink.request_metrics == list_result.request_metrics
    assert sink.iteration_metrics == list_result.iteration_metrics
    assert stats.emitted_request_count == len(list_result.request_metrics)
    assert stats.emitted_iteration_count == len(list_result.iteration_metrics)
    assert stats.final_active_requests == 0
    assert stats.max_active_requests > 0


def test_streaming_replay_matches_list_replay_for_progressive_compute_wait() -> None:
    requests = [
        _request("r1", start_time_ms=0.0, token_ids=[1, 2, 3, 4]),
        _request("r2", start_time_ms=0.0, token_ids=[5, 6, 7, 8]),
        _request("r3", start_time_ms=1.0, token_ids=[9, 10, 11, 12]),
    ]
    list_engine = _list_engine(
        max_num_batched_tokens=4,
        prefill_token_ms=1.0,
        timeline_mode=PROGRESSIVE_TIMELINE_MODE,
    )
    streaming_engine = _streaming_engine(
        max_num_batched_tokens=4,
        prefill_token_ms=1.0,
        timeline_mode=PROGRESSIVE_TIMELINE_MODE,
    )

    list_result = list_engine.run(requests)
    sink = InMemoryReplayMetricSink()
    stats = streaming_engine.run_instance_stream(
        instance_uuid="instance-a",
        request_source=ListRequestSource(requests),
        cache=HBMCache(capacity_blocks=1024),
        metric_sink=sink,
    )

    metrics_by_id = {item.request_id: item for item in sink.request_metrics}
    assert sink.request_metrics == list_result.request_metrics
    assert sink.iteration_metrics == list_result.iteration_metrics
    assert metrics_by_id["r2"].compute_wait_ms == 4.0
    assert metrics_by_id["r3"].compute_wait_ms == 7.0
    assert stats.emitted_request_count == len(list_result.request_metrics)
    assert stats.emitted_iteration_count == len(list_result.iteration_metrics)


def test_streaming_replay_preserves_zero_miss_fast_finish() -> None:
    request = _request("r1", start_time_ms=10.0, token_ids=[])
    engine = _streaming_engine(max_num_batched_tokens=4, prefill_token_ms=1.0)
    sink = InMemoryReplayMetricSink()

    stats = engine.run_instance_stream(
        instance_uuid="instance-a",
        request_source=ListRequestSource([request]),
        cache=HBMCache(capacity_blocks=2),
        metric_sink=sink,
    )

    (metrics,) = sink.request_metrics
    assert metrics.request_id == "r1"
    assert metrics.miss_tokens == 0
    assert metrics.scheduled_iteration_count == 0
    assert metrics.first_scheduled_time_ms == 10.0
    assert metrics.finish_time_ms == 10.0
    assert sink.iteration_metrics == ()
    assert stats.final_active_requests == 0


def test_streaming_replay_emits_cache_events_to_sink() -> None:
    first = _request("r1", start_time_ms=0.0, token_ids=[1, 2, 3, 4])
    repeat = _request("r2", start_time_ms=10.0, token_ids=[1, 2, 3, 4])
    engine = _streaming_engine(max_num_batched_tokens=4, prefill_token_ms=1.0)
    metric_sink = InMemoryReplayMetricSink()
    event_sink = InMemoryCacheEventSink()

    engine.run_instance_stream(
        instance_uuid="instance-a",
        request_source=ListRequestSource([first, repeat]),
        cache=HBMCache(capacity_blocks=2),
        metric_sink=metric_sink,
        cache_event_sink=event_sink,
    )

    assert event_sink.snapshot_stats().total_events > 0
    assert event_sink.snapshot_stats().lookup_hit_events > 0
    assert metric_sink.request_metrics[1].hbm_hit_tokens == 0
    assert metric_sink.request_metrics[1].miss_tokens == 4


def test_streaming_replay_fails_on_instance_mismatch() -> None:
    request = _request(
        "r1",
        instance_uuid="instance-b",
        start_time_ms=0.0,
        token_ids=[1, 2, 3, 4],
    )
    engine = _streaming_engine(max_num_batched_tokens=4, prefill_token_ms=1.0)

    with pytest.raises(ValueError, match="instance 'instance-a'"):
        engine.run_instance_stream(
            instance_uuid="instance-a",
            request_source=ListRequestSource([request]),
            cache=HBMCache(capacity_blocks=2),
            metric_sink=InMemoryReplayMetricSink(),
        )


def _list_engine(
    *,
    max_num_batched_tokens: int,
    prefill_token_ms: float,
    timeline_mode: str = LEGACY_TIMELINE_MODE,
) -> BatchAwareReplayEngine:
    scheduler = VllmLikeBatchScheduler(
        SchedulerConfig(
            max_num_batched_tokens=max_num_batched_tokens,
            max_num_seqs=8,
            enable_chunked_prefill=True,
        )
    )
    return BatchAwareReplayEngine(
        scheduler=scheduler,
        latency_backend=_latency_backend(prefill_token_ms=prefill_token_ms),
        cache_factory=lambda _instance_uuid: HBMCache(capacity_blocks=1024),
        timeline_mode=timeline_mode,
    )


def _streaming_engine(
    *,
    max_num_batched_tokens: int,
    prefill_token_ms: float,
    timeline_mode: str = LEGACY_TIMELINE_MODE,
) -> StreamingBatchAwareReplayEngine:
    scheduler = VllmLikeBatchScheduler(
        SchedulerConfig(
            max_num_batched_tokens=max_num_batched_tokens,
            max_num_seqs=8,
            enable_chunked_prefill=True,
        )
    )
    return StreamingBatchAwareReplayEngine(
        scheduler=scheduler,
        latency_backend=_latency_backend(prefill_token_ms=prefill_token_ms),
        timeline_mode=timeline_mode,
    )


def _latency_backend(*, prefill_token_ms: float) -> FormulaLatencyBackend:
    return FormulaLatencyBackend(
        iteration_fixed_overhead_ms=0.0,
        iteration_prefill_token_ms=prefill_token_ms,
        iteration_batch_overhead_ms=0.0,
        iteration_context_token_ms=0.0,
        model_name="glm-v5",
        hardware_name="local-dev",
    )


def _request(
    request_id: str,
    instance_uuid: str = "instance-a",
    start_time_ms: float = 0.0,
    token_ids: list[int] | None = None,
) -> SimulationRequest:
    if token_ids is None:
        token_ids = [1, 2, 3, 4]
    service_start_time = datetime.fromisoformat("2026-06-05 09:01:23") + timedelta(
        milliseconds=start_time_ms
    )
    blocks = build_prefix_blocks(
        token_ids=token_ids,
        block_size_tokens=4,
        model="glm-v5",
        tenant_id="tenant-a",
        kv_bytes_per_token=1,
    )
    return SimulationRequest(
        request_id=request_id,
        tenant_id="tenant-a",
        instance_uuid=instance_uuid,
        model="glm-v5",
        service_start_time=service_start_time,
        start_time_ms=start_time_ms,
        tokenizer_profile="glm-v5",
        prompt_tokens=len(token_ids),
        prompt_blocks=tuple(blocks),
        kv_bytes_per_token=1,
        requested_block_size=4,
        runtime_block_size=4,
        effective_block_size=4,
    )
