"""Scheduler configuration used by batch-aware replay."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class SchedulerConfig:
    """Controls the vLLM-like scheduler approximation.

    The first Step4 scheduler only supports FCFS prefill scheduling. It models
    token and sequence budgets, but not finite KV slot allocation or preemption.
    """

    max_num_batched_tokens: int
    max_num_seqs: int
    enable_chunked_prefill: bool = True
    long_prefill_token_threshold: int | None = None
    policy: Literal["fcfs"] = "fcfs"

    def __post_init__(self) -> None:
        if self.max_num_batched_tokens <= 0:
            raise ValueError("max_num_batched_tokens must be positive")
        if self.max_num_seqs <= 0:
            raise ValueError("max_num_seqs must be positive")
        if self.policy != "fcfs":
            raise ValueError("Step4 only supports scheduler policy 'fcfs'")
        if self.long_prefill_token_threshold is not None:
            if self.long_prefill_token_threshold <= 0:
                raise ValueError("long_prefill_token_threshold must be positive")

    def per_request_chunk_limit(self) -> int:
        """Return the maximum prefill tokens one request may receive this iteration."""

        if not self.enable_chunked_prefill:
            return self.max_num_batched_tokens
        if self.long_prefill_token_threshold is None:
            return self.max_num_batched_tokens
        return min(self.max_num_batched_tokens, self.long_prefill_token_threshold)
