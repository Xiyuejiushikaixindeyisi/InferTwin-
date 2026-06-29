from datetime import datetime, timedelta

import pytest

from infertwin.instance.request import SimulationRequest
from infertwin.latency.formula import FormulaLatencyBackend
from infertwin.replay.event_loop import BatchAwareReplayEngine, _record_iteration_compute_wait
from infertwin.replay.timeline import LEGACY_TIMELINE_MODE, PROGRESSIVE_TIMELINE_MODE
from infertwin.request.block_hasher import build_prefix_blocks
from infertwin.scheduler.config import SchedulerConfig
from infertwin.scheduler.queue import WaitingQueue
from infertwin.scheduler.state import RequestState, RequestStatus
from infertwin.scheduler.vllm_like import VllmLikeBatchScheduler


def test_legacy_mode_keeps_compute_wait_fields_zero() -> None:
    engine = _engine(max_num_batched_tokens=4, max_num_seqs=1, prefill_token_ms=1.0)
    first = _request("r1", start_time_ms=0.0, token_ids=[1, 2, 3, 4])
    second = _request("r2", start_time_ms=0.0, token_ids=[5, 6, 7, 8])

    result = engine.run([first, second])

    metrics_by_id = {item.request_id: item for item in result.request_metrics}
    assert metrics_by_id["r2"].timeline_mode == LEGACY_TIMELINE_MODE
    assert metrics_by_id["r2"].compute_wait_ms == 0.0
    assert metrics_by_id["r2"].kv_load_wait_ms == 0.0
    assert metrics_by_id["r2"].scheduler_wait_ms == 4.0
    assert [item.compute_wait_ms for item in result.iteration_metrics] == [0.0, 0.0]
    assert [item.waiting_for_compute_count for item in result.iteration_metrics] == [0, 0]


def test_progressive_mode_counts_waiting_request_compute_wait() -> None:
    engine = _engine(
        max_num_batched_tokens=4,
        max_num_seqs=1,
        prefill_token_ms=1.0,
        timeline_mode=PROGRESSIVE_TIMELINE_MODE,
    )
    first = _request("r1", start_time_ms=0.0, token_ids=[1, 2, 3, 4])
    second = _request("r2", start_time_ms=0.0, token_ids=[5, 6, 7, 8])

    result = engine.run([first, second])

    metrics_by_id = {item.request_id: item for item in result.request_metrics}
    assert metrics_by_id["r1"].compute_wait_ms == 0.0
    assert metrics_by_id["r1"].scheduler_wait_ms == 0.0
    assert metrics_by_id["r2"].timeline_mode == PROGRESSIVE_TIMELINE_MODE
    assert metrics_by_id["r2"].compute_wait_ms == 4.0
    assert metrics_by_id["r2"].kv_load_wait_ms == 0.0
    assert metrics_by_id["r2"].scheduler_wait_ms == 4.0
    assert metrics_by_id["r2"].ttft_ms == 8.0
    assert result.iteration_metrics[0].timeline_mode == PROGRESSIVE_TIMELINE_MODE
    assert result.iteration_metrics[0].waiting_for_compute_count == 1
    assert result.iteration_metrics[0].compute_wait_ms == 4.0
    assert result.iteration_metrics[1].waiting_for_compute_count == 0
    assert result.iteration_metrics[1].compute_wait_ms == 0.0


def test_progressive_mode_counts_request_arriving_during_iteration() -> None:
    engine = _engine(
        max_num_batched_tokens=4,
        prefill_token_ms=1.0,
        timeline_mode=PROGRESSIVE_TIMELINE_MODE,
    )
    first = _request("r1", start_time_ms=0.0, token_ids=[1, 2, 3, 4])
    second = _request("r2", start_time_ms=1.0, token_ids=[5, 6, 7, 8])

    result = engine.run([first, second])

    metrics_by_id = {item.request_id: item for item in result.request_metrics}
    assert metrics_by_id["r2"].first_scheduled_time_ms == 4.0
    assert metrics_by_id["r2"].compute_wait_ms == 3.0
    assert metrics_by_id["r2"].scheduler_wait_ms == 3.0
    assert result.iteration_metrics[0].waiting_for_compute_count == 0
    assert result.iteration_metrics[0].compute_wait_ms == 0.0


def test_progressive_mode_counts_zero_miss_arriving_during_iteration() -> None:
    engine = _engine(
        max_num_batched_tokens=4,
        prefill_token_ms=1.0,
        timeline_mode=PROGRESSIVE_TIMELINE_MODE,
    )
    first = _request("r1", start_time_ms=0.0, token_ids=[1, 2, 3, 4])
    zero_miss = _request("r2", start_time_ms=1.0, token_ids=[])

    result = engine.run([first, zero_miss])

    metrics_by_id = {item.request_id: item for item in result.request_metrics}
    assert metrics_by_id["r2"].scheduled_iteration_count == 0
    assert metrics_by_id["r2"].first_scheduled_time_ms == 4.0
    assert metrics_by_id["r2"].finish_time_ms == 4.0
    assert metrics_by_id["r2"].compute_wait_ms == 3.0
    assert metrics_by_id["r2"].scheduler_wait_ms == 3.0
    assert metrics_by_id["r2"].ttft_ms == 3.0


def test_running_request_not_scheduled_accumulates_compute_wait() -> None:
    scheduled = _state("scheduled", timeline_mode=PROGRESSIVE_TIMELINE_MODE)
    skipped = _state("skipped", timeline_mode=PROGRESSIVE_TIMELINE_MODE)

    accounting = _record_iteration_compute_wait(
        waiting=WaitingQueue(),
        running=[scheduled, skipped],
        scheduled_request_ids={"scheduled"},
        duration_ms=7.0,
        timeline_mode=PROGRESSIVE_TIMELINE_MODE,
    )

    assert accounting.waiting_for_compute_count == 1
    assert accounting.compute_wait_ms == 7.0
    assert scheduled.compute_wait_ms == 0.0
    assert skipped.compute_wait_ms == 7.0


def test_compute_wait_rejects_negative_duration() -> None:
    state = _state("r1", timeline_mode=PROGRESSIVE_TIMELINE_MODE)

    with pytest.raises(ValueError, match="compute wait duration"):
        state.record_compute_wait(-1.0)


def test_engine_rejects_unknown_timeline_mode() -> None:
    with pytest.raises(ValueError, match="unsupported timeline_mode"):
        _engine(max_num_batched_tokens=4, timeline_mode="unknown")


def _engine(
    *,
    max_num_batched_tokens: int,
    max_num_seqs: int = 8,
    prefill_token_ms: float = 1.0,
    timeline_mode: str = LEGACY_TIMELINE_MODE,
) -> BatchAwareReplayEngine:
    scheduler = VllmLikeBatchScheduler(
        SchedulerConfig(
            max_num_batched_tokens=max_num_batched_tokens,
            max_num_seqs=max_num_seqs,
            enable_chunked_prefill=True,
        )
    )
    latency_backend = FormulaLatencyBackend(
        iteration_fixed_overhead_ms=0.0,
        iteration_prefill_token_ms=prefill_token_ms,
        iteration_batch_overhead_ms=0.0,
        iteration_context_token_ms=0.0,
        model_name="glm-v5",
        hardware_name="local-dev",
    )
    return BatchAwareReplayEngine(
        scheduler=scheduler,
        latency_backend=latency_backend,
        timeline_mode=timeline_mode,
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


def _state(request_id: str, *, timeline_mode: str) -> RequestState:
    return RequestState(
        request_id=request_id,
        tenant_id="tenant-a",
        instance_uuid="instance-a",
        arrival_time_ms=0.0,
        prompt_tokens=8,
        effective_block_size=4,
        status=RequestStatus.RUNNING,
        timeline_mode=timeline_mode,
    )
