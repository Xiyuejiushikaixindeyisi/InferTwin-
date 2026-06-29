"""Deterministic KV transfer queue models for replay timeline accounting."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class KVTransferRequest:
    """One request-level KV transfer submitted to an instance-local link."""

    request_id: str
    instance_uuid: str
    ready_time_ms: float
    transfer_ms: float
    kv_load_tokens: int = 0
    kv_load_bytes: int = 0

    def __post_init__(self) -> None:
        if not self.request_id:
            raise ValueError("request_id is required")
        if not self.instance_uuid:
            raise ValueError("instance_uuid is required")
        if self.ready_time_ms < 0:
            raise ValueError("ready_time_ms must be non-negative")
        if self.transfer_ms < 0:
            raise ValueError("transfer_ms must be non-negative")
        if self.kv_load_tokens < 0:
            raise ValueError("kv_load_tokens must be non-negative")
        if self.kv_load_bytes < 0:
            raise ValueError("kv_load_bytes must be non-negative")


@dataclass(frozen=True, slots=True)
class KVTransferResult:
    """Timeline result returned after a KV transfer queue submission."""

    request_id: str
    instance_uuid: str
    ready_time_ms: float
    start_time_ms: float
    finish_time_ms: float
    transfer_ms: float
    queue_wait_ms: float
    elapsed_ms: float
    queue_depth_before: int
    queue_depth_after: int

    def __post_init__(self) -> None:
        if not self.request_id:
            raise ValueError("request_id is required")
        if not self.instance_uuid:
            raise ValueError("instance_uuid is required")
        if self.ready_time_ms < 0:
            raise ValueError("ready_time_ms must be non-negative")
        if self.start_time_ms < self.ready_time_ms:
            raise ValueError("start_time_ms cannot be earlier than ready_time_ms")
        if self.finish_time_ms < self.start_time_ms:
            raise ValueError("finish_time_ms cannot be earlier than start_time_ms")
        if self.transfer_ms < 0:
            raise ValueError("transfer_ms must be non-negative")
        if self.queue_wait_ms < 0:
            raise ValueError("queue_wait_ms must be non-negative")
        if self.elapsed_ms < 0:
            raise ValueError("elapsed_ms must be non-negative")
        if self.queue_depth_before < 0 or self.queue_depth_after < 0:
            raise ValueError("queue depth values must be non-negative")


class SharedLinkFIFOTransferQueue:
    """Instance-local FIFO queue for v1 shared-link KV transfer accounting."""

    def __init__(self, *, instance_uuid: str) -> None:
        if not instance_uuid:
            raise ValueError("instance_uuid is required")
        self.instance_uuid = instance_uuid
        self._next_available_time_ms = 0.0
        self._unfinished_finish_times: list[float] = []

    def submit(self, request: KVTransferRequest) -> KVTransferResult:
        """Submit one transfer and return deterministic FIFO timing."""

        if request.instance_uuid != self.instance_uuid:
            raise ValueError(
                f"transfer for instance {request.instance_uuid!r} cannot be submitted "
                f"to queue for {self.instance_uuid!r}"
            )

        self._prune_completed(request.ready_time_ms)
        queue_depth_before = len(self._unfinished_finish_times)
        start_time_ms = max(request.ready_time_ms, self._next_available_time_ms)
        finish_time_ms = start_time_ms + request.transfer_ms
        queue_wait_ms = start_time_ms - request.ready_time_ms
        elapsed_ms = finish_time_ms - request.ready_time_ms

        self._next_available_time_ms = finish_time_ms
        if request.transfer_ms > 0:
            self._unfinished_finish_times.append(finish_time_ms)
        queue_depth_after = len(self._unfinished_finish_times)

        return KVTransferResult(
            request_id=request.request_id,
            instance_uuid=request.instance_uuid,
            ready_time_ms=request.ready_time_ms,
            start_time_ms=start_time_ms,
            finish_time_ms=finish_time_ms,
            transfer_ms=request.transfer_ms,
            queue_wait_ms=queue_wait_ms,
            elapsed_ms=elapsed_ms,
            queue_depth_before=queue_depth_before,
            queue_depth_after=queue_depth_after,
        )

    def _prune_completed(self, ready_time_ms: float) -> None:
        self._unfinished_finish_times = [
            finish_time_ms
            for finish_time_ms in self._unfinished_finish_times
            if finish_time_ms > ready_time_ms
        ]
