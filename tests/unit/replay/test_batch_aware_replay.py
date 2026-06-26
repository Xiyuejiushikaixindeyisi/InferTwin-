from datetime import datetime, timedelta

import pytest

from hitfloor.instance.request import SimulationRequest
from hitfloor.latency.formula import FormulaLatencyBackend
from hitfloor.replay.event_loop import BatchAwareReplayEngine
from hitfloor.request.block_hasher import build_prefix_blocks
from hitfloor.scheduler.config import SchedulerConfig
from hitfloor.scheduler.vllm_like import VllmLikeBatchScheduler


def test_replay_finishes_single_chunked_request() -> None:
    engine = _engine(max_num_batched_tokens=4, prefill_token_ms=1.0)
    request = _request("r1", start_time_ms=0.0, token_ids=list(range(8)))

    result = engine.run([request])

    assert len(result.request_metrics) == 1
    metrics = result.request_metrics[0]
    assert metrics.request_id == "r1"
    assert metrics.miss_tokens == 8
    assert metrics.hbm_hit_tokens == 0
    assert metrics.scheduled_iteration_count == 2
    assert metrics.finish_time_ms == 8.0
    assert metrics.ttft_ms == 8.0
    assert [item.scheduled_prefill_tokens for item in result.iteration_metrics] == [4, 4]


def test_zero_miss_request_uses_fast_finish_without_iteration() -> None:
    engine = _engine(max_num_batched_tokens=4, prefill_token_ms=1.0)
    first = _request("r1", start_time_ms=0.0, token_ids=[1, 2, 3, 4])
    second = _request("r2", start_time_ms=10.0, token_ids=[1, 2, 3, 4])

    result = engine.run([first, second])

    first_metrics, second_metrics = result.request_metrics
    assert first_metrics.miss_tokens == 4
    assert second_metrics.hbm_hit_tokens == 4
    assert second_metrics.miss_tokens == 0
    assert second_metrics.scheduled_iteration_count == 0
    assert second_metrics.first_scheduled_time_ms == 10.0
    assert second_metrics.finish_time_ms == 10.0
    assert second_metrics.ttft_ms == 0.0
    assert len(result.iteration_metrics) == 1


def test_lookup_happens_on_first_schedule_not_arrival() -> None:
    engine = _engine(max_num_batched_tokens=4, fixed_overhead_ms=10.0, prefill_token_ms=0.0)
    first = _request("r1", start_time_ms=0.0, token_ids=[1, 2, 3, 4])
    second = _request("r2", start_time_ms=1.0, token_ids=[1, 2, 3, 4])

    result = engine.run([first, second])

    first_metrics, second_metrics = result.request_metrics
    assert first_metrics.finish_time_ms == 10.0
    assert second_metrics.hbm_hit_tokens == 4
    assert second_metrics.miss_tokens == 0
    assert second_metrics.first_scheduled_time_ms == 10.0
    assert second_metrics.scheduler_wait_ms == 9.0
    assert second_metrics.ttft_ms == 9.0
    assert len(result.iteration_metrics) == 1


def test_materialization_not_visible_within_same_iteration() -> None:
    engine = _engine(max_num_batched_tokens=8, prefill_token_ms=1.0)
    first = _request("r1", start_time_ms=0.0, token_ids=[1, 2, 3, 4])
    second = _request("r2", start_time_ms=0.0, token_ids=[1, 2, 3, 4])

    result = engine.run([first, second])

    first_metrics, second_metrics = result.request_metrics
    assert first_metrics.miss_tokens == 4
    assert second_metrics.miss_tokens == 4
    assert second_metrics.hbm_hit_tokens == 0
    assert len(result.iteration_metrics) == 1
    assert result.iteration_metrics[0].batch_size == 2
    assert result.iteration_metrics[0].scheduled_prefill_tokens == 8


def test_instances_do_not_share_cache() -> None:
    engine = _engine(max_num_batched_tokens=4, prefill_token_ms=1.0)
    first = _request("r1", "instance-a", 0.0, [1, 2, 3, 4])
    second = _request("r2", "instance-b", 10.0, [1, 2, 3, 4])

    result = engine.run([first, second])

    metrics_by_id = {item.request_id: item for item in result.request_metrics}
    assert metrics_by_id["r1"].miss_tokens == 4
    assert metrics_by_id["r2"].miss_tokens == 4
    assert metrics_by_id["r2"].hbm_hit_tokens == 0


def test_empty_schedule_fails_fast_when_chunking_disabled_and_request_exceeds_budget() -> None:
    engine = _engine(
        max_num_batched_tokens=4,
        enable_chunked_prefill=False,
        prefill_token_ms=1.0,
    )
    request = _request("r1", start_time_ms=0.0, token_ids=list(range(8)))

    with pytest.raises(ValueError, match="empty batch"):
        engine.run([request])


def test_total_scheduled_prefill_tokens_equals_total_miss_tokens() -> None:
    engine = _engine(max_num_batched_tokens=8, prefill_token_ms=1.0)
    requests = [
        _request("r1", start_time_ms=0.0, token_ids=[1, 2, 3, 4]),
        _request("r2", start_time_ms=0.0, token_ids=[5, 6, 7, 8]),
        _request("r3", start_time_ms=20.0, token_ids=[1, 2, 3, 4]),
    ]

    result = engine.run(requests)

    assert sum(item.scheduled_prefill_tokens for item in result.iteration_metrics) == sum(
        item.miss_tokens for item in result.request_metrics
    )


def _engine(
    *,
    max_num_batched_tokens: int,
    max_num_seqs: int = 8,
    enable_chunked_prefill: bool = True,
    fixed_overhead_ms: float = 0.0,
    prefill_token_ms: float = 1.0,
) -> BatchAwareReplayEngine:
    scheduler = VllmLikeBatchScheduler(
        SchedulerConfig(
            max_num_batched_tokens=max_num_batched_tokens,
            max_num_seqs=max_num_seqs,
            enable_chunked_prefill=enable_chunked_prefill,
        )
    )
    latency_backend = FormulaLatencyBackend(
        iteration_fixed_overhead_ms=fixed_overhead_ms,
        iteration_prefill_token_ms=prefill_token_ms,
        iteration_batch_overhead_ms=0.0,
        iteration_context_token_ms=0.0,
        model_name="glm-v5",
        hardware_name="local-dev",
    )
    return BatchAwareReplayEngine(scheduler=scheduler, latency_backend=latency_backend)


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
    )
