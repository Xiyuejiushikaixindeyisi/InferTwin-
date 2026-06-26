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
        for row in reader:
            yield TraceRecord(
                request_id=row["request_id"],
                tenant_id=row["tenant_id"],
                instance_uuid=row["instance_uuid"],
                request_params=row["request_params"],
                service_start_time=datetime.fromisoformat(row["service_start_time"]),
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
