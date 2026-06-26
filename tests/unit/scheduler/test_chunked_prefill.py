from infertwin.scheduler.config import SchedulerConfig
from infertwin.scheduler.queue import WaitingQueue
from infertwin.scheduler.state import RequestState
from infertwin.scheduler.vllm_like import VllmLikeBatchScheduler


def test_chunked_prefill_splits_long_request() -> None:
    scheduler = VllmLikeBatchScheduler(
        SchedulerConfig(
            max_num_batched_tokens=32,
            max_num_seqs=4,
            enable_chunked_prefill=True,
            long_prefill_token_threshold=16,
        )
    )
    request = _request("long", prompt_tokens=100)
    waiting = WaitingQueue([request])

    result = scheduler.schedule(
        instance_uuid="instance-a",
        iteration_id=0,
        start_time_ms=0.0,
        waiting=waiting,
        running=[],
    )

    assert result.shape.batch_size == 1
    assert result.shape.scheduled_prefill_tokens == 16
    assert result.shape.request_slices[0].computed_tokens_after == 16


def test_non_chunked_prefill_waits_when_request_exceeds_budget() -> None:
    scheduler = VllmLikeBatchScheduler(
        SchedulerConfig(
            max_num_batched_tokens=32,
            max_num_seqs=4,
            enable_chunked_prefill=False,
        )
    )
    request = _request("long", prompt_tokens=100)
    waiting = WaitingQueue([request])
    running: list[RequestState] = []

    result = scheduler.schedule(
        instance_uuid="instance-a",
        iteration_id=0,
        start_time_ms=0.0,
        waiting=waiting,
        running=running,
    )

    assert result.is_empty
    assert list(waiting) == [request]
    assert running == []


def test_apply_scheduled_tokens_finishes_request_only_at_finish_time() -> None:
    request = _request("short", prompt_tokens=8)

    request.apply_scheduled_tokens(scheduled_tokens=8, finish_time_ms=123.0)

    assert request.finish_time_ms == 123.0
    assert request.remaining_prefill_tokens() == 0


def _request(request_id: str, prompt_tokens: int) -> RequestState:
    request = RequestState(
        request_id=request_id,
        tenant_id="tenant-a",
        instance_uuid="instance-a",
        arrival_time_ms=0.0,
        prompt_tokens=prompt_tokens,
    )
    request.set_cache_lookup(cached_tokens=0, miss_tokens=prompt_tokens)
    return request
