"""HBM capacity sweep orchestration and metrics."""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from infertwin.cache.event_sink import CacheEventSink, CacheEventStats, StatsOnlyCacheEventSink
from infertwin.cache.eviction import LRUEvictor
from infertwin.cache.hbm_lru import HBMCache
from infertwin.experiment.request_builder import (
    RequestBuildResult,
    build_request_build_result_from_config,
)
from infertwin.instance.request import SimulationRequest
from infertwin.latency.factory import build_batch_latency_backend
from infertwin.report.cache_events import CsvCacheEventWriter
from infertwin.replay.event_loop import BatchAwareReplayEngine
from infertwin.replay.metrics import (
    BatchAwareReplayResult,
    BatchAwareRequestMetrics,
    IterationMetrics,
)
from infertwin.scheduler.config import SchedulerConfig
from infertwin.scheduler.vllm_like import VllmLikeBatchScheduler

TRACE_SCOPE = "trace"
INSTANCE_SCOPE = "instance"


@dataclass(frozen=True, slots=True)
class CapacitySweepConfig:
    capacities: tuple[int, ...]
    cache_events: bool
    cache_event_capacities: tuple[int, ...]
    parallel_instances: bool = False


@dataclass(frozen=True, slots=True)
class CapacitySweepRow:
    hbm_capacity_blocks: int
    scope: str
    instance_uuid: str
    request_count: int
    iteration_count: int
    total_prompt_tokens: int
    hbm_hit_tokens: int
    ddr_hit_tokens: int
    miss_tokens: int
    total_hit_tokens: int
    kv_hit_rate: float
    hbm_hit_rate: float
    ddr_hit_rate: float
    p50_ttft_ms: float
    p90_ttft_ms: float
    p99_ttft_ms: float
    cache_event_count: int


@dataclass(frozen=True, slots=True)
class CapacitySweepResult:
    rows: tuple[CapacitySweepRow, ...]
    config_details: Mapping[str, object]
    cache_event_paths: Mapping[int, Path]


@dataclass(frozen=True, slots=True)
class CapacitySweepReportPaths:
    capacity_sweep_path: Path
    summary_path: Path


class CapacitySweepRunner:
    """Run finite HBM LRU replay for each configured capacity."""

    def __init__(self, config: Mapping[str, Any]) -> None:
        self.config = config
        self.sweep_config = build_capacity_sweep_config(config)

    def run(self) -> CapacitySweepResult:
        build_result = build_request_build_result_from_config(self.config)
        requests = list(build_result.requests)
        rows: list[CapacitySweepRow] = []
        cache_event_paths: dict[int, Path] = {}

        for capacity in self.sweep_config.capacities:
            event_path = self._cache_event_path(capacity)
            if event_path is None:
                sink = StatsOnlyCacheEventSink()
                replay_result = self._run_capacity(
                    capacity=capacity,
                    requests=requests,
                    cache_event_sink=sink,
                )
            else:
                cache_event_paths[capacity] = event_path
                with CsvCacheEventWriter(event_path) as sink:
                    replay_result = self._run_capacity(
                        capacity=capacity,
                        requests=requests,
                        cache_event_sink=sink,
                    )

            rows.extend(
                build_capacity_rows(
                    capacity=capacity,
                    request_metrics=replay_result.request_metrics,
                    iteration_metrics=replay_result.iteration_metrics,
                    cache_event_stats=replay_result.cache_event_stats,
                )
            )

        return CapacitySweepResult(
            rows=sort_capacity_rows(rows),
            config_details=self._config_details(build_result),
            cache_event_paths=cache_event_paths,
        )

    def _run_capacity(
        self,
        *,
        capacity: int,
        requests: list[SimulationRequest],
        cache_event_sink: CacheEventSink,
    ) -> BatchAwareReplayResult:
        latency_backend = build_batch_latency_backend(self.config)
        engine = BatchAwareReplayEngine(
            scheduler=VllmLikeBatchScheduler(_build_scheduler_config(self.config)),
            latency_backend=latency_backend,
            cache_factory=lambda _instance_uuid: HBMCache(
                capacity_blocks=capacity,
                evictor=LRUEvictor(),
            ),
        )
        return engine.run(requests, cache_event_sink=cache_event_sink)

    def _cache_event_path(self, capacity: int) -> Path | None:
        if capacity not in self.sweep_config.cache_event_capacities:
            return None
        output_dir = _output_dir(self.config)
        return output_dir / f"capacity_{capacity}" / "cache_events.csv"

    def _config_details(self, build_result: RequestBuildResult) -> Mapping[str, object]:
        latency_config = _mapping(self.config, "latency")
        backend = _required_str(latency_config, "backend")
        details: dict[str, object] = {
            "phase": "capacity_sweep",
            "request_build_accepted_count": build_result.accepted_count,
            "request_build_rejected_count": build_result.rejected_count,
            "capacities": self.sweep_config.capacities,
            "cache_events": self.sweep_config.cache_events,
            "cache_event_capacities": self.sweep_config.cache_event_capacities,
            "parallel_instances": self.sweep_config.parallel_instances,
            "latency_backend": backend,
            "model_name": _required_str(latency_config, "model_name"),
            "hardware_name": _required_str(latency_config, "hardware_name"),
            "eviction_policy": "lru",
        }
        backend_config = latency_config.get(backend)
        if isinstance(backend_config, Mapping):
            for key, value in backend_config.items():
                if isinstance(value, str | int | float | bool):
                    details[key] = value
        return details


def build_capacity_sweep_config(
    config: Mapping[str, Any],
    *,
    allowed_modes: tuple[str, ...] = ("capacity_sweep",),
) -> CapacitySweepConfig:
    """Validate and normalize the Step6 sweep config section."""

    mode = _mapping(config, "simulation").get("mode")
    if mode not in allowed_modes:
        allowed = ", ".join(allowed_modes)
        raise ValueError(f"capacity sweep requires simulation.mode in: {allowed}")
    if "targets" in config:
        raise ValueError(
            "Step6 capacity_sweep does not support targets; use hbm_capacity_blocks sweep "
            "and inspect capacity_sweep.csv."
        )

    sweep_config = _mapping(config, "sweep")
    capacities = _positive_int_tuple(
        sweep_config.get("hbm_capacity_blocks"),
        field_name="sweep.hbm_capacity_blocks",
    )
    if len(set(capacities)) != len(capacities):
        raise ValueError("sweep.hbm_capacity_blocks must not contain duplicate capacities")

    parallel_instances = _optional_bool(
        sweep_config,
        "parallel_instances",
        default=False,
    )
    if parallel_instances:
        raise ValueError("parallel_instances is reserved but not implemented in Step6 v1")

    cache_config = config.get("cache", {})
    if cache_config is not None and not isinstance(cache_config, Mapping):
        raise ValueError("cache config must be a mapping")
    if isinstance(cache_config, Mapping):
        eviction_policy = cache_config.get("eviction_policy", "lru")
        if eviction_policy != "lru":
            raise ValueError("capacity_sweep only supports cache.eviction_policy: lru")

    output_config = config.get("output", {})
    if output_config is None:
        output_config = {}
    if not isinstance(output_config, Mapping):
        raise ValueError("output config must be a mapping")
    cache_events = _optional_bool(output_config, "cache_events", default=False)
    cache_event_capacities = _optional_positive_int_tuple(
        output_config.get("cache_event_capacities", ()),
        field_name="output.cache_event_capacities",
    )
    if cache_events and not cache_event_capacities:
        raise ValueError(
            "output.cache_event_capacities must be non-empty when output.cache_events is true"
        )
    if not cache_events and cache_event_capacities:
        raise ValueError(
            "output.cache_event_capacities must be empty when output.cache_events is false"
        )
    unknown_event_capacities = sorted(set(cache_event_capacities).difference(capacities))
    if unknown_event_capacities:
        raise ValueError(
            "output.cache_event_capacities must be a subset of sweep.hbm_capacity_blocks"
        )

    return CapacitySweepConfig(
        capacities=capacities,
        cache_events=cache_events,
        cache_event_capacities=cache_event_capacities,
        parallel_instances=parallel_instances,
    )


def build_capacity_rows(
    *,
    capacity: int,
    request_metrics: Sequence[BatchAwareRequestMetrics],
    iteration_metrics: Sequence[IterationMetrics],
    cache_event_stats: CacheEventStats,
) -> tuple[CapacitySweepRow, ...]:
    """Aggregate one capacity replay into trace and per-instance rows."""

    rows = [
        _aggregate_row(
            capacity=capacity,
            scope=TRACE_SCOPE,
            instance_uuid="",
            request_metrics=request_metrics,
            iteration_metrics=iteration_metrics,
            cache_event_count=cache_event_stats.total_events,
        )
    ]

    requests_by_instance: dict[str, list[BatchAwareRequestMetrics]] = defaultdict(list)
    for metric in request_metrics:
        requests_by_instance[metric.instance_uuid].append(metric)
    iterations_by_instance: dict[str, list[IterationMetrics]] = defaultdict(list)
    for metric in iteration_metrics:
        iterations_by_instance[metric.instance_uuid].append(metric)

    for instance_uuid in sorted(requests_by_instance):
        rows.append(
            _aggregate_row(
                capacity=capacity,
                scope=INSTANCE_SCOPE,
                instance_uuid=instance_uuid,
                request_metrics=requests_by_instance[instance_uuid],
                iteration_metrics=iterations_by_instance.get(instance_uuid, ()),
                cache_event_count=0,
            )
        )

    return tuple(rows)


def sort_capacity_rows(rows: Sequence[CapacitySweepRow]) -> tuple[CapacitySweepRow, ...]:
    return tuple(sorted(rows, key=_row_sort_key))


def percentile(values: Sequence[float], percentile_value: float) -> float:
    if not values:
        return 0.0
    if percentile_value < 0 or percentile_value > 100:
        raise ValueError("percentile must be between 0 and 100")
    sorted_values = sorted(values)
    rank = math.ceil((percentile_value / 100) * len(sorted_values))
    index = min(max(rank - 1, 0), len(sorted_values) - 1)
    return sorted_values[index]


def _aggregate_row(
    *,
    capacity: int,
    scope: str,
    instance_uuid: str,
    request_metrics: Sequence[BatchAwareRequestMetrics],
    iteration_metrics: Sequence[IterationMetrics],
    cache_event_count: int,
) -> CapacitySweepRow:
    total_prompt_tokens = sum(item.prompt_tokens for item in request_metrics)
    hbm_hit_tokens = sum(item.hbm_hit_tokens for item in request_metrics)
    ddr_hit_tokens = sum(item.ddr_hit_tokens for item in request_metrics)
    miss_tokens = sum(item.miss_tokens for item in request_metrics)
    total_hit_tokens = hbm_hit_tokens + ddr_hit_tokens
    if hbm_hit_tokens + ddr_hit_tokens + miss_tokens != total_prompt_tokens:
        raise ValueError("capacity sweep token invariant failed")

    ttft_values = [item.ttft_ms for item in request_metrics]
    return CapacitySweepRow(
        hbm_capacity_blocks=capacity,
        scope=scope,
        instance_uuid=instance_uuid,
        request_count=len(request_metrics),
        iteration_count=len(iteration_metrics),
        total_prompt_tokens=total_prompt_tokens,
        hbm_hit_tokens=hbm_hit_tokens,
        ddr_hit_tokens=ddr_hit_tokens,
        miss_tokens=miss_tokens,
        total_hit_tokens=total_hit_tokens,
        kv_hit_rate=_safe_rate(total_hit_tokens, total_prompt_tokens),
        hbm_hit_rate=_safe_rate(hbm_hit_tokens, total_prompt_tokens),
        ddr_hit_rate=_safe_rate(ddr_hit_tokens, total_prompt_tokens),
        p50_ttft_ms=percentile(ttft_values, 50),
        p90_ttft_ms=percentile(ttft_values, 90),
        p99_ttft_ms=percentile(ttft_values, 99),
        cache_event_count=cache_event_count,
    )


def _row_sort_key(row: CapacitySweepRow) -> tuple[int, int, str]:
    scope_order = 0 if row.scope == TRACE_SCOPE else 1
    return (row.hbm_capacity_blocks, scope_order, row.instance_uuid)


def _safe_rate(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return numerator / denominator


def _mapping(parent: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = parent.get(key)
    if not isinstance(value, Mapping):
        raise ValueError(f"{key} config must be a mapping")
    return value


def _required_str(config: Mapping[str, Any], key: str) -> str:
    value = config.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be a non-empty string")
    return value


def _optional_bool(config: Mapping[str, Any], key: str, *, default: bool) -> bool:
    value = config.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be a boolean")
    return value


def _positive_int_tuple(value: object, *, field_name: str) -> tuple[int, ...]:
    if not isinstance(value, list | tuple) or not value:
        raise ValueError(f"{field_name} must be a non-empty list of positive integers")
    return _int_tuple(value, field_name=field_name)


def _optional_positive_int_tuple(value: object, *, field_name: str) -> tuple[int, ...]:
    if value is None:
        return ()
    if not isinstance(value, list | tuple):
        raise ValueError(f"{field_name} must be a list of positive integers")
    if not value:
        return ()
    return _int_tuple(value, field_name=field_name)


def _int_tuple(value: Sequence[object], *, field_name: str) -> tuple[int, ...]:
    items: list[int] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int) or item <= 0:
            raise ValueError(f"{field_name} must contain only positive integers")
        items.append(item)
    return tuple(items)


def build_scheduler_config_from_config(config: Mapping[str, Any]) -> SchedulerConfig:
    scheduler_config = _mapping(config, "scheduler")
    return SchedulerConfig(
        max_num_batched_tokens=_required_int(
            scheduler_config,
            "max_num_batched_tokens",
        ),
        max_num_seqs=_required_int(scheduler_config, "max_num_seqs"),
        enable_chunked_prefill=_optional_bool(
            scheduler_config,
            "enable_chunked_prefill",
            default=True,
        ),
        long_prefill_token_threshold=_optional_int(
            scheduler_config,
            "long_prefill_token_threshold",
        ),
        policy=_optional_str(scheduler_config, "policy", default="fcfs"),  # type: ignore[arg-type]
    )


def capacity_sweep_output_dir(config: Mapping[str, Any]) -> Path:
    output_config = config.get("output", {})
    if output_config is None:
        output_config = {}
    if not isinstance(output_config, Mapping):
        raise ValueError("output config must be a mapping")
    directory = output_config.get("directory", "reports")
    if not isinstance(directory, str) or not directory:
        raise ValueError("output.directory must be a non-empty string")
    return Path(directory)


def _build_scheduler_config(config: Mapping[str, Any]) -> SchedulerConfig:
    return build_scheduler_config_from_config(config)


def _output_dir(config: Mapping[str, Any]) -> Path:
    return capacity_sweep_output_dir(config)


def _required_int(config: Mapping[str, Any], key: str) -> int:
    value = config.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key} must be an integer")
    return value


def _optional_int(config: Mapping[str, Any], key: str) -> int | None:
    if key not in config or config[key] is None:
        return None
    return _required_int(config, key)


def _optional_str(config: Mapping[str, Any], key: str, *, default: str) -> str:
    value = config.get(key, default)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be a non-empty string")
    return value
