import csv
from pathlib import Path

from infertwin.experiment.runner import ExperimentRunner


def test_step5_hbm_lru_runner_writes_cache_events_and_summary(tmp_path: Path) -> None:
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
                    "different prompt",
                    "2026-06-05 09:01:24",
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    config = {
        "simulation": {"mode": "batch_aware_hbm_lru"},
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
            "hbm_capacity_blocks": 1,
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
        "output": {"directory": str(tmp_path / "reports")},
    }

    result = ExperimentRunner(config).run()

    assert result.metrics["phase"] == "batch_aware_hbm_lru"
    assert result.metrics["hbm_capacity_blocks"] == 1
    assert result.metrics["eviction_policy"] == "lru"
    assert result.metrics["cache_event_count"] > 0

    output_dir = tmp_path / "reports"
    request_metrics_path = output_dir / "request_metrics.csv"
    iteration_metrics_path = output_dir / "iteration_metrics.csv"
    cache_events_path = output_dir / "cache_events.csv"
    summary_path = output_dir / "summary.md"
    assert request_metrics_path.is_file()
    assert iteration_metrics_path.is_file()
    assert cache_events_path.is_file()
    assert summary_path.is_file()

    cache_event_rows = list(csv.DictReader(cache_events_path.open(encoding="utf-8")))
    assert cache_event_rows
    assert result.metrics["cache_event_count"] == len(cache_event_rows)
    assert {"event_type", "eviction_policy", "hbm_capacity_blocks"}.issubset(cache_event_rows[0])
    assert {row["eviction_policy"] for row in cache_event_rows} == {"lru"}
    assert {row["hbm_capacity_blocks"] for row in cache_event_rows} == {"1"}

    summary = summary_path.read_text(encoding="utf-8")
    assert "batch_aware_hbm_lru" in summary
    assert "hbm_capacity_blocks" in summary
    assert "eviction_policy" in summary
    assert "Cache events:" in summary
    assert f"- Cache events: {len(cache_event_rows)}" in summary
    assert "Evict events:" in summary


def test_runner_writes_rejected_requests_for_prompt_length_guard(tmp_path: Path) -> None:
    trace_path = tmp_path / "trace.csv"
    tokenizer_root = tmp_path / "tokenizers"
    _write_simple_tokenizer_profile(tokenizer_root, profile="simple-model")
    trace_path.write_text(
        "\n".join(
            [
                "request_id,tenant_id,instance_uuid,request_params,service_start_time",
                _row_with_model(
                    "00000000000000000000000000000001",
                    "instance-a",
                    "simple-model",
                    "short",
                    "2026-06-05 09:01:23",
                ),
                _row_with_model(
                    "00000000000000000000000000000002",
                    "instance-a",
                    "simple-model",
                    "too many prompt tokens",
                    "2026-06-05 09:01:24",
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    result = ExperimentRunner(
        {
            "simulation": {"mode": "batch_aware_hbm_lru"},
            "trace": {"path": str(trace_path)},
            "tokenizers": {
                "root": str(tokenizer_root),
                "default_profile": "simple-model",
                "max_prompt_tokens": 3,
            },
            "cache": {
                "block_size_tokens": 4,
                "policy": "hbm",
                "eviction_policy": "lru",
                "hbm_capacity_blocks": 8,
            },
            "scheduler": {
                "policy": "fcfs",
                "max_num_batched_tokens": 8192,
                "max_num_seqs": 32,
                "enable_chunked_prefill": True,
            },
            "latency": {
                "backend": "fitted_ttft",
                "model_name": "simple-model",
                "hardware_name": "local-dev",
                "fitted_ttft": {
                    "profile": "simple_default",
                    "function": "token_linear_v1",
                    "intercept_ms": 0.0,
                    "ms_per_uncached_token": 1.0,
                    "calibrated_from": "integration-test",
                },
            },
            "output": {"directory": str(tmp_path / "reports")},
        }
    ).run()

    assert result.metrics["request_build_accepted_count"] == 1
    assert result.metrics["request_build_rejected_count"] == 1
    rejected_path = tmp_path / "reports" / "rejected_requests.csv"
    assert result.metrics["rejected_requests_path"] == str(rejected_path)

    rows = list(csv.DictReader(rejected_path.open(encoding="utf-8")))
    assert len(rows) == 1
    assert rows[0]["request_id"] == "00000000000000000000000000000002"
    assert rows[0]["reason"] == "prompt_too_long"
    assert rows[0]["max_prompt_tokens"] == "3"


def _row(
    request_id: str,
    instance_uuid: str,
    prompt: str,
    timestamp: str,
) -> str:
    return _row_with_model(request_id, instance_uuid, "glm-v5", prompt, timestamp)


def _row_with_model(
    request_id: str,
    instance_uuid: str,
    model: str,
    prompt: str,
    timestamp: str,
) -> str:
    request_params = (
        "{"
        f'""model"":""{model}"",'
        '""messages"":[{""role"":""user"",""content"":""'
        f"{prompt}"
        '""}],'
        '""tools"":[]'
        "}"
    )
    return f'{request_id},tenant-a,{instance_uuid},"{request_params}",{timestamp}'


def _write_simple_tokenizer_profile(root: Path, *, profile: str) -> None:
    profile_dir = root / profile
    profile_dir.mkdir(parents=True)
    (profile_dir / "manifest.yaml").write_text(
        f"""
tokenizer:
  profile: {profile}
  type: simple
  include_tools: true
  model_aliases:
    - {profile}
""",
        encoding="utf-8",
    )
