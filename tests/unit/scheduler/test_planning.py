from infertwin.scheduler.config import SchedulerConfig
from infertwin.scheduler.planning import planned_prefill_tokens
from infertwin.scheduler.state import RequestState


def test_planned_prefill_tokens_respects_token_budget() -> None:
    config = SchedulerConfig(max_num_batched_tokens=16, max_num_seqs=4)
    request = _request(prompt_tokens=64)

    assert planned_prefill_tokens(config, request, token_budget=10) == 10


def test_planned_prefill_tokens_respects_chunk_limit() -> None:
    config = SchedulerConfig(
        max_num_batched_tokens=32,
        max_num_seqs=4,
        enable_chunked_prefill=True,
        long_prefill_token_threshold=8,
    )
    request = _request(prompt_tokens=64)

    assert planned_prefill_tokens(config, request, token_budget=32) == 8


def test_planned_prefill_tokens_returns_zero_when_chunking_disabled_and_over_budget() -> None:
    config = SchedulerConfig(
        max_num_batched_tokens=16,
        max_num_seqs=4,
        enable_chunked_prefill=False,
    )
    request = _request(prompt_tokens=64)

    assert planned_prefill_tokens(config, request, token_budget=16) == 0


def test_planned_prefill_tokens_does_not_mutate_request_state() -> None:
    config = SchedulerConfig(max_num_batched_tokens=16, max_num_seqs=4)
    request = _request(prompt_tokens=64, cached_tokens=4)

    planned = planned_prefill_tokens(config, request, token_budget=16)

    assert planned == 16
    assert request.num_computed_tokens == 4
    assert request.cached_tokens == 4
    assert request.finish_time_ms is None


def test_planned_prefill_tokens_returns_zero_for_full_prefix_hit() -> None:
    config = SchedulerConfig(max_num_batched_tokens=16, max_num_seqs=4)
    request = _request(prompt_tokens=16, cached_tokens=16)

    assert planned_prefill_tokens(config, request, token_budget=16) == 0


def _request(prompt_tokens: int, cached_tokens: int = 0) -> RequestState:
    request = RequestState(
        request_id="request-a",
        tenant_id="tenant-a",
        instance_uuid="instance-a",
        arrival_time_ms=0.0,
        prompt_tokens=prompt_tokens,
    )
    request.set_cache_lookup(
        cached_tokens=cached_tokens,
        miss_tokens=prompt_tokens - cached_tokens,
    )
    return request
