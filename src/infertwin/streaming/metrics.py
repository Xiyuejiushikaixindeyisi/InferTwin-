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

    def __init__(self) -> None:
        self._trace = _ScopeAccumulator()
        self._instances: dict[str, _ScopeAccumulator] = {}

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
            accumulator = _ScopeAccumulator()
            self._instances[instance_uuid] = accumulator
        return accumulator


@dataclass(slots=True)
class _ScopeAccumulator:
    request_count: int = 0
    iteration_count: int = 0
    total_prompt_tokens: int = 0
    hbm_hit_tokens: int = 0
    ddr_hit_tokens: int = 0
    miss_tokens: int = 0
    total_kv_load_ms: float = 0.0
    ttft_values: list[float] | None = None
    kv_load_values: list[float] | None = None

    def on_request(self, metric: BatchAwareRequestMetrics) -> None:
        self.request_count += 1
        self.total_prompt_tokens += metric.prompt_tokens
        self.hbm_hit_tokens += metric.hbm_hit_tokens
        self.ddr_hit_tokens += metric.ddr_hit_tokens
        self.miss_tokens += metric.miss_tokens
        self.total_kv_load_ms += metric.kv_load_ms
        self._ttft_values.append(metric.ttft_ms)
        self._kv_load_values.append(metric.kv_load_ms)

    def on_iteration(self, _metric: IterationMetrics) -> None:
        self.iteration_count += 1

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
        )

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
