import pytest

from infertwin.scheduler.state import RequestState


def test_request_state_stores_and_consumes_pending_kv_load_once() -> None:
    request = _request(prompt_tokens=8)

    request.set_cache_lookup(
        cached_tokens=4,
        miss_tokens=4,
        kv_load_tokens=4,
        kv_load_bytes=1024,
    )

    assert request.has_pending_kv_load() is True
    assert request.consume_pending_kv_load() == (4, 1024)
    assert request.has_pending_kv_load() is False
    assert request.consume_pending_kv_load() == (0, 0)


def test_request_state_rejects_negative_kv_load_tokens() -> None:
    request = _request(prompt_tokens=8)

    with pytest.raises(ValueError, match="kv_load_tokens"):
        request.set_cache_lookup(
            cached_tokens=4,
            miss_tokens=4,
            kv_load_tokens=-1,
        )


def test_request_state_rejects_negative_kv_load_bytes() -> None:
    request = _request(prompt_tokens=8)

    with pytest.raises(ValueError, match="kv_load_bytes"):
        request.set_cache_lookup(
            cached_tokens=4,
            miss_tokens=4,
            kv_load_bytes=-1,
        )


def test_request_state_keeps_cache_lookup_token_invariant() -> None:
    request = _request(prompt_tokens=8)

    with pytest.raises(ValueError, match="cached_tokens \\+ miss_tokens"):
        request.set_cache_lookup(
            cached_tokens=4,
            miss_tokens=3,
            kv_load_tokens=4,
        )


def test_request_state_applies_load_only_iteration() -> None:
    request = _request(prompt_tokens=4)
    request.set_cache_lookup(
        cached_tokens=4,
        miss_tokens=0,
        kv_load_tokens=4,
    )
    request.consume_pending_kv_load()

    request.apply_load_only_iteration(finish_time_ms=12.0)

    assert request.scheduled_iteration_count == 1
    assert request.finish_time_ms == 12.0


def test_request_state_rejects_load_only_when_prefill_remains() -> None:
    request = _request(prompt_tokens=8)
    request.set_cache_lookup(
        cached_tokens=4,
        miss_tokens=4,
        kv_load_tokens=4,
    )

    with pytest.raises(ValueError, match="zero remaining prefill"):
        request.apply_load_only_iteration(finish_time_ms=12.0)


def _request(*, prompt_tokens: int) -> RequestState:
    return RequestState(
        request_id="r1",
        tenant_id="tenant-a",
        instance_uuid="instance-a",
        arrival_time_ms=0.0,
        prompt_tokens=prompt_tokens,
    )
