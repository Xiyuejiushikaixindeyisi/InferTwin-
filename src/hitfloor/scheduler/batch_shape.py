"""Iteration-level batch shape emitted by the scheduler."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ScheduledSlice:
    """Tokens scheduled for one request in one iteration."""

    request_id: str
    scheduled_prefill_tokens: int
    computed_tokens_before: int
    computed_tokens_after: int
    prompt_tokens: int
    cached_prefix_tokens: int
    previous_chunk_tokens: int

    def __post_init__(self) -> None:
        if self.scheduled_prefill_tokens <= 0:
            raise ValueError("scheduled_prefill_tokens must be positive")
        if self.computed_tokens_before < 0 or self.computed_tokens_after < 0:
            raise ValueError("computed token counts must be non-negative")
        if self.cached_prefix_tokens < 0:
            raise ValueError("cached_prefix_tokens must be non-negative")
        if self.previous_chunk_tokens < 0:
            raise ValueError("previous_chunk_tokens must be non-negative")
        expected_before = self.cached_prefix_tokens + self.previous_chunk_tokens
        if self.computed_tokens_before != expected_before:
            raise ValueError(
                "computed_tokens_before must equal cached_prefix_tokens + previous_chunk_tokens"
            )
        expected_after = self.computed_tokens_before + self.scheduled_prefill_tokens
        if self.computed_tokens_after != expected_after:
            raise ValueError("computed_tokens_after must equal before + scheduled tokens")
        if self.prompt_tokens < self.computed_tokens_after:
            raise ValueError("computed_tokens_after cannot exceed prompt_tokens")


@dataclass(frozen=True, slots=True)
class BatchShape:
    """Scheduler output for one replay iteration.

    External simulators such as AIConfigurator and MkSim must consume this
    through an explicit adapter/converter; it is not a direct simulator input.
    """

    instance_uuid: str
    iteration_id: int
    start_time_ms: float
    request_slices: tuple[ScheduledSlice, ...]
    scheduled_decode_tokens: int = 0

    def __post_init__(self) -> None:
        if self.iteration_id < 0:
            raise ValueError("iteration_id must be non-negative")
        if self.start_time_ms < 0:
            raise ValueError("start_time_ms must be non-negative")
        if self.scheduled_decode_tokens < 0:
            raise ValueError("scheduled_decode_tokens must be non-negative")

    @property
    def batch_size(self) -> int:
        return len(self.request_slices)

    @property
    def scheduled_prefill_tokens(self) -> int:
        return sum(item.scheduled_prefill_tokens for item in self.request_slices)

    @property
    def max_query_len(self) -> int:
        if not self.request_slices:
            return 0
        return max(item.scheduled_prefill_tokens for item in self.request_slices)

    @property
    def total_context_tokens(self) -> int:
        return sum(item.computed_tokens_before for item in self.request_slices)
