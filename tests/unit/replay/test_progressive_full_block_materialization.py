from datetime import datetime, timedelta

import pytest

from infertwin.cache.cache_block_conversion import CacheBlockConversionResult
from infertwin.cache.ddr_lru import DDRLRUCache
from infertwin.cache.event_sink import InMemoryCacheEventSink
from infertwin.cache.events import CACHE_TIER_DDR, CACHE_TIER_HBM, MATERIALIZE, STORE
from infertwin.cache.hbm_lru import HBMCache
from infertwin.cache.tiered import TieredPrefixCache
from infertwin.instance.request import SimulationRequest
from infertwin.latency.formula import FormulaLatencyBackend
from infertwin.replay.event_loop import BatchAwareReplayEngine
from infertwin.replay.timeline import LEGACY_TIMELINE_MODE, PROGRESSIVE_TIMELINE_MODE
from infertwin.request.block_hasher import build_prefix_blocks
from infertwin.scheduler.config import SchedulerConfig
from infertwin.scheduler.vllm_like import VllmLikeBatchScheduler


def test_legacy_mode_keeps_finish_time_materialization_visibility() -> None:
    engine = _engine(timeline_mode=LEGACY_TIMELINE_MODE, max_num_batched_tokens=8)
    first = _request("r1", start_time_ms=0.0, token_ids=list(range(12)))
    repeat_before_finish = _request("r2", start_time_ms=8.0, token_ids=list(range(12)))

    result = engine.run([first, repeat_before_finish])

    metrics_by_id = {item.request_id: item for item in result.request_metrics}
    assert metrics_by_id["r2"].hbm_hit_tokens == 0
    assert metrics_by_id["r2"].miss_tokens == 12
    assert all(item.progressive_materialized_blocks == 0 for item in result.request_metrics)
    assert all(item.progressive_materialized_blocks == 0 for item in result.iteration_metrics)


def test_progressive_mode_makes_full_block_visible_after_chunk_finish() -> None:
    engine = _engine(timeline_mode=PROGRESSIVE_TIMELINE_MODE, max_num_batched_tokens=8)
    first = _request("r1", start_time_ms=0.0, token_ids=list(range(12)))
    repeat_after_first_chunk = _request("r2", start_time_ms=8.0, token_ids=list(range(12)))

    result = engine.run([first, repeat_after_first_chunk])

    metrics_by_id = {item.request_id: item for item in result.request_metrics}
    assert metrics_by_id["r1"].miss_tokens == 12
    assert metrics_by_id["r2"].hbm_hit_tokens == 8
    assert metrics_by_id["r2"].miss_tokens == 4
    assert metrics_by_id["r1"].progressive_materialized_blocks == 3
    assert metrics_by_id["r1"].progressive_materialized_tokens == 12
    assert result.iteration_metrics[0].progressive_materialized_blocks == 2
    assert result.iteration_metrics[0].progressive_materialized_tokens == 8


def test_progressive_mode_keeps_partial_block_invisible() -> None:
    engine = _engine(timeline_mode=PROGRESSIVE_TIMELINE_MODE, max_num_batched_tokens=4)
    first = _request("r1", start_time_ms=0.0, token_ids=list(range(6)))
    repeat_after_finish = _request("r2", start_time_ms=10.0, token_ids=list(range(6)))

    result = engine.run([first, repeat_after_finish])

    metrics_by_id = {item.request_id: item for item in result.request_metrics}
    assert metrics_by_id["r1"].progressive_materialized_blocks == 1
    assert metrics_by_id["r1"].progressive_materialized_tokens == 4
    assert metrics_by_id["r2"].hbm_hit_tokens == 4
    assert metrics_by_id["r2"].miss_tokens == 2


def test_progressive_mode_emits_distinct_hbm_event_reason() -> None:
    engine = _engine(timeline_mode=PROGRESSIVE_TIMELINE_MODE, max_num_batched_tokens=4)
    request = _request("r1", start_time_ms=0.0, token_ids=list(range(4)))
    sink = InMemoryCacheEventSink()

    result = engine.run([request], cache_event_sink=sink)

    assert result.cache_events == sink.snapshot_events()
    materialize_events = [
        event for event in result.cache_events if event.event_type == MATERIALIZE
    ]
    assert len(materialize_events) == 1
    assert materialize_events[0].cache_tier == CACHE_TIER_HBM
    assert materialize_events[0].reason == "progressive_chunk_materialization"


def test_progressive_mode_emits_tiered_hbm_and_ddr_event_reasons() -> None:
    engine = _engine(
        timeline_mode=PROGRESSIVE_TIMELINE_MODE,
        max_num_batched_tokens=4,
        cache_factory=lambda _instance_uuid: TieredPrefixCache(
            hbm=HBMCache(capacity_blocks=4),
            ddr=DDRLRUCache(capacity_blocks=4),
        ),
    )
    request = _request("r1", start_time_ms=0.0, token_ids=list(range(4)))
    sink = InMemoryCacheEventSink()

    result = engine.run([request], cache_event_sink=sink)

    write_events = [
        event
        for event in result.cache_events
        if event.event_type in {MATERIALIZE, STORE}
    ]
    assert [(event.cache_tier, event.reason) for event in write_events] == [
        (CACHE_TIER_HBM, "progressive_chunk_materialization"),
        (CACHE_TIER_DDR, "progressive_chunk_store"),
    ]


def test_progressive_materialization_is_isolated_by_instance() -> None:
    engine = _engine(timeline_mode=PROGRESSIVE_TIMELINE_MODE, max_num_batched_tokens=4)
    first = _request(
        "r1",
        instance_uuid="instance-a",
        start_time_ms=0.0,
        token_ids=list(range(8)),
    )
    same_prompt_other_instance = _request(
        "r2",
        instance_uuid="instance-b",
        start_time_ms=4.0,
        token_ids=list(range(8)),
    )

    result = engine.run([first, same_prompt_other_instance])

    metrics_by_id = {item.request_id: item for item in result.request_metrics}
    assert metrics_by_id["r1"].hbm_hit_tokens == 0
    assert metrics_by_id["r2"].hbm_hit_tokens == 0
    assert metrics_by_id["r2"].miss_tokens == 8


def test_progressive_mode_rejects_finish_time_only_policy() -> None:
    with pytest.raises(ValueError, match="supports progressive chunks"):
        _engine(
            timeline_mode=PROGRESSIVE_TIMELINE_MODE,
            materialization_policy=_FinishOnlyPolicy(),
        )


def _engine(
    *,
    timeline_mode: str,
    max_num_batched_tokens: int = 4,
    cache_factory=None,
    materialization_policy=None,
) -> BatchAwareReplayEngine:
    scheduler = VllmLikeBatchScheduler(
        SchedulerConfig(
            max_num_batched_tokens=max_num_batched_tokens,
            max_num_seqs=8,
            enable_chunked_prefill=True,
        )
    )
    return BatchAwareReplayEngine(
        scheduler=scheduler,
        latency_backend=FormulaLatencyBackend(
            iteration_fixed_overhead_ms=0.0,
            iteration_prefill_token_ms=1.0,
            iteration_batch_overhead_ms=0.0,
            iteration_context_token_ms=0.0,
            model_name="glm-v5",
            hardware_name="local-dev",
        ),
        cache_factory=cache_factory
        or (lambda _instance_uuid: HBMCache(capacity_blocks=16)),
        materialization_policy=materialization_policy,
        timeline_mode=timeline_mode,
    )


def _request(
    request_id: str,
    *,
    instance_uuid: str = "instance-a",
    start_time_ms: float,
    token_ids: list[int],
    block_size_tokens: int = 4,
) -> SimulationRequest:
    service_start_time = datetime.fromisoformat("2026-06-05 09:01:23") + timedelta(
        milliseconds=start_time_ms
    )
    blocks = build_prefix_blocks(
        token_ids=token_ids,
        block_size_tokens=block_size_tokens,
        model="glm-v5",
        tenant_id="tenant-a",
        kv_bytes_per_token=1,
    )
    return SimulationRequest(
        request_id=request_id,
        tenant_id="tenant-a",
        instance_uuid=instance_uuid,
        model="glm-v5",
        service_start_time=service_start_time,
        start_time_ms=start_time_ms,
        tokenizer_profile="glm-v5",
        prompt_tokens=len(token_ids),
        prompt_blocks=tuple(blocks),
        kv_bytes_per_token=1,
        requested_block_size=block_size_tokens,
        runtime_block_size=block_size_tokens,
        effective_block_size=block_size_tokens,
        block_conversion_result=_conversion_result(
            prompt_tokens=len(token_ids),
            block_size=block_size_tokens,
        ),
    )


def _conversion_result(*, prompt_tokens: int, block_size: int) -> CacheBlockConversionResult:
    max_cache_hit_length = max(prompt_tokens - 1, 0)
    max_matchable_blocks = max_cache_hit_length // block_size
    return CacheBlockConversionResult(
        requested_block_size=block_size,
        runtime_block_size=block_size,
        effective_block_size=block_size,
        max_cache_hit_length=max_cache_hit_length,
        max_matchable_blocks=max_matchable_blocks,
        matched_blocks=max_matchable_blocks,
        speculative_drop_blocks=0,
        cached_blocks=max_matchable_blocks,
        cached_tokens=max_matchable_blocks * block_size,
    )


class _FinishOnlyPolicy:
    name = "finish_only"
    supports_progressive_chunks = False

    def materialize_finished_request(
        self,
        *,
        cache,
        blocks,
        finish_time_ms: float,
        request_id: str,
        instance_uuid: str,
    ) -> None:
        cache.materialize(
            blocks,
            now_ms=finish_time_ms,
            request_id=request_id,
            instance_uuid=instance_uuid,
        )
