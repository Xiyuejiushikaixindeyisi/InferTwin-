from pathlib import Path

import pytest

from infertwin.cli.main import main
from scripts.run_simulation import main as run_simulation_script_main
from scripts.validate_trace import main as validate_trace_script_main


def test_package_cli_simulate_runs_replay_and_writes_reports(tmp_path: Path) -> None:
    trace_path = _write_trace(tmp_path)
    config_path = _write_config(tmp_path, trace_path)

    assert main(["simulate", "--config", str(config_path)]) == 0

    output_dir = tmp_path / "reports"
    assert (output_dir / "request_metrics.csv").is_file()
    assert (output_dir / "iteration_metrics.csv").is_file()
    assert (output_dir / "cache_events.csv").is_file()
    assert (output_dir / "summary.md").is_file()


def test_scripts_are_thin_wrappers_for_package_cli(tmp_path: Path) -> None:
    trace_path = _write_trace(tmp_path)
    config_path = _write_config(tmp_path, trace_path)

    assert run_simulation_script_main(["--config", str(config_path)]) == 0
    assert validate_trace_script_main(["--trace", str(trace_path)]) == 0


def test_package_cli_validate_trace_rejects_missing_columns(tmp_path: Path) -> None:
    trace_path = tmp_path / "bad_trace.csv"
    trace_path.write_text("request_id,tenant_id\n1,tenant-a\n", encoding="utf-8")

    with pytest.raises(ValueError, match="missing required columns"):
        main(["validate-trace", "--input", str(trace_path)])


def _write_trace(tmp_path: Path) -> Path:
    trace_path = tmp_path / "trace.csv"
    trace_path.write_text(
        "\n".join(
            [
                "request_id,tenant_id,instance_uuid,request_params,service_start_time",
                _row(
                    "00000000000000000000000000000001",
                    "instance-a",
                    "same prompt",
                    "2026-06-05 09:01:23",
                ),
                _row(
                    "00000000000000000000000000000002",
                    "instance-a",
                    "same prompt",
                    "2026-06-05 09:01:24",
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return trace_path


def _write_config(tmp_path: Path, trace_path: Path) -> Path:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        f"""
simulation:
  mode: batch_aware_hbm_lru
trace:
  path: {trace_path}
tokenizers:
  root: tokenizers
  default_profile: glm-v5
  cache_scope: tenant_isolated
cache:
  block_size_tokens: 4
  policy: hbm
  eviction_policy: lru
  hbm_capacity_blocks: 8
scheduler:
  policy: fcfs
  max_num_batched_tokens: 8192
  max_num_seqs: 32
  enable_chunked_prefill: true
  long_prefill_token_threshold: 4096
latency:
  backend: fitted_ttft
  model_name: glm-v5
  hardware_name: ascend910c
  fitted_ttft:
    profile: glm-v5_ascend910c_default
    function: token_linear_v1
    intercept_ms: 0.0
    ms_per_uncached_token: 1.0
    calibrated_from: integration-test
output:
  directory: {tmp_path / "reports"}
""",
        encoding="utf-8",
    )
    return config_path


def _row(
    request_id: str,
    instance_uuid: str,
    prompt: str,
    timestamp: str,
) -> str:
    request_params = (
        "{"
        '""model"":""glm-v5"",'
        '""messages"":[{""role"":""user"",""content"":""'
        f"{prompt}"
        '""}],'
        '""tools"":[]'
        "}"
    )
    return f'{request_id},tenant-a,{instance_uuid},"{request_params}",{timestamp}'
