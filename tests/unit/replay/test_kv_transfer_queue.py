import pytest

from infertwin.replay.kv_transfer import (
    KVTransferRequest,
    SharedLinkFIFOTransferQueue,
)


def test_single_transfer_has_no_queue_wait() -> None:
    queue = SharedLinkFIFOTransferQueue(instance_uuid="instance-a")

    result = queue.submit(
        KVTransferRequest(
            request_id="r1",
            instance_uuid="instance-a",
            ready_time_ms=10.0,
            transfer_ms=2.5,
            kv_load_tokens=128,
            kv_load_bytes=4096,
        )
    )

    assert result.start_time_ms == 10.0
    assert result.finish_time_ms == 12.5
    assert result.queue_wait_ms == 0.0
    assert result.elapsed_ms == 2.5
    assert result.queue_depth_before == 0
    assert result.queue_depth_after == 1


def test_same_ready_time_uses_fifo_submission_order() -> None:
    queue = SharedLinkFIFOTransferQueue(instance_uuid="instance-a")

    first = queue.submit(
        KVTransferRequest(
            request_id="r1",
            instance_uuid="instance-a",
            ready_time_ms=0.0,
            transfer_ms=2.0,
        )
    )
    second = queue.submit(
        KVTransferRequest(
            request_id="r2",
            instance_uuid="instance-a",
            ready_time_ms=0.0,
            transfer_ms=3.0,
        )
    )

    assert first.start_time_ms == 0.0
    assert first.finish_time_ms == 2.0
    assert first.queue_depth_before == 0
    assert first.queue_depth_after == 1
    assert second.start_time_ms == 2.0
    assert second.finish_time_ms == 5.0
    assert second.queue_wait_ms == 2.0
    assert second.elapsed_ms == 5.0
    assert second.queue_depth_before == 1
    assert second.queue_depth_after == 2


def test_completed_transfers_are_pruned_by_ready_time() -> None:
    queue = SharedLinkFIFOTransferQueue(instance_uuid="instance-a")
    queue.submit(
        KVTransferRequest(
            request_id="r1",
            instance_uuid="instance-a",
            ready_time_ms=0.0,
            transfer_ms=2.0,
        )
    )
    queue.submit(
        KVTransferRequest(
            request_id="r2",
            instance_uuid="instance-a",
            ready_time_ms=0.0,
            transfer_ms=3.0,
        )
    )

    result = queue.submit(
        KVTransferRequest(
            request_id="r3",
            instance_uuid="instance-a",
            ready_time_ms=6.0,
            transfer_ms=1.0,
        )
    )

    assert result.queue_depth_before == 0
    assert result.start_time_ms == 6.0
    assert result.finish_time_ms == 7.0
    assert result.elapsed_ms == 1.0
    assert result.queue_depth_after == 1


def test_zero_duration_transfer_waits_behind_backlog_without_increasing_depth() -> None:
    queue = SharedLinkFIFOTransferQueue(instance_uuid="instance-a")
    queue.submit(
        KVTransferRequest(
            request_id="r1",
            instance_uuid="instance-a",
            ready_time_ms=0.0,
            transfer_ms=2.0,
        )
    )

    result = queue.submit(
        KVTransferRequest(
            request_id="r2",
            instance_uuid="instance-a",
            ready_time_ms=0.0,
            transfer_ms=0.0,
        )
    )

    assert result.queue_depth_before == 1
    assert result.start_time_ms == 2.0
    assert result.finish_time_ms == 2.0
    assert result.queue_wait_ms == 2.0
    assert result.elapsed_ms == 2.0
    assert result.queue_depth_after == 1


def test_queue_rejects_instance_mismatch() -> None:
    queue = SharedLinkFIFOTransferQueue(instance_uuid="instance-a")

    with pytest.raises(ValueError, match="cannot be submitted"):
        queue.submit(
            KVTransferRequest(
                request_id="r1",
                instance_uuid="instance-b",
                ready_time_ms=0.0,
                transfer_ms=1.0,
            )
        )


def test_transfer_request_rejects_negative_values() -> None:
    with pytest.raises(ValueError, match="transfer_ms"):
        KVTransferRequest(
            request_id="r1",
            instance_uuid="instance-a",
            ready_time_ms=0.0,
            transfer_ms=-1.0,
        )
