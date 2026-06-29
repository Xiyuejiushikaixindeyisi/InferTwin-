import pytest

from infertwin.replay.metrics import BatchAwareRequestMetrics, IterationMetrics
from infertwin.replay.timeline import (
    CHUNK_TTFT_GRANULARITY,
    ITERATION_TTFT_GRANULARITY,
    LEGACY_TIMELINE_MODE,
    PROGRESSIVE_TIMELINE_MODE,
    ChunkTimelineEntry,
    KVLoadTimelineEntry,
    RequestTimelineState,
    RequestTimelineSummary,
)


def test_request_timeline_state_values_are_stable() -> None:
    assert RequestTimelineState.PENDING.value == "pending"
    assert RequestTimelineState.WAITING_FOR_COMPUTE.value == "waiting_for_compute"
    assert RequestTimelineState.WAITING_FOR_KV_LOAD.value == "waiting_for_kv_load"
    assert RequestTimelineState.RUNNING_CHUNK.value == "running_chunk"
    assert RequestTimelineState.FINISHED.value == "finished"


def test_chunk_timeline_entry_accepts_valid_chunk() -> None:
    entry = ChunkTimelineEntry(
        request_id="r1",
        instance_uuid="i1",
        iteration_id=3,
        start_time_ms=10.0,
        finish_time_ms=14.0,
        scheduled_prefill_tokens=128,
        computed_tokens_before=256,
        computed_tokens_after=384,
        prefill_compute_ms=4.0,
    )

    assert entry.request_id == "r1"
    assert entry.scheduled_prefill_tokens == 128


def test_chunk_timeline_entry_rejects_invalid_time_order() -> None:
    with pytest.raises(ValueError, match="finish_time_ms"):
        ChunkTimelineEntry(
            request_id="r1",
            instance_uuid="i1",
            iteration_id=0,
            start_time_ms=2.0,
            finish_time_ms=1.0,
            scheduled_prefill_tokens=1,
            computed_tokens_before=0,
            computed_tokens_after=1,
        )


def test_chunk_timeline_entry_rejects_invalid_token_order() -> None:
    with pytest.raises(ValueError, match="computed_tokens_after"):
        ChunkTimelineEntry(
            request_id="r1",
            instance_uuid="i1",
            iteration_id=0,
            start_time_ms=0.0,
            finish_time_ms=1.0,
            scheduled_prefill_tokens=1,
            computed_tokens_before=2,
            computed_tokens_after=1,
        )


def test_kv_load_timeline_entry_accepts_token_and_byte_shape() -> None:
    entry = KVLoadTimelineEntry(
        request_id="r1",
        instance_uuid="i1",
        ready_time_ms=10.0,
        start_time_ms=12.0,
        finish_time_ms=15.0,
        kv_load_tokens=128,
        kv_load_bytes=4096,
        kv_load_ms=3.0,
        kv_load_wait_ms=2.0,
        source_tier="ddr",
    )

    assert entry.kv_load_tokens == 128
    assert entry.kv_load_bytes == 4096


def test_kv_load_timeline_entry_rejects_start_before_ready() -> None:
    with pytest.raises(ValueError, match="ready_time_ms"):
        KVLoadTimelineEntry(
            request_id="r1",
            instance_uuid="i1",
            ready_time_ms=2.0,
            start_time_ms=1.0,
            finish_time_ms=3.0,
        )


def test_kv_load_timeline_entry_rejects_negative_values() -> None:
    with pytest.raises(ValueError, match="kv_load_bytes"):
        KVLoadTimelineEntry(
            request_id="r1",
            instance_uuid="i1",
            ready_time_ms=0.0,
            start_time_ms=0.0,
            finish_time_ms=1.0,
            kv_load_bytes=-1,
        )


def test_request_timeline_summary_uses_legacy_defaults() -> None:
    summary = RequestTimelineSummary()

    assert summary.timeline_mode == LEGACY_TIMELINE_MODE
    assert summary.ttft_granularity == ITERATION_TTFT_GRANULARITY
    assert summary.compute_wait_ms == 0.0
    assert summary.kv_load_wait_ms == 0.0
    assert summary.scheduler_wait_ms == 0.0
    assert summary.unattributed_ttft_ms == 0.0
    assert summary.composed_ttft_ms == 0.0


def test_request_timeline_summary_scheduler_wait_is_derived() -> None:
    summary = RequestTimelineSummary(
        timeline_mode=PROGRESSIVE_TIMELINE_MODE,
        ttft_granularity=CHUNK_TTFT_GRANULARITY,
        compute_wait_ms=3.0,
        kv_load_wait_ms=5.0,
        uncached_prefill_compute_ms=7.0,
        unattributed_ttft_ms=2.0,
        chunk_count=2,
        load_event_count=1,
    )

    assert summary.scheduler_wait_ms == 8.0
    assert summary.composed_ttft_ms == 17.0


def test_request_timeline_summary_rejects_negative_counts() -> None:
    with pytest.raises(ValueError, match="chunk_count"):
        RequestTimelineSummary(chunk_count=-1)


def test_request_metrics_keep_legacy_timeline_defaults() -> None:
    metric = BatchAwareRequestMetrics(
        request_id="r1",
        tenant_id="tenant-a",
        instance_uuid="i1",
        model="glm-v5",
        tokenizer_profile="glm-v5",
        arrival_time_ms=0.0,
        first_scheduled_time_ms=2.0,
        finish_time_ms=10.0,
        scheduler_wait_ms=2.0,
        ttft_ms=10.0,
        prompt_tokens=8,
        prompt_blocks=2,
        hbm_hit_tokens=4,
        ddr_hit_tokens=0,
        miss_tokens=4,
        effective_hit_rate=0.5,
        scheduled_iteration_count=1,
        prefill_compute_ms=8.0,
    )

    assert metric.timeline_mode == LEGACY_TIMELINE_MODE
    assert metric.ttft_granularity == ITERATION_TTFT_GRANULARITY
    assert metric.compute_wait_ms == 0.0
    assert metric.kv_load_wait_ms == 0.0
    assert metric.uncached_prefill_compute_ms == 0.0
    assert metric.unattributed_ttft_ms == 0.0


def test_iteration_metrics_keep_legacy_timeline_defaults() -> None:
    metric = IterationMetrics(
        instance_uuid="i1",
        iteration_id=0,
        start_time_ms=0.0,
        finish_time_ms=1.0,
        duration_ms=1.0,
        batch_size=1,
        scheduled_prefill_tokens=8,
        scheduled_decode_tokens=0,
        max_query_len=8,
        total_context_tokens=0,
        backend="fitted_ttft",
        shape_key="shape",
        memoized=False,
        request_ids=("r1",),
    )

    assert metric.timeline_mode == LEGACY_TIMELINE_MODE
    assert metric.ttft_granularity == ITERATION_TTFT_GRANULARITY
    assert metric.waiting_for_compute_count == 0
    assert metric.waiting_for_kv_load_count == 0
    assert metric.scheduled_chunk_count == 0
