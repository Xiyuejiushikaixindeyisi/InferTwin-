"""External project adapter primitives.

These helpers define process-execution boundaries for future adapters. They are
not wired into `ExperimentRunner` in Step1-Step5; current replay uses internal
latency backends only.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence


@dataclass(frozen=True, slots=True)
class ExternalCommand:
    executable: Path
    args: Sequence[str]
    working_dir: Path | None = None
    timeout_seconds: int = 300
    env: Mapping[str, str] | None = None


@dataclass(frozen=True, slots=True)
class ExternalCommandResult:
    returncode: int
    stdout: str
    stderr: str


class ExternalToolRunner:
    def run(self, command: ExternalCommand) -> ExternalCommandResult:
        completed = subprocess.run(
            [str(command.executable), *command.args],
            cwd=command.working_dir,
            env=dict(command.env) if command.env is not None else None,
            text=True,
            capture_output=True,
            timeout=command.timeout_seconds,
            check=False,
        )
        return ExternalCommandResult(
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )
