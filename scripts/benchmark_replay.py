#!/usr/bin/env python
"""Benchmark HitFloor replay state-machine throughput on synthetic requests."""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from math import ceil
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"


@dataclass(frozen=True, slots=True)
class BenchmarkConfig:
    request_count: int
    instance_count: int
    prompt_tokens: int
    reuse_period: int
    arrival_interval_ms: float
    mode: str
    hbm_capacity_blocks: int
    block_size_tokens: int
    max_num_batched_tokens: int
    max_num_seqs: int
    cache_events: str
    output_json: Path | None


def main(argv: list[str] | None = None) -> int:
    _ensure_src_path()
    config = _parse_args(argv)
    summary = run_benchmark(config)
    _print_summary(summary)
    if config.output_json is not None:
        config.output_json.parent.mkdir(parents=True, exist_ok=True)
        config.output_json.write_text(
            json.dumps(summary, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return 0


def run_benchmark(config: BenchmarkConfig) -> dict[str, Any]:
    _validate_config(config)
    build_start = time.perf_counter()
    requests = build_synthetic_requests(config)
    build_ms = _elapsed_ms(build_start)

    replay_start = time.perf_counter()
    replay_result = _build_engine(config).run(
        requests,
        cache_event_sink=_build_cache_event_sink(config),
    )
    replay_ms = _elapsed_ms(replay_start)
    total_ms = build_ms + replay_ms

    total_prompt_tokens = sum(item.prompt_tokens for item in replay_result.request_metrics)
    total_hit_tokens = sum(
        item.hbm_hit_tokens + item.ddr_hit_tokens for item in replay_result.request_metrics
    )
    return {
        "request_count": config.request_count,
        "instance_count": config.instance_count,
        "mode": config.mode,
        "build_ms": build_ms,
        "replay_ms": replay_ms,
        "total_ms": total_ms,
        "requests_per_second": _safe_rate(config.request_count, total_ms / 1000.0),
        "iteration_count": len(replay_result.iteration_metrics),
        "p90_ttft_ms": _percentile(
            [item.ttft_ms for item in replay_result.request_metrics],
            90,
        ),
        "effective_hit_rate": _safe_rate(total_hit_tokens, total_prompt_tokens),
        "cache_event_count": replay_result.cache_event_stats.total_events,
    }


def build_synthetic_requests(config: BenchmarkConfig):
    from hitfloor.instance.request import SimulationRequest
    from hitfloor.request.block_hasher import build_prefix_blocks

    base_time = datetime.fromisoformat("2026-06-05 09:01:23")
    requests: list[SimulationRequest] = []
    for index in range(config.request_count):
        pattern_id = index % config.reuse_period
        token_ids = [pattern_id * 1_000_000 + offset for offset in range(config.prompt_tokens)]
        instance_uuid = f"instance-{index % config.instance_count}"
        start_time_ms = index * config.arrival_interval_ms
        blocks = build_prefix_blocks(
            token_ids=token_ids,
            block_size_tokens=config.block_size_tokens,
            model="glm-v5",
            tenant_id="tenant-a",
            kv_bytes_per_token=1,
        )
        requests.append(
            SimulationRequest(
                request_id=f"{index:032d}",
                tenant_id="tenant-a",
                instance_uuid=instance_uuid,
                model="glm-v5",
                service_start_time=base_time + timedelta(milliseconds=start_time_ms),
                start_time_ms=start_time_ms,
                tokenizer_profile="glm-v5",
                prompt_tokens=len(token_ids),
                prompt_blocks=tuple(blocks),
                kv_bytes_per_token=1,
            )
        )
    return requests


def _build_engine(config: BenchmarkConfig):
    from hitfloor.cache.hbm_lru import HBMCache
    from hitfloor.latency.formula import FormulaLatencyBackend
    from hitfloor.replay.event_loop import BatchAwareReplayEngine
    from hitfloor.scheduler.config import SchedulerConfig
    from hitfloor.scheduler.vllm_like import VllmLikeBatchScheduler

    scheduler = VllmLikeBatchScheduler(
        SchedulerConfig(
            max_num_batched_tokens=config.max_num_batched_tokens,
            max_num_seqs=config.max_num_seqs,
            enable_chunked_prefill=True,
        )
    )
    latency_backend = FormulaLatencyBackend(
        iteration_fixed_overhead_ms=0.0,
        iteration_prefill_token_ms=0.01,
        iteration_batch_overhead_ms=0.0,
        iteration_context_token_ms=0.0,
        model_name="glm-v5",
        hardware_name="synthetic-benchmark",
    )
    if config.mode == "batch_aware_infinite_hbm":
        return BatchAwareReplayEngine(scheduler=scheduler, latency_backend=latency_backend)
    if config.mode == "batch_aware_hbm_lru":
        return BatchAwareReplayEngine(
            scheduler=scheduler,
            latency_backend=latency_backend,
            cache_factory=lambda _instance_uuid: HBMCache(
                capacity_blocks=config.hbm_capacity_blocks
            ),
        )
    raise ValueError(f"unsupported benchmark mode: {config.mode}")


def _build_cache_event_sink(config: BenchmarkConfig):
    if config.cache_events == "off":
        return None
    if config.cache_events == "memory":
        from hitfloor.cache.event_sink import InMemoryCacheEventSink

        return InMemoryCacheEventSink()
    raise ValueError(f"unsupported cache event mode: {config.cache_events}")


def _parse_args(argv: list[str] | None) -> BenchmarkConfig:
    parser = argparse.ArgumentParser(
        description="Benchmark HitFloor replay state-machine throughput."
    )
    parser.add_argument("--requests", type=int, default=10_000, dest="request_count")
    parser.add_argument("--instances", type=int, default=1, dest="instance_count")
    parser.add_argument("--prompt-tokens", type=int, default=128)
    parser.add_argument("--reuse-period", type=int, default=32)
    parser.add_argument("--arrival-interval-ms", type=float, default=0.0)
    parser.add_argument(
        "--mode",
        choices=("batch_aware_infinite_hbm", "batch_aware_hbm_lru"),
        default="batch_aware_infinite_hbm",
    )
    parser.add_argument("--hbm-capacity-blocks", type=int, default=4096)
    parser.add_argument("--block-size-tokens", type=int, default=16)
    parser.add_argument("--max-num-batched-tokens", type=int, default=8192)
    parser.add_argument("--max-num-seqs", type=int, default=32)
    parser.add_argument("--cache-events", choices=("off", "memory"), default="off")
    parser.add_argument("--output-json", type=Path)
    args = parser.parse_args(argv)
    return BenchmarkConfig(**vars(args))


def _validate_config(config: BenchmarkConfig) -> None:
    positive_fields = {
        "request_count": config.request_count,
        "instance_count": config.instance_count,
        "prompt_tokens": config.prompt_tokens,
        "reuse_period": config.reuse_period,
        "hbm_capacity_blocks": config.hbm_capacity_blocks,
        "block_size_tokens": config.block_size_tokens,
        "max_num_batched_tokens": config.max_num_batched_tokens,
        "max_num_seqs": config.max_num_seqs,
    }
    for field_name, value in positive_fields.items():
        if value <= 0:
            raise ValueError(f"{field_name} must be positive")
    if config.arrival_interval_ms < 0:
        raise ValueError("arrival_interval_ms must be non-negative")


def _print_summary(summary: dict[str, Any]) -> None:
    for key in (
        "request_count",
        "instance_count",
        "mode",
        "build_ms",
        "replay_ms",
        "total_ms",
        "requests_per_second",
        "iteration_count",
        "p90_ttft_ms",
        "effective_hit_rate",
        "cache_event_count",
    ):
        print(f"{key}: {summary[key]}")


def _percentile(values: list[float], percentile: int) -> float:
    if not values:
        return 0.0
    sorted_values = sorted(values)
    rank = ceil((percentile / 100) * len(sorted_values))
    index = min(max(rank - 1, 0), len(sorted_values) - 1)
    return sorted_values[index]


def _safe_rate(numerator: float, denominator: float) -> float:
    if denominator <= 0:
        return 0.0
    return numerator / denominator


def _elapsed_ms(start_time: float) -> float:
    return (time.perf_counter() - start_time) * 1000.0


def _ensure_src_path() -> None:
    if str(SRC_ROOT) not in sys.path:
        sys.path.insert(0, str(SRC_ROOT))


if __name__ == "__main__":
    raise SystemExit(main())
