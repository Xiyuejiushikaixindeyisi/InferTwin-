from infertwin.scheduler.config import SchedulerConfig
from infertwin.scheduler.queue import WaitingQueue
from infertwin.scheduler.state import RequestState, RequestStatus
from infertwin.scheduler.vllm_like import VllmLikeBatchScheduler


def test_fcfs_schedules_waiting_requests_by_arrival_seq() -> None:
    scheduler = VllmLikeBatchScheduler(SchedulerConfig(max_num_batched_tokens=16, max_num_seqs=4))
    waiting = WaitingQueue([_request("r1", prompt_tokens=4, arrival_seq=0), _request("r2", 6, 1)])
    running: list[RequestState] = []

    result = scheduler.schedule(
        instance_uuid="instance-a",
        iteration_id=0,
        start_time_ms=100.0,
        waiting=waiting,
        running=running,
    )

    assert [item.request_id for item in result.shape.request_slices] == ["r1", "r2"]
    assert result.shape.batch_size == 2
    assert list(waiting) == []
    assert [request.request_id for request in running] == ["r1", "r2"]
    assert running[0].first_scheduled_time_ms == 100.0
    assert running[0].status == RequestStatus.RUNNING


def test_running_requests_are_scheduled_before_waiting_requests() -> None:
    scheduler = VllmLikeBatchScheduler(SchedulerConfig(max_num_batched_tokens=8, max_num_seqs=4))
    running_request = _request("running", prompt_tokens=10, arrival_seq=0)
    running_request.status = RequestStatus.RUNNING
    running_request.num_computed_tokens = 5
    waiting = WaitingQueue([_request("waiting", prompt_tokens=10, arrival_seq=1)])
    running = [running_request]

    result = scheduler.schedule(
        instance_uuid="instance-a",
        iteration_id=0,
        start_time_ms=100.0,
        waiting=waiting,
        running=running,
    )

    slices = result.shape.request_slices
    assert [item.request_id for item in slices] == ["running", "waiting"]
    assert [item.scheduled_prefill_tokens for item in slices] == [5, 3]


def test_scheduled_slice_separates_cached_prefix_from_previous_chunks() -> None:
    scheduler = VllmLikeBatchScheduler(SchedulerConfig(max_num_batched_tokens=8, max_num_seqs=4))
    running_request = _request(
        "running",
        prompt_tokens=10,
        arrival_seq=0,
        cached_tokens=3,
    )
    running_request.status = RequestStatus.RUNNING
    running_request.num_computed_tokens = 5
    running = [running_request]

    result = scheduler.schedule(
        instance_uuid="instance-a",
        iteration_id=0,
        start_time_ms=100.0,
        waiting=WaitingQueue(),
        running=running,
    )

    scheduled_slice = result.shape.request_slices[0]
    assert scheduled_slice.computed_tokens_before == 5
    assert scheduled_slice.cached_prefix_tokens == 3
    assert scheduled_slice.previous_chunk_tokens == 2


def test_scheduler_respects_max_num_batched_tokens() -> None:
    scheduler = VllmLikeBatchScheduler(SchedulerConfig(max_num_batched_tokens=10, max_num_seqs=4))
    waiting = WaitingQueue([_request("r1", 8, 0), _request("r2", 8, 1)])

    result = scheduler.schedule(
        instance_uuid="instance-a",
        iteration_id=0,
        start_time_ms=0.0,
        waiting=waiting,
        running=[],
    )

    assert result.shape.scheduled_prefill_tokens == 10
    assert [item.scheduled_prefill_tokens for item in result.shape.request_slices] == [8, 2]


def test_scheduler_respects_max_num_seqs() -> None:
    scheduler = VllmLikeBatchScheduler(SchedulerConfig(max_num_batched_tokens=100, max_num_seqs=1))
    waiting = WaitingQueue([_request("r1", 8, 0), _request("r2", 8, 1)])
    running: list[RequestState] = []

    result = scheduler.schedule(
        instance_uuid="instance-a",
        iteration_id=0,
        start_time_ms=0.0,
        waiting=waiting,
        running=running,
    )

    assert result.shape.batch_size == 1
    assert [item.request_id for item in result.shape.request_slices] == ["r1"]
    assert [request.request_id for request in waiting] == ["r2"]
    assert [request.request_id for request in running] == ["r1"]


def _request(
    request_id: str,
    prompt_tokens: int,
    arrival_seq: int,
    cached_tokens: int = 0,
) -> RequestState:
    request = RequestState(
        request_id=request_id,
        tenant_id="tenant-a",
        instance_uuid="instance-a",
        arrival_time_ms=float(arrival_seq),
        prompt_tokens=prompt_tokens,
        arrival_seq=arrival_seq,
    )
    request.set_cache_lookup(
        cached_tokens=cached_tokens,
        miss_tokens=prompt_tokens - cached_tokens,
    )
    return request
