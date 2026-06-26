from pathlib import Path

from hitfloor.experiment.runner import ExperimentRunner


def test_phase1_runner_writes_request_metrics_and_summary(tmp_path: Path) -> None:
    config = {
        "trace": {"path": "data/samples/sample_trace.csv"},
        "tokenizers": {
            "root": "tokenizers",
            "default_profile": "glm-v5",
            "cache_scope": "tenant_isolated",
        },
        "cache": {"block_size_tokens": 4},
        "output": {"directory": str(tmp_path)},
    }

    result = ExperimentRunner(config).run()

    assert result.metrics["phase"] == "infinite_hbm"
    assert (tmp_path / "request_metrics.csv").is_file()
    assert (tmp_path / "summary.md").is_file()
