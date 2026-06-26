"""Build simulation requests from documented experiment config."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from hitfloor.instance.request import SimulationRequest, build_simulation_requests
from hitfloor.request.tokenizer_registry import TokenizerRegistry
from hitfloor.trace.reader import read_trace_csv


def build_requests_from_config(config: Mapping[str, Any]) -> list[SimulationRequest]:
    """Parse trace rows and build immutable simulation requests once."""

    trace_config = _mapping(config, "trace")
    trace_path = Path(_required_str(trace_config, "path"))

    tokenizer_config = config.get("tokenizers", {})
    if tokenizer_config is None:
        tokenizer_config = {}
    if not isinstance(tokenizer_config, Mapping):
        raise ValueError("tokenizers config must be a mapping")

    cache_config = config.get("cache", {})
    if cache_config is None:
        cache_config = {}
    if not isinstance(cache_config, Mapping):
        raise ValueError("cache config must be a mapping")

    tokenizer_root = _optional_str(tokenizer_config, "root", default="tokenizers")
    default_profile = _optional_nullable_str(tokenizer_config, "default_profile")
    cache_scope = _optional_str(tokenizer_config, "cache_scope", default="tenant_isolated")
    block_size_tokens = _optional_int(cache_config, "block_size_tokens", default=16)
    if block_size_tokens <= 0:
        raise ValueError("block_size_tokens must be a positive integer")

    records = list(read_trace_csv(trace_path))
    registry = TokenizerRegistry.from_root(
        tokenizer_root,
        default_profile=default_profile,
    )
    return build_simulation_requests(
        records,
        tokenizer_registry=registry,
        block_size_tokens=block_size_tokens,
        cache_scope=cache_scope,
    )


def _mapping(parent: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = parent.get(key)
    if not isinstance(value, Mapping):
        raise ValueError(f"{key} config must be a mapping")
    return value


def _required_str(config: Mapping[str, Any], key: str) -> str:
    value = config.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be a non-empty string")
    return value


def _optional_str(config: Mapping[str, Any], key: str, *, default: str) -> str:
    value = config.get(key, default)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be a non-empty string")
    return value


def _optional_nullable_str(config: Mapping[str, Any], key: str) -> str | None:
    value = config.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be a non-empty string when provided")
    return value


def _optional_int(config: Mapping[str, Any], key: str, *, default: int) -> int:
    value = config.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key} must be an integer")
    return value
