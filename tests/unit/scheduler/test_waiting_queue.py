import pytest

from hitfloor.scheduler.queue import WaitingQueue
from hitfloor.scheduler.state import RequestState


def test_waiting_queue_iterates_in_fifo_order() -> None:
    queue = WaitingQueue([_request("r1"), _request("r2")])

    queue.append(_request("r3"))

    assert [request.request_id for request in queue] == ["r1", "r2", "r3"]
    assert len(queue) == 3
    assert queue


def test_popleft_removes_logical_head_without_losing_order() -> None:
    queue = WaitingQueue([_request("r1"), _request("r2"), _request("r3")])

    assert queue.popleft().request_id == "r1"

    assert queue[0].request_id == "r2"
    assert queue[-1].request_id == "r3"
    assert [request.request_id for request in queue] == ["r2", "r3"]


def test_pop_zero_is_equivalent_to_popleft() -> None:
    queue = WaitingQueue([_request("r1"), _request("r2")])

    assert queue.pop(0).request_id == "r1"

    assert [request.request_id for request in queue] == ["r2"]


def test_pop_middle_uses_logical_index() -> None:
    queue = WaitingQueue([_request("r1"), _request("r2"), _request("r3"), _request("r4")])

    assert queue.popleft().request_id == "r1"
    assert queue.pop(1).request_id == "r3"

    assert [request.request_id for request in queue] == ["r2", "r4"]


def test_append_after_many_popleft_preserves_fifo_order() -> None:
    queue = WaitingQueue(_request(f"r{index}") for index in range(80))

    popped = [queue.popleft().request_id for _ in range(70)]
    queue.append(_request("new"))

    assert popped[:3] == ["r0", "r1", "r2"]
    assert [request.request_id for request in queue] == [
        *[f"r{index}" for index in range(70, 80)],
        "new",
    ]


def test_empty_queue_pop_fails_explicitly() -> None:
    queue = WaitingQueue()

    with pytest.raises(IndexError, match="empty"):
        queue.popleft()
    with pytest.raises(IndexError, match="out of range"):
        queue.pop()


def _request(request_id: str) -> RequestState:
    return RequestState(
        request_id=request_id,
        tenant_id="tenant-a",
        instance_uuid="instance-a",
        arrival_time_ms=0.0,
        prompt_tokens=4,
        arrival_seq=0,
    )
