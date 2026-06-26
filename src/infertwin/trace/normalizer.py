"""Outer trace normalization utilities."""

from __future__ import annotations

import csv
import os
import uuid
from dataclasses import dataclass
from pathlib import Path

_REQUIRED_UNROUTED_COLUMNS = {
    "request_id",
    "tenant_id",
    "request_params",
    "service_start_time",
}


@dataclass(frozen=True, slots=True)
class TraceNormalizeResult:
    input_path: Path
    output_path: Path
    row_count: int
    added_instance_uuid_column: bool
    filled_empty_instance_uuid_count: int
    instance_uuid: str


def normalize_unrouted_trace(
    input_path: str | Path,
    output_path: str | Path,
    *,
    instance_uuid: str,
) -> TraceNormalizeResult:
    """Convert an unrouted trace CSV into a single-instance routed trace CSV.

    This is an outer data-preparation utility. It does not parse request JSON,
    tokenize prompts, validate timestamps, or change core replay semantics.
    """

    source_path = Path(input_path)
    destination_path = Path(output_path)
    normalized_instance_uuid = _validate_instance_uuid(instance_uuid)

    if destination_path.exists():
        raise FileExistsError(f"{destination_path}: output path already exists")

    destination_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = destination_path.with_name(f".{destination_path.name}.{uuid.uuid4().hex}.tmp")

    row_count = 0
    try:
        with (
            source_path.open("r", encoding="utf-8", newline="") as input_file,
            temp_path.open("w", encoding="utf-8", newline="") as output_file,
        ):
            reader = csv.DictReader(input_file)
            input_fieldnames = list(reader.fieldnames or [])
            _validate_unrouted_columns(input_fieldnames, source_path)

            output_fieldnames = _insert_instance_uuid_after_tenant(input_fieldnames)
            writer = csv.DictWriter(output_file, fieldnames=output_fieldnames)
            writer.writeheader()

            for row_number, row in enumerate(reader, start=2):
                _validate_row_shape(row, input_fieldnames, source_path, row_number)
                row["instance_uuid"] = normalized_instance_uuid
                writer.writerow(row)
                row_count += 1

        os.replace(temp_path, destination_path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise

    return TraceNormalizeResult(
        input_path=source_path,
        output_path=destination_path,
        row_count=row_count,
        added_instance_uuid_column=True,
        filled_empty_instance_uuid_count=0,
        instance_uuid=normalized_instance_uuid,
    )


def _validate_instance_uuid(instance_uuid: str) -> str:
    normalized = instance_uuid.strip()
    if not normalized:
        raise ValueError("instance_uuid must be a non-empty string")
    if "\n" in normalized or "\r" in normalized:
        raise ValueError("instance_uuid must not contain newlines")
    return normalized


def _validate_unrouted_columns(fieldnames: list[str], path: Path) -> None:
    if "instance_uuid" in fieldnames:
        raise ValueError(f"{path}: instance_uuid column already exists")

    missing = _REQUIRED_UNROUTED_COLUMNS - set(fieldnames)
    if missing:
        raise ValueError(f"{path}: missing required columns {sorted(missing)}")


def _insert_instance_uuid_after_tenant(fieldnames: list[str]) -> list[str]:
    output_fieldnames: list[str] = []
    for fieldname in fieldnames:
        output_fieldnames.append(fieldname)
        if fieldname == "tenant_id":
            output_fieldnames.append("instance_uuid")
    return output_fieldnames


def _validate_row_shape(
    row: dict[str | None, str | list[str] | None],
    fieldnames: list[str],
    path: Path,
    row_number: int,
) -> None:
    if None in row:
        raise ValueError(f"{path}: row {row_number} has more fields than the header")

    missing_values = [fieldname for fieldname in fieldnames if row.get(fieldname) is None]
    if missing_values:
        raise ValueError(
            f"{path}: row {row_number} has missing values for columns {missing_values}"
        )
