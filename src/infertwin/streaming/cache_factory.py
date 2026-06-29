"""Streaming replay cache backend factory."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from infertwin.cache.base import PrefixCache
from infertwin.cache.ddr_lru import DDRLRUCache
from infertwin.cache.eviction import LRUEvictor
from infertwin.cache.hbm_lru import HBMCache
from infertwin.cache.tiered import TieredPrefixCache
from infertwin.config.model_runtime import ModelCacheDefaults
from infertwin.replay.timeline import (
    CHUNK_TTFT_GRANULARITY,
    ITERATION_TTFT_GRANULARITY,
    LEGACY_TIMELINE_MODE,
    PROGRESSIVE_TIMELINE_MODE,
)

CACHE_MODE_HBM_LRU = "batch_aware_hbm_lru"
CACHE_MODE_HBM_DDR_LRU = "batch_aware_hbm_ddr_lru"
CACHE_MODE_HBM_DDR_LRU_PROGRESSIVE_TIMELINE = PROGRESSIVE_TIMELINE_MODE
CACHE_MODES = (
    CACHE_MODE_HBM_LRU,
    CACHE_MODE_HBM_DDR_LRU,
    CACHE_MODE_HBM_DDR_LRU_PROGRESSIVE_TIMELINE,
)


@dataclass(frozen=True, slots=True)
class StreamingCacheFactoryConfig:
    """Cache backend selection for streaming replay."""

    mode: str
    eviction_policy: str


def build_streaming_cache_factory_config(
    config: Mapping[str, Any],
) -> StreamingCacheFactoryConfig:
    """Validate and normalize streaming replay cache mode."""

    cache_config = config.get("cache", {})
    if cache_config is None:
        cache_config = {}
    if not isinstance(cache_config, Mapping):
        raise ValueError("cache config must be a mapping")

    eviction_policy = _optional_str(
        cache_config,
        "eviction_policy",
        default="lru",
        field_name="cache.eviction_policy",
    )
    if eviction_policy != "lru":
        raise ValueError("streaming replay only supports cache.eviction_policy: lru")

    mode = _optional_str(
        cache_config,
        "mode",
        default="",
        field_name="cache.mode",
    )
    legacy_policy = _optional_str(
        cache_config,
        "policy",
        default="hbm",
        field_name="cache.policy",
    )
    if legacy_policy != "hbm":
        raise ValueError("streaming replay only supports legacy cache.policy: hbm")
    if not mode:
        mode = CACHE_MODE_HBM_LRU
    if mode not in CACHE_MODES:
        raise ValueError(f"streaming replay cache.mode must be one of: {', '.join(CACHE_MODES)}")

    return StreamingCacheFactoryConfig(mode=mode, eviction_policy=eviction_policy)


def build_streaming_prefix_cache(
    *,
    capacity: int,
    instance_uuid: str,
    cache_defaults: ModelCacheDefaults | None,
    config: StreamingCacheFactoryConfig,
) -> PrefixCache:
    """Build one isolated cache backend for one streaming shard replay."""

    _require_lru(config.eviction_policy, field_name="cache.eviction_policy")
    if cache_defaults is not None:
        _require_lru(
            cache_defaults.eviction_policy,
            field_name=f"model default cache for {instance_uuid!r}",
        )

    if config.mode == CACHE_MODE_HBM_LRU:
        return HBMCache(capacity_blocks=capacity, evictor=LRUEvictor())
    if config.mode in (
        CACHE_MODE_HBM_DDR_LRU,
        CACHE_MODE_HBM_DDR_LRU_PROGRESSIVE_TIMELINE,
    ):
        _require_ddr_defaults(instance_uuid=instance_uuid, cache_defaults=cache_defaults)
        ddr_capacity_blocks = cache_defaults.ddr_capacity_blocks
        if ddr_capacity_blocks is None:
            raise ValueError(
                "cache.mode=batch_aware_hbm_ddr_lru requires "
                f"default_cache.ddr_capacity_blocks for instance {instance_uuid!r}"
            )
        return TieredPrefixCache(
            hbm=HBMCache(capacity_blocks=capacity, evictor=LRUEvictor()),
            ddr=DDRLRUCache(
                capacity_blocks=ddr_capacity_blocks,
                evictor=LRUEvictor(),
            ),
        )

    raise ValueError(f"unsupported streaming cache mode {config.mode!r}")


def timeline_mode_for_cache_mode(cache_mode: str) -> str:
    """Return the replay timeline mode implied by a streaming cache mode."""

    if cache_mode == CACHE_MODE_HBM_DDR_LRU_PROGRESSIVE_TIMELINE:
        return PROGRESSIVE_TIMELINE_MODE
    if cache_mode in (CACHE_MODE_HBM_LRU, CACHE_MODE_HBM_DDR_LRU):
        return LEGACY_TIMELINE_MODE
    raise ValueError(f"unsupported streaming cache mode {cache_mode!r}")


def ttft_granularity_for_timeline_mode(timeline_mode: str) -> str:
    """Return the TTFT granularity associated with a replay timeline mode."""

    if timeline_mode == PROGRESSIVE_TIMELINE_MODE:
        return CHUNK_TTFT_GRANULARITY
    if timeline_mode == LEGACY_TIMELINE_MODE:
        return ITERATION_TTFT_GRANULARITY
    raise ValueError(f"unsupported timeline_mode {timeline_mode!r}")


def _require_ddr_defaults(
    *,
    instance_uuid: str,
    cache_defaults: ModelCacheDefaults | None,
) -> None:
    if cache_defaults is None:
        raise ValueError(
            "cache.mode=batch_aware_hbm_ddr_lru requires model registry and "
            f"instance runtime defaults for instance {instance_uuid!r}"
        )

    pooling = cache_defaults.pooling
    if not pooling.enabled:
        raise ValueError(
            "cache.mode=batch_aware_hbm_ddr_lru requires "
            f"default_cache.pooling.enabled=true for instance {instance_uuid!r}"
        )
    if not pooling.single_instance:
        raise ValueError(
            "cache.mode=batch_aware_hbm_ddr_lru only supports "
            f"single_instance pooling for instance {instance_uuid!r}"
        )
    if pooling.multi_instance:
        raise ValueError(
            "cache.mode=batch_aware_hbm_ddr_lru does not support "
            f"multi_instance pooling for instance {instance_uuid!r}"
        )
    if not pooling.ddr_enabled:
        raise ValueError(
            "cache.mode=batch_aware_hbm_ddr_lru requires "
            f"default_cache.pooling.ddr_enabled=true for instance {instance_uuid!r}"
        )
    if pooling.remote_enabled:
        raise ValueError(
            "cache.mode=batch_aware_hbm_ddr_lru does not support "
            f"remote pooling for instance {instance_uuid!r}"
        )
    if pooling.ssd_enabled:
        raise ValueError(
            "cache.mode=batch_aware_hbm_ddr_lru does not support "
            f"SSD pooling for instance {instance_uuid!r}"
        )
    if cache_defaults.ddr_capacity_blocks is None:
        raise ValueError(
            "cache.mode=batch_aware_hbm_ddr_lru requires "
            f"default_cache.ddr_capacity_blocks for instance {instance_uuid!r}"
        )


def _require_lru(value: str, *, field_name: str) -> None:
    if value != "lru":
        raise ValueError(f"{field_name} only supports lru")


def _optional_str(
    config: Mapping[str, Any],
    key: str,
    *,
    default: str,
    field_name: str,
) -> str:
    value = config.get(key, default)
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    return value
