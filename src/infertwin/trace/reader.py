"""CSV trace reader."""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from typing import Iterable

from infertwin.trace.schema import TraceRecord


def read_trace_csv(path: str | Path) -> Iterable[TraceRecord]:
    trace_path = Path(path)
    with trace_path.open("r", encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)
        _validate_columns(reader.fieldnames or [], trace_path)
        for line_number, row in enumerate(reader, start=2):
            yield TraceRecord(
                request_id=_required_non_empty(row, "request_id", trace_path, line_number),
                tenant_id=_required_non_empty(row, "tenant_id", trace_path, line_number),
                instance_uuid=_required_non_empty_instance_uuid(row, trace_path, line_number),
                request_params=_required_non_empty(row, "request_params", trace_path, line_number),
                service_start_time=_parse_service_start_time(row, trace_path, line_number),
            )


def _validate_columns(fieldnames: list[str], path: Path) -> None:
    required = {
        "request_id",
        "tenant_id",
        "instance_uuid",
        "request_params",
        "service_start_time",
    }
    missing = required - set(fieldnames)
    if missing:
        raise ValueError(f"{path}: missing required columns {sorted(missing)}")


def _required_non_empty(
    row: dict[str, str],
    column: str,
    path: Path,
    line_number: int,
) -> str:
    value = row[column]
    if value is None or not value.strip():
        raise ValueError(f"{path}: row {line_number} column {column!r} must be non-empty")
    return value


def _required_non_empty_instance_uuid(
    row: dict[str, str],
    path: Path,
    line_number: int,
) -> str:
    value = row["instance_uuid"]
    if value is None or not value.strip():
        raise ValueError(
            f"{path}: row {line_number} column 'instance_uuid' must be non-empty; "
            "core replay requires a routed trace. If you explicitly do not want "
            "gateway routing simulation, run `infertwin normalize-trace` first."
        )
    return value


def _parse_service_start_time(
    row: dict[str, str],
    path: Path,
    line_number: int,
) -> datetime:
    raw_value = _required_non_empty(row, "service_start_time", path, line_number)
    try:
        return datetime.fromisoformat(raw_value)
    except ValueError as exc:
        raise ValueError(
            f"{path}: row {line_number} column 'service_start_time' must be ISO datetime, "
            f"got {raw_value!r}"
        ) from exc
