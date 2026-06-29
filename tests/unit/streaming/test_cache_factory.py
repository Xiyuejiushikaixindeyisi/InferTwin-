import pytest

from infertwin.cache.hbm_lru import HBMCache
from infertwin.cache.tiered import TieredPrefixCache
from infertwin.config.model_runtime import ModelCacheDefaults, ModelCachePoolingDefaults
from infertwin.replay.timeline import CHUNK_TTFT_GRANULARITY, PROGRESSIVE_TIMELINE_MODE
from infertwin.streaming.cache_factory import (
    CACHE_MODE_HBM_DDR_LRU_PROGRESSIVE_TIMELINE,
    CACHE_MODE_HBM_DDR_LRU,
    CACHE_MODE_HBM_LRU,
    build_streaming_cache_factory_config,
    build_streaming_prefix_cache,
    timeline_mode_for_cache_mode,
    ttft_granularity_for_timeline_mode,
)


def test_legacy_hbm_policy_defaults_to_hbm_lru_mode() -> None:
    config = build_streaming_cache_factory_config(
        {"cache": {"policy": "hbm", "eviction_policy": "lru"}}
    )

    assert config.mode == CACHE_MODE_HBM_LRU
    assert config.eviction_policy == "lru"


def test_explicit_hbm_lru_mode_builds_hbm_cache() -> None:
    config = build_streaming_cache_factory_config(
        {"cache": {"mode": CACHE_MODE_HBM_LRU, "eviction_policy": "lru"}}
    )

    cache = build_streaming_prefix_cache(
        capacity=4,
        instance_uuid="instance-a",
        cache_defaults=None,
        config=config,
    )

    assert isinstance(cache, HBMCache)
    assert cache.capacity_blocks == 4


def test_explicit_hbm_ddr_mode_builds_tiered_cache() -> None:
    config = build_streaming_cache_factory_config(
        {"cache": {"mode": CACHE_MODE_HBM_DDR_LRU, "eviction_policy": "lru"}}
    )

    cache = build_streaming_prefix_cache(
        capacity=2,
        instance_uuid="instance-a",
        cache_defaults=_ddr_cache_defaults(ddr_capacity_blocks=8),
        config=config,
    )

    assert isinstance(cache, TieredPrefixCache)
    assert cache.stats.hbm_capacity_blocks == 2
    assert cache.stats.ddr_capacity_blocks == 8


def test_progressive_timeline_mode_builds_tiered_cache_and_timeline_mode() -> None:
    config = build_streaming_cache_factory_config(
        {
            "cache": {
                "mode": CACHE_MODE_HBM_DDR_LRU_PROGRESSIVE_TIMELINE,
                "eviction_policy": "lru",
            }
        }
    )

    cache = build_streaming_prefix_cache(
        capacity=2,
        instance_uuid="instance-a",
        cache_defaults=_ddr_cache_defaults(ddr_capacity_blocks=8),
        config=config,
    )

    assert isinstance(cache, TieredPrefixCache)
    assert timeline_mode_for_cache_mode(config.mode) == PROGRESSIVE_TIMELINE_MODE
    assert ttft_granularity_for_timeline_mode(PROGRESSIVE_TIMELINE_MODE) == (CHUNK_TTFT_GRANULARITY)


def test_hbm_ddr_mode_requires_instance_cache_defaults() -> None:
    config = build_streaming_cache_factory_config(
        {"cache": {"mode": CACHE_MODE_HBM_DDR_LRU, "eviction_policy": "lru"}}
    )

    with pytest.raises(ValueError, match="requires model registry and instance runtime defaults"):
        build_streaming_prefix_cache(
            capacity=2,
            instance_uuid="instance-a",
            cache_defaults=None,
            config=config,
        )


def test_hbm_ddr_mode_requires_ddr_capacity() -> None:
    config = build_streaming_cache_factory_config(
        {"cache": {"mode": CACHE_MODE_HBM_DDR_LRU, "eviction_policy": "lru"}}
    )

    with pytest.raises(ValueError, match="default_cache.ddr_capacity_blocks"):
        build_streaming_prefix_cache(
            capacity=2,
            instance_uuid="instance-a",
            cache_defaults=_ddr_cache_defaults(ddr_capacity_blocks=None),
            config=config,
        )


def test_hbm_ddr_mode_requires_pooling_enabled() -> None:
    config = build_streaming_cache_factory_config(
        {"cache": {"mode": CACHE_MODE_HBM_DDR_LRU, "eviction_policy": "lru"}}
    )

    with pytest.raises(ValueError, match="default_cache.pooling.enabled=true"):
        build_streaming_prefix_cache(
            capacity=2,
            instance_uuid="instance-a",
            cache_defaults=ModelCacheDefaults(
                hbm_capacity_blocks=4,
                ddr_capacity_blocks=8,
                block_size_tokens=2,
                eviction_policy="lru",
            ),
            config=config,
        )


def test_hbm_ddr_mode_rejects_multi_instance_pooling() -> None:
    config = build_streaming_cache_factory_config(
        {"cache": {"mode": CACHE_MODE_HBM_DDR_LRU, "eviction_policy": "lru"}}
    )

    with pytest.raises(ValueError, match="does not support multi_instance pooling"):
        build_streaming_prefix_cache(
            capacity=2,
            instance_uuid="instance-a",
            cache_defaults=ModelCacheDefaults(
                hbm_capacity_blocks=4,
                ddr_capacity_blocks=8,
                block_size_tokens=2,
                eviction_policy="lru",
                pooling=ModelCachePoolingDefaults(
                    enabled=True,
                    single_instance=True,
                    multi_instance=True,
                    ddr_enabled=True,
                ),
            ),
            config=config,
        )


def test_unsupported_cache_mode_fails() -> None:
    with pytest.raises(ValueError, match="cache.mode must be one of"):
        build_streaming_cache_factory_config(
            {"cache": {"mode": "batch_aware_remote_pool", "eviction_policy": "lru"}}
        )


def test_unsupported_eviction_policy_fails() -> None:
    with pytest.raises(ValueError, match="cache.eviction_policy: lru"):
        build_streaming_cache_factory_config(
            {"cache": {"mode": CACHE_MODE_HBM_LRU, "eviction_policy": "fifo"}}
        )


def _ddr_cache_defaults(*, ddr_capacity_blocks: int | None) -> ModelCacheDefaults:
    return ModelCacheDefaults(
        hbm_capacity_blocks=4,
        ddr_capacity_blocks=ddr_capacity_blocks,
        block_size_tokens=2,
        eviction_policy="lru",
        pooling=ModelCachePoolingDefaults(
            enabled=True,
            single_instance=True,
            multi_instance=False,
            ddr_enabled=True,
            remote_enabled=False,
            ssd_enabled=False,
        ),
    )
