import csv
from pathlib import Path

import pytest

from infertwin.trace.normalizer import normalize_unrouted_trace


def test_normalize_unrouted_trace_adds_instance_uuid_and_preserves_columns(
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "unrouted.csv"
    output_path = tmp_path / "routed.csv"
    _write_csv(
        input_path,
        ["request_id", "tenant_id", "request_params", "service_start_time", "extra"],
        [
            [
                "req-1",
                "tenant-a",
                '{"model":"glm-v5","messages":[]}',
                "2026-06-05 09:01:23",
                "keep-me",
            ],
            [
                "req-2",
                "tenant-b",
                '{"model":"glm-v5","messages":[]}',
                "2026-06-05 09:01:24",
                "keep-me-too",
            ],
        ],
    )

    result = normalize_unrouted_trace(
        input_path,
        output_path,
        instance_uuid="instance-single",
    )

    assert result.input_path == input_path
    assert result.output_path == output_path
    assert result.row_count == 2
    assert result.added_instance_uuid_column is True
    assert result.filled_empty_instance_uuid_count == 0
    assert result.instance_uuid == "instance-single"

    rows = _read_csv(output_path)
    assert rows.fieldnames == [
        "request_id",
        "tenant_id",
        "instance_uuid",
        "request_params",
        "service_start_time",
        "extra",
    ]
    assert [row["instance_uuid"] for row in rows.rows] == [
        "instance-single",
        "instance-single",
    ]
    assert rows.rows[0]["extra"] == "keep-me"
    assert rows.rows[1]["request_params"] == '{"model":"glm-v5","messages":[]}'


def test_normalize_unrouted_trace_rejects_empty_instance_uuid(tmp_path: Path) -> None:
    input_path = _write_minimal_unrouted_trace(tmp_path)

    with pytest.raises(ValueError, match="instance_uuid must be a non-empty string"):
        normalize_unrouted_trace(input_path, tmp_path / "out.csv", instance_uuid=" ")


def test_normalize_unrouted_trace_rejects_instance_uuid_column(tmp_path: Path) -> None:
    input_path = tmp_path / "already_routed.csv"
    _write_csv(
        input_path,
        [
            "request_id",
            "tenant_id",
            "instance_uuid",
            "request_params",
            "service_start_time",
        ],
        [["req-1", "tenant-a", "instance-a", "{}", "2026-06-05 09:01:23"]],
    )

    with pytest.raises(ValueError, match="instance_uuid column already exists"):
        normalize_unrouted_trace(
            input_path,
            tmp_path / "out.csv",
            instance_uuid="instance-single",
        )


def test_normalize_unrouted_trace_rejects_missing_required_columns(
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "bad.csv"
    _write_csv(input_path, ["request_id", "tenant_id"], [["req-1", "tenant-a"]])

    with pytest.raises(ValueError, match="missing required columns"):
        normalize_unrouted_trace(
            input_path,
            tmp_path / "out.csv",
            instance_uuid="instance-single",
        )


def test_normalize_unrouted_trace_rejects_existing_output_path(tmp_path: Path) -> None:
    input_path = _write_minimal_unrouted_trace(tmp_path)
    output_path = tmp_path / "out.csv"
    output_path.write_text("do not overwrite\n", encoding="utf-8")

    with pytest.raises(FileExistsError, match="output path already exists"):
        normalize_unrouted_trace(
            input_path,
            output_path,
            instance_uuid="instance-single",
        )

    assert output_path.read_text(encoding="utf-8") == "do not overwrite\n"


def test_normalize_unrouted_trace_keeps_header_for_empty_trace(tmp_path: Path) -> None:
    input_path = tmp_path / "empty.csv"
    _write_csv(
        input_path,
        ["request_id", "tenant_id", "request_params", "service_start_time"],
        [],
    )
    output_path = tmp_path / "out.csv"

    result = normalize_unrouted_trace(
        input_path,
        output_path,
        instance_uuid="instance-single",
    )

    assert result.row_count == 0
    rows = _read_csv(output_path)
    assert rows.fieldnames == [
        "request_id",
        "tenant_id",
        "instance_uuid",
        "request_params",
        "service_start_time",
    ]
    assert rows.rows == []


def test_normalize_unrouted_trace_rejects_malformed_rows(tmp_path: Path) -> None:
    input_path = tmp_path / "malformed.csv"
    input_path.write_text(
        "request_id,tenant_id,request_params,service_start_time\n"
        "req-1,tenant-a,{},2026-06-05 09:01:23,extra\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="more fields than the header"):
        normalize_unrouted_trace(
            input_path,
            tmp_path / "out.csv",
            instance_uuid="instance-single",
        )


class _CsvRows:
    def __init__(self, fieldnames: list[str] | None, rows: list[dict[str, str]]) -> None:
        self.fieldnames = fieldnames
        self.rows = rows


def _write_minimal_unrouted_trace(tmp_path: Path) -> Path:
    input_path = tmp_path / "unrouted.csv"
    _write_csv(
        input_path,
        ["request_id", "tenant_id", "request_params", "service_start_time"],
        [["req-1", "tenant-a", "{}", "2026-06-05 09:01:23"]],
    )
    return input_path


def _write_csv(path: Path, fieldnames: list[str], rows: list[list[str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(fieldnames)
        writer.writerows(rows)


def _read_csv(path: Path) -> _CsvRows:
    with path.open("r", encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)
        return _CsvRows(reader.fieldnames, list(reader))
