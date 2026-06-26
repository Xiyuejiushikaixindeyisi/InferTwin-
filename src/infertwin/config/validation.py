"""Profile loading helpers for the engineering-optimization config schema."""

from __future__ import annotations

from pathlib import Path

from infertwin.config.loader import load_yaml
from infertwin.config.model_registry import ModelRegistry
from infertwin.config.profiles import (
    DeploymentProfile,
    HardwareProfile,
    InstanceProfile,
    ModelProfile,
)
from infertwin.config.run_spec import RunSpec


def load_run_spec(path: str | Path) -> RunSpec:
    return RunSpec.from_mapping(load_yaml(path))


def load_model_profile(path: str | Path) -> ModelProfile:
    return ModelProfile.from_mapping(load_yaml(path))


def load_model_registry(path: str | Path) -> ModelRegistry:
    return ModelRegistry.from_mapping(load_yaml(path))


def load_hardware_profile(path: str | Path) -> HardwareProfile:
    return HardwareProfile.from_mapping(load_yaml(path))


def load_deployment_profile(path: str | Path) -> DeploymentProfile:
    return DeploymentProfile.from_mapping(load_yaml(path))


def load_instance_profile(path: str | Path) -> InstanceProfile:
    return InstanceProfile.from_mapping(load_yaml(path))
