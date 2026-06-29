"""MKsim adapter boundary.

This module is a future integration contract, not a production adapter. Default
runner/report paths do not invoke MKSim. Until a concrete schema conversion is
implemented, calls fail explicitly with NotImplementedError.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from infertwin.external.base import ExternalToolRunner
from infertwin.latency.base import LatencyEstimate, PrefillEstimateInput


@dataclass(slots=True)
class MKSimAdapter:
    executable: Path
    working_dir: Path | None = None
    runner: ExternalToolRunner = field(default_factory=ExternalToolRunner)

    def estimate_prefill(self, request: PrefillEstimateInput) -> LatencyEstimate:
        raise NotImplementedError("Map PrefillEstimateInput to the MKsim contract here.")
