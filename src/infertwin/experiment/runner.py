"""Small-trace experiment runner."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from infertwin.cache.eviction import LRUEvictor
from infertwin.cache.hbm_lru import HBMCache
from infertwin.experiment.request_builder import (
    RequestBuildResult,
    build_request_build_result_from_config,
)
from infertwin.instance.replay import InfiniteHBMReplayEngine
from infertwin.instance.request import SimulationRequest
from infertwin.latency.factory import build_batch_latency_backend
from infertwin.report.cache_events import CsvCacheEventWriter
from infertwin.replay.event_loop import BatchAwareReplayEngine
from infertwin.report.summary import write_batch_aware_summary, write_phase1_summary
from infertwin.report.tables import write_csv_table
from infertwin.scheduler.config import SchedulerConfig
from infertwin.scheduler.vllm_like import VllmLikeBatchScheduler


@dataclass(frozen=True, slots=True)
class ExperimentResult:
    output_dir: Path
    metrics: dict[str, Any]


class ExperimentRunner:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config

    def run(self) -> ExperimentResult:
        output_dir = Path(self.config.get("output", {}).get("directory", "reports"))
        output_dir.mkdir(parents=True, exist_ok=True)
        build_result = self._build_request_result()
        requests = list(build_result.requests)
        rejected_requests_path = _write_rejected_requests_if_needed(
            output_dir=output_dir,
            build_result=build_result,
        )

        mode = self.config.get("simulation", {}).get("mode", "infinite_hbm")
        if mode == "infinite_hbm":
            result = self._run_infinite_hbm(output_dir=output_dir, requests=requests)
            return _with_request_build_metrics(
                result,
                build_result=build_result,
                rejected_requests_path=rejected_requests_path,
            )
        if mode == "batch_aware_infinite_hbm":
            result = self._run_batch_aware_infinite_hbm(
                output_dir=output_dir,
                requests=requests,
            )
            return _with_request_build_metrics(
                result,
                build_result=build_result,
                rejected_requests_path=rejected_requests_path,
            )
        if mode == "batch_aware_hbm_lru":
            result = self._run_batch_aware_hbm_lru(
                output_dir=output_dir,
                requests=requests,
            )
            return _with_request_build_metrics(
                result,
                build_result=build_result,
                rejected_requests_path=rejected_requests_path,
            )
        raise ValueError(f"unsupported simulation mode: {mode}")

    def _build_request_result(self) -> RequestBuildResult:
        return build_request_build_result_from_config(self.config)

    def _run_infinite_hbm(
        self,
        *,
        output_dir: Path,
        requests: list[SimulationRequest],
    ) -> ExperimentResult:
        request_metrics = InfiniteHBMReplayEngine().run(requests)

        request_rows = _dataclass_rows(request_metrics)
        request_metrics_path = output_dir / "request_metrics.csv"
        summary_path = output_dir / "summary.md"
        write_csv_table(request_metrics_path, request_rows)
        write_phase1_summary(
            path=summary_path,
            request_count=len(request_metrics),
            instance_count=len({row.instance_uuid for row in request_metrics}),
            total_prompt_tokens=sum(row.prompt_tokens for row in request_metrics),
            total_hbm_hit_tokens=sum(row.hbm_hit_tokens for row in request_metrics),
            total_miss_tokens=sum(row.miss_tokens for row in request_metrics),
        )

        return ExperimentResult(
            output_dir=output_dir,
            metrics={
                "phase": "infinite_hbm",
                "request_count": len(request_metrics),
                "request_metrics_path": str(request_metrics_path),
                "summary_path": str(summary_path),
            },
        )

    def _run_batch_aware_infinite_hbm(
        self,
        *,
        output_dir: Path,
        requests: list[SimulationRequest],
    ) -> ExperimentResult:
        scheduler = VllmLikeBatchScheduler(_build_scheduler_config(self.config))
        latency_backend = build_batch_latency_backend(self.config)
        replay_result = BatchAwareReplayEngine(
            scheduler=scheduler,
            latency_backend=latency_backend,
        ).run(requests)

        request_metrics_path = output_dir / "request_metrics.csv"
        iteration_metrics_path = output_dir / "iteration_metrics.csv"
        summary_path = output_dir / "summary.md"
        write_csv_table(
            request_metrics_path,
            _dataclass_rows(replay_result.request_metrics),
        )
        write_csv_table(
            iteration_metrics_path,
            _dataclass_rows(replay_result.iteration_metrics),
        )
        write_batch_aware_summary(
            path=summary_path,
            request_metrics=replay_result.request_metrics,
            iteration_metrics=replay_result.iteration_metrics,
            latency_backend=latency_backend.name,
            latency_details=_latency_summary_details(self.config),
        )

        return ExperimentResult(
            output_dir=output_dir,
            metrics={
                "phase": "batch_aware_infinite_hbm",
                "request_count": len(replay_result.request_metrics),
                "iteration_count": len(replay_result.iteration_metrics),
                "request_metrics_path": str(request_metrics_path),
                "iteration_metrics_path": str(iteration_metrics_path),
                "summary_path": str(summary_path),
            },
        )

    def _run_batch_aware_hbm_lru(
        self,
        *,
        output_dir: Path,
        requests: list[SimulationRequest],
    ) -> ExperimentResult:
        scheduler = VllmLikeBatchScheduler(_build_scheduler_config(self.config))
        latency_backend = build_batch_latency_backend(self.config)
        cache_capacity_blocks = _hbm_capacity_blocks(self.config)
        eviction_policy = _hbm_eviction_policy(self.config)
        request_metrics_path = output_dir / "request_metrics.csv"
        iteration_metrics_path = output_dir / "iteration_metrics.csv"
        cache_events_path = output_dir / "cache_events.csv"
        summary_path = output_dir / "summary.md"

        with CsvCacheEventWriter(cache_events_path) as cache_event_writer:
            replay_result = BatchAwareReplayEngine(
                scheduler=scheduler,
                latency_backend=latency_backend,
                cache_factory=lambda _instance_uuid: HBMCache(
                    capacity_blocks=cache_capacity_blocks,
                    evictor=LRUEvictor(),
                ),
            ).run(requests, cache_event_sink=cache_event_writer)

        write_csv_table(
            request_metrics_path,
            _dataclass_rows(replay_result.request_metrics),
        )
        write_csv_table(
            iteration_metrics_path,
            _dataclass_rows(replay_result.iteration_metrics),
        )
        write_batch_aware_summary(
            path=summary_path,
            request_metrics=replay_result.request_metrics,
            iteration_metrics=replay_result.iteration_metrics,
            latency_backend=latency_backend.name,
            latency_details=_latency_summary_details(self.config),
            cache_assumption="Instance-local finite HBM prefix cache.",
            cache_details={
                "simulation_mode": "batch_aware_hbm_lru",
                "hbm_capacity_blocks": cache_capacity_blocks,
                "eviction_policy": eviction_policy,
            },
            cache_event_stats=replay_result.cache_event_stats,
        )

        return ExperimentResult(
            output_dir=output_dir,
            metrics={
                "phase": "batch_aware_hbm_lru",
                "request_count": len(replay_result.request_metrics),
                "iteration_count": len(replay_result.iteration_metrics),
                "cache_event_count": replay_result.cache_event_stats.total_events,
                "hbm_capacity_blocks": cache_capacity_blocks,
                "eviction_policy": eviction_policy,
                "request_metrics_path": str(request_metrics_path),
                "iteration_metrics_path": str(iteration_metrics_path),
                "cache_events_path": str(cache_events_path),
                "summary_path": str(summary_path),
            },
        )


def _build_scheduler_config(config: Mapping[str, Any]) -> SchedulerConfig:
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


def _hbm_capacity_blocks(config: Mapping[str, Any]) -> int:
    cache_config = _mapping(config, "cache")
    capacity_blocks = _required_int(cache_config, "hbm_capacity_blocks")
    if capacity_blocks <= 0:
        raise ValueError("hbm_capacity_blocks must be a positive integer")
    return capacity_blocks


def _hbm_eviction_policy(config: Mapping[str, Any]) -> str:
    cache_config = _mapping(config, "cache")
    eviction_policy = _optional_str(cache_config, "eviction_policy", default="lru")
    if eviction_policy != "lru":
        raise ValueError("batch_aware_hbm_lru only supports eviction_policy: lru")
    return eviction_policy


def _dataclass_rows(items) -> list[dict[str, object]]:
    return [{key: _csv_value(value) for key, value in asdict(item).items()} for item in items]


def _write_rejected_requests_if_needed(
    *,
    output_dir: Path,
    build_result: RequestBuildResult,
) -> Path | None:
    if build_result.rejected_count == 0:
        return None
    path = output_dir / "rejected_requests.csv"
    write_csv_table(path, _dataclass_rows(build_result.rejected_records))
    return path


def _with_request_build_metrics(
    result: ExperimentResult,
    *,
    build_result: RequestBuildResult,
    rejected_requests_path: Path | None,
) -> ExperimentResult:
    metrics = {
        **result.metrics,
        "request_build_accepted_count": build_result.accepted_count,
        "request_build_rejected_count": build_result.rejected_count,
    }
    if rejected_requests_path is not None:
        metrics["rejected_requests_path"] = str(rejected_requests_path)
    return ExperimentResult(output_dir=result.output_dir, metrics=metrics)


def _csv_value(value: object) -> object:
    if isinstance(value, tuple | list):
        return json.dumps(list(value), ensure_ascii=True)
    return value


def _latency_summary_details(config: Mapping[str, Any]) -> dict[str, object]:
    latency_config = _mapping(config, "latency")
    backend = _required_str(latency_config, "backend")
    details: dict[str, object] = {
        "model_name": _required_str(latency_config, "model_name"),
        "hardware_name": _required_str(latency_config, "hardware_name"),
    }
    backend_config = latency_config.get(backend)
    if isinstance(backend_config, Mapping):
        for key, value in backend_config.items():
            if isinstance(value, str | int | float | bool):
                details[key] = value
    return details


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


def _optional_str(config: Mapping[str, Any], key: str, *, default: str) -> str:
    value = config.get(key, default)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be a non-empty string")
    return value


def _required_int(config: Mapping[str, Any], key: str) -> int:
    value = config.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key} must be an integer")
    return value


def _optional_int(config: Mapping[str, Any], key: str) -> int | None:
    if key not in config or config[key] is None:
        return None
    return _required_int(config, key)


def _optional_bool(
    config: Mapping[str, Any],
    key: str,
    *,
    default: bool,
) -> bool:
    value = config.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be a boolean")
    return value
