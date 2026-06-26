"""RunSpec schema for one InferTwin simulation run."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class RunSpec:
    """Top-level run request consumed by core simulator orchestration."""

    trace_path: Path
    output_dir: Path
    mode: str
    model_name: str
    requested_block_size: int
    capacity_candidates: tuple[int, ...] = ()
    model_profile: Path | None = None
    hardware_profile: Path | None = None
    deployment_profile: Path | None = None
    instance_profile: Path | None = None

    @classmethod
    def from_mapping(cls, data: object) -> "RunSpec":
        mapping = _section(data, "run")
        return cls(
            trace_path=Path(_required_str(mapping, "trace_path", field_name="run")),
            output_dir=Path(_required_str(mapping, "output_dir", field_name="run")),
            mode=_required_str(mapping, "mode", field_name="run"),
            model_name=_required_str(mapping, "model_name", field_name="run"),
            requested_block_size=_positive_int(
                mapping.get("requested_block_size"),
                field_name="run.requested_block_size",
            ),
            capacity_candidates=_positive_int_tuple(
                mapping.get("capacity_candidates", ()),
                field_name="run.capacity_candidates",
            ),
            model_profile=_optional_path(
                mapping.get("model_profile"), field_name="run.model_profile"
            ),
            hardware_profile=_optional_path(
                mapping.get("hardware_profile"),
                field_name="run.hardware_profile",
            ),
            deployment_profile=_optional_path(
                mapping.get("deployment_profile"),
                field_name="run.deployment_profile",
            ),
            instance_profile=_optional_path(
                mapping.get("instance_profile"),
                field_name="run.instance_profile",
            ),
        )


def _section(data: object, section_name: str) -> dict[str, Any]:
    mapping = _mapping(data, section_name)
    if section_name in mapping:
        return _mapping(mapping[section_name], section_name)
    return mapping


def _mapping(data: object, field_name: str) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError(f"{field_name} must be a mapping")
    return data


def _required_str(mapping: dict[str, Any], key: str, *, field_name: str) -> str:
    if key not in mapping:
        raise ValueError(f"{field_name}.{key} is required")
    value = mapping[key]
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name}.{key} must be a non-empty string")
    return value


def _positive_int(value: object, *, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{field_name} must be a positive integer")
    return value


def _positive_int_tuple(value: object, *, field_name: str) -> tuple[int, ...]:
    if value is None:
        return ()
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        raise ValueError(f"{field_name} must be a sequence of positive integers")
    return tuple(_positive_int(item, field_name=f"{field_name}[]") for item in value)


def _optional_path(value: object, *, field_name: str) -> Path | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return Path(value)
