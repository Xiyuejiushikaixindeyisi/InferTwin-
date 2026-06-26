"""Open-source aiconfigurator_git reference boundary.

The public GitHub project is cloned locally as ``aiconfigurator_git`` for
testing and calibration experiments.  It is distinct from the company-internal
``AIConfigurator`` adapter in :mod:`infertwin.external.aiconfigurator`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class AiconfiguratorGitEstimateRequest:
    """Single-point estimate shape accepted by open-source aiconfigurator."""

    model_path: str
    system_name: str
    backend: str = "vllm"
    estimate_mode: str = "agg"
    batch_size: int = 1
    isl: int = 1
    osl: int = 1
    tp_size: int = 1
    pp_size: int = 1
    prefix_tokens: int = 0

    def __post_init__(self) -> None:
        if not self.model_path:
            raise ValueError("model_path must be non-empty")
        if not self.system_name:
            raise ValueError("system_name must be non-empty")
        if not self.backend:
            raise ValueError("backend must be non-empty")
        if not self.estimate_mode:
            raise ValueError("estimate_mode must be non-empty")
        positive_fields = {
            "batch_size": self.batch_size,
            "isl": self.isl,
            "osl": self.osl,
            "tp_size": self.tp_size,
            "pp_size": self.pp_size,
        }
        for name, value in positive_fields.items():
            if value <= 0:
                raise ValueError(f"{name} must be positive")
        if self.prefix_tokens < 0:
            raise ValueError("prefix_tokens must be non-negative")


@dataclass(frozen=True, slots=True)
class AiconfiguratorGitReference:
    """Validated local checkout and CLI argument builder for aiconfigurator_git."""

    repo_path: Path
    source_name: str = "aiconfigurator_git"
    package_name: str = "aiconfigurator"

    def validate_checkout(self) -> None:
        required_paths = (
            self.repo_path / "pyproject.toml",
            self.repo_path / "README.md",
            self.repo_path / "docs" / "cli_user_guide.md",
            self.repo_path / "src" / "aiconfigurator" / "cli" / "api.py",
        )
        missing = [str(path) for path in required_paths if not path.exists()]
        if missing:
            raise FileNotFoundError(
                "aiconfigurator_git checkout is incomplete; missing: " + ", ".join(missing)
            )

    def build_estimate_cli_args(
        self,
        request: AiconfiguratorGitEstimateRequest,
    ) -> tuple[str, ...]:
        """Build the public CLI shape without executing the external project."""

        return (
            "cli",
            "estimate",
            "--model-path",
            request.model_path,
            "--system",
            request.system_name,
            "--backend",
            request.backend,
            "--estimate-mode",
            request.estimate_mode,
            "--batch-size",
            str(request.batch_size),
            "--isl",
            str(request.isl),
            "--osl",
            str(request.osl),
            "--tp-size",
            str(request.tp_size),
            "--pp-size",
            str(request.pp_size),
            "--prefix",
            str(request.prefix_tokens),
        )
