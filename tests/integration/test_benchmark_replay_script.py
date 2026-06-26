import json
from pathlib import Path

from scripts.benchmark_replay import main


def test_benchmark_replay_script_writes_json_for_infinite_hbm(tmp_path: Path) -> None:
    output_path = tmp_path / "benchmark.json"

    assert (
        main(
            [
                "--requests",
                "16",
                "--instances",
                "2",
                "--prompt-tokens",
                "16",
                "--reuse-period",
                "4",
                "--output-json",
                str(output_path),
            ]
        )
        == 0
    )

    summary = json.loads(output_path.read_text(encoding="utf-8"))
    assert summary["request_count"] == 16
    assert summary["instance_count"] == 2
    assert summary["mode"] == "batch_aware_infinite_hbm"
    assert summary["iteration_count"] > 0
    assert "requests_per_second" in summary
    assert "effective_hit_rate" in summary


def test_benchmark_replay_script_runs_finite_hbm_mode(tmp_path: Path) -> None:
    output_path = tmp_path / "benchmark_hbm.json"

    assert (
        main(
            [
                "--requests",
                "16",
                "--instances",
                "2",
                "--prompt-tokens",
                "16",
                "--reuse-period",
                "4",
                "--mode",
                "batch_aware_hbm_lru",
                "--hbm-capacity-blocks",
                "8",
                "--cache-events",
                "memory",
                "--output-json",
                str(output_path),
            ]
        )
        == 0
    )

    summary = json.loads(output_path.read_text(encoding="utf-8"))
    assert summary["mode"] == "batch_aware_hbm_lru"
    assert summary["request_count"] == 16
    assert summary["cache_event_count"] > 0
