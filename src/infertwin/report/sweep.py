"""Report/export helpers for capacity sweep results."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from pathlib import Path

from infertwin.experiment.sweep import (
    TRACE_SCOPE,
    CapacitySweepReportPaths,
    CapacitySweepResult,
    CapacitySweepRow,
)
from infertwin.report.tables import write_csv_table

_CACHE_MODE_HBM_LRU = "batch_aware_hbm_lru"
_CACHE_MODE_HBM_DDR_LRU = "batch_aware_hbm_ddr_lru"
_CACHE_MODE_HBM_DDR_LRU_PROGRESSIVE_TIMELINE = "batch_aware_hbm_ddr_lru_progressive_timeline"


def write_capacity_sweep_report(
    result: CapacitySweepResult,
    output_dir: str | Path,
) -> CapacitySweepReportPaths:
    """Write the standard capacity sweep CSV and Markdown report around typed results."""

    report_dir = Path(output_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    capacity_sweep_path = report_dir / "capacity_sweep.csv"
    summary_path = report_dir / "summary.md"

    write_csv_table(capacity_sweep_path, _dataclass_rows(result.rows))
    write_capacity_sweep_summary(
        summary_path,
        rows=result.rows,
        config_details=result.config_details,
        cache_event_paths=result.cache_event_paths,
    )
    return CapacitySweepReportPaths(
        capacity_sweep_path=capacity_sweep_path,
        summary_path=summary_path,
    )


def write_capacity_sweep_summary(
    path: str | Path,
    *,
    rows: Sequence[CapacitySweepRow],
    config_details: Mapping[str, object],
    cache_event_paths: Mapping[int, Path] | None = None,
) -> None:
    """Render a compact Markdown summary from already-aggregated sweep rows."""

    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    trace_rows = [row for row in rows if row.scope == TRACE_SCOPE]
    cache_event_paths = cache_event_paths or {}

    lines = [
        "# InferTwin Capacity Sweep Summary",
        "",
        "## Assumptions",
        "",
        "- Fixed-routing, multi-instance isolated replay.",
        *_cache_assumption_lines(config_details),
        "- P90 target matching / hit floor search is not performed.",
        "- Cache event details are disabled by default and only dumped for selected capacities.",
        "",
        "## Config",
        "",
        f"- Latency backend: {_detail(config_details, 'latency_backend')}",
        f"- Model: {_detail(config_details, 'model_name')}",
        f"- Hardware: {_detail(config_details, 'hardware_name')}",
        f"- Streaming cache mode: {_detail(config_details, 'streaming_cache_mode')}",
        f"- Cache eviction policy: {_detail(config_details, 'streaming_cache_eviction_policy') or _detail(config_details, 'eviction_policy')}",
        f"- Capacities: {_format_sequence(config_details.get('capacities', ()))}",
        f"- Cache event capacities: {_format_sequence(config_details.get('cache_event_capacities', ()))}",
        "",
        "## Trace-Level Results",
        "",
        "| hbm_capacity_blocks | kv_hit_rate | hbm_hit_rate | ddr_hit_rate | p90_ttft_ms | p90_kv_load_ms | request_count | cache_event_count |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in trace_rows:
        lines.append(
            "| "
            f"{row.hbm_capacity_blocks} | "
            f"{row.kv_hit_rate:.6f} | "
            f"{row.hbm_hit_rate:.6f} | "
            f"{row.ddr_hit_rate:.6f} | "
            f"{row.p90_ttft_ms:.6f} | "
            f"{row.p90_kv_load_ms:.6f} | "
            f"{row.request_count} | "
            f"{row.cache_event_count} |"
        )

    lines.extend(
        [
            "",
            "## Timeline Results",
            "",
            "| hbm_capacity_blocks | timeline_mode | ttft_granularity | p90_compute_wait_ms | p90_kv_load_wait_ms | p90_uncached_prefill_compute_ms | total_chunk_count | total_progressive_materialized_tokens | max_kv_transfer_queue_depth |",
            "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in trace_rows:
        lines.append(
            "| "
            f"{row.hbm_capacity_blocks} | "
            f"{row.timeline_mode} | "
            f"{row.ttft_granularity} | "
            f"{row.p90_compute_wait_ms:.6f} | "
            f"{row.p90_kv_load_wait_ms:.6f} | "
            f"{row.p90_uncached_prefill_compute_ms:.6f} | "
            f"{row.total_chunk_count} | "
            f"{row.total_progressive_materialized_tokens} | "
            f"{row.max_kv_transfer_queue_depth} |"
        )

    latency_source_by_instance = config_details.get("latency_source_by_instance", {})
    if isinstance(latency_source_by_instance, Mapping) and latency_source_by_instance:
        lines.extend(
            [
                "",
                "## Latency Resolution",
                "",
                f"- Model registry enabled: {_detail(config_details, 'model_registry_enabled')}",
                f"- Model registry profile: {_detail(config_details, 'model_registry_profile_path')}",
                "",
                "| instance_uuid | latency_source |",
                "| --- | --- |",
            ]
        )
        for instance_uuid, source in sorted(latency_source_by_instance.items()):
            lines.append(f"| {instance_uuid} | {source} |")

    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- `capacity_sweep.csv` contains both trace and instance scope rows.",
            "- Instance rows set `cache_event_count` to 0; this means instance-level event count is not provided.",
            "- Use `scope=trace` rows for whole-trace capacity comparisons.",
            "- Use `scope=instance` rows to inspect fixed-routing instance imbalance.",
        ]
    )

    if cache_event_paths:
        lines.extend(["", "## Cache Event Dumps", ""])
        for capacity in sorted(cache_event_paths):
            lines.append(f"- {capacity}: {cache_event_paths[capacity]}")

    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _dataclass_rows(items: Sequence[CapacitySweepRow]) -> list[dict[str, object]]:
    return [{key: _csv_value(value) for key, value in asdict(item).items()} for item in items]


def _csv_value(value: object) -> object:
    if isinstance(value, tuple | list):
        return json.dumps(list(value), ensure_ascii=True)
    return value


def _detail(config_details: Mapping[str, object], key: str) -> object:
    return config_details.get(key, "")


def _cache_assumption_lines(config_details: Mapping[str, object]) -> list[str]:
    cache_mode = config_details.get("streaming_cache_mode")
    if cache_mode == _CACHE_MODE_HBM_DDR_LRU_PROGRESSIVE_TIMELINE:
        return [
            "- Finite instance-local HBM prefix cache.",
            "- Finite instance-local DDR/CPU prefix cache is enabled.",
            "- Progressive timeline mode is enabled for chunk-level TTFT accounting.",
            "- Full miss blocks become visible after scheduled chunk finish.",
            "- DDR KV load wait and compute wait are modeled as typed replay metrics.",
            "- DDR hit promotion to HBM is not modeled.",
            "- Cross-instance KV pooling is not modeled.",
            "- HBM and DDR eviction policy: lru.",
        ]
    if cache_mode == _CACHE_MODE_HBM_DDR_LRU:
        return [
            "- Finite instance-local HBM prefix cache.",
            "- Finite instance-local DDR/CPU prefix cache is enabled.",
            "- DDR hit accounting is modeled in `ddr_hit_tokens` and `ddr_hit_rate`.",
            "- DDR KV load latency is modeled when configured by Step8 `KVLoadLatencyProfile`.",
            "- DDR hit promotion to HBM is not modeled in Step7.",
            "- Cross-instance KV pooling is not modeled.",
            "- HBM and DDR eviction policy: lru.",
        ]
    if cache_mode == _CACHE_MODE_HBM_LRU:
        return [
            "- Finite instance-local HBM prefix cache.",
            "- DDR / SSD cache hits are not modeled in this mode; DDR fields are reserved as 0.",
            "- KV load metrics remain 0 unless the replay mode produces DDR hits and Step8 KV load is configured.",
            "- HBM eviction policy: lru.",
        ]
    return [
        "- Finite instance-local HBM prefix cache.",
        "- DDR / SSD cache hits are not modeled unless the streaming cache mode enables them.",
        "- KV load latency is modeled only when configured by Step8 `KVLoadLatencyProfile`.",
        "- HBM eviction policy: lru.",
    ]


def _format_sequence(value: object) -> str:
    if isinstance(value, tuple | list):
        return ", ".join(str(item) for item in value)
    return str(value)
