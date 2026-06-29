"""Metric sinks for streaming replay."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from infertwin.cache.event_sink import CacheEventSink, CacheEventStats
from infertwin.experiment.sweep import (
    INSTANCE_SCOPE,
    TRACE_SCOPE,
    CapacitySweepRow,
    percentile,
    sort_capacity_rows,
)
from infertwin.replay.metrics import (
    BatchAwareReplayResult,
    BatchAwareRequestMetrics,
    IterationMetrics,
)
from infertwin.replay.timeline import ITERATION_TTFT_GRANULARITY, LEGACY_TIMELINE_MODE


class ReplayMetricSink(Protocol):
    """Sink for streaming replay metrics."""

    def on_request(self, metric: BatchAwareRequestMetrics) -> None:
        """Consume one completed request metric."""

    def on_iteration(self, metric: IterationMetrics) -> None:
        """Consume one completed iteration metric."""


@dataclass(frozen=True, slots=True)
class StreamingReplayStats:
    """Small execution stats for one streaming replay run."""

    emitted_request_count: int
    emitted_iteration_count: int
    max_active_requests: int
    final_active_requests: int


class InMemoryReplayMetricSink:
    """Metric sink used by tests and small equivalence checks."""

    def __init__(self) -> None:
        self._request_metrics: list[BatchAwareRequestMetrics] = []
        self._iteration_metrics: list[IterationMetrics] = []

    @property
    def request_metrics(self) -> tuple[BatchAwareRequestMetrics, ...]:
        return tuple(self._request_metrics)

    @property
    def iteration_metrics(self) -> tuple[IterationMetrics, ...]:
        return tuple(self._iteration_metrics)

    def on_request(self, metric: BatchAwareRequestMetrics) -> None:
        self._request_metrics.append(metric)

    def on_iteration(self, metric: IterationMetrics) -> None:
        self._iteration_metrics.append(metric)

    def to_replay_result(self, cache_event_sink: CacheEventSink) -> BatchAwareReplayResult:
        return BatchAwareReplayResult(
            request_metrics=self.request_metrics,
            iteration_metrics=self.iteration_metrics,
            cache_event_stats=cache_event_sink.snapshot_stats(),
            cache_events=cache_event_sink.snapshot_events(),
        )


class CapacitySweepStreamingMetricAggregator:
    """Aggregate streaming replay metrics into capacity sweep rows."""

    def __init__(
        self,
        *,
        timeline_mode: str = LEGACY_TIMELINE_MODE,
        ttft_granularity: str = ITERATION_TTFT_GRANULARITY,
    ) -> None:
        self._trace = _ScopeAccumulator(
            timeline_mode=timeline_mode,
            ttft_granularity=ttft_granularity,
        )
        self._instances: dict[str, _ScopeAccumulator] = {}
        self._timeline_mode = timeline_mode
        self._ttft_granularity = ttft_granularity

    @property
    def request_count(self) -> int:
        return self._trace.request_count

    @property
    def iteration_count(self) -> int:
        return self._trace.iteration_count

    def on_request(self, metric: BatchAwareRequestMetrics) -> None:
        _validate_request_metric(metric)
        self._trace.on_request(metric)
        self._instance(metric.instance_uuid).on_request(metric)

    def on_iteration(self, metric: IterationMetrics) -> None:
        self._trace.on_iteration(metric)
        self._instance(metric.instance_uuid).on_iteration(metric)

    def build_rows(
        self,
        *,
        capacity: int,
        cache_event_stats: CacheEventStats,
    ) -> tuple[CapacitySweepRow, ...]:
        rows = [
            self._trace.to_row(
                capacity=capacity,
                scope=TRACE_SCOPE,
                instance_uuid="",
                cache_event_count=cache_event_stats.total_events,
            )
        ]
        rows.extend(
            accumulator.to_row(
                capacity=capacity,
                scope=INSTANCE_SCOPE,
                instance_uuid=instance_uuid,
                cache_event_count=0,
            )
            for instance_uuid, accumulator in sorted(self._instances.items())
            if accumulator.request_count > 0
        )
        return sort_capacity_rows(rows)

    def _instance(self, instance_uuid: str) -> "_ScopeAccumulator":
        if not instance_uuid:
            raise ValueError("instance_uuid must be a non-empty string")
        accumulator = self._instances.get(instance_uuid)
        if accumulator is None:
            accumulator = _ScopeAccumulator(
                timeline_mode=self._timeline_mode,
                ttft_granularity=self._ttft_granularity,
            )
            self._instances[instance_uuid] = accumulator
        return accumulator


@dataclass(slots=True)
class _ScopeAccumulator:
    timeline_mode: str = LEGACY_TIMELINE_MODE
    ttft_granularity: str = ITERATION_TTFT_GRANULARITY
    request_count: int = 0
    iteration_count: int = 0
    total_prompt_tokens: int = 0
    hbm_hit_tokens: int = 0
    ddr_hit_tokens: int = 0
    miss_tokens: int = 0
    total_kv_load_ms: float = 0.0
    total_compute_wait_ms: float = 0.0
    total_kv_load_wait_ms: float = 0.0
    total_uncached_prefill_compute_ms: float = 0.0
    total_unattributed_ttft_ms: float = 0.0
    total_chunk_count: int = 0
    total_load_event_count: int = 0
    total_progressive_materialized_blocks: int = 0
    total_progressive_materialized_tokens: int = 0
    total_waiting_for_compute_count: int = 0
    total_waiting_for_kv_load_count: int = 0
    total_scheduled_chunk_count: int = 0
    max_kv_transfer_queue_depth: int = 0
    ttft_values: list[float] | None = None
    kv_load_values: list[float] | None = None
    compute_wait_values: list[float] | None = None
    kv_load_wait_values: list[float] | None = None
    uncached_prefill_compute_values: list[float] | None = None

    def on_request(self, metric: BatchAwareRequestMetrics) -> None:
        self._record_timeline_values(
            timeline_mode=metric.timeline_mode,
            ttft_granularity=metric.ttft_granularity,
        )
        self.request_count += 1
        self.total_prompt_tokens += metric.prompt_tokens
        self.hbm_hit_tokens += metric.hbm_hit_tokens
        self.ddr_hit_tokens += metric.ddr_hit_tokens
        self.miss_tokens += metric.miss_tokens
        self.total_kv_load_ms += metric.kv_load_ms
        self.total_compute_wait_ms += metric.compute_wait_ms
        self.total_kv_load_wait_ms += metric.kv_load_wait_ms
        self.total_uncached_prefill_compute_ms += metric.uncached_prefill_compute_ms
        self.total_unattributed_ttft_ms += metric.unattributed_ttft_ms
        self.total_chunk_count += metric.chunk_count
        self.total_load_event_count += metric.load_event_count
        self.total_progressive_materialized_blocks += metric.progressive_materialized_blocks
        self.total_progressive_materialized_tokens += metric.progressive_materialized_tokens
        self._ttft_values.append(metric.ttft_ms)
        self._kv_load_values.append(metric.kv_load_ms)
        self._compute_wait_values.append(metric.compute_wait_ms)
        self._kv_load_wait_values.append(metric.kv_load_wait_ms)
        self._uncached_prefill_compute_values.append(metric.uncached_prefill_compute_ms)

    def on_iteration(self, metric: IterationMetrics) -> None:
        self._record_timeline_values(
            timeline_mode=metric.timeline_mode,
            ttft_granularity=metric.ttft_granularity,
        )
        self.iteration_count += 1
        self.total_waiting_for_compute_count += metric.waiting_for_compute_count
        self.total_waiting_for_kv_load_count += metric.waiting_for_kv_load_count
        self.total_scheduled_chunk_count += metric.scheduled_chunk_count
        self.max_kv_transfer_queue_depth = max(
            self.max_kv_transfer_queue_depth,
            metric.kv_transfer_queue_depth_max,
        )

    def to_row(
        self,
        *,
        capacity: int,
        scope: str,
        instance_uuid: str,
        cache_event_count: int,
    ) -> CapacitySweepRow:
        total_hit_tokens = self.hbm_hit_tokens + self.ddr_hit_tokens
        if total_hit_tokens + self.miss_tokens != self.total_prompt_tokens:
            raise ValueError("capacity sweep token invariant failed")
        return CapacitySweepRow(
            hbm_capacity_blocks=capacity,
            scope=scope,
            instance_uuid=instance_uuid,
            request_count=self.request_count,
            iteration_count=self.iteration_count,
            total_prompt_tokens=self.total_prompt_tokens,
            hbm_hit_tokens=self.hbm_hit_tokens,
            ddr_hit_tokens=self.ddr_hit_tokens,
            miss_tokens=self.miss_tokens,
            total_hit_tokens=total_hit_tokens,
            kv_hit_rate=_safe_rate(total_hit_tokens, self.total_prompt_tokens),
            hbm_hit_rate=_safe_rate(self.hbm_hit_tokens, self.total_prompt_tokens),
            ddr_hit_rate=_safe_rate(self.ddr_hit_tokens, self.total_prompt_tokens),
            p50_ttft_ms=percentile(self._ttft_values, 50),
            p90_ttft_ms=percentile(self._ttft_values, 90),
            p99_ttft_ms=percentile(self._ttft_values, 99),
            cache_event_count=cache_event_count,
            total_kv_load_ms=self.total_kv_load_ms,
            avg_kv_load_ms=_safe_rate(self.total_kv_load_ms, self.request_count),
            p50_kv_load_ms=percentile(self._kv_load_values, 50),
            p90_kv_load_ms=percentile(self._kv_load_values, 90),
            p99_kv_load_ms=percentile(self._kv_load_values, 99),
            timeline_mode=self.timeline_mode,
            ttft_granularity=self.ttft_granularity,
            total_compute_wait_ms=self.total_compute_wait_ms,
            avg_compute_wait_ms=_safe_rate(self.total_compute_wait_ms, self.request_count),
            p50_compute_wait_ms=percentile(self._compute_wait_values, 50),
            p90_compute_wait_ms=percentile(self._compute_wait_values, 90),
            p99_compute_wait_ms=percentile(self._compute_wait_values, 99),
            total_kv_load_wait_ms=self.total_kv_load_wait_ms,
            avg_kv_load_wait_ms=_safe_rate(self.total_kv_load_wait_ms, self.request_count),
            p50_kv_load_wait_ms=percentile(self._kv_load_wait_values, 50),
            p90_kv_load_wait_ms=percentile(self._kv_load_wait_values, 90),
            p99_kv_load_wait_ms=percentile(self._kv_load_wait_values, 99),
            total_uncached_prefill_compute_ms=self.total_uncached_prefill_compute_ms,
            avg_uncached_prefill_compute_ms=_safe_rate(
                self.total_uncached_prefill_compute_ms,
                self.request_count,
            ),
            p90_uncached_prefill_compute_ms=percentile(
                self._uncached_prefill_compute_values,
                90,
            ),
            total_unattributed_ttft_ms=self.total_unattributed_ttft_ms,
            avg_unattributed_ttft_ms=_safe_rate(
                self.total_unattributed_ttft_ms,
                self.request_count,
            ),
            total_chunk_count=self.total_chunk_count,
            total_load_event_count=self.total_load_event_count,
            total_progressive_materialized_blocks=self.total_progressive_materialized_blocks,
            total_progressive_materialized_tokens=self.total_progressive_materialized_tokens,
            total_waiting_for_compute_count=self.total_waiting_for_compute_count,
            total_waiting_for_kv_load_count=self.total_waiting_for_kv_load_count,
            total_scheduled_chunk_count=self.total_scheduled_chunk_count,
            max_kv_transfer_queue_depth=self.max_kv_transfer_queue_depth,
        )

    def _record_timeline_values(
        self,
        *,
        timeline_mode: str,
        ttft_granularity: str,
    ) -> None:
        if timeline_mode != self.timeline_mode:
            raise ValueError("capacity sweep timeline_mode invariant failed: mixed values")
        if ttft_granularity != self.ttft_granularity:
            raise ValueError("capacity sweep ttft_granularity invariant failed: mixed values")

    @property
    def _ttft_values(self) -> list[float]:
        if self.ttft_values is None:
            self.ttft_values = []
        return self.ttft_values

    @property
    def _kv_load_values(self) -> list[float]:
        if self.kv_load_values is None:
            self.kv_load_values = []
        return self.kv_load_values

    @property
    def _compute_wait_values(self) -> list[float]:
        if self.compute_wait_values is None:
            self.compute_wait_values = []
        return self.compute_wait_values

    @property
    def _kv_load_wait_values(self) -> list[float]:
        if self.kv_load_wait_values is None:
            self.kv_load_wait_values = []
        return self.kv_load_wait_values

    @property
    def _uncached_prefill_compute_values(self) -> list[float]:
        if self.uncached_prefill_compute_values is None:
            self.uncached_prefill_compute_values = []
        return self.uncached_prefill_compute_values


def _validate_request_metric(metric: BatchAwareRequestMetrics) -> None:
    total_tokens = metric.hbm_hit_tokens + metric.ddr_hit_tokens + metric.miss_tokens
    if total_tokens != metric.prompt_tokens:
        raise ValueError(
            "request metric token invariant failed for "
            f"{metric.request_id}: hbm_hit_tokens + ddr_hit_tokens + miss_tokens "
            "must equal prompt_tokens"
        )


def _safe_rate(numerator: float, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return numerator / denominator
