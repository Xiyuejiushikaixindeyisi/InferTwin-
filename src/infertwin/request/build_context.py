"""Request build context for legacy and profile-aware request construction."""

from __future__ import annotations

from dataclasses import dataclass, field

from infertwin.cache.block_size import BlockSizeInput, BlockSizeResolution, BlockSizeResolver
from infertwin.cache.cache_block_conversion import (
    CacheBlockConversionInput,
    CacheBlockConversionPolicy,
    CacheBlockConversionResult,
)
from infertwin.config.guard import guard_request_model
from infertwin.config.profiles import DeploymentProfile, ModelProfile
from infertwin.config.run_spec import RunSpec


@dataclass(frozen=True, slots=True)
class RequestBuildContext:
    """Context shared by all request builds in one simulation run."""

    run_spec: RunSpec | None
    model_profile: ModelProfile | None
    deployment_profile: DeploymentProfile | None
    block_size_resolution: BlockSizeResolution
    max_prompt_tokens: int | None = None
    conversion_policy: CacheBlockConversionPolicy = field(
        default_factory=CacheBlockConversionPolicy
    )

    @classmethod
    def legacy(
        cls,
        block_size_tokens: int,
        *,
        max_prompt_tokens: int | None = None,
    ) -> "RequestBuildContext":
        resolution = BlockSizeResolver().resolve(
            BlockSizeInput(requested_block_size=block_size_tokens)
        )
        return cls(
            run_spec=None,
            model_profile=None,
            deployment_profile=None,
            block_size_resolution=resolution,
            max_prompt_tokens=max_prompt_tokens,
        )

    @classmethod
    def from_profiles(
        cls,
        *,
        run_spec: RunSpec,
        model_profile: ModelProfile,
        deployment_profile: DeploymentProfile,
        max_prompt_tokens: int | None = None,
    ) -> "RequestBuildContext":
        resolution = BlockSizeResolver().resolve(
            BlockSizeInput(
                requested_block_size=run_spec.requested_block_size,
                runtime_block_size=deployment_profile.cache_features.runtime_block_size,
                prefill_context_parallel_size=(
                    deployment_profile.parallel.prefill_context_parallel_size
                ),
                decode_context_parallel_size=(
                    deployment_profile.parallel.decode_context_parallel_size
                ),
                cache_family=model_profile.cache_family,
                hybrid_group_block_sizes=tuple(
                    group.block_size for group in model_profile.cache_groups
                ),
            )
        )
        if not resolution.supported:
            raise ValueError(
                f"BlockSizeResolver rejected profile combination: {resolution.unsupported_reason}"
            )
        return cls(
            run_spec=run_spec,
            model_profile=model_profile,
            deployment_profile=deployment_profile,
            block_size_resolution=resolution,
            max_prompt_tokens=_resolve_max_prompt_tokens(
                explicit_max_prompt_tokens=max_prompt_tokens,
                model_profile=model_profile,
                deployment_profile=deployment_profile,
            ),
        )

    @property
    def requested_block_size(self) -> int:
        return self.block_size_resolution.requested_block_size

    @property
    def runtime_block_size(self) -> int:
        return self.block_size_resolution.runtime_block_size

    @property
    def effective_block_size(self) -> int:
        return self.block_size_resolution.effective_block_size

    @property
    def speculative_drop_blocks(self) -> int:
        if self.deployment_profile is None:
            return 0
        return self.deployment_profile.speculative.speculative_drop_blocks

    @property
    def tokenizer_profile(self) -> str | None:
        if self.model_profile is None:
            return None
        return self.model_profile.tokenizer_profile

    def validate_request_model(self, request_model: str) -> None:
        if self.run_spec is None or self.model_profile is None:
            return
        guard_request_model(
            request_model=request_model,
            run_spec=self.run_spec,
            model_profile=self.model_profile,
        ).raise_if_blocked()

    def calculate_block_conversion(self, prompt_tokens: int) -> CacheBlockConversionResult:
        result = self.conversion_policy.calculate(
            CacheBlockConversionInput(
                prompt_tokens=prompt_tokens,
                block_size=self.block_size_resolution,
                speculative_drop_blocks=self.speculative_drop_blocks,
            )
        )
        if not result.supported:
            raise ValueError(
                f"Cache block conversion rejected profile combination: {result.unsupported_reason}"
            )
        return result


def _resolve_max_prompt_tokens(
    *,
    explicit_max_prompt_tokens: int | None,
    model_profile: ModelProfile,
    deployment_profile: DeploymentProfile,
) -> int | None:
    limits = [
        explicit_max_prompt_tokens,
        model_profile.max_model_len,
        _deployment_max_model_len(deployment_profile),
    ]
    resolved = [limit for limit in limits if limit is not None]
    if not resolved:
        return None
    return min(resolved)


def _deployment_max_model_len(deployment_profile: DeploymentProfile) -> int | None:
    for key in ("max_model_len", "max-model-len", "max_model_lens", "max-model-lens"):
        value = deployment_profile.startup_args.get(key)
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"deployment.startup_args.{key} must be a positive integer")
        return value
    return None
