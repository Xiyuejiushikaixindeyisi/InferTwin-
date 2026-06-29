"""Markdown summary writers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from math import ceil
from pathlib import Path

from infertwin.cache.event_sink import CacheEventStats
from infertwin.cache.events import CacheEvent
from infertwin.replay.metrics import BatchAwareRequestMetrics, IterationMetrics


def write_phase1_summary(
    path: str | Path,
    request_count: int,
    instance_count: int,
    total_prompt_tokens: int,
    total_hbm_hit_tokens: int,
    total_miss_tokens: int,
) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    effective_hit_rate = _safe_rate(total_hbm_hit_tokens, total_prompt_tokens)
    output_path.write_text(
        "\n".join(
            [
                "# InferTwin Phase 1 Summary",
                "",
                "Assumptions:",
                "",
                "- Instance-local infinite HBM prefix cache.",
                "- No cross-instance KV reuse.",
                "- `batch_admission_delay = 0`.",
                "- Cache stores hash keys and metadata only.",
                "",
                "Metrics:",
                "",
                f"- Requests: {request_count}",
                f"- Instances: {instance_count}",
                f"- Prompt tokens: {total_prompt_tokens}",
                f"- HBM hit tokens: {total_hbm_hit_tokens}",
                f"- Miss tokens: {total_miss_tokens}",
                f"- Effective hit rate: {effective_hit_rate:.6f}",
                "",
            ]
        ),
        encoding="utf-8",
    )


def write_batch_aware_summary(
    path: str | Path,
    *,
    request_metrics: Sequence[BatchAwareRequestMetrics],
    iteration_metrics: Sequence[IterationMetrics],
    latency_backend: str,
    latency_details: Mapping[str, object],
    cache_assumption: str = "Instance-local infinite HBM prefix cache.",
    cache_details: Mapping[str, object] | None = None,
    cache_events: Sequence[CacheEvent] = (),
    cache_event_stats: CacheEventStats | None = None,
) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    request_count = len(request_metrics)
    instance_count = len({item.instance_uuid for item in request_metrics})
    total_prompt_tokens = sum(item.prompt_tokens for item in request_metrics)
    total_hbm_hit_tokens = sum(item.hbm_hit_tokens for item in request_metrics)
    total_ddr_hit_tokens = sum(item.ddr_hit_tokens for item in request_metrics)
    total_miss_tokens = sum(item.miss_tokens for item in request_metrics)
    effective_hit_rate = _safe_rate(
        total_hbm_hit_tokens + total_ddr_hit_tokens,
        total_prompt_tokens,
    )
    ttft_values = [item.ttft_ms for item in request_metrics]
    scheduler_wait_values = [item.scheduler_wait_ms for item in request_metrics]
    kv_load_values = [item.kv_load_ms for item in request_metrics]
    total_kv_load_ms = sum(kv_load_values)

    output_path.write_text(
        "\n".join(
            [
                "# InferTwin Batch-Aware Summary",
                "",
                "Assumptions:",
                "",
                f"- {cache_assumption}",
                "- No cross-instance KV reuse.",
                "- vLLM-like FCFS continuous batching approximation.",
                "- Chunked prefill is controlled by InferTwin replay.",
                "- Queue time outside model service is not modeled.",
                "- DDR KV load latency is modeled when configured by Step8 `KVLoadLatencyProfile`.",
                "- Decode TPOT interference is not modeled.",
                "",
                "Latency backend:",
                "",
                f"- Backend: {latency_backend}",
                *_detail_lines(latency_details),
                "",
                "Request metrics:",
                "",
                f"- Requests: {request_count}",
                f"- Instances: {instance_count}",
                f"- Prompt tokens: {total_prompt_tokens}",
                f"- HBM hit tokens: {total_hbm_hit_tokens}",
                f"- DDR hit tokens: {total_ddr_hit_tokens}",
                f"- Miss tokens: {total_miss_tokens}",
                f"- Effective hit rate: {effective_hit_rate:.6f}",
                "",
                "TTFT:",
                "",
                f"- P50 TTFT ms: {_percentile(ttft_values, 50):.6f}",
                f"- P90 TTFT ms: {_percentile(ttft_values, 90):.6f}",
                f"- P99 TTFT ms: {_percentile(ttft_values, 99):.6f}",
                "",
                "Scheduler wait:",
                "",
                f"- P50 scheduler wait ms: {_percentile(scheduler_wait_values, 50):.6f}",
                f"- P90 scheduler wait ms: {_percentile(scheduler_wait_values, 90):.6f}",
                f"- P99 scheduler wait ms: {_percentile(scheduler_wait_values, 99):.6f}",
                "",
                "KV load latency:",
                "",
                f"- Total KV load ms: {total_kv_load_ms:.6f}",
                f"- P50 KV load ms: {_percentile(kv_load_values, 50):.6f}",
                f"- P90 KV load ms: {_percentile(kv_load_values, 90):.6f}",
                f"- P99 KV load ms: {_percentile(kv_load_values, 99):.6f}",
                "",
                "Iteration metrics:",
                "",
                f"- Iterations: {len(iteration_metrics)}",
                f"- Total scheduled prefill tokens: {sum(item.scheduled_prefill_tokens for item in iteration_metrics)}",
                f"- Total iteration KV load ms: {sum(item.kv_load_ms for item in iteration_metrics):.6f}",
                f"- Total iteration duration ms: {sum(item.duration_ms for item in iteration_metrics):.6f}",
                *_cache_event_lines(cache_details, cache_events, cache_event_stats),
                "",
            ]
        ),
        encoding="utf-8",
    )


def _safe_rate(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return numerator / denominator


def _percentile(values: Sequence[float], percentile: int) -> float:
    if not values:
        return 0.0
    if percentile < 0 or percentile > 100:
        raise ValueError("percentile must be between 0 and 100")
    sorted_values = sorted(values)
    rank = ceil((percentile / 100) * len(sorted_values))
    index = min(max(rank - 1, 0), len(sorted_values) - 1)
    return sorted_values[index]


def _detail_lines(details: Mapping[str, object]) -> list[str]:
    return [f"- {key}: {details[key]}" for key in sorted(details)]


def _cache_event_lines(
    details: Mapping[str, object] | None,
    events: Sequence[CacheEvent],
    stats: CacheEventStats | None,
) -> list[str]:
    if details is None and stats is None and not events:
        return []

    event_stats = stats or _cache_event_stats_from_events(events)
    detail_lines = _detail_lines(details or {})
    return [
        "",
        "Cache:",
        "",
        *detail_lines,
        f"- Cache events: {event_stats.total_events}",
        f"- Lookup hit events: {event_stats.lookup_hit_events}",
        f"- Lookup miss events: {event_stats.lookup_miss_events}",
        f"- Materialize events: {event_stats.materialize_events}",
        f"- Evict events: {event_stats.evict_events}",
        f"- Peak HBM resident blocks: {event_stats.peak_hbm_used_blocks}",
        f"- Final HBM resident blocks: {event_stats.final_hbm_used_blocks}",
    ]


def _cache_event_stats_from_events(events: Sequence[CacheEvent]) -> CacheEventStats:
    stats = CacheEventStats()
    for event in events:
        stats.record(event)
    return stats
