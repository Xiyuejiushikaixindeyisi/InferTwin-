"""InferTwin command-line interface."""

from __future__ import annotations

import argparse
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from infertwin.config.loader import load_yaml
from infertwin.experiment.runner import ExperimentResult, ExperimentRunner
from infertwin.experiment.sweep import CapacitySweepReportPaths, CapacitySweepRunner
from infertwin.report.sweep import write_capacity_sweep_report
from infertwin.streaming.sweep import StreamingCapacitySweepRunner
from infertwin.trace.normalizer import TraceNormalizeResult, normalize_unrouted_trace
from infertwin.trace.reader import read_trace_csv


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="infertwin", description="Run InferTwin simulations.")
    subparsers = parser.add_subparsers(dest="command")

    simulate = subparsers.add_parser("simulate", help="Run an offline hit-floor simulation.")
    simulate.add_argument("--config", required=True, type=Path, help="Experiment config path.")

    sweep = subparsers.add_parser("sweep", help="Run an HBM cache capacity sweep.")
    sweep.add_argument("--config", required=True, type=Path, help="Capacity sweep config path.")

    sweep_streaming = subparsers.add_parser(
        "sweep-streaming",
        help="Run an HBM cache capacity sweep through streaming request shards.",
    )
    sweep_streaming.add_argument(
        "--config",
        required=True,
        type=Path,
        help="Streaming capacity sweep config path.",
    )

    validate = subparsers.add_parser("validate-trace", help="Validate a trace CSV file.")
    validate.add_argument("--input", required=True, type=Path, help="Trace CSV path.")

    normalize = subparsers.add_parser(
        "normalize-trace",
        help="Convert an unrouted trace CSV into a single-instance routed trace CSV.",
    )
    normalize.add_argument("--input", required=True, type=Path, help="Unrouted trace CSV path.")
    normalize.add_argument("--output", required=True, type=Path, help="Output routed trace path.")
    normalize.add_argument(
        "--instance-uuid",
        required=True,
        help="Instance UUID to assign to every request in the output trace.",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "simulate":
        result = run_simulation(args.config)
        print_simulation_result(result)
        return 0

    if args.command == "sweep":
        paths = run_capacity_sweep(args.config)
        print_capacity_sweep_result(paths)
        return 0

    if args.command == "sweep-streaming":
        paths = run_streaming_capacity_sweep(args.config)
        print_capacity_sweep_result(paths)
        return 0

    if args.command == "validate-trace":
        summary = validate_trace(args.input)
        print_trace_summary(summary)
        return 0

    if args.command == "normalize-trace":
        result = run_trace_normalization(
            input_path=args.input,
            output_path=args.output,
            instance_uuid=args.instance_uuid,
        )
        print_trace_normalization_result(result)
        return 0

    parser.print_help()
    return 0


def run_simulation(config_path: Path) -> ExperimentResult:
    config = load_yaml(config_path)
    return ExperimentRunner(config).run()


def run_capacity_sweep(config_path: Path) -> CapacitySweepReportPaths:
    config = load_yaml(config_path)
    result = CapacitySweepRunner(config).run()
    return write_capacity_sweep_report(result, _output_dir(config))


def run_streaming_capacity_sweep(config_path: Path) -> CapacitySweepReportPaths:
    config = load_yaml(config_path)
    result = StreamingCapacitySweepRunner(config).run()
    return write_capacity_sweep_report(result, _output_dir(config))


def print_simulation_result(result: ExperimentResult) -> None:
    print(f"InferTwin {result.metrics['phase']} simulation completed.")
    print(f"Output directory: {result.output_dir}")
    print(f"Request metrics: {result.metrics['request_metrics_path']}")
    if "iteration_metrics_path" in result.metrics:
        print(f"Iteration metrics: {result.metrics['iteration_metrics_path']}")
    if "cache_events_path" in result.metrics:
        print(f"Cache events: {result.metrics['cache_events_path']}")
    print(f"Summary: {result.metrics['summary_path']}")


def print_capacity_sweep_result(paths: CapacitySweepReportPaths) -> None:
    print("InferTwin capacity_sweep completed.")
    print(f"Capacity sweep: {paths.capacity_sweep_path}")
    print(f"Summary: {paths.summary_path}")


def validate_trace(trace_path: Path) -> dict[str, object]:
    records = list(read_trace_csv(trace_path))
    instances = sorted({record.instance_uuid for record in records})
    tenants = sorted({record.tenant_id for record in records})
    timestamps = [record.service_start_time for record in records]
    return {
        "record_count": len(records),
        "instance_count": len(instances),
        "tenant_count": len(tenants),
        "start_time": min(timestamps).isoformat(sep=" ") if timestamps else "",
        "end_time": max(timestamps).isoformat(sep=" ") if timestamps else "",
    }


def run_trace_normalization(
    *,
    input_path: Path,
    output_path: Path,
    instance_uuid: str,
) -> TraceNormalizeResult:
    return normalize_unrouted_trace(
        input_path,
        output_path,
        instance_uuid=instance_uuid,
    )


def print_trace_summary(summary: dict[str, object]) -> None:
    print(f"Parsed {summary['record_count']} trace records.")
    print(f"Instances: {summary['instance_count']}")
    print(f"Tenants: {summary['tenant_count']}")
    if summary["start_time"] and summary["end_time"]:
        print(f"Time range: {summary['start_time']} -> {summary['end_time']}")


def print_trace_normalization_result(result: TraceNormalizeResult) -> None:
    print("InferTwin trace normalization completed.")
    print(f"Input: {result.input_path}")
    print(f"Output: {result.output_path}")
    print(f"Rows: {result.row_count}")
    print(f"Instance UUID: {result.instance_uuid}")


def _output_dir(config: Mapping[str, Any]) -> Path:
    output_config = config.get("output", {})
    if output_config is None:
        output_config = {}
    if not isinstance(output_config, Mapping):
        raise ValueError("output config must be a mapping")
    directory = output_config.get("directory", "reports")
    if not isinstance(directory, str) or not directory:
        raise ValueError("output.directory must be a non-empty string")
    return Path(directory)


if __name__ == "__main__":
    raise SystemExit(main())
