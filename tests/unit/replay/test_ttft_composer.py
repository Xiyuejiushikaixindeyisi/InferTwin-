from datetime import datetime

import pytest

from infertwin.instance.request import SimulationRequest
from infertwin.replay.timeline import (
    CHUNK_TTFT_GRANULARITY,
    ITERATION_TTFT_GRANULARITY,
    LEGACY_TIMELINE_MODE,
    PROGRESSIVE_TIMELINE_MODE,
)
from infertwin.replay.ttft import RequestTTFTComposer
from infertwin.scheduler.state import RequestState


def test_legacy_composition_keeps_iteration_ttft_semantics() -> None:
    request = _request(start_time_ms=2.0)
    state = _state(
        timeline_mode=LEGACY_TIMELINE_MODE,
        prefill_compute_ms=7.0,
        compute_wait_ms=3.0,
        kv_load_wait_ms=5.0,
        chunk_count=2,
        load_event_count=1,
    )

    composition = RequestTTFTComposer().compose(
        request=request,
        state=state,
        finish_time_ms=12.0,
        first_scheduled_time_ms=4.0,
    )

    assert composition.timeline_mode == LEGACY_TIMELINE_MODE
    assert composition.ttft_granularity == ITERATION_TTFT_GRANULARITY
    assert composition.observed_ttft_ms == 10.0
    assert composition.ttft_ms == 10.0
    assert composition.scheduler_wait_ms == 2.0
    assert composition.compute_wait_ms == 0.0
    assert composition.kv_load_wait_ms == 0.0
    assert composition.uncached_prefill_compute_ms == 7.0
    assert composition.unattributed_ttft_ms == 0.0
    assert composition.chunk_count == 0
    assert composition.load_event_count == 0


def test_progressive_composition_closes_with_unattributed_ttft() -> None:
    request = _request(start_time_ms=0.0)
    state = _state(
        timeline_mode=PROGRESSIVE_TIMELINE_MODE,
        compute_wait_ms=3.0,
        kv_load_wait_ms=2.0,
        prefill_compute_ms=5.0,
        chunk_count=2,
        load_event_count=1,
    )

    composition = RequestTTFTComposer().compose(
        request=request,
        state=state,
        finish_time_ms=12.0,
        first_scheduled_time_ms=3.0,
    )

    assert composition.timeline_mode == PROGRESSIVE_TIMELINE_MODE
    assert composition.ttft_granularity == CHUNK_TTFT_GRANULARITY
    assert composition.scheduler_wait_ms == 5.0
    assert composition.ttft_ms == 12.0
    assert composition.unattributed_ttft_ms == 2.0
    assert (
        composition.compute_wait_ms
        + composition.kv_load_wait_ms
        + composition.uncached_prefill_compute_ms
        + composition.unattributed_ttft_ms
        == composition.ttft_ms
    )
    assert composition.chunk_count == 2
    assert composition.load_event_count == 1


def test_progressive_composition_keeps_zero_residual_clean() -> None:
    request = _request(start_time_ms=0.0)
    state = _state(
        timeline_mode=PROGRESSIVE_TIMELINE_MODE,
        compute_wait_ms=1.0,
        kv_load_wait_ms=2.0,
        prefill_compute_ms=3.0,
    )

    composition = RequestTTFTComposer().compose(
        request=request,
        state=state,
        finish_time_ms=6.0,
        first_scheduled_time_ms=1.0,
    )

    assert composition.ttft_ms == 6.0
    assert composition.unattributed_ttft_ms == 0.0


def test_progressive_composition_rejects_negative_residual() -> None:
    request = _request(start_time_ms=0.0)
    state = _state(
        timeline_mode=PROGRESSIVE_TIMELINE_MODE,
        compute_wait_ms=3.0,
        kv_load_wait_ms=2.0,
        prefill_compute_ms=5.0,
    )

    with pytest.raises(ValueError, match="negative unattributed TTFT residual"):
        RequestTTFTComposer().compose(
            request=request,
            state=state,
            finish_time_ms=9.0,
            first_scheduled_time_ms=3.0,
        )


def test_composition_rejects_first_schedule_before_arrival() -> None:
    request = _request(start_time_ms=10.0)
    state = _state(timeline_mode=LEGACY_TIMELINE_MODE)

    with pytest.raises(ValueError, match="first_scheduled_time_ms"):
        RequestTTFTComposer().compose(
            request=request,
            state=state,
            finish_time_ms=12.0,
            first_scheduled_time_ms=9.0,
        )


def _request(*, start_time_ms: float) -> SimulationRequest:
    return SimulationRequest(
        request_id="r1",
        tenant_id="tenant-a",
        instance_uuid="instance-a",
        model="glm-v5",
        service_start_time=datetime.fromisoformat("2026-06-05 09:01:23"),
        start_time_ms=start_time_ms,
        tokenizer_profile="glm-v5",
        prompt_tokens=8,
        prompt_blocks=(),
        kv_bytes_per_token=1,
    )


def _state(
    *,
    timeline_mode: str,
    compute_wait_ms: float = 0.0,
    kv_load_wait_ms: float = 0.0,
    prefill_compute_ms: float = 0.0,
    chunk_count: int = 0,
    load_event_count: int = 0,
) -> RequestState:
    return RequestState(
        request_id="r1",
        tenant_id="tenant-a",
        instance_uuid="instance-a",
        arrival_time_ms=0.0,
        prompt_tokens=8,
        timeline_mode=timeline_mode,
        compute_wait_ms=compute_wait_ms,
        kv_load_wait_ms=kv_load_wait_ms,
        prefill_compute_ms=prefill_compute_ms,
        chunk_count=chunk_count,
        load_event_count=load_event_count,
    )
