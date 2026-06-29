"""Mutable request state used by the batch scheduler."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from infertwin.request.block_hasher import PrefixBlock
from infertwin.replay.timeline import LEGACY_TIMELINE_MODE, RequestTimelineState


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
    effective_block_size: int = 0
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
    pending_kv_load_tokens: int = 0
    pending_kv_load_bytes: int = 0
    kv_load_scheduled: bool = False
    prefill_compute_ms: float = 0.0
    kv_load_ms: float = 0.0
    queue_ms: float = 0.0
    timeline_mode: str = LEGACY_TIMELINE_MODE
    timeline_state: RequestTimelineState = RequestTimelineState.WAITING_FOR_COMPUTE
    compute_wait_ms: float = 0.0
    kv_load_wait_ms: float = 0.0
    unattributed_ttft_ms: float = 0.0
    chunk_count: int = 0
    load_event_count: int = 0
    progressive_materialized_blocks: int = 0
    progressive_materialized_tokens: int = 0
    progressive_materialized_block_keys: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        if self.prompt_tokens < 0:
            raise ValueError("prompt_tokens must be non-negative")
        if self.effective_block_size < 0:
            raise ValueError("effective_block_size must be non-negative")
        if self.arrival_time_ms < 0:
            raise ValueError("arrival_time_ms must be non-negative")
        if self.arrival_seq < 0:
            raise ValueError("arrival_seq must be non-negative")
        if (
            self.compute_wait_ms < 0
            or self.kv_load_wait_ms < 0
            or self.unattributed_ttft_ms < 0
        ):
            raise ValueError("timeline latency values must be non-negative")
        if self.chunk_count < 0 or self.load_event_count < 0:
            raise ValueError("timeline event counts must be non-negative")
        if self.progressive_materialized_blocks < 0 or self.progressive_materialized_tokens < 0:
            raise ValueError("progressive materialization counters must be non-negative")

    def set_cache_lookup(
        self,
        cached_tokens: int,
        miss_tokens: int,
        kv_load_tokens: int = 0,
        kv_load_bytes: int = 0,
    ) -> None:
        """Attach prefix-cache lookup results before scheduling this request."""

        if self.cache_lookup_done:
            raise ValueError(f"cache lookup already recorded for request {self.request_id}")
        if cached_tokens < 0 or miss_tokens < 0:
            raise ValueError("cached_tokens and miss_tokens must be non-negative")
        if kv_load_tokens < 0:
            raise ValueError("kv_load_tokens must be non-negative")
        if kv_load_bytes < 0:
            raise ValueError("kv_load_bytes must be non-negative")
        if cached_tokens + miss_tokens != self.prompt_tokens:
            raise ValueError("cached_tokens + miss_tokens must equal prompt_tokens")

        self.cached_tokens = cached_tokens
        self.miss_tokens = miss_tokens
        self.num_computed_tokens = cached_tokens
        self.pending_kv_load_tokens = kv_load_tokens
        self.pending_kv_load_bytes = kv_load_bytes
        self.kv_load_scheduled = False
        self.cache_lookup_done = True

    def has_pending_kv_load(self) -> bool:
        """Return whether the first scheduled slice still owes KV load latency."""

        self.require_cache_lookup()
        return (
            not self.kv_load_scheduled
            and (self.pending_kv_load_tokens > 0 or self.pending_kv_load_bytes > 0)
        )

    def consume_pending_kv_load(self) -> tuple[int, int]:
        """Consume request-level KV load shape exactly once."""

        if not self.has_pending_kv_load():
            return 0, 0
        tokens = self.pending_kv_load_tokens
        bytes_ = self.pending_kv_load_bytes
        self.pending_kv_load_tokens = 0
        self.pending_kv_load_bytes = 0
        self.kv_load_scheduled = True
        return tokens, bytes_

    def record_latency_contribution(
        self,
        *,
        prefill_compute_ms: float,
        kv_load_ms: float,
        queue_ms: float,
    ) -> None:
        """Accumulate report-only latency attribution for this request."""

        if prefill_compute_ms < 0 or kv_load_ms < 0 or queue_ms < 0:
            raise ValueError("latency contributions must be non-negative")
        self.prefill_compute_ms += prefill_compute_ms
        self.kv_load_ms += kv_load_ms
        self.queue_ms += queue_ms

    def record_compute_wait(self, duration_ms: float) -> None:
        """Accumulate Step9 engine-internal compute wait for this request."""

        if duration_ms < 0:
            raise ValueError("compute wait duration must be non-negative")
        self.compute_wait_ms += duration_ms

    def record_kv_load_wait(self, duration_ms: float) -> None:
        """Accumulate Step9 KV-load wait for this request."""

        if duration_ms < 0:
            raise ValueError("KV load wait duration must be non-negative")
        self.kv_load_wait_ms += duration_ms

    def record_kv_load_event(self, duration_ms: float) -> None:
        """Record one request-level KV load event and its elapsed wait."""

        self.record_kv_load_wait(duration_ms)
        self.load_event_count += 1

    def record_prefill_chunk(self) -> None:
        """Record one scheduled uncached-prefill compute chunk."""

        self.chunk_count += 1

    def record_progressive_materialization(
        self,
        blocks: tuple[PrefixBlock, ...],
    ) -> tuple[PrefixBlock, ...]:
        """Record request-local progressive materialization counters."""

        recorded: list[PrefixBlock] = []
        for block in blocks:
            if block.block_key in self.progressive_materialized_block_keys:
                continue
            self.progressive_materialized_block_keys.add(block.block_key)
            self.progressive_materialized_blocks += 1
            self.progressive_materialized_tokens += block.token_count
            recorded.append(block)
        return tuple(recorded)

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
        self.record_prefill_chunk()
        if self.num_computed_tokens == self.prompt_tokens:
            self.status = RequestStatus.FINISHED
            self.timeline_state = RequestTimelineState.FINISHED
            self.finish_time_ms = finish_time_ms

    def apply_load_only_iteration(self, finish_time_ms: float) -> None:
        """Apply a completed load-only iteration for an all-cached DDR hit."""

        self.require_cache_lookup()
        if finish_time_ms < self.arrival_time_ms:
            raise ValueError("finish_time_ms cannot be earlier than arrival_time_ms")
        if self.remaining_prefill_tokens() != 0:
            raise ValueError("load-only iteration requires zero remaining prefill tokens")

        self.scheduled_iteration_count += 1
        self.status = RequestStatus.FINISHED
        self.timeline_state = RequestTimelineState.FINISHED
        self.finish_time_ms = finish_time_ms
