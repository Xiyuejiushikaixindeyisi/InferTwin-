import pytest

from infertwin.scheduler.batch_shape import BatchShape, ScheduledSlice


def test_scheduled_slice_defaults_kv_load_to_zero() -> None:
    scheduled_slice = _slice("r1", scheduled_tokens=8)

    assert scheduled_slice.kv_load_tokens == 0
    assert scheduled_slice.kv_load_bytes == 0


def test_batch_shape_aggregates_kv_load_shape() -> None:
    shape = BatchShape(
        instance_uuid="instance-a",
        iteration_id=0,
        start_time_ms=0.0,
        request_slices=(
            _slice("r1", scheduled_tokens=8, kv_load_tokens=4, kv_load_bytes=1024),
            _slice("r2", scheduled_tokens=8, kv_load_tokens=0, kv_load_bytes=512),
            _slice("r3", scheduled_tokens=8),
        ),
    )

    assert shape.kv_load_tokens == 4
    assert shape.kv_load_bytes == 1536
    assert shape.kv_load_request_count == 2


def test_scheduled_slice_rejects_negative_kv_load_tokens() -> None:
    with pytest.raises(ValueError, match="kv_load_tokens"):
        _slice("r1", scheduled_tokens=8, kv_load_tokens=-1)


def test_scheduled_slice_rejects_negative_kv_load_bytes() -> None:
    with pytest.raises(ValueError, match="kv_load_bytes"):
        _slice("r1", scheduled_tokens=8, kv_load_bytes=-1)


def test_scheduled_slice_allows_load_only_kv_load() -> None:
    scheduled_slice = ScheduledSlice(
        request_id="r1",
        scheduled_prefill_tokens=0,
        computed_tokens_before=8,
        computed_tokens_after=8,
        prompt_tokens=8,
        cached_prefix_tokens=8,
        previous_chunk_tokens=0,
        kv_load_tokens=8,
        kv_load_bytes=1024,
    )

    assert scheduled_slice.scheduled_prefill_tokens == 0
    assert scheduled_slice.kv_load_tokens == 8


def test_scheduled_slice_rejects_empty_zero_token_slice() -> None:
    with pytest.raises(ValueError, match="zero-token scheduled slice"):
        ScheduledSlice(
            request_id="r1",
            scheduled_prefill_tokens=0,
            computed_tokens_before=8,
            computed_tokens_after=8,
            prompt_tokens=8,
            cached_prefix_tokens=8,
            previous_chunk_tokens=0,
        )


def test_scheduled_slice_rejects_negative_scheduled_prefill_tokens() -> None:
    with pytest.raises(ValueError, match="scheduled_prefill_tokens"):
        _slice("r1", scheduled_tokens=-1)


def _slice(
    request_id: str,
    *,
    scheduled_tokens: int,
    kv_load_tokens: int = 0,
    kv_load_bytes: int = 0,
) -> ScheduledSlice:
    return ScheduledSlice(
        request_id=request_id,
        scheduled_prefill_tokens=scheduled_tokens,
        computed_tokens_before=0,
        computed_tokens_after=scheduled_tokens,
        prompt_tokens=scheduled_tokens,
        cached_prefix_tokens=0,
        previous_chunk_tokens=0,
        kv_load_tokens=kv_load_tokens,
        kv_load_bytes=kv_load_bytes,
    )
