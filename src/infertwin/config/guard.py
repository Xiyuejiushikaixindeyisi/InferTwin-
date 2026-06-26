"""Config guard checks for unsupported core simulator semantics."""

from __future__ import annotations

from dataclasses import dataclass

from infertwin.config.profiles import DeploymentProfile, ModelProfile
from infertwin.config.run_spec import RunSpec


SEVERITY_ERROR = "error"
SEVERITY_WARNING = "warning"
SEVERITY_INFO = "info"


@dataclass(frozen=True, slots=True)
class ConfigGuardIssue:
    code: str
    severity: str
    blocked: bool
    affected_profile: str
    affected_field: str
    reason: str
    suggestion: str


@dataclass(frozen=True, slots=True)
class ConfigGuardResult:
    issues: tuple[ConfigGuardIssue, ...] = ()

    @property
    def blocked(self) -> bool:
        return any(issue.blocked for issue in self.issues)

    def raise_if_blocked(self) -> None:
        if not self.blocked:
            return
        details = "; ".join(f"{issue.code}: {issue.reason}" for issue in self.issues)
        raise ValueError(f"ConfigGuard blocked replay: {details}")


def guard_core_profiles(
    *,
    run_spec: RunSpec,
    model_profile: ModelProfile,
    deployment_profile: DeploymentProfile,
    block_conversion_enabled: bool = False,
) -> ConfigGuardResult:
    """Check profile combinations before entering core replay."""

    issues: list[ConfigGuardIssue] = []
    if run_spec.model_name not in model_profile.accepted_model_names:
        issues.append(
            _error(
                code="RUNSPEC_MODEL_PROFILE_MISMATCH",
                affected_profile=model_profile.name,
                affected_field="run.model_name",
                reason=(
                    f"run model {run_spec.model_name!r} is not model profile "
                    f"{model_profile.name!r} or one of its aliases"
                ),
                suggestion="Use the matching ModelProfile or add an explicit alias.",
            )
        )

    if (
        deployment_profile.speculative.enabled
        and deployment_profile.speculative.speculative_drop_blocks > 0
        and not block_conversion_enabled
    ):
        issues.append(
            _error(
                code="SPECULATIVE_DROP_REQUIRES_BLOCK_CONVERSION",
                affected_profile=deployment_profile.name,
                affected_field="deployment.speculative.speculative_drop_blocks",
                reason="speculative decoding changes cached block accounting",
                suggestion="Enable the cache block conversion module before replay.",
            )
        )

    if deployment_profile.parallel.context_parallel_factor > 1 and model_profile.cache_family in {
        "sliding_window",
        "mamba",
        "hybrid",
    }:
        issues.append(
            _error(
                code="UNSUPPORTED_CONTEXT_PARALLEL_CACHE_FAMILY",
                affected_profile=deployment_profile.name,
                affected_field="deployment.parallel",
                reason=(
                    f"context parallelism is not supported for cache_family "
                    f"{model_profile.cache_family!r}"
                ),
                suggestion="Use PCP=DCP=1 or add a dedicated cache manager.",
            )
        )

    if model_profile.cache_family == "hybrid" and not model_profile.cache_groups:
        issues.append(
            _error(
                code="HYBRID_CACHE_GROUPS_REQUIRED",
                affected_profile=model_profile.name,
                affected_field="model.cache_groups",
                reason="hybrid cache profiles require explicit cache group block sizes",
                suggestion="Declare model.cache_groups before enabling hybrid cache replay.",
            )
        )

    return ConfigGuardResult(tuple(issues))


def guard_request_model(
    *,
    request_model: str,
    run_spec: RunSpec,
    model_profile: ModelProfile,
) -> ConfigGuardResult:
    """Check one trace request model against the selected RunSpec model."""

    if request_model in model_profile.accepted_model_names and (
        run_spec.model_name in model_profile.accepted_model_names
    ):
        return ConfigGuardResult()
    return ConfigGuardResult(
        (
            _error(
                code="REQUEST_MODEL_MISMATCH",
                affected_profile=model_profile.name,
                affected_field="request_params.model",
                reason=(
                    f"request model {request_model!r} does not match run model "
                    f"{run_spec.model_name!r} or aliases"
                ),
                suggestion="Use a single-model trace or add an explicit ModelProfile alias.",
            ),
        )
    )


def _error(
    *,
    code: str,
    affected_profile: str,
    affected_field: str,
    reason: str,
    suggestion: str,
) -> ConfigGuardIssue:
    return ConfigGuardIssue(
        code=code,
        severity=SEVERITY_ERROR,
        blocked=True,
        affected_profile=affected_profile,
        affected_field=affected_field,
        reason=reason,
        suggestion=suggestion,
    )
