from dataclasses import asdict
from datetime import datetime, timedelta

from infertwin.cache.event_sink import InMemoryCacheEventSink
from infertwin.cache.hbm_lru import HBMCache
from infertwin.instance.request import SimulationRequest
from infertwin.latency.formula import FormulaLatencyBackend
from infertwin.replay.event_loop import BatchAwareReplayEngine
from infertwin.replay.metrics import BatchAwareReplayResult
from infertwin.request.block_hasher import build_prefix_blocks
from infertwin.scheduler.config import SchedulerConfig
from infertwin.scheduler.vllm_like import VllmLikeBatchScheduler


def test_batch_aware_hbm_lru_golden_is_deterministic_and_instance_isolated() -> None:
    requests = [
        _request("r1", "instance-a", 0.0, [1, 2, 3, 4]),
        _request("r2", "instance-a", 0.0, [1, 2, 3, 4]),
        _request("r3", "instance-b", 2.0, [1, 2, 3, 4]),
        _request("r4", "instance-a", 5.0, [1, 2, 3, 4]),
        _request("r5", "instance-a", 6.0, [5, 6, 7, 8]),
    ]

    first = _run(requests)
    second = _run(list(reversed(requests)))

    assert _snapshot(first) == _snapshot(second)
    assert _request_rows(first) == [
        {
            "request_id": "r1",
            "instance_uuid": "instance-a",
            "arrival_time_ms": 0.0,
            "first_scheduled_time_ms": 0.0,
            "finish_time_ms": 4.0,
            "scheduler_wait_ms": 0.0,
            "ttft_ms": 4.0,
            "prompt_tokens": 4,
            "prompt_blocks": 1,
            "hbm_hit_tokens": 0,
            "ddr_hit_tokens": 0,
            "miss_tokens": 4,
            "effective_hit_rate": 0.0,
            "scheduled_iteration_count": 1,
        },
        {
            "request_id": "r2",
            "instance_uuid": "instance-a",
            "arrival_time_ms": 0.0,
            "first_scheduled_time_ms": 0.0,
            "finish_time_ms": 4.0,
            "scheduler_wait_ms": 0.0,
            "ttft_ms": 4.0,
            "prompt_tokens": 4,
            "prompt_blocks": 1,
            "hbm_hit_tokens": 0,
            "ddr_hit_tokens": 0,
            "miss_tokens": 4,
            "effective_hit_rate": 0.0,
            "scheduled_iteration_count": 1,
        },
        {
            "request_id": "r3",
            "instance_uuid": "instance-b",
            "arrival_time_ms": 2.0,
            "first_scheduled_time_ms": 2.0,
            "finish_time_ms": 4.0,
            "scheduler_wait_ms": 0.0,
            "ttft_ms": 2.0,
            "prompt_tokens": 4,
            "prompt_blocks": 1,
            "hbm_hit_tokens": 0,
            "ddr_hit_tokens": 0,
            "miss_tokens": 4,
            "effective_hit_rate": 0.0,
            "scheduled_iteration_count": 1,
        },
        {
            "request_id": "r4",
            "instance_uuid": "instance-a",
            "arrival_time_ms": 5.0,
            "first_scheduled_time_ms": 5.0,
            "finish_time_ms": 7.0,
            "scheduler_wait_ms": 0.0,
            "ttft_ms": 2.0,
            "prompt_tokens": 4,
            "prompt_blocks": 1,
            "hbm_hit_tokens": 0,
            "ddr_hit_tokens": 0,
            "miss_tokens": 4,
            "effective_hit_rate": 0.0,
            "scheduled_iteration_count": 1,
        },
        {
            "request_id": "r5",
            "instance_uuid": "instance-a",
            "arrival_time_ms": 6.0,
            "first_scheduled_time_ms": 7.0,
            "finish_time_ms": 9.0,
            "scheduler_wait_ms": 1.0,
            "ttft_ms": 3.0,
            "prompt_tokens": 4,
            "prompt_blocks": 1,
            "hbm_hit_tokens": 0,
            "ddr_hit_tokens": 0,
            "miss_tokens": 4,
            "effective_hit_rate": 0.0,
            "scheduled_iteration_count": 1,
        },
    ]
    assert _iteration_rows(first) == [
        {
            "instance_uuid": "instance-a",
            "iteration_id": 0,
            "start_time_ms": 0.0,
            "finish_time_ms": 4.0,
            "duration_ms": 4.0,
            "batch_size": 2,
            "scheduled_prefill_tokens": 8,
            "scheduled_decode_tokens": 0,
            "request_ids": ("r1", "r2"),
        },
        {
            "instance_uuid": "instance-b",
            "iteration_id": 0,
            "start_time_ms": 2.0,
            "finish_time_ms": 4.0,
            "duration_ms": 2.0,
            "batch_size": 1,
            "scheduled_prefill_tokens": 4,
            "scheduled_decode_tokens": 0,
            "request_ids": ("r3",),
        },
        {
            "instance_uuid": "instance-a",
            "iteration_id": 1,
            "start_time_ms": 5.0,
            "finish_time_ms": 7.0,
            "duration_ms": 2.0,
            "batch_size": 1,
            "scheduled_prefill_tokens": 4,
            "scheduled_decode_tokens": 0,
            "request_ids": ("r4",),
        },
        {
            "instance_uuid": "instance-a",
            "iteration_id": 2,
            "start_time_ms": 7.0,
            "finish_time_ms": 9.0,
            "duration_ms": 2.0,
            "batch_size": 1,
            "scheduled_prefill_tokens": 4,
            "scheduled_decode_tokens": 0,
            "request_ids": ("r5",),
        },
    ]
    assert asdict(first.cache_event_stats) == {
        "total_events": 9,
        "lookup_hit_events": 1,
        "lookup_miss_events": 4,
        "materialize_events": 3,
        "evict_events": 1,
        "peak_hbm_used_blocks": 1,
        "final_hbm_used_blocks": 1,
    }


def _run(requests: list[SimulationRequest]) -> BatchAwareReplayResult:
    scheduler = VllmLikeBatchScheduler(
        SchedulerConfig(
            max_num_batched_tokens=8,
            max_num_seqs=8,
            enable_chunked_prefill=True,
        )
    )
    latency_backend = FormulaLatencyBackend(
        iteration_fixed_overhead_ms=0.0,
        iteration_prefill_token_ms=0.5,
        iteration_batch_overhead_ms=0.0,
        iteration_context_token_ms=0.0,
        model_name="glm-v5",
        hardware_name="local-dev",
    )
    engine = BatchAwareReplayEngine(
        scheduler=scheduler,
        latency_backend=latency_backend,
        cache_factory=lambda _instance_uuid: HBMCache(capacity_blocks=1),
    )
    return engine.run(requests, cache_event_sink=InMemoryCacheEventSink())


def _snapshot(result: BatchAwareReplayResult) -> dict[str, object]:
    return {
        "requests": _request_rows(result),
        "iterations": _iteration_rows(result),
        "event_stats": asdict(result.cache_event_stats),
    }


def _request_rows(result: BatchAwareReplayResult) -> list[dict[str, object]]:
    return [
        {
            "request_id": metric.request_id,
            "instance_uuid": metric.instance_uuid,
            "arrival_time_ms": metric.arrival_time_ms,
            "first_scheduled_time_ms": metric.first_scheduled_time_ms,
            "finish_time_ms": metric.finish_time_ms,
            "scheduler_wait_ms": metric.scheduler_wait_ms,
            "ttft_ms": metric.ttft_ms,
            "prompt_tokens": metric.prompt_tokens,
            "prompt_blocks": metric.prompt_blocks,
            "hbm_hit_tokens": metric.hbm_hit_tokens,
            "ddr_hit_tokens": metric.ddr_hit_tokens,
            "miss_tokens": metric.miss_tokens,
            "effective_hit_rate": metric.effective_hit_rate,
            "scheduled_iteration_count": metric.scheduled_iteration_count,
        }
        for metric in result.request_metrics
    ]


def _iteration_rows(result: BatchAwareReplayResult) -> list[dict[str, object]]:
    return [
        {
            "instance_uuid": metric.instance_uuid,
            "iteration_id": metric.iteration_id,
            "start_time_ms": metric.start_time_ms,
            "finish_time_ms": metric.finish_time_ms,
            "duration_ms": metric.duration_ms,
            "batch_size": metric.batch_size,
            "scheduled_prefill_tokens": metric.scheduled_prefill_tokens,
            "scheduled_decode_tokens": metric.scheduled_decode_tokens,
            "request_ids": metric.request_ids,
        }
        for metric in result.iteration_metrics
    ]


def _request(
    request_id: str,
    instance_uuid: str,
    start_time_ms: float,
    token_ids: list[int],
) -> SimulationRequest:
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
    )
