import pytest

from infertwin.latency.schema import LatencyResult, ShapeKey
from infertwin.replay.metrics import (
    build_iteration_metrics,
    latency_breakdown_from_result,
    split_iteration_latency_contributions,
)
from infertwin.replay.timeline import ITERATION_TTFT_GRANULARITY, LEGACY_TIMELINE_MODE
from infertwin.scheduler.batch_shape import BatchShape, ScheduledSlice


def test_latency_breakdown_treats_legacy_backend_as_prefill_compute() -> None:
    latency = _latency(duration_ms=12.5, details={})

    breakdown = latency_breakdown_from_result(latency)

    assert breakdown.prefill_compute_ms == 12.5
    assert breakdown.kv_load_ms == 0.0
    assert breakdown.queue_ms == 0.0


def test_latency_contributions_split_prefill_by_tokens_and_kv_load_by_bytes() -> None:
    shape = BatchShape(
        instance_uuid="instance-a",
        iteration_id=0,
        start_time_ms=0.0,
        request_slices=(
            _slice(
                "r1",
                scheduled_prefill_tokens=2,
                kv_load_tokens=10,
                kv_load_bytes=100,
            ),
            _slice(
                "r2",
                scheduled_prefill_tokens=6,
                kv_load_tokens=10,
                kv_load_bytes=300,
            ),
        ),
    )
    latency = _latency(
        duration_ms=130.0,
        details={"ttft_ms": 80.0, "kv_load_ms": 40.0, "queue_ms": 10.0},
    )

    contributions = split_iteration_latency_contributions(shape=shape, latency=latency)

    assert contributions["r1"].prefill_compute_ms == pytest.approx(20.0)
    assert contributions["r2"].prefill_compute_ms == pytest.approx(60.0)
    assert contributions["r1"].kv_load_ms == pytest.approx(10.0)
    assert contributions["r2"].kv_load_ms == pytest.approx(30.0)
    assert contributions["r1"].queue_ms == pytest.approx(5.0)
    assert contributions["r2"].queue_ms == pytest.approx(5.0)
    assert sum(item.prefill_compute_ms for item in contributions.values()) == pytest.approx(80.0)
    assert sum(item.kv_load_ms for item in contributions.values()) == pytest.approx(40.0)
    assert sum(item.queue_ms for item in contributions.values()) == pytest.approx(10.0)


def test_latency_contributions_fall_back_to_tokens_when_bytes_are_absent() -> None:
    shape = BatchShape(
        instance_uuid="instance-a",
        iteration_id=0,
        start_time_ms=0.0,
        request_slices=(
            _slice("r1", scheduled_prefill_tokens=1, kv_load_tokens=1),
            _slice("r2", scheduled_prefill_tokens=1, kv_load_tokens=3),
        ),
    )
    latency = _latency(
        duration_ms=10.0,
        details={"ttft_ms": 0.0, "kv_load_ms": 8.0, "queue_ms": 2.0},
    )

    contributions = split_iteration_latency_contributions(shape=shape, latency=latency)

    assert contributions["r1"].kv_load_ms == pytest.approx(2.0)
    assert contributions["r2"].kv_load_ms == pytest.approx(6.0)
    assert sum(item.kv_load_ms for item in contributions.values()) == pytest.approx(8.0)


def test_latency_contributions_support_load_only_iteration() -> None:
    shape = BatchShape(
        instance_uuid="instance-a",
        iteration_id=0,
        start_time_ms=0.0,
        request_slices=(
            _slice(
                "r1",
                scheduled_prefill_tokens=0,
                computed_tokens_before=8,
                prompt_tokens=8,
                cached_prefix_tokens=8,
                previous_chunk_tokens=0,
                kv_load_tokens=8,
                kv_load_bytes=80,
            ),
        ),
    )
    latency = _latency(duration_ms=4.0, details={"ttft_ms": 0.0, "kv_load_ms": 4.0})

    contributions = split_iteration_latency_contributions(shape=shape, latency=latency)

    assert contributions["r1"].prefill_compute_ms == 0.0
    assert contributions["r1"].kv_load_ms == 4.0
    assert contributions["r1"].queue_ms == 0.0


def test_latency_breakdown_rejects_non_numeric_component_details() -> None:
    latency = _latency(duration_ms=1.0, details={"kv_load_ms": "bad"})

    with pytest.raises(ValueError, match="kv_load_ms"):
        latency_breakdown_from_result(latency)


def test_build_iteration_metrics_uses_legacy_timeline_defaults() -> None:
    shape = BatchShape(
        instance_uuid="instance-a",
        iteration_id=0,
        start_time_ms=0.0,
        request_slices=(_slice("r1", scheduled_prefill_tokens=4),),
    )
    latency = _latency(duration_ms=4.0, details={"ttft_ms": 4.0})

    metric = build_iteration_metrics(shape=shape, latency=latency, finish_time_ms=4.0)

    assert metric.timeline_mode == LEGACY_TIMELINE_MODE
    assert metric.ttft_granularity == ITERATION_TTFT_GRANULARITY
    assert metric.compute_wait_ms == 0.0
    assert metric.kv_load_wait_ms == 0.0


def _slice(
    request_id: str,
    *,
    scheduled_prefill_tokens: int,
    kv_load_tokens: int = 0,
    kv_load_bytes: int = 0,
    computed_tokens_before: int = 0,
    prompt_tokens: int = 16,
    cached_prefix_tokens: int = 0,
    previous_chunk_tokens: int = 0,
) -> ScheduledSlice:
    return ScheduledSlice(
        request_id=request_id,
        scheduled_prefill_tokens=scheduled_prefill_tokens,
        computed_tokens_before=computed_tokens_before,
        computed_tokens_after=computed_tokens_before + scheduled_prefill_tokens,
        prompt_tokens=prompt_tokens,
        cached_prefix_tokens=cached_prefix_tokens,
        previous_chunk_tokens=previous_chunk_tokens,
        kv_load_tokens=kv_load_tokens,
        kv_load_bytes=kv_load_bytes,
    )


def _latency(
    *,
    duration_ms: float,
    details: dict[str, float | int | str | bool],
) -> LatencyResult:
    shape_key = ShapeKey(
        backend="unit-test",
        model_name="model-a",
        hardware_name="hardware-a",
        batch_size=1,
        scheduled_prefill_tokens=1,
        scheduled_decode_tokens=0,
        max_query_len=1,
        total_context_tokens=0,
    )
    return LatencyResult(
        duration_ms=duration_ms,
        backend="unit-test",
        shape_key=shape_key,
        details=details,
    )
