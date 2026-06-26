import csv
from pathlib import Path

import pytest
import yaml

from infertwin.cli.main import main


def test_unrouted_trace_requires_normalization_before_streaming_replay(
    tmp_path: Path,
) -> None:
    unrouted_trace = _write_unrouted_trace(tmp_path)
    routed_trace = tmp_path / "routed.csv"

    with pytest.raises(ValueError, match="missing required columns"):
        main(["validate-trace", "--input", str(unrouted_trace)])

    assert (
        main(
            [
                "normalize-trace",
                "--input",
                str(unrouted_trace),
                "--output",
                str(routed_trace),
                "--instance-uuid",
                "single-instance",
            ]
        )
        == 0
    )

    assert main(["validate-trace", "--input", str(routed_trace)]) == 0

    config_path = _write_streaming_sweep_config(
        tmp_path,
        trace_path=routed_trace,
        output_dir=tmp_path / "reports",
    )
    assert main(["sweep-streaming", "--config", str(config_path)]) == 0

    capacity_rows = list(
        csv.DictReader((tmp_path / "reports" / "capacity_sweep.csv").open(encoding="utf-8"))
    )
    assert {row["scope"] for row in capacity_rows} == {"trace", "instance"}

    instance_rows = [row for row in capacity_rows if row["scope"] == "instance"]
    assert len(instance_rows) == 1
    assert instance_rows[0]["instance_uuid"] == "single-instance"
    assert instance_rows[0]["request_count"] == "2"


def _write_unrouted_trace(tmp_path: Path) -> Path:
    trace_path = tmp_path / "unrouted.csv"
    trace_path.write_text(
        "\n".join(
            [
                "request_id,tenant_id,request_params,service_start_time",
                _row("00000000000000000000000000000001", "same prompt", 23),
                _row("00000000000000000000000000000002", "same prompt", 24),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return trace_path


def _write_streaming_sweep_config(
    tmp_path: Path,
    *,
    trace_path: Path,
    output_dir: Path,
) -> Path:
    config = {
        "simulation": {"mode": "capacity_sweep_streaming"},
        "trace": {"path": str(trace_path)},
        "tokenizers": {
            "root": "tokenizers",
            "default_profile": "glm-v5",
            "cache_scope": "tenant_isolated",
        },
        "cache": {
            "block_size_tokens": 4,
            "policy": "hbm",
            "eviction_policy": "lru",
        },
        "sweep": {
            "hbm_capacity_blocks": [4],
            "parallel_instances": False,
        },
        "scheduler": {
            "policy": "fcfs",
            "max_num_batched_tokens": 8192,
            "max_num_seqs": 32,
            "enable_chunked_prefill": True,
            "long_prefill_token_threshold": 4096,
        },
        "latency": {
            "backend": "fitted_ttft",
            "model_name": "glm-v5",
            "hardware_name": "ascend910c",
            "fitted_ttft": {
                "profile": "glm-v5_ascend910c_default",
                "function": "token_linear_v1",
                "intercept_ms": 0.0,
                "ms_per_uncached_token": 1.0,
                "calibrated_from": "integration-test",
            },
        },
        "output": {
            "directory": str(output_dir),
            "cache_events": False,
        },
    }
    config_path = tmp_path / "streaming_sweep.yaml"
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    return config_path


def _row(request_id: str, prompt: str, second: int) -> str:
    request_params = (
        "{"
        '""model"":""glm-v5"",'
        '""messages"":[{""role"":""user"",""content"":""'
        f"{prompt}"
        '""}],'
        '""tools"":[]'
        "}"
    )
    return f'{request_id},tenant-a,"{request_params}",2026-06-05 09:01:{second}'
