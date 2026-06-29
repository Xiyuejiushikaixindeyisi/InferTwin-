from infertwin.latency.schema import ShapeKey
from infertwin.scheduler.batch_shape import BatchShape, ScheduledSlice


def test_shape_key_includes_kv_load_shape() -> None:
    key = ShapeKey.from_shape(
        backend="serving_latency_profile",
        model_name="glm-v5",
        hardware_name="ascend910c",
        shape=_shape(kv_load_tokens=16, kv_load_bytes=4096),
    )

    assert key.kv_load_tokens == 16
    assert key.kv_load_bytes == 4096
    assert key.kv_load_request_count == 1


def test_shape_key_separates_same_compute_shape_with_different_kv_load() -> None:
    no_load = ShapeKey.from_shape(
        backend="serving_latency_profile",
        model_name="glm-v5",
        hardware_name="ascend910c",
        shape=_shape(kv_load_tokens=0, kv_load_bytes=0),
    )
    with_load = ShapeKey.from_shape(
        backend="serving_latency_profile",
        model_name="glm-v5",
        hardware_name="ascend910c",
        shape=_shape(kv_load_tokens=16, kv_load_bytes=4096),
    )

    assert no_load != with_load


def test_shape_key_string_exposes_kv_load_shape() -> None:
    key = ShapeKey.from_shape(
        backend="serving_latency_profile",
        model_name="glm-v5",
        hardware_name="ascend910c",
        shape=_shape(kv_load_tokens=16, kv_load_bytes=4096),
    )

    assert "kvload_tokens=16" in str(key)
    assert "kvload_bytes=4096" in str(key)
    assert "kvload_reqs=1" in str(key)


def _shape(*, kv_load_tokens: int, kv_load_bytes: int) -> BatchShape:
    scheduled_tokens = 8
    return BatchShape(
        instance_uuid="instance-a",
        iteration_id=0,
        start_time_ms=0.0,
        request_slices=(
            ScheduledSlice(
                request_id="r1",
                scheduled_prefill_tokens=scheduled_tokens,
                computed_tokens_before=0,
                computed_tokens_after=scheduled_tokens,
                prompt_tokens=scheduled_tokens,
                cached_prefix_tokens=0,
                previous_chunk_tokens=0,
                kv_load_tokens=kv_load_tokens,
                kv_load_bytes=kv_load_bytes,
            ),
        ),
    )
