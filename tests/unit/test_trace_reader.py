import csv
from pathlib import Path

import pytest

from infertwin.trace.reader import read_trace_csv


def test_read_sample_trace() -> None:
    trace_path = Path("data/samples/sample_trace.csv")

    records = list(read_trace_csv(trace_path))

    assert len(records) == 1
    assert records[0].request_id == "00000000000000000000000000000001"


def test_read_trace_rejects_empty_instance_uuid(tmp_path: Path) -> None:
    trace_path = _write_trace(tmp_path, {"instance_uuid": "  "})

    with pytest.raises(ValueError, match="instance_uuid.*routed trace.*normalize-trace"):
        list(read_trace_csv(trace_path))


@pytest.mark.parametrize("column", ["request_id", "tenant_id", "request_params"])
def test_read_trace_rejects_empty_required_fields(tmp_path: Path, column: str) -> None:
    trace_path = _write_trace(tmp_path, {column: ""})

    with pytest.raises(ValueError, match=rf"row 2 column '{column}' must be non-empty"):
        list(read_trace_csv(trace_path))


def test_read_trace_rejects_empty_service_start_time(tmp_path: Path) -> None:
    trace_path = _write_trace(tmp_path, {"service_start_time": ""})

    with pytest.raises(
        ValueError,
        match="row 2 column 'service_start_time' must be non-empty",
    ):
        list(read_trace_csv(trace_path))


def test_read_trace_rejects_invalid_service_start_time(tmp_path: Path) -> None:
    trace_path = _write_trace(tmp_path, {"service_start_time": "not-a-timestamp"})

    with pytest.raises(
        ValueError,
        match="row 2 column 'service_start_time' must be ISO datetime",
    ):
        list(read_trace_csv(trace_path))


def _write_trace(tmp_path: Path, overrides: dict[str, str]) -> Path:
    trace_path = tmp_path / "trace.csv"
    row = {
        "request_id": "req-1",
        "tenant_id": "tenant-a",
        "instance_uuid": "instance-a",
        "request_params": "{}",
        "service_start_time": "2026-06-05 09:01:23",
    }
    row.update(overrides)
    with trace_path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=tuple(row))
        writer.writeheader()
        writer.writerow(row)
    return trace_path
