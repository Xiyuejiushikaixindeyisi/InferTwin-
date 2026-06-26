"""Build simulation requests from documented experiment config."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from infertwin.config.guard import guard_core_profiles
from infertwin.config.profiles import DeploymentProfile, ModelProfile
from infertwin.config.run_spec import RunSpec
from infertwin.config.validation import load_deployment_profile, load_model_profile
from infertwin.instance.request import SimulationRequest, build_simulation_request
from infertwin.request.build_context import RequestBuildContext
from infertwin.request.tokenizer_registry import PromptTooLongError, TokenizerRegistry
from infertwin.trace.schema import TraceRecord
from infertwin.trace.reader import read_trace_csv


@dataclass(frozen=True, slots=True)
class RejectedTraceRecord:
    request_id: str
    tenant_id: str
    instance_uuid: str
    reason: str
    detail: str
    prompt_tokens: int | None = None
    max_prompt_tokens: int | None = None
    tokenizer_profile: str | None = None


@dataclass(frozen=True, slots=True)
class RequestBuildResult:
    requests: tuple[SimulationRequest, ...]
    rejected_records: tuple[RejectedTraceRecord, ...]

    @property
    def accepted_count(self) -> int:
        return len(self.requests)

    @property
    def rejected_count(self) -> int:
        return len(self.rejected_records)


@dataclass(frozen=True, slots=True)
class RequestBuildSettings:
    """Reusable request-build dependencies parsed from experiment config."""

    trace_path: Path
    tokenizer_registry: TokenizerRegistry
    block_size_tokens: int
    cache_scope: str
    build_context: RequestBuildContext


def build_requests_from_config(config: Mapping[str, Any]) -> list[SimulationRequest]:
    """Parse trace rows and build immutable simulation requests once."""

    return list(build_request_build_result_from_config(config).requests)


def build_request_build_result_from_config(config: Mapping[str, Any]) -> RequestBuildResult:
    """Build simulation requests and explicit request-build rejection records."""

    settings = build_request_build_settings_from_config(config)
    return _build_requests_from_records(
        read_trace_csv(settings.trace_path),
        tokenizer_registry=settings.tokenizer_registry,
        block_size_tokens=settings.block_size_tokens,
        cache_scope=settings.cache_scope,
        build_context=settings.build_context,
    )


def build_request_build_settings_from_config(config: Mapping[str, Any]) -> RequestBuildSettings:
    """Parse config into reusable request-build dependencies."""

    run_spec = _optional_run_spec(config)
    trace_path = _trace_path(config, run_spec=run_spec)

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
    if run_spec is not None:
        block_size_tokens = run_spec.requested_block_size

    max_prompt_tokens = _optional_positive_int(tokenizer_config, "max_prompt_tokens")
    build_context = _build_request_context(
        run_spec=run_spec,
        block_size_tokens=block_size_tokens,
        max_prompt_tokens=max_prompt_tokens,
    )
    default_profile = _default_tokenizer_profile(tokenizer_config, build_context)
    registry = TokenizerRegistry.from_root(
        tokenizer_root,
        default_profile=default_profile,
    )
    return RequestBuildSettings(
        trace_path=trace_path,
        tokenizer_registry=registry,
        block_size_tokens=block_size_tokens,
        cache_scope=cache_scope,
        build_context=build_context,
    )


def _optional_run_spec(config: Mapping[str, Any]) -> RunSpec | None:
    if "run" not in config:
        return None
    return RunSpec.from_mapping({"run": config["run"]})


def _trace_path(config: Mapping[str, Any], *, run_spec: RunSpec | None) -> Path:
    if run_spec is not None:
        return run_spec.trace_path
    trace_config = _mapping(config, "trace")
    return Path(_required_str(trace_config, "path"))


def _build_request_context(
    *,
    run_spec: RunSpec | None,
    block_size_tokens: int,
    max_prompt_tokens: int | None,
) -> RequestBuildContext:
    if run_spec is None:
        return RequestBuildContext.legacy(
            block_size_tokens,
            max_prompt_tokens=max_prompt_tokens,
        )

    model_profile = _load_required_model_profile(run_spec)
    deployment_profile = _load_required_deployment_profile(run_spec)
    guard_core_profiles(
        run_spec=run_spec,
        model_profile=model_profile,
        deployment_profile=deployment_profile,
        block_conversion_enabled=True,
    ).raise_if_blocked()
    return RequestBuildContext.from_profiles(
        run_spec=run_spec,
        model_profile=model_profile,
        deployment_profile=deployment_profile,
        max_prompt_tokens=max_prompt_tokens,
    )


def _build_requests_from_records(
    records,
    *,
    tokenizer_registry: TokenizerRegistry,
    block_size_tokens: int,
    cache_scope: str,
    build_context: RequestBuildContext,
) -> RequestBuildResult:
    requests: list[SimulationRequest] = []
    rejected_records: list[RejectedTraceRecord] = []

    for record in records:
        try:
            requests.append(
                build_simulation_request(
                    record,
                    tokenizer_registry=tokenizer_registry,
                    block_size_tokens=block_size_tokens,
                    cache_scope=cache_scope,
                    build_context=build_context,
                )
            )
        except PromptTooLongError as exc:
            rejected_records.append(_rejected_prompt_too_long(record, exc))

    return RequestBuildResult(
        requests=tuple(
            sorted(
                requests,
                key=lambda request: (
                    request.service_start_time,
                    request.instance_uuid,
                    request.request_id,
                ),
            )
        ),
        rejected_records=tuple(rejected_records),
    )


def _rejected_prompt_too_long(
    record: TraceRecord,
    exc: PromptTooLongError,
) -> RejectedTraceRecord:
    return build_prompt_too_long_rejection(record, exc)


def build_prompt_too_long_rejection(
    record: TraceRecord,
    exc: PromptTooLongError,
) -> RejectedTraceRecord:
    """Build a stable rejection record for tokenizer-stage prompt length guard."""

    return RejectedTraceRecord(
        request_id=record.request_id,
        tenant_id=record.tenant_id,
        instance_uuid=record.instance_uuid,
        reason="prompt_too_long",
        detail=str(exc),
        prompt_tokens=exc.prompt_tokens,
        max_prompt_tokens=exc.max_prompt_tokens,
        tokenizer_profile=exc.tokenizer_profile,
    )


def _load_required_model_profile(run_spec: RunSpec) -> ModelProfile:
    if run_spec.model_profile is None:
        raise ValueError("run.model_profile is required for profile-aware request build")
    return load_model_profile(run_spec.model_profile)


def _load_required_deployment_profile(run_spec: RunSpec) -> DeploymentProfile:
    if run_spec.deployment_profile is None:
        raise ValueError("run.deployment_profile is required for profile-aware request build")
    return load_deployment_profile(run_spec.deployment_profile)


def _default_tokenizer_profile(
    tokenizer_config: Mapping[str, Any],
    build_context: RequestBuildContext,
) -> str | None:
    configured_default = _optional_nullable_str(tokenizer_config, "default_profile")
    if configured_default is not None:
        return configured_default
    return build_context.tokenizer_profile


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


def _optional_positive_int(config: Mapping[str, Any], key: str) -> int | None:
    value = config.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{key} must be a positive integer when provided")
    return value
