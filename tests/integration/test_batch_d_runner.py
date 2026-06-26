import csv
from pathlib import Path

from hitfloor.experiment.runner import ExperimentRunner


def test_batch_d_runner_writes_batch_aware_reports(tmp_path: Path) -> None:
    config = {
        "simulation": {"mode": "batch_aware_infinite_hbm"},
        "trace": {"path": "data/samples/sample_trace.csv"},
        "tokenizers": {
            "root": "tokenizers",
            "default_profile": "glm-v5",
            "cache_scope": "tenant_isolated",
        },
        "cache": {"block_size_tokens": 4},
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
        "output": {"directory": str(tmp_path)},
    }

    result = ExperimentRunner(config).run()

    assert result.metrics["phase"] == "batch_aware_infinite_hbm"
    request_metrics_path = tmp_path / "request_metrics.csv"
    iteration_metrics_path = tmp_path / "iteration_metrics.csv"
    summary_path = tmp_path / "summary.md"
    assert request_metrics_path.is_file()
    assert iteration_metrics_path.is_file()
    assert summary_path.is_file()

    request_rows = list(csv.DictReader(request_metrics_path.open(encoding="utf-8")))
    iteration_rows = list(csv.DictReader(iteration_metrics_path.open(encoding="utf-8")))
    assert request_rows
    assert iteration_rows
    assert "ttft_ms" in request_rows[0]
    assert iteration_rows[0]["backend"] == "fitted_ttft"
    assert "shape_key" in iteration_rows[0]

    summary = summary_path.read_text(encoding="utf-8")
    assert "fitted_ttft" in summary
    assert "token_linear_v1" in summary
    assert "HBM / DDR KV load time is not modeled" in summary
