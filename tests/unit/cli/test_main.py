from pathlib import Path

from hitfloor.cli.main import validate_trace


def test_validate_trace_returns_basic_summary(tmp_path: Path) -> None:
    trace_path = tmp_path / "trace.csv"
    trace_path.write_text(
        "\n".join(
            [
                "request_id,tenant_id,instance_uuid,request_params,service_start_time",
                _row("00000000000000000000000000000001", "tenant-a", "instance-a"),
                _row("00000000000000000000000000000002", "tenant-b", "instance-b"),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    summary = validate_trace(trace_path)

    assert summary["record_count"] == 2
    assert summary["instance_count"] == 2
    assert summary["tenant_count"] == 2
    assert summary["start_time"] == "2026-06-05 09:01:23"
    assert summary["end_time"] == "2026-06-05 09:01:23"


def _row(request_id: str, tenant_id: str, instance_uuid: str) -> str:
    request_params = (
        "{"
        '""model"":""glm-v5"",'
        '""messages"":[{""role"":""user"",""content"":""hello""}],'
        '""tools"":[]'
        "}"
    )
    return f'{request_id},{tenant_id},{instance_uuid},"{request_params}",2026-06-05 09:01:23'
