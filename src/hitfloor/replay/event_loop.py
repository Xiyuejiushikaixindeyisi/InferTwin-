"""Batch-aware replay engine for fixed-routing, per-instance isolated replay."""

from __future__ import annotations

from collections.abc import Callable

from hitfloor.cache.base import PrefixCache
from hitfloor.cache.event_sink import CacheEventSink, NullCacheEventSink
from hitfloor.cache.infinite_hbm import InfiniteHBMCache
from hitfloor.instance.request import SimulationRequest
from hitfloor.latency.backend import BatchLatencyBackend
from hitfloor.latency.memo import ShapeMemo
from hitfloor.latency.schema import LatencyResult, ShapeKey
from hitfloor.replay.metrics import (
    BatchAwareReplayResult,
    BatchAwareRequestMetrics,
    IterationMetrics,
    LookupMetrics,
    build_iteration_metrics,
    build_request_metrics,
)
from hitfloor.scheduler.batch_shape import BatchShape
from hitfloor.scheduler.planning import planned_prefill_tokens
from hitfloor.scheduler.queue import WaitingQueue
from hitfloor.scheduler.state import RequestState, RequestStatus
from hitfloor.scheduler.vllm_like import ScheduleResult, VllmLikeBatchScheduler


class BatchAwareReplayEngine:
    """Replay requests with vLLM-like batching and per-instance prefix cache."""

    def __init__(
        self,
        *,
        scheduler: VllmLikeBatchScheduler,
        latency_backend: BatchLatencyBackend,
        shape_memo: ShapeMemo | None = None,
        cache_factory: Callable[[str], PrefixCache] | None = None,
    ) -> None:
        self.scheduler = scheduler
        self.latency_backend = latency_backend
        self.shape_memo = shape_memo or ShapeMemo()
        self.cache_factory = cache_factory or _default_cache_factory

    def run(
        self,
        requests: list[SimulationRequest],
        *,
        cache_event_sink: CacheEventSink | None = None,
    ) -> BatchAwareReplayResult:
        sink = cache_event_sink or NullCacheEventSink()
        request_metrics: list[BatchAwareRequestMetrics] = []
        iteration_metrics: list[IterationMetrics] = []

        for instance_uuid, instance_requests in _group_by_instance(requests).items():
            cache = self.cache_factory(instance_uuid)
            instance_result = self._run_instance(
                instance_uuid=instance_uuid,
                requests=instance_requests,
                cache=cache,
                cache_event_sink=sink,
            )
            request_metrics.extend(instance_result.request_metrics)
            iteration_metrics.extend(instance_result.iteration_metrics)

        return BatchAwareReplayResult(
            request_metrics=tuple(
                sorted(request_metrics, key=lambda item: (item.arrival_time_ms, item.request_id))
            ),
            iteration_metrics=tuple(
                sorted(
                    iteration_metrics,
                    key=lambda item: (item.start_time_ms, item.instance_uuid, item.iteration_id),
                )
            ),
            cache_event_stats=sink.snapshot_stats(),
            cache_events=sink.snapshot_events(),
        )

    def _run_instance(
        self,
        *,
        instance_uuid: str,
        requests: list[SimulationRequest],
        cache: PrefixCache,
        cache_event_sink: CacheEventSink,
    ) -> BatchAwareReplayResult:
        if not requests:
            return BatchAwareReplayResult(request_metrics=(), iteration_metrics=())

        pending = sorted(requests, key=lambda item: (item.start_time_ms, item.request_id))
        pending_index = 0
        now_ms = pending[0].start_time_ms
        iteration_id = 0
        waiting = WaitingQueue()
        running: list[RequestState] = []
        states_by_id: dict[str, RequestState] = {}
        requests_by_id: dict[str, SimulationRequest] = {}
        lookup_by_id: dict[str, LookupMetrics] = {}
        request_metrics: list[BatchAwareRequestMetrics] = []
        iteration_metrics: list[IterationMetrics] = []

        while pending_index < len(pending) or waiting or running:
            pending_index = self._move_arrivals(
                pending=pending,
                pending_index=pending_index,
                now_ms=now_ms,
                waiting=waiting,
                states_by_id=states_by_id,
                requests_by_id=requests_by_id,
            )

            if not waiting and not running:
                now_ms = pending[pending_index].start_time_ms
                continue

            running = self._prepare_scheduler_frontier(
                waiting=waiting,
                running=running,
                cache=cache,
                now_ms=now_ms,
                lookup_by_id=lookup_by_id,
                requests_by_id=requests_by_id,
                request_metrics=request_metrics,
            )
            _drain_cache_events(cache=cache, sink=cache_event_sink)

            if not waiting and not running:
                if pending_index < len(pending):
                    now_ms = max(now_ms, pending[pending_index].start_time_ms)
                    continue
                break

            schedule_result = self.scheduler.schedule(
                instance_uuid=instance_uuid,
                iteration_id=iteration_id,
                start_time_ms=now_ms,
                waiting=waiting,
                running=running,
            )
            if schedule_result.is_empty:
                raise ValueError(
                    "Scheduler produced an empty batch with pending work; check "
                    "max_num_batched_tokens, max_num_seqs, and chunked prefill settings."
                )

            latency = self._estimate_latency(schedule_result.shape)
            finish_ms = now_ms + latency.duration_ms
            self._apply_schedule_result(
                schedule_result=schedule_result,
                latency=latency,
                finish_ms=finish_ms,
                cache=cache,
                states_by_id=states_by_id,
                requests_by_id=requests_by_id,
                lookup_by_id=lookup_by_id,
                request_metrics=request_metrics,
                iteration_metrics=iteration_metrics,
            )
            _drain_cache_events(cache=cache, sink=cache_event_sink)
            running = [state for state in running if state.status != RequestStatus.FINISHED]
            now_ms = finish_ms
            iteration_id += 1

        _drain_cache_events(cache=cache, sink=cache_event_sink)
        return BatchAwareReplayResult(
            request_metrics=tuple(request_metrics),
            iteration_metrics=tuple(iteration_metrics),
            cache_event_stats=cache_event_sink.snapshot_stats(),
            cache_events=cache_event_sink.snapshot_events(),
        )

    def _prepare_scheduler_frontier(
        self,
        *,
        waiting: WaitingQueue,
        running: list[RequestState],
        cache: PrefixCache,
        now_ms: float,
        lookup_by_id: dict[str, LookupMetrics],
        requests_by_id: dict[str, SimulationRequest],
        request_metrics: list[BatchAwareRequestMetrics],
    ) -> list[RequestState]:
        self._prepare_running(
            running=running,
            cache=cache,
            now_ms=now_ms,
            lookup_by_id=lookup_by_id,
        )
        self._finish_zero_miss_requests(
            states=running,
            now_ms=now_ms,
            requests_by_id=requests_by_id,
            lookup_by_id=lookup_by_id,
            metrics=request_metrics,
        )
        active_running = [state for state in running if state.status != RequestStatus.FINISHED]
        self._prepare_waiting_frontier(
            waiting=waiting,
            running=active_running,
            cache=cache,
            now_ms=now_ms,
            lookup_by_id=lookup_by_id,
            requests_by_id=requests_by_id,
            request_metrics=request_metrics,
        )
        return active_running

    def _move_arrivals(
        self,
        *,
        pending: list[SimulationRequest],
        pending_index: int,
        now_ms: float,
        waiting: WaitingQueue,
        states_by_id: dict[str, RequestState],
        requests_by_id: dict[str, SimulationRequest],
    ) -> int:
        while pending_index < len(pending) and pending[pending_index].start_time_ms <= now_ms:
            request = pending[pending_index]
            state = _state_from_request(request, arrival_seq=pending_index)
            waiting.append(state)
            states_by_id[state.request_id] = state
            requests_by_id[state.request_id] = request
            pending_index += 1
        return pending_index

    def _apply_schedule_result(
        self,
        *,
        schedule_result: ScheduleResult,
        latency: LatencyResult,
        finish_ms: float,
        cache: PrefixCache,
        states_by_id: dict[str, RequestState],
        requests_by_id: dict[str, SimulationRequest],
        lookup_by_id: dict[str, LookupMetrics],
        request_metrics: list[BatchAwareRequestMetrics],
        iteration_metrics: list[IterationMetrics],
    ) -> None:
        for scheduled_slice in schedule_result.shape.request_slices:
            state = states_by_id[scheduled_slice.request_id]
            state.apply_scheduled_tokens(
                scheduled_tokens=scheduled_slice.scheduled_prefill_tokens,
                finish_time_ms=finish_ms,
            )
            if state.status == RequestStatus.FINISHED:
                lookup_state = lookup_by_id[state.request_id]
                cache.materialize(
                    lookup_state.miss_blocks,
                    now_ms=finish_ms,
                    request_id=state.request_id,
                    instance_uuid=state.instance_uuid,
                )
                request_metrics.append(
                    build_request_metrics(
                        request=requests_by_id[state.request_id],
                        state=state,
                        lookup=lookup_state,
                    )
                )

        iteration_metrics.append(
            build_iteration_metrics(
                shape=schedule_result.shape,
                latency=latency,
                finish_time_ms=finish_ms,
            )
        )

    def _prepare_running(
        self,
        *,
        running: list[RequestState],
        cache: PrefixCache,
        now_ms: float,
        lookup_by_id: dict[str, LookupMetrics],
    ) -> None:
        for state in running:
            self._ensure_lookup(
                state=state,
                cache=cache,
                now_ms=now_ms,
                lookup_by_id=lookup_by_id,
            )

    def _prepare_waiting_frontier(
        self,
        *,
        waiting: WaitingQueue,
        running: list[RequestState],
        cache: PrefixCache,
        now_ms: float,
        lookup_by_id: dict[str, LookupMetrics],
        requests_by_id: dict[str, SimulationRequest],
        request_metrics: list[BatchAwareRequestMetrics],
    ) -> None:
        token_budget = self.scheduler.config.max_num_batched_tokens
        seq_budget = self.scheduler.config.max_num_seqs
        planned_slices = 0

        for state in running:
            if state.status != RequestStatus.RUNNING:
                continue
            planned_tokens = planned_prefill_tokens(self.scheduler.config, state, token_budget)
            if planned_tokens <= 0:
                continue
            token_budget -= planned_tokens
            planned_slices += 1
            if token_budget <= 0 or planned_slices >= seq_budget:
                return

        index = 0
        while index < len(waiting) and token_budget > 0 and planned_slices < seq_budget:
            state = waiting[index]
            self._ensure_lookup(
                state=state,
                cache=cache,
                now_ms=now_ms,
                lookup_by_id=lookup_by_id,
            )
            if state.remaining_prefill_tokens() == 0:
                waiting.pop(index)
                self._finish_zero_miss_request(
                    state=state,
                    now_ms=now_ms,
                    request=requests_by_id[state.request_id],
                    lookup=lookup_by_id[state.request_id],
                    metrics=request_metrics,
                )
                continue

            planned_tokens = planned_prefill_tokens(self.scheduler.config, state, token_budget)
            if planned_tokens <= 0:
                return

            token_budget -= planned_tokens
            planned_slices += 1
            index += 1

    def _ensure_lookup(
        self,
        *,
        state: RequestState,
        cache: PrefixCache,
        now_ms: float,
        lookup_by_id: dict[str, LookupMetrics],
    ) -> None:
        if state.cache_lookup_done:
            return

        lookup = cache.lookup_prefix(
            state.prompt_blocks,
            now_ms=now_ms,
            request_id=state.request_id,
            instance_uuid=state.instance_uuid,
        )
        lookup_by_id[state.request_id] = LookupMetrics.from_result(lookup)
        state.set_cache_lookup(
            cached_tokens=lookup.effective_hit_tokens,
            miss_tokens=lookup.miss_tokens,
        )

    def _finish_zero_miss_requests(
        self,
        *,
        states: list[RequestState],
        now_ms: float,
        requests_by_id: dict[str, SimulationRequest],
        lookup_by_id: dict[str, LookupMetrics],
        metrics: list[BatchAwareRequestMetrics],
    ) -> None:
        for state in states:
            if state.remaining_prefill_tokens() != 0:
                continue
            self._finish_zero_miss_request(
                state=state,
                now_ms=now_ms,
                request=requests_by_id[state.request_id],
                lookup=lookup_by_id[state.request_id],
                metrics=metrics,
            )

    @staticmethod
    def _finish_zero_miss_request(
        *,
        state: RequestState,
        now_ms: float,
        request: SimulationRequest,
        lookup: LookupMetrics,
        metrics: list[BatchAwareRequestMetrics],
    ) -> None:
        if state.status == RequestStatus.FINISHED:
            return
        if state.first_scheduled_time_ms is None:
            state.first_scheduled_time_ms = now_ms
        state.finish_time_ms = now_ms
        state.status = RequestStatus.FINISHED
        metrics.append(build_request_metrics(request=request, state=state, lookup=lookup))

    def _estimate_latency(self, shape: BatchShape) -> LatencyResult:
        key = ShapeKey.from_shape(
            backend=self.latency_backend.name,
            model_name=self.latency_backend.model_name,
            hardware_name=self.latency_backend.hardware_name,
            shape=shape,
        )
        return self.shape_memo.get_or_compute(
            key,
            lambda: self.latency_backend.estimate_iteration(shape),
        )


def _group_by_instance(
    requests: list[SimulationRequest],
) -> dict[str, list[SimulationRequest]]:
    grouped: dict[str, list[SimulationRequest]] = {}
    for request in sorted(requests, key=lambda item: (item.start_time_ms, item.request_id)):
        grouped.setdefault(request.instance_uuid, []).append(request)
    return grouped


def _default_cache_factory(instance_uuid: str) -> PrefixCache:
    return InfiniteHBMCache()


def _drain_cache_events(*, cache: PrefixCache, sink: CacheEventSink) -> None:
    sink.emit_many(cache.take_events())


def _state_from_request(request: SimulationRequest, arrival_seq: int) -> RequestState:
    return RequestState(
        request_id=request.request_id,
        tenant_id=request.tenant_id,
        instance_uuid=request.instance_uuid,
        arrival_time_ms=request.start_time_ms,
        prompt_tokens=request.prompt_tokens,
        prompt_blocks=request.prompt_blocks,
        model=request.model,
        tokenizer_profile=request.tokenizer_profile,
        arrival_seq=arrival_seq,
    )
