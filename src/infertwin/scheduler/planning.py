"""Shared token planning helpers for scheduler and replay."""

from __future__ import annotations

from infertwin.scheduler.config import SchedulerConfig
from infertwin.scheduler.state import RequestState


def planned_prefill_tokens(
    config: SchedulerConfig,
    request: RequestState,
    token_budget: int,
) -> int:
    """Return how many prefill tokens this request may receive this iteration.

    The helper is intentionally pure with respect to request state: it reads the
    request's remaining prefill tokens but does not mutate queues, status, or
    token progress. Batch-aware replay can use the same helper to decide which
    waiting requests may be looked up without duplicating scheduler logic.
    """

    if token_budget <= 0:
        return 0

    remaining = request.remaining_prefill_tokens()
    if remaining <= 0:
        return 0

    if not config.enable_chunked_prefill and remaining > token_budget:
        return 0

    chunk_limit = config.per_request_chunk_limit()
    return min(remaining, token_budget, chunk_limit)
