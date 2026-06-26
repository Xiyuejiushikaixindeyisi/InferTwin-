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


def write_capacity_sweep_report(
    result: CapacitySweepResult,
    output_dir: str | Path,
) -> CapacitySweepReportPaths:
    """Write the standard Step6 CSV and Markdown report around typed results."""

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
        "- Finite instance-local HBM prefix cache.",
        "- HBM eviction policy: lru.",
        "- DDR / SSD cache hits are not modeled in Step6; DDR fields are reserved as 0.",
        "- KV load latency is not modeled; TTFT comes from the configured latency backend.",
        "- P90 target matching / hit floor search is not performed.",
        "- Cache event details are disabled by default and only dumped for selected capacities.",
        "",
        "## Config",
        "",
        f"- Latency backend: {_detail(config_details, 'latency_backend')}",
        f"- Model: {_detail(config_details, 'model_name')}",
        f"- Hardware: {_detail(config_details, 'hardware_name')}",
        f"- Capacities: {_format_sequence(config_details.get('capacities', ()))}",
        f"- Cache event capacities: {_format_sequence(config_details.get('cache_event_capacities', ()))}",
        "",
        "## Trace-Level Results",
        "",
        "| hbm_capacity_blocks | kv_hit_rate | hbm_hit_rate | ddr_hit_rate | p90_ttft_ms | request_count | cache_event_count |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in trace_rows:
        lines.append(
            "| "
            f"{row.hbm_capacity_blocks} | "
            f"{row.kv_hit_rate:.6f} | "
            f"{row.hbm_hit_rate:.6f} | "
            f"{row.ddr_hit_rate:.6f} | "
            f"{row.p90_ttft_ms:.6f} | "
            f"{row.request_count} | "
            f"{row.cache_event_count} |"
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
            "- Instance rows set `cache_event_count` to 0 in Step6 v1; this means instance-level event count is not provided.",
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


def _format_sequence(value: object) -> str:
    if isinstance(value, tuple | list):
        return ", ".join(str(item) for item in value)
    return str(value)
