import json
from pathlib import Path

from scripts.benchmark_streaming_replay import main


def test_benchmark_streaming_replay_script_writes_json_and_reports(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "streaming_benchmark"
    output_path = tmp_path / "streaming_benchmark.json"

    assert (
        main(
            [
                "--requests",
                "8",
                "--instances",
                "2",
                "--prompt-words",
                "4",
                "--reuse-period",
                "2",
                "--capacities",
                "1,4",
                "--cache-event-capacities",
                "1",
                "--output-dir",
                str(output_dir),
                "--output-json",
                str(output_path),
            ]
        )
        == 0
    )

    summary = json.loads(output_path.read_text(encoding="utf-8"))
    assert summary["request_count"] == 8
    assert summary["accepted_request_count"] == 8
    assert summary["replayed_request_count"] == 16
    assert summary["iteration_count"] > 0
    assert summary["cache_event_count"] > 0
    assert summary["requests_per_second"] > 0.0
    assert summary["iterations_per_second"] > 0.0
    assert summary["cache_events_per_second"] > 0.0
    assert summary["peak_traced_memory_mb"] > 0.0
    assert (output_dir / "capacity_sweep.csv").is_file()
    assert (output_dir / "summary.md").is_file()
    assert (output_dir / "capacity_1" / "cache_events.csv").is_file()
    assert not (output_dir / "capacity_4" / "cache_events.csv").exists()
