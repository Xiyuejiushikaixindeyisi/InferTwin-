import csv
from pathlib import Path

from infertwin.cli.main import main
from scripts.normalize_unrouted_trace import main as normalize_trace_script_main


def test_package_cli_normalize_trace_writes_routed_trace(tmp_path: Path) -> None:
    input_path = _write_unrouted_trace(tmp_path, "unrouted.csv")
    output_path = tmp_path / "routed.csv"

    assert (
        main(
            [
                "normalize-trace",
                "--input",
                str(input_path),
                "--output",
                str(output_path),
                "--instance-uuid",
                "instance-single",
            ]
        )
        == 0
    )

    rows = _read_csv(output_path)
    assert rows.fieldnames == [
        "request_id",
        "tenant_id",
        "instance_uuid",
        "request_params",
        "service_start_time",
    ]
    assert [row["instance_uuid"] for row in rows.rows] == [
        "instance-single",
        "instance-single",
    ]


def test_normalize_trace_script_is_thin_wrapper(tmp_path: Path) -> None:
    input_path = _write_unrouted_trace(tmp_path, "script_unrouted.csv")
    output_path = tmp_path / "script_routed.csv"

    assert (
        normalize_trace_script_main(
            [
                "--input",
                str(input_path),
                "--output",
                str(output_path),
                "--instance-uuid",
                "script-instance",
            ]
        )
        == 0
    )

    rows = _read_csv(output_path)
    assert [row["instance_uuid"] for row in rows.rows] == [
        "script-instance",
        "script-instance",
    ]


class _CsvRows:
    def __init__(self, fieldnames: list[str] | None, rows: list[dict[str, str]]) -> None:
        self.fieldnames = fieldnames
        self.rows = rows


def _write_unrouted_trace(tmp_path: Path, filename: str) -> Path:
    trace_path = tmp_path / filename
    with trace_path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(["request_id", "tenant_id", "request_params", "service_start_time"])
        writer.writerow(
            [
                "00000000000000000000000000000001",
                "tenant-a",
                '{"model":"glm-v5","messages":[],"tools":[]}',
                "2026-06-05 09:01:23",
            ]
        )
        writer.writerow(
            [
                "00000000000000000000000000000002",
                "tenant-b",
                '{"model":"glm-v5","messages":[],"tools":[]}',
                "2026-06-05 09:01:24",
            ]
        )
    return trace_path


def _read_csv(path: Path) -> _CsvRows:
    with path.open("r", encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)
        return _CsvRows(reader.fieldnames, list(reader))
