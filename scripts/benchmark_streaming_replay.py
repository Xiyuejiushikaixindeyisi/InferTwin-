#!/usr/bin/env python
"""Benchmark InferTwin streaming capacity sweep on synthetic traces."""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
import tracemalloc
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"


@dataclass(frozen=True, slots=True)
class StreamingBenchmarkConfig:
    request_count: int
    instance_count: int
    prompt_words: int
    reuse_period: int
    arrival_interval_ms: float
    capacities: tuple[int, ...]
    block_size_tokens: int
    max_num_batched_tokens: int
    max_num_seqs: int
    output_dir: Path
    cache_event_capacities: tuple[int, ...]
    tokenizers_root: Path
    tokenizer_profile: str
    max_prompt_tokens: int | None
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


def run_benchmark(config: StreamingBenchmarkConfig) -> dict[str, Any]:
    """Run an end-to-end streaming capacity sweep benchmark."""

    _validate_config(config)
    total_start = time.perf_counter()
    config.output_dir.mkdir(parents=True, exist_ok=True)

    trace_path = config.output_dir / "synthetic_trace.csv"
    trace_start = time.perf_counter()
    write_synthetic_trace(config, trace_path)
    trace_write_ms = _elapsed_ms(trace_start)

    benchmark_config = _build_infertwin_config(config, trace_path)
    started_tracemalloc = _start_tracemalloc()
    run_start = time.perf_counter()
    try:
        from infertwin.report.sweep import write_capacity_sweep_report
        from infertwin.streaming.sweep import StreamingCapacitySweepRunner

        result = StreamingCapacitySweepRunner(benchmark_config).run()
        run_ms = _elapsed_ms(run_start)

        report_start = time.perf_counter()
        report_paths = write_capacity_sweep_report(result, config.output_dir)
        report_write_ms = _elapsed_ms(report_start)
        current_bytes, peak_bytes = tracemalloc.get_traced_memory()
    finally:
        if started_tracemalloc:
            tracemalloc.stop()

    total_elapsed_ms = _elapsed_ms(total_start)
    trace_rows = [row for row in result.rows if row.scope == "trace"]
    accepted_request_count = int(result.config_details["request_build_accepted_count"])
    rejected_request_count = int(result.config_details["request_build_rejected_count"])
    replayed_request_count = accepted_request_count * len(config.capacities)
    iteration_count = sum(row.iteration_count for row in trace_rows)
    cache_event_count = sum(row.cache_event_count for row in trace_rows)

    return {
        "request_count": config.request_count,
        "accepted_request_count": accepted_request_count,
        "rejected_request_count": rejected_request_count,
        "instance_count": config.instance_count,
        "capacity_count": len(config.capacities),
        "capacities": list(config.capacities),
        "replayed_request_count": replayed_request_count,
        "iteration_count": iteration_count,
        "cache_event_count": cache_event_count,
        "trace_write_ms": trace_write_ms,
        "streaming_run_ms": run_ms,
        "report_write_ms": report_write_ms,
        "total_elapsed_ms": total_elapsed_ms,
        "requests_per_second": _safe_rate(replayed_request_count, run_ms / 1000.0),
        "iterations_per_second": _safe_rate(iteration_count, run_ms / 1000.0),
        "cache_events_per_second": _safe_rate(cache_event_count, run_ms / 1000.0),
        "end_to_end_requests_per_second": _safe_rate(
            replayed_request_count,
            total_elapsed_ms / 1000.0,
        ),
        "peak_traced_memory_mb": _bytes_to_mib(peak_bytes),
        "current_traced_memory_mb": _bytes_to_mib(current_bytes),
        "max_rss_mb": _max_rss_mb(),
        "output_dir": str(config.output_dir),
        "trace_path": str(trace_path),
        "capacity_sweep_path": str(report_paths.capacity_sweep_path),
        "summary_path": str(report_paths.summary_path),
    }


def write_synthetic_trace(config: StreamingBenchmarkConfig, trace_path: Path) -> None:
    """Write a sorted synthetic trace CSV for streaming benchmark runs."""

    rows = []
    base_time = datetime.fromisoformat("2026-06-05 09:01:23")
    for index in range(config.request_count):
        start_ms = index * config.arrival_interval_ms
        instance_uuid = f"instance-{index % config.instance_count}"
        request_id = f"{index:032d}"
        rows.append(
            (
                start_ms,
                instance_uuid,
                request_id,
                {
                    "request_id": request_id,
                    "tenant_id": "tenant-a",
                    "instance_uuid": instance_uuid,
                    "request_params": json.dumps(
                        _request_params(
                            model=config.tokenizer_profile,
                            prompt=_prompt_text(
                                prompt_words=config.prompt_words,
                                pattern_id=index % config.reuse_period,
                            ),
                        ),
                        ensure_ascii=True,
                        separators=(",", ":"),
                    ),
                    "service_start_time": (base_time + timedelta(milliseconds=start_ms)).strftime(
                        "%Y-%m-%d %H:%M:%S.%f"
                    ),
                },
            )
        )

    rows.sort(key=lambda item: (item[0], item[1], item[2]))
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    with trace_path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=(
                "request_id",
                "tenant_id",
                "instance_uuid",
                "request_params",
                "service_start_time",
            ),
        )
        writer.writeheader()
        for _start_ms, _instance_uuid, _request_id, row in rows:
            writer.writerow(row)


def _build_infertwin_config(
    config: StreamingBenchmarkConfig,
    trace_path: Path,
) -> dict[str, object]:
    tokenizers_config: dict[str, object] = {
        "root": str(config.tokenizers_root),
        "default_profile": config.tokenizer_profile,
        "cache_scope": "tenant_isolated",
    }
    if config.max_prompt_tokens is not None:
        tokenizers_config["max_prompt_tokens"] = config.max_prompt_tokens

    return {
        "simulation": {"mode": "capacity_sweep_streaming"},
        "trace": {"path": str(trace_path)},
        "tokenizers": tokenizers_config,
        "cache": {
            "block_size_tokens": config.block_size_tokens,
            "policy": "hbm",
            "eviction_policy": "lru",
        },
        "sweep": {
            "hbm_capacity_blocks": list(config.capacities),
            "parallel_instances": False,
        },
        "scheduler": {
            "policy": "fcfs",
            "max_num_batched_tokens": config.max_num_batched_tokens,
            "max_num_seqs": config.max_num_seqs,
            "enable_chunked_prefill": True,
            "long_prefill_token_threshold": 4096,
        },
        "latency": {
            "backend": "fitted_ttft",
            "model_name": config.tokenizer_profile,
            "hardware_name": "synthetic-benchmark",
            "fitted_ttft": {
                "profile": f"{config.tokenizer_profile}_synthetic_benchmark",
                "function": "token_linear_v1",
                "intercept_ms": 0.0,
                "ms_per_uncached_token": 0.01,
                "calibrated_from": "streaming-benchmark",
            },
        },
        "streaming": {
            "shard_root": str(config.output_dir / "streaming_shards"),
            "rejected_path": str(config.output_dir / "rejected_requests.csv"),
            "require_sorted_trace": True,
        },
        "output": {
            "directory": str(config.output_dir),
            "cache_events": bool(config.cache_event_capacities),
            "cache_event_capacities": list(config.cache_event_capacities),
        },
    }


def _parse_args(argv: list[str] | None) -> StreamingBenchmarkConfig:
    parser = argparse.ArgumentParser(
        description="Benchmark InferTwin true-streaming capacity sweep throughput."
    )
    parser.add_argument("--requests", type=int, default=1_000, dest="request_count")
    parser.add_argument("--instances", type=int, default=4, dest="instance_count")
    parser.add_argument("--prompt-words", type=int, default=256)
    parser.add_argument("--reuse-period", type=int, default=64)
    parser.add_argument("--arrival-interval-ms", type=float, default=1.0)
    parser.add_argument("--capacities", type=_parse_int_tuple, default=(128,))
    parser.add_argument("--block-size-tokens", type=int, default=16)
    parser.add_argument("--max-num-batched-tokens", type=int, default=8192)
    parser.add_argument("--max-num-seqs", type=int, default=32)
    parser.add_argument("--output-dir", type=Path, default=Path("reports/streaming_benchmark"))
    parser.add_argument("--cache-event-capacities", type=_parse_int_tuple, default=())
    parser.add_argument("--tokenizers-root", type=Path, default=Path("tokenizers"))
    parser.add_argument("--tokenizer-profile", default="glm-v5")
    parser.add_argument("--max-prompt-tokens", type=int)
    parser.add_argument("--output-json", type=Path)
    args = parser.parse_args(argv)
    return StreamingBenchmarkConfig(**vars(args))


def _request_params(*, model: str, prompt: str) -> dict[str, object]:
    return {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "tools": [],
    }


def _prompt_text(*, prompt_words: int, pattern_id: int) -> str:
    return " ".join(f"p{pattern_id}_w{word_index}" for word_index in range(prompt_words))


def _validate_config(config: StreamingBenchmarkConfig) -> None:
    positive_fields = {
        "request_count": config.request_count,
        "instance_count": config.instance_count,
        "prompt_words": config.prompt_words,
        "reuse_period": config.reuse_period,
        "block_size_tokens": config.block_size_tokens,
        "max_num_batched_tokens": config.max_num_batched_tokens,
        "max_num_seqs": config.max_num_seqs,
    }
    for field_name, value in positive_fields.items():
        if value <= 0:
            raise ValueError(f"{field_name} must be positive")
    if config.arrival_interval_ms < 0:
        raise ValueError("arrival_interval_ms must be non-negative")
    if not config.capacities:
        raise ValueError("capacities must be non-empty")
    if len(set(config.capacities)) != len(config.capacities):
        raise ValueError("capacities must not contain duplicates")
    for capacity in config.capacities:
        if capacity <= 0:
            raise ValueError("capacities must contain only positive integers")
    unknown_event_capacities = sorted(
        set(config.cache_event_capacities).difference(config.capacities)
    )
    if unknown_event_capacities:
        raise ValueError("cache_event_capacities must be a subset of capacities")
    if config.max_prompt_tokens is not None and config.max_prompt_tokens <= 0:
        raise ValueError("max_prompt_tokens must be positive when provided")


def _print_summary(summary: dict[str, Any]) -> None:
    for key in (
        "request_count",
        "accepted_request_count",
        "rejected_request_count",
        "instance_count",
        "capacity_count",
        "replayed_request_count",
        "iteration_count",
        "cache_event_count",
        "streaming_run_ms",
        "report_write_ms",
        "total_elapsed_ms",
        "requests_per_second",
        "iterations_per_second",
        "cache_events_per_second",
        "peak_traced_memory_mb",
        "max_rss_mb",
        "output_dir",
    ):
        print(f"{key}: {summary[key]}")


def _parse_int_tuple(value: str | tuple[int, ...]) -> tuple[int, ...]:
    if isinstance(value, tuple):
        return value
    items = [item.strip() for item in value.split(",") if item.strip()]
    if not items:
        return ()
    return tuple(int(item) for item in items)


def _start_tracemalloc() -> bool:
    if tracemalloc.is_tracing():
        tracemalloc.reset_peak()
        return False
    tracemalloc.start()
    return True


def _max_rss_mb() -> float:
    try:
        import resource

        max_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    except (ImportError, OSError):
        return 0.0
    if sys.platform == "darwin":
        return _bytes_to_mib(max_rss)
    return max_rss / 1024.0


def _bytes_to_mib(value: int) -> float:
    return value / (1024.0 * 1024.0)


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
