"""vLLM-like FCFS scheduler for batch-aware prefill replay."""

from __future__ import annotations

from dataclasses import dataclass

from infertwin.scheduler.batch_shape import BatchShape, ScheduledSlice
from infertwin.scheduler.config import SchedulerConfig
from infertwin.scheduler.planning import planned_prefill_tokens
from infertwin.scheduler.queue import WaitingQueue
from infertwin.scheduler.state import RequestState, RequestStatus


@dataclass(frozen=True, slots=True)
class ScheduleResult:
    """Scheduler output for one iteration."""

    shape: BatchShape

    @property
    def is_empty(self) -> bool:
        return self.shape.batch_size == 0


class VllmLikeBatchScheduler:
    """Approximate vLLM continuous batching for TTFT-focused prefill replay.

    The scheduler is responsible for forming iteration-level token slices. It
    does not estimate latency and does not apply completed tokens; the replay
    engine will apply slices after the latency backend returns an iteration
    finish time.
    """

    def __init__(self, config: SchedulerConfig) -> None:
        self.config = config

    def schedule(
        self,
        *,
        instance_uuid: str,
        iteration_id: int,
        start_time_ms: float,
        waiting: WaitingQueue,
        running: list[RequestState],
    ) -> ScheduleResult:
        token_budget = self.config.max_num_batched_tokens
        seq_budget = self.config.max_num_seqs
        slices: list[ScheduledSlice] = []

        for request in list(running):
            if len(slices) >= seq_budget:
                break
            if request.status != RequestStatus.RUNNING:
                continue
            can_schedule_load_only = _can_schedule_load_only(request)
            if token_budget <= 0 and not can_schedule_load_only:
                break
            scheduled_tokens = self._tokens_for_request(request, token_budget)
            if scheduled_tokens <= 0 and not can_schedule_load_only:
                continue
            slices.append(self._slice_for(request, scheduled_tokens))
            if scheduled_tokens > 0:
                token_budget -= scheduled_tokens

        while waiting and len(slices) < seq_budget:
            request = waiting[0]
            if request.status != RequestStatus.WAITING:
                raise ValueError(f"waiting request {request.request_id} is not in waiting state")
            if token_budget <= 0 and not request.cache_lookup_done:
                break
            can_schedule_load_only = _can_schedule_load_only(request)
            if token_budget <= 0 and not can_schedule_load_only:
                break

            scheduled_tokens = self._tokens_for_request(request, token_budget)
            if scheduled_tokens <= 0 and not can_schedule_load_only:
                break

            waiting.popleft()
            request.status = RequestStatus.RUNNING
            if request.first_scheduled_time_ms is None:
                request.first_scheduled_time_ms = start_time_ms
            running.append(request)

            slices.append(self._slice_for(request, scheduled_tokens))
            if scheduled_tokens > 0:
                token_budget -= scheduled_tokens

        return ScheduleResult(
            shape=BatchShape(
                instance_uuid=instance_uuid,
                iteration_id=iteration_id,
                start_time_ms=start_time_ms,
                request_slices=tuple(slices),
            )
        )

    def _tokens_for_request(self, request: RequestState, token_budget: int) -> int:
        return planned_prefill_tokens(self.config, request, token_budget)

    @staticmethod
    def _slice_for(request: RequestState, scheduled_tokens: int) -> ScheduledSlice:
        computed_before = request.num_computed_tokens
        previous_chunk_tokens = computed_before - request.cached_tokens
        if previous_chunk_tokens < 0:
            raise ValueError(
                f"request {request.request_id} has computed tokens below cached prefix tokens"
            )
        kv_load_tokens, kv_load_bytes = request.consume_pending_kv_load()
        return ScheduledSlice(
            request_id=request.request_id,
            scheduled_prefill_tokens=scheduled_tokens,
            computed_tokens_before=computed_before,
            computed_tokens_after=computed_before + scheduled_tokens,
            prompt_tokens=request.prompt_tokens,
            cached_prefix_tokens=request.cached_tokens,
            previous_chunk_tokens=previous_chunk_tokens,
            kv_load_tokens=kv_load_tokens,
            kv_load_bytes=kv_load_bytes,
        )


def _can_schedule_load_only(request: RequestState) -> bool:
    return request.remaining_prefill_tokens() == 0 and request.has_pending_kv_load()
