"""Ramulator2 adapter and calibration boundary.

This module is a future integration contract for KV restore latency, not a
production adapter. Default runner/report paths do not invoke Ramulator2.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from infertwin.external.base import ExternalToolRunner
from infertwin.latency.base import KVRestoreEstimateInput, LatencyEstimate


@dataclass(frozen=True, slots=True)
class Ramulator2CalibrationReference:
    """Validated local Ramulator2 checkout metadata for opt-in calibration."""

    repo_path: Path
    executable: Path
    source_name: str = "ramulator2_git"

    def __post_init__(self) -> None:
        if not self.source_name:
            raise ValueError("source_name must be non-empty")

    @property
    def resolved_executable(self) -> Path:
        if self.executable.is_absolute():
            return self.executable
        return self.repo_path / self.executable

    def validate_checkout(self) -> None:
        required_paths = (
            self.repo_path / "README.md",
            self.repo_path / "CMakeLists.txt",
            self.repo_path / "src",
            self.repo_path / "perf_comparison",
            self.resolved_executable,
        )
        missing = [str(path) for path in required_paths if not path.exists()]
        if missing:
            raise FileNotFoundError(
                "ramulator2_git checkout is incomplete; missing: " + ", ".join(missing)
            )

    def calibrated_from(self, run_id: str) -> str:
        if not run_id:
            raise ValueError("run_id must be non-empty")
        return f"{self.source_name}:{run_id}"


@dataclass(slots=True)
class Ramulator2Adapter:
    executable: Path
    working_dir: Path | None = None
    config_template: Path | None = None
    runner: ExternalToolRunner = field(default_factory=ExternalToolRunner)

    def estimate_kv_restore(self, request: KVRestoreEstimateInput) -> LatencyEstimate:
        raise NotImplementedError(
            "Map KVRestoreEstimateInput to the Ramulator2 config and parse output here."
        )
