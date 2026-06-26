import csv
from pathlib import Path

import yaml

from infertwin.cli.main import run_capacity_sweep
from scripts.run_capacity_sweep import main as run_capacity_sweep_script


def test_capacity_sweep_package_cli_writes_report(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path, output_dir=tmp_path / "reports")

    paths = run_capacity_sweep(config_path)

    assert paths.capacity_sweep_path.is_file()
    assert paths.summary_path.is_file()
    rows = list(csv.DictReader(paths.capacity_sweep_path.open(encoding="utf-8")))
    assert rows
    assert {row["scope"] for row in rows} == {"trace", "instance"}
    assert {"hbm_capacity_blocks", "kv_hit_rate", "p90_ttft_ms", "ddr_hit_tokens"}.issubset(rows[0])
    assert {row["ddr_hit_tokens"] for row in rows} == {"0"}


def test_capacity_sweep_script_wrapper_writes_report(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path, output_dir=tmp_path / "wrapper_reports")

    assert run_capacity_sweep_script(["--config", str(config_path)]) == 0

    assert (tmp_path / "wrapper_reports" / "capacity_sweep.csv").is_file()
    assert (tmp_path / "wrapper_reports" / "summary.md").is_file()


def _write_config(tmp_path: Path, *, output_dir: Path) -> Path:
    trace_path = tmp_path / "trace.csv"
    trace_path.write_text(
        "\n".join(
            [
                "request_id,tenant_id,instance_uuid,request_params,service_start_time",
                _row("00000000000000000000000000000001", "instance-a", "same prompt", 23),
                _row("00000000000000000000000000000002", "instance-a", "same prompt", 24),
                _row("00000000000000000000000000000003", "instance-b", "same prompt", 25),
                _row("00000000000000000000000000000004", "instance-b", "same prompt", 26),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    config = {
        "simulation": {"mode": "capacity_sweep"},
        "trace": {"path": str(trace_path)},
        "tokenizers": {
            "root": "tokenizers",
            "default_profile": "glm-v5",
            "cache_scope": "tenant_isolated",
        },
        "cache": {"block_size_tokens": 4, "policy": "hbm", "eviction_policy": "lru"},
        "sweep": {"hbm_capacity_blocks": [1, 4], "parallel_instances": False},
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
            "cache_event_capacities": [],
        },
    }
    config_path = tmp_path / f"{output_dir.name}.yaml"
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    return config_path


def _row(request_id: str, instance_uuid: str, prompt: str, second: int) -> str:
    request_params = (
        "{"
        '""model"":""glm-v5"",'
        '""messages"":[{""role"":""user"",""content"":""'
        f"{prompt}"
        '""}],'
        '""tools"":[]'
        "}"
    )
    return f'{request_id},tenant-a,{instance_uuid},"{request_params}",2026-06-05 09:01:{second}'
