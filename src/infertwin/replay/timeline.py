"""Timeline schema for Step9 replay-facing metrics.

This module defines pure data structures only. It does not advance replay
state, estimate latency, materialize cache blocks, or render reports.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

LEGACY_TIMELINE_MODE = "legacy_iteration_v1"
PROGRESSIVE_TIMELINE_MODE = "batch_aware_hbm_ddr_lru_progressive_timeline"

ITERATION_TTFT_GRANULARITY = "iteration"
CHUNK_TTFT_GRANULARITY = "chunk"


class RequestTimelineState(str, Enum):
    """Replay-facing request states for chunk timeline accounting."""

    PENDING = "pending"
    WAITING_FOR_COMPUTE = "waiting_for_compute"
    WAITING_FOR_KV_LOAD = "waiting_for_kv_load"
    RUNNING_CHUNK = "running_chunk"
    FINISHED = "finished"


@dataclass(frozen=True, slots=True)
class ChunkTimelineEntry:
    """One scheduled prefill chunk on a request timeline."""

    request_id: str
    instance_uuid: str
    iteration_id: int
    start_time_ms: float
    finish_time_ms: float
    scheduled_prefill_tokens: int
    computed_tokens_before: int
    computed_tokens_after: int
    prefill_compute_ms: float = 0.0

    def __post_init__(self) -> None:
        _require_non_empty(self.request_id, "request_id")
        _require_non_empty(self.instance_uuid, "instance_uuid")
        _require_non_negative_int(self.iteration_id, "iteration_id")
        _require_non_negative_float(self.start_time_ms, "start_time_ms")
        _require_non_negative_float(self.finish_time_ms, "finish_time_ms")
        _require_time_order(
            start_time_ms=self.start_time_ms,
            finish_time_ms=self.finish_time_ms,
        )
        _require_non_negative_int(
            self.scheduled_prefill_tokens,
            "scheduled_prefill_tokens",
        )
        _require_non_negative_int(self.computed_tokens_before, "computed_tokens_before")
        _require_non_negative_int(self.computed_tokens_after, "computed_tokens_after")
        if self.computed_tokens_after < self.computed_tokens_before:
            raise ValueError("computed_tokens_after cannot be smaller than computed_tokens_before")
        _require_non_negative_float(self.prefill_compute_ms, "prefill_compute_ms")


@dataclass(frozen=True, slots=True)
class KVLoadTimelineEntry:
    """One KV load request on a request timeline."""

    request_id: str
    instance_uuid: str
    ready_time_ms: float
    start_time_ms: float
    finish_time_ms: float
    kv_load_tokens: int = 0
    kv_load_bytes: int = 0
    kv_load_ms: float = 0.0
    kv_load_wait_ms: float = 0.0
    source_tier: str = "ddr"

    def __post_init__(self) -> None:
        _require_non_empty(self.request_id, "request_id")
        _require_non_empty(self.instance_uuid, "instance_uuid")
        _require_non_empty(self.source_tier, "source_tier")
        _require_non_negative_float(self.ready_time_ms, "ready_time_ms")
        _require_non_negative_float(self.start_time_ms, "start_time_ms")
        _require_non_negative_float(self.finish_time_ms, "finish_time_ms")
        if self.start_time_ms < self.ready_time_ms:
            raise ValueError("start_time_ms cannot be earlier than ready_time_ms")
        _require_time_order(
            start_time_ms=self.start_time_ms,
            finish_time_ms=self.finish_time_ms,
        )
        _require_non_negative_int(self.kv_load_tokens, "kv_load_tokens")
        _require_non_negative_int(self.kv_load_bytes, "kv_load_bytes")
        _require_non_negative_float(self.kv_load_ms, "kv_load_ms")
        _require_non_negative_float(self.kv_load_wait_ms, "kv_load_wait_ms")


@dataclass(frozen=True, slots=True)
class RequestTimelineSummary:
    """Aggregate timeline metrics for one completed request."""

    timeline_mode: str = LEGACY_TIMELINE_MODE
    ttft_granularity: str = ITERATION_TTFT_GRANULARITY
    compute_wait_ms: float = 0.0
    kv_load_wait_ms: float = 0.0
    uncached_prefill_compute_ms: float = 0.0
    unattributed_ttft_ms: float = 0.0
    chunk_count: int = 0
    load_event_count: int = 0
    progressive_materialized_blocks: int = 0
    progressive_materialized_tokens: int = 0

    def __post_init__(self) -> None:
        _require_non_empty(self.timeline_mode, "timeline_mode")
        _require_non_empty(self.ttft_granularity, "ttft_granularity")
        _require_non_negative_float(self.compute_wait_ms, "compute_wait_ms")
        _require_non_negative_float(self.kv_load_wait_ms, "kv_load_wait_ms")
        _require_non_negative_float(
            self.uncached_prefill_compute_ms,
            "uncached_prefill_compute_ms",
        )
        _require_non_negative_float(
            self.unattributed_ttft_ms,
            "unattributed_ttft_ms",
        )
        _require_non_negative_int(self.chunk_count, "chunk_count")
        _require_non_negative_int(self.load_event_count, "load_event_count")
        _require_non_negative_int(
            self.progressive_materialized_blocks,
            "progressive_materialized_blocks",
        )
        _require_non_negative_int(
            self.progressive_materialized_tokens,
            "progressive_materialized_tokens",
        )

    @property
    def scheduler_wait_ms(self) -> float:
        """Compatibility wait field for Step9 progressive timeline mode."""

        return self.compute_wait_ms + self.kv_load_wait_ms

    @property
    def composed_ttft_ms(self) -> float:
        """TTFT explained by the current replay timeline granularity."""

        return (
            self.compute_wait_ms
            + self.kv_load_wait_ms
            + self.uncached_prefill_compute_ms
            + self.unattributed_ttft_ms
        )


def _require_non_empty(value: str, field_name: str) -> None:
    if not value:
        raise ValueError(f"{field_name} must be non-empty")


def _require_non_negative_float(value: float, field_name: str) -> None:
    if value < 0:
        raise ValueError(f"{field_name} must be non-negative")


def _require_non_negative_int(value: int, field_name: str) -> None:
    if value < 0:
        raise ValueError(f"{field_name} must be non-negative")


def _require_time_order(*, start_time_ms: float, finish_time_ms: float) -> None:
    if finish_time_ms < start_time_ms:
        raise ValueError("finish_time_ms cannot be earlier than start_time_ms")
