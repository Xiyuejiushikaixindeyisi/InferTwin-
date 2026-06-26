"""Company-internal AIConfigurator adapter boundary.

This module is a future integration contract for the company-internal
AIConfigurator, not the public GitHub project.  The open-source test/reference
checkout is named ``aiconfigurator_git`` and lives behind
``infertwin.external.aiconfigurator_git``.

Step1-Step6 do not invoke AIConfigurator from runner/report paths. Until a
concrete schema conversion is implemented, calls fail explicitly with
NotImplementedError.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from infertwin.external.base import ExternalToolRunner
from infertwin.latency.base import LatencyEstimate, PrefillEstimateInput


@dataclass(slots=True)
class AIConfiguratorAdapter:
    executable: Path
    working_dir: Path | None = None
    runner: ExternalToolRunner = field(default_factory=ExternalToolRunner)

    def estimate_prefill(self, request: PrefillEstimateInput) -> LatencyEstimate:
        raise NotImplementedError(
            "Map PrefillEstimateInput to the AIConfigurator CLI/API contract here."
        )
