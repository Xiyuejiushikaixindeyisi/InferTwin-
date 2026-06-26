"""Mutable request state used by the batch scheduler."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from infertwin.request.block_hasher import PrefixBlock


class RequestStatus(str, Enum):
    WAITING = "waiting"
    RUNNING = "running"
    FINISHED = "finished"


@dataclass(slots=True)
class RequestState:
    """Lifecycle state for one request inside an instance replay.

    This type deliberately owns only scheduler-visible state. Cache lookup,
    latency estimation, and report rendering live in separate modules.
    """

    request_id: str
    tenant_id: str
    instance_uuid: str
    arrival_time_ms: float
    prompt_tokens: int
    prompt_blocks: tuple[PrefixBlock, ...] = ()
    model: str = ""
    tokenizer_profile: str = ""
    arrival_seq: int = 0
    status: RequestStatus = RequestStatus.WAITING
    cache_lookup_done: bool = False
    cached_tokens: int = 0
    miss_tokens: int | None = None
    num_computed_tokens: int = 0
    first_scheduled_time_ms: float | None = None
    finish_time_ms: float | None = None
    scheduled_iteration_count: int = 0

    def __post_init__(self) -> None:
        if self.prompt_tokens < 0:
            raise ValueError("prompt_tokens must be non-negative")
        if self.arrival_time_ms < 0:
            raise ValueError("arrival_time_ms must be non-negative")
        if self.arrival_seq < 0:
            raise ValueError("arrival_seq must be non-negative")

    def set_cache_lookup(self, cached_tokens: int, miss_tokens: int) -> None:
        """Attach prefix-cache lookup results before scheduling this request."""

        if self.cache_lookup_done:
            raise ValueError(f"cache lookup already recorded for request {self.request_id}")
        if cached_tokens < 0 or miss_tokens < 0:
            raise ValueError("cached_tokens and miss_tokens must be non-negative")
        if cached_tokens + miss_tokens != self.prompt_tokens:
            raise ValueError("cached_tokens + miss_tokens must equal prompt_tokens")

        self.cached_tokens = cached_tokens
        self.miss_tokens = miss_tokens
        self.num_computed_tokens = cached_tokens
        self.cache_lookup_done = True

    def require_cache_lookup(self) -> None:
        if not self.cache_lookup_done or self.miss_tokens is None:
            raise ValueError(f"cache lookup is required before scheduling {self.request_id}")

    def remaining_prefill_tokens(self) -> int:
        """Return uncached prompt tokens that still need prefill compute."""

        self.require_cache_lookup()
        return max(0, self.prompt_tokens - self.num_computed_tokens)

    def apply_scheduled_tokens(self, scheduled_tokens: int, finish_time_ms: float) -> None:
        """Apply a completed prefill slice at iteration finish time."""

        self.require_cache_lookup()
        if scheduled_tokens <= 0:
            raise ValueError("scheduled_tokens must be positive")
        if finish_time_ms < self.arrival_time_ms:
            raise ValueError("finish_time_ms cannot be earlier than arrival_time_ms")

        remaining = self.remaining_prefill_tokens()
        if scheduled_tokens > remaining:
            raise ValueError("scheduled_tokens cannot exceed remaining prefill tokens")

        self.num_computed_tokens += scheduled_tokens
        self.scheduled_iteration_count += 1
        if self.num_computed_tokens == self.prompt_tokens:
            self.status = RequestStatus.FINISHED
            self.finish_time_ms = finish_time_ms
