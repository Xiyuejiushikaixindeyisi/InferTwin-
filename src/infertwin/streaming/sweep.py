"""Streaming HBM capacity sweep orchestration."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from infertwin.cache.event_sink import CacheEventSink, StatsOnlyCacheEventSink
from infertwin.cache.eviction import LRUEvictor
from infertwin.cache.hbm_lru import HBMCache
from infertwin.experiment.sweep import (
    CapacitySweepResult,
    CapacitySweepRow,
    build_capacity_sweep_config,
    build_scheduler_config_from_config,
    capacity_sweep_output_dir,
    sort_capacity_rows,
)
from infertwin.latency.instance_resolver import (
    InstanceLatencyBackendResolver,
    build_instance_latency_backend_resolver,
)
from infertwin.report.cache_events import CsvCacheEventWriter
from infertwin.scheduler.config import SchedulerConfig
from infertwin.scheduler.vllm_like import VllmLikeBatchScheduler
from infertwin.streaming.build import StreamingBuildResult, StreamingRequestShardBuilder
from infertwin.streaming.metrics import CapacitySweepStreamingMetricAggregator
from infertwin.streaming.replay import StreamingBatchAwareReplayEngine
from infertwin.streaming.source import JsonlRequestSource

STREAMING_CAPACITY_SWEEP_MODE = "capacity_sweep_streaming"


@dataclass(frozen=True, slots=True)
class StreamingCapacitySweepConfig:
    """Streaming-specific capacity sweep config."""

    shard_root: Path
    rejected_path: Path
    require_sorted_trace: bool


class StreamingCapacitySweepRunner:
    """Run capacity sweep from per-instance request shards."""

    def __init__(self, config: Mapping[str, Any]) -> None:
        self.config = config
        self.sweep_config = build_capacity_sweep_config(
            config,
            allowed_modes=(STREAMING_CAPACITY_SWEEP_MODE,),
        )
        self.streaming_config = build_streaming_capacity_sweep_config(config)
        self.latency_resolver = build_instance_latency_backend_resolver(config)
        self.scheduler_config = build_scheduler_config_from_config(config)

    def run(self) -> CapacitySweepResult:
        build_result = StreamingRequestShardBuilder(
            self.config,
            shard_root=self.streaming_config.shard_root,
            rejected_path=self.streaming_config.rejected_path,
            require_sorted_trace=self.streaming_config.require_sorted_trace,
        ).build()

        rows: list[CapacitySweepRow] = []
        cache_event_paths: dict[int, Path] = {}

        for capacity in self.sweep_config.capacities:
            event_path = self._cache_event_path(capacity)
            if event_path is None:
                sink = StatsOnlyCacheEventSink()
                rows.extend(
                    self._run_capacity(
                        capacity=capacity,
                        build_result=build_result,
                        cache_event_sink=sink,
                    )
                )
            else:
                cache_event_paths[capacity] = event_path
                with CsvCacheEventWriter(event_path) as sink:
                    rows.extend(
                        self._run_capacity(
                            capacity=capacity,
                            build_result=build_result,
                            cache_event_sink=sink,
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
        build_result: StreamingBuildResult,
        cache_event_sink: CacheEventSink,
    ) -> tuple[CapacitySweepRow, ...]:
        aggregator = CapacitySweepStreamingMetricAggregator()

        for shard in build_result.manifest.shards:
            engine = _build_streaming_replay_engine(
                instance_uuid=shard.instance_uuid,
                scheduler_config=self.scheduler_config,
                latency_resolver=self.latency_resolver,
            )
            with JsonlRequestSource(shard.path) as request_source:
                stats = engine.run_instance_stream(
                    instance_uuid=shard.instance_uuid,
                    request_source=request_source,
                    cache=HBMCache(capacity_blocks=capacity, evictor=LRUEvictor()),
                    metric_sink=aggregator,
                    cache_event_sink=cache_event_sink,
                )
            if stats.emitted_request_count != shard.request_count:
                raise ValueError(
                    "streaming replay emitted request count mismatch for "
                    f"{shard.instance_uuid!r}: emitted={stats.emitted_request_count}, "
                    f"shard={shard.request_count}"
                )
            if stats.final_active_requests != 0:
                raise ValueError(
                    "streaming replay finished with active requests for "
                    f"{shard.instance_uuid!r}: {stats.final_active_requests}"
                )

        return aggregator.build_rows(
            capacity=capacity,
            cache_event_stats=cache_event_sink.snapshot_stats(),
        )

    def _cache_event_path(self, capacity: int) -> Path | None:
        if capacity not in self.sweep_config.cache_event_capacities:
            return None
        return capacity_sweep_output_dir(self.config) / f"capacity_{capacity}" / "cache_events.csv"

    def _config_details(self, build_result: StreamingBuildResult) -> Mapping[str, object]:
        latency_config = _mapping(self.config, "latency")
        backend = _required_str(latency_config, "backend")
        details: dict[str, object] = {
            "phase": STREAMING_CAPACITY_SWEEP_MODE,
            "request_build_accepted_count": build_result.manifest.accepted_count,
            "request_build_rejected_count": build_result.manifest.rejected_count,
            "request_build_rejected_path": str(build_result.rejected_path or ""),
            "streaming_shard_root": str(build_result.manifest.shard_root),
            "streaming_require_sorted_trace": build_result.manifest.require_sorted_trace,
            "capacities": self.sweep_config.capacities,
            "cache_events": self.sweep_config.cache_events,
            "cache_event_capacities": self.sweep_config.cache_event_capacities,
            "parallel_instances": self.sweep_config.parallel_instances,
            "latency_backend": backend,
            "model_name": _required_str(latency_config, "model_name"),
            "hardware_name": _required_str(latency_config, "hardware_name"),
            "eviction_policy": "lru",
            "instance_latency_enabled": self.latency_resolver.uses_instance_profiles,
            "instance_latency_profile_path": str(self.latency_resolver.profile_path or ""),
            "instance_latency_profile_count": self.latency_resolver.instance_profile_count,
            "instance_latency_require_all_trace_instances": (
                self.latency_resolver.require_all_trace_instances
            ),
            "model_registry_enabled": self.latency_resolver.uses_model_registry,
            "model_registry_profile_path": str(self.latency_resolver.model_registry_path or ""),
            "latency_source_by_instance": dict(self.latency_resolver.latency_source_by_instance),
        }
        backend_config = latency_config.get(backend)
        if isinstance(backend_config, Mapping):
            for key, value in backend_config.items():
                if isinstance(value, str | int | float | bool):
                    details[key] = value
        return details


def _build_streaming_replay_engine(
    *,
    instance_uuid: str,
    scheduler_config: SchedulerConfig,
    latency_resolver: InstanceLatencyBackendResolver,
) -> StreamingBatchAwareReplayEngine:
    return StreamingBatchAwareReplayEngine(
        scheduler=VllmLikeBatchScheduler(scheduler_config),
        latency_backend=latency_resolver.backend_for(instance_uuid),
    )


def build_streaming_capacity_sweep_config(
    config: Mapping[str, Any],
) -> StreamingCapacitySweepConfig:
    """Validate streaming capacity sweep options."""

    output_dir = capacity_sweep_output_dir(config)
    streaming_config = config.get("streaming", {})
    if streaming_config is None:
        streaming_config = {}
    if not isinstance(streaming_config, Mapping):
        raise ValueError("streaming config must be a mapping")

    return StreamingCapacitySweepConfig(
        shard_root=_path_option(
            streaming_config,
            "shard_root",
            default=output_dir / "streaming_shards",
        ),
        rejected_path=_path_option(
            streaming_config,
            "rejected_path",
            default=output_dir / "rejected_requests.csv",
        ),
        require_sorted_trace=_optional_bool(
            streaming_config,
            "require_sorted_trace",
            default=True,
        ),
    )


def _path_option(config: Mapping[str, Any], key: str, *, default: Path) -> Path:
    value = config.get(key, default)
    if isinstance(value, Path):
        return value
    if isinstance(value, str) and value:
        return Path(value)
    raise ValueError(f"streaming.{key} must be a non-empty path string")


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
        raise ValueError(f"streaming.{key} must be a boolean")
    return value
