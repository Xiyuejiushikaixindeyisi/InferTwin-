"""Ramulator2 adapter boundary.

This module is a future integration contract for KV restore latency, not a
production adapter. Step1-Step5 do not model HBM/DDR KV load latency or invoke
Ramulator2 from runner/report paths.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from infertwin.external.base import ExternalToolRunner
from infertwin.latency.base import KVRestoreEstimateInput, LatencyEstimate


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
