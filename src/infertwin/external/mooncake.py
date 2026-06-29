"""Mooncake calibration boundary.

InferTwin does not import or execute Mooncake in the default replay path. This
module only records source metadata for opt-in KV-load calibration experiments.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MooncakeCalibrationReference:
    """Metadata for Mooncake benchmark or measurement based calibration."""

    source_name: str = "mooncake_benchmark"
    protocol: str = "unknown"
    transfer_path: str = "mooncake"

    def __post_init__(self) -> None:
        if not self.source_name:
            raise ValueError("source_name must be non-empty")
        if not self.protocol:
            raise ValueError("protocol must be non-empty")
        if not self.transfer_path:
            raise ValueError("transfer_path must be non-empty")

    def calibrated_from(self, run_id: str) -> str:
        if not run_id:
            raise ValueError("run_id must be non-empty")
        return f"{self.source_name}:{run_id}"
