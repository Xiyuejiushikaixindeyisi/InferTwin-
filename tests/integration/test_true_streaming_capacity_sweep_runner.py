import csv
from pathlib import Path

import pytest
import yaml

from infertwin.cli.main import run_streaming_capacity_sweep
from infertwin.experiment.sweep import CapacitySweepRunner
from infertwin.report.sweep import write_capacity_sweep_report
from infertwin.streaming.sweep import (
    STREAMING_CAPACITY_SWEEP_MODE,
    StreamingCapacitySweepRunner,
)


def test_streaming_capacity_sweep_runner_matches_batch_runner(tmp_path: Path) -> None:
    trace_path = _write_trace(tmp_path)
    batch_config = _config(
        trace_path,
        mode="capacity_sweep",
        output_dir=tmp_path / "batch_reports",
        capacities=[1, 4],
        cache_events=False,
    )
    streaming_config = _config(
        trace_path,
        mode=STREAMING_CAPACITY_SWEEP_MODE,
        output_dir=tmp_path / "streaming_reports",
        capacities=[1, 4],
        cache_events=False,
    )

    batch_result = CapacitySweepRunner(batch_config).run()
    streaming_result = StreamingCapacitySweepRunner(streaming_config).run()

    assert streaming_result.rows == batch_result.rows
    assert streaming_result.config_details["phase"] == STREAMING_CAPACITY_SWEEP_MODE
    assert streaming_result.config_details["request_build_accepted_count"] == 4
    assert streaming_result.config_details["request_build_rejected_count"] == 0
    assert streaming_result.config_details["instance_latency_enabled"] is False
    assert (tmp_path / "streaming_reports" / "streaming_shards").is_dir()


def test_streaming_capacity_sweep_runner_uses_instance_latency_profiles(
    tmp_path: Path,
) -> None:
    trace_path = _write_trace(tmp_path)
    instance_profile_path = _write_instance_latency_profile(
        tmp_path,
        instance_slopes={
            "instance-a": 1.0,
            "instance-b": 3.0,
        },
    )
    config = _config(
        trace_path,
        mode=STREAMING_CAPACITY_SWEEP_MODE,
        output_dir=tmp_path / "streaming_reports",
        capacities=[4],
        cache_events=False,
        instance_latency_profile_path=instance_profile_path,
    )

    result = StreamingCapacitySweepRunner(config).run()

    rows = {
        (row.scope, row.instance_uuid): row for row in result.rows if row.hbm_capacity_blocks == 4
    }
    instance_a = rows[("instance", "instance-a")]
    instance_b = rows[("instance", "instance-b")]
    assert instance_a.request_count == 2
    assert instance_b.request_count == 2
    assert instance_b.p90_ttft_ms == instance_a.p90_ttft_ms * 3
    assert result.config_details["instance_latency_enabled"] is True
    assert result.config_details["instance_latency_profile_path"] == str(instance_profile_path)
    assert result.config_details["instance_latency_profile_count"] == 2


def test_streaming_capacity_sweep_runner_reports_model_default_latency_sources(
    tmp_path: Path,
) -> None:
    trace_path = _write_trace(tmp_path)
    instance_profile_path = _write_mixed_instance_latency_profile(tmp_path)
    model_registry_path = _write_model_registry(tmp_path, default_slope=3.0)
    config = _config(
        trace_path,
        mode=STREAMING_CAPACITY_SWEEP_MODE,
        output_dir=tmp_path / "streaming_reports",
        capacities=[4],
        cache_events=False,
        instance_latency_profile_path=instance_profile_path,
        model_registry_profile_path=model_registry_path,
    )

    result = StreamingCapacitySweepRunner(config).run()

    rows = {
        (row.scope, row.instance_uuid): row for row in result.rows if row.hbm_capacity_blocks == 4
    }
    instance_a = rows[("instance", "instance-a")]
    instance_b = rows[("instance", "instance-b")]
    assert instance_b.p90_ttft_ms == instance_a.p90_ttft_ms * 3
    assert result.config_details["model_registry_enabled"] is True
    assert result.config_details["model_registry_profile_path"] == str(model_registry_path)
    assert result.config_details["latency_source_by_instance"] == {
        "instance-a": "instance_profile",
        "instance-b": "model_default",
    }

    paths = write_capacity_sweep_report(result, tmp_path / "streaming_reports")
    summary = paths.summary_path.read_text(encoding="utf-8")
    assert "## Latency Resolution" in summary
    assert "| instance-a | instance_profile |" in summary
    assert "| instance-b | model_default |" in summary


def test_streaming_capacity_sweep_runner_fails_when_instance_latency_profile_missing(
    tmp_path: Path,
) -> None:
    trace_path = _write_trace(tmp_path)
    instance_profile_path = _write_instance_latency_profile(
        tmp_path,
        instance_slopes={"instance-a": 1.0},
    )
    config = _config(
        trace_path,
        mode=STREAMING_CAPACITY_SWEEP_MODE,
        output_dir=tmp_path / "streaming_reports",
        capacities=[4],
        cache_events=False,
        instance_latency_profile_path=instance_profile_path,
    )

    with pytest.raises(ValueError, match="instance latency profile missing"):
        StreamingCapacitySweepRunner(config).run()


def test_streaming_capacity_sweep_runner_dumps_selected_cache_events(
    tmp_path: Path,
) -> None:
    trace_path = _write_trace(tmp_path)
    config = _config(
        trace_path,
        mode=STREAMING_CAPACITY_SWEEP_MODE,
        output_dir=tmp_path / "streaming_reports",
        capacities=[1, 4],
        cache_events=True,
        cache_event_capacities=[1],
    )

    result = StreamingCapacitySweepRunner(config).run()

    selected_path = tmp_path / "streaming_reports" / "capacity_1" / "cache_events.csv"
    other_path = tmp_path / "streaming_reports" / "capacity_4" / "cache_events.csv"
    assert result.cache_event_paths == {1: selected_path}
    assert selected_path.is_file()
    assert not other_path.exists()
    event_rows = list(csv.DictReader(selected_path.open(encoding="utf-8")))
    assert event_rows

    paths = write_capacity_sweep_report(result, tmp_path / "streaming_reports")
    assert paths.capacity_sweep_path.is_file()
    assert paths.summary_path.is_file()
    summary = paths.summary_path.read_text(encoding="utf-8")
    assert "capacity_1/cache_events.csv" in summary


def test_streaming_capacity_sweep_package_cli_writes_report(tmp_path: Path) -> None:
    trace_path = _write_trace(tmp_path)
    config = _config(
        trace_path,
        mode=STREAMING_CAPACITY_SWEEP_MODE,
        output_dir=tmp_path / "streaming_cli_reports",
        capacities=[1, 4],
        cache_events=False,
    )
    config_path = tmp_path / "streaming_capacity_sweep.yaml"
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")

    paths = run_streaming_capacity_sweep(config_path)

    assert paths.capacity_sweep_path.is_file()
    assert paths.summary_path.is_file()
    rows = list(csv.DictReader(paths.capacity_sweep_path.open(encoding="utf-8")))
    assert rows
    assert {row["scope"] for row in rows} == {"trace", "instance"}
    assert {"hbm_capacity_blocks", "kv_hit_rate", "p90_ttft_ms", "ddr_hit_tokens"}.issubset(rows[0])
    assert {row["ddr_hit_tokens"] for row in rows} == {"0"}
    assert (tmp_path / "streaming_cli_reports" / "streaming_shards").is_dir()


def _write_trace(tmp_path: Path) -> Path:
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
    return trace_path


def _config(
    trace_path: Path,
    *,
    mode: str,
    output_dir: Path,
    capacities: list[int],
    cache_events: bool,
    cache_event_capacities: list[int] | None = None,
    instance_latency_profile_path: Path | None = None,
    model_registry_profile_path: Path | None = None,
) -> dict[str, object]:
    config: dict[str, object] = {
        "simulation": {"mode": mode},
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
            "hbm_capacity_blocks": capacities,
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
            "cache_events": cache_events,
            "cache_event_capacities": cache_event_capacities or [],
        },
    }
    if instance_latency_profile_path is not None:
        config["instance_latency"] = {
            "profile_path": str(instance_latency_profile_path),
            "require_all_trace_instances": True,
        }
    if model_registry_profile_path is not None:
        config["model_registry"] = {"profile_path": str(model_registry_profile_path)}
    return config


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


def _write_instance_latency_profile(
    tmp_path: Path,
    *,
    instance_slopes: dict[str, float],
) -> Path:
    latency_profiles = {
        f"{instance_uuid}-ttft": {
            "backend": "fitted_ttft",
            "model_name": "glm-v5",
            "hardware_name": f"hardware-{instance_uuid}",
            "fitted_ttft": {
                "profile": f"{instance_uuid}-ttft",
                "function": "token_linear_v1",
                "intercept_ms": 0.0,
                "ms_per_uncached_token": slope,
                "calibrated_from": "integration-test",
                "calibration_window_requests": 500,
            },
            "kv_load": {
                "ddr_ms_per_cached_token": 0.0,
                "remote_ms_per_cached_token": 0.0,
            },
        }
        for instance_uuid, slope in instance_slopes.items()
    }
    items = {
        instance_uuid: {
            "deployment": "glm-v5-shared-deployment",
            "latency_profile": f"{instance_uuid}-ttft",
        }
        for instance_uuid in instance_slopes
    }
    path = tmp_path / "instance_latency_profiles.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "instances": {
                    "name": "integration-instance-latency",
                    "latency_profiles": latency_profiles,
                    "items": items,
                }
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return path


def _write_mixed_instance_latency_profile(tmp_path: Path) -> Path:
    path = tmp_path / "mixed_instance_latency_profiles.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "instances": {
                    "name": "integration-mixed-instance-latency",
                    "latency_profiles": {
                        "instance-a-ttft": {
                            "backend": "fitted_ttft",
                            "model_name": "glm-v5.1",
                            "hardware_name": "hardware-instance-a",
                            "fitted_ttft": {
                                "profile": "instance-a-ttft",
                                "function": "token_linear_v1",
                                "intercept_ms": 0.0,
                                "ms_per_uncached_token": 1.0,
                                "calibrated_from": "integration-test",
                                "calibration_window_requests": 500,
                            },
                            "kv_load": {
                                "ddr_ms_per_cached_token": 0.0,
                                "remote_ms_per_cached_token": 0.0,
                            },
                        }
                    },
                    "items": {
                        "instance-a": {
                            "deployment": "glm-v5-shared-deployment",
                            "model_name": "glm-v5.1",
                            "latency_profile": "instance-a-ttft",
                        },
                        "instance-b": {
                            "deployment": "glm-v5-shared-deployment",
                            "model_name": "glm-v5.1",
                        },
                    },
                }
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return path


def _write_model_registry(tmp_path: Path, *, default_slope: float) -> Path:
    model_profile_path = tmp_path / "glm-v5.1.yaml"
    model_profile_path.write_text(
        yaml.safe_dump(
            {
                "model": {
                    "name": "glm-v5.1",
                    "aliases": ["glm-v5"],
                    "tokenizer_profile": "glm-v5",
                    "chat_template_profile": "glm-v5",
                    "cache_family": "full_attention",
                }
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    registry_path = tmp_path / "model_registry.yaml"
    registry_path.write_text(
        yaml.safe_dump(
            {
                "models": {
                    "glm-v5.1": {
                        "model_profile_path": str(model_profile_path),
                        "tokenizer_profile": "glm-v5",
                        "chat_template_profile": "glm-v5",
                        "default_latency": {
                            "backend": "fitted_ttft",
                            "model_name": "glm-v5.1",
                            "hardware_name": "registry-default-hardware",
                            "fitted_ttft": {
                                "profile": "glm-v5.1-default-ttft",
                                "function": "token_linear_v1",
                                "intercept_ms": 0.0,
                                "ms_per_uncached_token": default_slope,
                                "calibrated_from": "model-registry-default",
                                "calibration_window_requests": 500,
                            },
                            "kv_load": {
                                "ddr_ms_per_cached_token": 0.0,
                                "remote_ms_per_cached_token": 0.0,
                            },
                        },
                    }
                }
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return registry_path
