"""Batch-aware replay engine for fixed-routing, per-instance isolated replay."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from infertwin.cache.base import PrefixCache
from infertwin.cache.event_sink import CacheEventSink, StatsOnlyCacheEventSink
from infertwin.cache.infinite_hbm import InfiniteHBMCache
from infertwin.cache.materialization import (
    FinishTimeMaterializationPolicy,
    MaterializationResult,
    MaterializationPolicy,
    ProgressiveFullBlockMaterializationPolicy,
)
from infertwin.instance.request import SimulationRequest
from infertwin.latency.backend import BatchLatencyBackend
from infertwin.latency.memo import ShapeMemo
from infertwin.latency.schema import LatencyResult, ShapeKey
from infertwin.replay.kv_transfer import (
    KVTransferRequest,
    SharedLinkFIFOTransferQueue,
)
from infertwin.replay.metrics import (
    BatchAwareReplayResult,
    BatchAwareRequestMetrics,
    IterationMetrics,
    LookupMetrics,
    build_iteration_metrics,
    build_request_metrics,
    split_iteration_latency_contributions,
)
from infertwin.replay.timeline import (
    LEGACY_TIMELINE_MODE,
    PROGRESSIVE_TIMELINE_MODE,
    RequestTimelineState,
)
from infertwin.scheduler.batch_shape import BatchShape, ScheduledSlice
from infertwin.scheduler.planning import planned_prefill_tokens
from infertwin.scheduler.queue import WaitingQueue
from infertwin.scheduler.state import RequestState, RequestStatus
from infertwin.scheduler.vllm_like import ScheduleResult, VllmLikeBatchScheduler


class BatchAwareReplayEngine:
    """Replay requests with vLLM-like batching and per-instance prefix cache."""

    def __init__(
        self,
        *,
        scheduler: VllmLikeBatchScheduler,
        latency_backend: BatchLatencyBackend,
        shape_memo: ShapeMemo | None = None,
        cache_factory: Callable[[str], PrefixCache] | None = None,
        materialization_policy: MaterializationPolicy | None = None,
        timeline_mode: str = LEGACY_TIMELINE_MODE,
    ) -> None:
        _validate_timeline_mode(timeline_mode)
        self.scheduler = scheduler
        self.latency_backend = latency_backend
        self.shape_memo = shape_memo or ShapeMemo()
        self.cache_factory = cache_factory or _default_cache_factory
        self.timeline_mode = timeline_mode
        self.materialization_policy = materialization_policy or _default_materialization_policy(
            timeline_mode
        )
        _validate_materialization_policy(
            self.materialization_policy,
            timeline_mode=timeline_mode,
        )

    def run(
        self,
        requests: list[SimulationRequest],
        *,
        cache_event_sink: CacheEventSink | None = None,
    ) -> BatchAwareReplayResult:
        sink = cache_event_sink or StatsOnlyCacheEventSink()
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
        transfer_queue = SharedLinkFIFOTransferQueue(instance_uuid=instance_uuid)

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
            compute_wait = _record_iteration_compute_wait(
                waiting=waiting,
                running=running,
                scheduled_request_ids={
                    item.request_id for item in schedule_result.shape.request_slices
                },
                duration_ms=latency.duration_ms,
                timeline_mode=self.timeline_mode,
            )
            self._apply_schedule_result(
                schedule_result=schedule_result,
                latency=latency,
                finish_ms=finish_ms,
                compute_wait=compute_wait,
                cache=cache,
                states_by_id=states_by_id,
                requests_by_id=requests_by_id,
                lookup_by_id=lookup_by_id,
                request_metrics=request_metrics,
                iteration_metrics=iteration_metrics,
                transfer_queue=transfer_queue,
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
            requests_by_id=requests_by_id,
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
            state = _state_from_request(
                request,
                arrival_seq=pending_index,
                timeline_mode=self.timeline_mode,
            )
            _record_initial_compute_wait(state=state, now_ms=now_ms)
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
        compute_wait: ComputeWaitAccounting,
        cache: PrefixCache,
        states_by_id: dict[str, RequestState],
        requests_by_id: dict[str, SimulationRequest],
        lookup_by_id: dict[str, LookupMetrics],
        request_metrics: list[BatchAwareRequestMetrics],
        iteration_metrics: list[IterationMetrics],
        transfer_queue: SharedLinkFIFOTransferQueue,
    ) -> None:
        latency_contributions = split_iteration_latency_contributions(
            shape=schedule_result.shape,
            latency=latency,
        )
        kv_load_timing_count = 0
        kv_load_wait_ms = 0.0
        kv_transfer_queue_depth_max = 0
        progressive_materialized_blocks = 0
        progressive_materialized_tokens = 0
        for scheduled_slice in schedule_result.shape.request_slices:
            state = states_by_id[scheduled_slice.request_id]
            lookup_state = lookup_by_id[state.request_id]
            contribution = latency_contributions[scheduled_slice.request_id]
            kv_load_timing = _record_scheduled_kv_load_timing(
                state=state,
                scheduled_slice=scheduled_slice,
                kv_load_ms=contribution.kv_load_ms,
                timeline_mode=self.timeline_mode,
                transfer_queue=transfer_queue,
                ready_time_ms=schedule_result.shape.start_time_ms,
            )
            kv_load_timing_count += kv_load_timing.waiting_for_kv_load_count
            kv_load_wait_ms += kv_load_timing.kv_load_wait_ms
            kv_transfer_queue_depth_max = max(
                kv_transfer_queue_depth_max,
                kv_load_timing.kv_transfer_queue_depth_max,
            )
            state.record_latency_contribution(
                prefill_compute_ms=contribution.prefill_compute_ms,
                kv_load_ms=contribution.kv_load_ms,
                queue_ms=contribution.queue_ms,
            )
            if scheduled_slice.scheduled_prefill_tokens == 0:
                state.apply_load_only_iteration(finish_time_ms=finish_ms)
            else:
                if self.timeline_mode == PROGRESSIVE_TIMELINE_MODE:
                    state.timeline_state = RequestTimelineState.RUNNING_CHUNK
                state.apply_scheduled_tokens(
                    scheduled_tokens=scheduled_slice.scheduled_prefill_tokens,
                    finish_time_ms=finish_ms,
                )
                result = self._materialize_scheduled_chunk(
                    state=state,
                    scheduled_slice=scheduled_slice,
                    lookup_state=lookup_state,
                    cache=cache,
                    finish_ms=finish_ms,
                )
                progressive_materialized_blocks += result.block_count
                progressive_materialized_tokens += result.token_count
            if (
                self.timeline_mode == PROGRESSIVE_TIMELINE_MODE
                and state.status != RequestStatus.FINISHED
            ):
                state.timeline_state = RequestTimelineState.WAITING_FOR_COMPUTE
            if state.status == RequestStatus.FINISHED:
                if lookup_state.materialization_blocks:
                    result = self._materialize_finished_request(
                        state=state,
                        lookup_state=lookup_state,
                        cache=cache,
                        finish_ms=finish_ms,
                    )
                    progressive_materialized_blocks += result.block_count
                    progressive_materialized_tokens += result.token_count
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
                timeline_mode=self.timeline_mode,
                waiting_for_compute_count=compute_wait.waiting_for_compute_count,
                waiting_for_kv_load_count=kv_load_timing_count,
                compute_wait_ms=compute_wait.compute_wait_ms,
                kv_load_wait_ms=kv_load_wait_ms,
                kv_transfer_queue_depth_max=kv_transfer_queue_depth_max,
                progressive_materialized_blocks=progressive_materialized_blocks,
                progressive_materialized_tokens=progressive_materialized_tokens,
            )
        )

    def _materialize_scheduled_chunk(
        self,
        *,
        state: RequestState,
        scheduled_slice: ScheduledSlice,
        lookup_state: LookupMetrics,
        cache: PrefixCache,
        finish_ms: float,
    ) -> MaterializationResult:
        if self.timeline_mode != PROGRESSIVE_TIMELINE_MODE:
            return MaterializationResult()
        result = self.materialization_policy.materialize_scheduled_chunk(
            cache=cache,
            materialization_blocks=lookup_state.materialization_blocks,
            prompt_blocks=state.prompt_blocks,
            effective_block_size=state.effective_block_size,
            computed_tokens_before=scheduled_slice.computed_tokens_before,
            computed_tokens_after=scheduled_slice.computed_tokens_after,
            chunk_finish_time_ms=finish_ms,
            request_id=state.request_id,
            instance_uuid=state.instance_uuid,
            already_materialized_block_keys=frozenset(
                state.progressive_materialized_block_keys
            ),
        )
        return _record_progressive_result(state=state, result=result)

    def _materialize_finished_request(
        self,
        *,
        state: RequestState,
        lookup_state: LookupMetrics,
        cache: PrefixCache,
        finish_ms: float,
    ) -> MaterializationResult:
        if self.timeline_mode == PROGRESSIVE_TIMELINE_MODE:
            result = self.materialization_policy.materialize_finished_request(
                cache=cache,
                blocks=lookup_state.materialization_blocks,
                finish_time_ms=finish_ms,
                request_id=state.request_id,
                instance_uuid=state.instance_uuid,
                prompt_blocks=state.prompt_blocks,
                effective_block_size=state.effective_block_size,
                already_materialized_block_keys=frozenset(
                    state.progressive_materialized_block_keys
                ),
            )
            return _record_progressive_result(state=state, result=result)

        self.materialization_policy.materialize_finished_request(
            cache=cache,
            blocks=lookup_state.materialization_blocks,
            finish_time_ms=finish_ms,
            request_id=state.request_id,
            instance_uuid=state.instance_uuid,
        )
        return MaterializationResult()

    def _prepare_running(
        self,
        *,
        running: list[RequestState],
        cache: PrefixCache,
        now_ms: float,
        lookup_by_id: dict[str, LookupMetrics],
        requests_by_id: dict[str, SimulationRequest],
    ) -> None:
        for state in running:
            self._ensure_lookup(
                state=state,
                cache=cache,
                now_ms=now_ms,
                lookup_by_id=lookup_by_id,
                request=requests_by_id[state.request_id],
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
                if state.remaining_prefill_tokens() == 0 and state.has_pending_kv_load():
                    planned_slices += 1
                    if planned_slices >= seq_budget:
                        return
                continue
            token_budget -= planned_tokens
            planned_slices += 1
            if token_budget <= 0 or planned_slices >= seq_budget:
                return

        index = 0
        while index < len(waiting) and planned_slices < seq_budget:
            state = waiting[index]
            self._ensure_lookup(
                state=state,
                cache=cache,
                now_ms=now_ms,
                lookup_by_id=lookup_by_id,
                request=requests_by_id[state.request_id],
            )
            if state.remaining_prefill_tokens() == 0:
                if state.has_pending_kv_load():
                    planned_slices += 1
                    index += 1
                    continue
                waiting.pop(index)
                self._finish_zero_miss_request(
                    state=state,
                    now_ms=now_ms,
                    request=requests_by_id[state.request_id],
                    lookup=lookup_by_id[state.request_id],
                    metrics=request_metrics,
                )
                continue

            if token_budget <= 0:
                return
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
        request: SimulationRequest,
    ) -> None:
        if state.cache_lookup_done:
            return

        lookup = cache.lookup_prefix(
            state.prompt_blocks,
            now_ms=now_ms,
            request_id=state.request_id,
            instance_uuid=state.instance_uuid,
        )
        lookup_metrics = LookupMetrics.from_result(lookup, request=request)
        lookup_by_id[state.request_id] = lookup_metrics
        state.set_cache_lookup(
            cached_tokens=lookup_metrics.effective_hit_tokens,
            miss_tokens=lookup_metrics.miss_tokens,
            kv_load_tokens=lookup_metrics.ddr_hit_tokens,
            kv_load_bytes=lookup_metrics.ddr_hit_bytes,
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
            if state.has_pending_kv_load():
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
        state.timeline_state = RequestTimelineState.FINISHED
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


@dataclass(frozen=True, slots=True)
class ComputeWaitAccounting:
    waiting_for_compute_count: int = 0
    compute_wait_ms: float = 0.0


@dataclass(frozen=True, slots=True)
class KVLoadTimingAccounting:
    waiting_for_kv_load_count: int = 0
    kv_load_wait_ms: float = 0.0
    kv_transfer_queue_depth_max: int = 0


def _record_initial_compute_wait(*, state: RequestState, now_ms: float) -> None:
    if state.timeline_mode != PROGRESSIVE_TIMELINE_MODE:
        return
    if now_ms < state.arrival_time_ms:
        raise ValueError("now_ms cannot be earlier than request arrival time")
    state.record_compute_wait(now_ms - state.arrival_time_ms)


def _record_iteration_compute_wait(
    *,
    waiting: WaitingQueue,
    running: list[RequestState],
    scheduled_request_ids: set[str],
    duration_ms: float,
    timeline_mode: str,
) -> ComputeWaitAccounting:
    if duration_ms < 0:
        raise ValueError("iteration duration must be non-negative")
    if timeline_mode != PROGRESSIVE_TIMELINE_MODE:
        return ComputeWaitAccounting()

    waiting_for_compute_count = 0
    compute_wait_ms = 0.0
    seen_request_ids: set[str] = set()
    active_states = list(waiting)
    active_states.extend(running)
    for state in active_states:
        if state.request_id in seen_request_ids:
            continue
        seen_request_ids.add(state.request_id)
        if state.status == RequestStatus.FINISHED:
            continue
        if state.request_id in scheduled_request_ids:
            continue
        state.record_compute_wait(duration_ms)
        waiting_for_compute_count += 1
        compute_wait_ms += duration_ms

    return ComputeWaitAccounting(
        waiting_for_compute_count=waiting_for_compute_count,
        compute_wait_ms=compute_wait_ms,
    )


def _record_scheduled_kv_load_timing(
    *,
    state: RequestState,
    scheduled_slice: ScheduledSlice,
    kv_load_ms: float,
    timeline_mode: str,
    transfer_queue: SharedLinkFIFOTransferQueue,
    ready_time_ms: float,
) -> KVLoadTimingAccounting:
    if kv_load_ms < 0:
        raise ValueError("KV load wait duration must be non-negative")
    if timeline_mode != PROGRESSIVE_TIMELINE_MODE:
        return KVLoadTimingAccounting()
    if scheduled_slice.kv_load_tokens == 0 and scheduled_slice.kv_load_bytes == 0:
        return KVLoadTimingAccounting()

    transfer = transfer_queue.submit(
        KVTransferRequest(
            request_id=state.request_id,
            instance_uuid=state.instance_uuid,
            ready_time_ms=ready_time_ms,
            transfer_ms=kv_load_ms,
            kv_load_tokens=scheduled_slice.kv_load_tokens,
            kv_load_bytes=scheduled_slice.kv_load_bytes,
        )
    )
    state.timeline_state = RequestTimelineState.WAITING_FOR_KV_LOAD
    state.record_kv_load_event(transfer.elapsed_ms)
    return KVLoadTimingAccounting(
        waiting_for_kv_load_count=1,
        kv_load_wait_ms=transfer.elapsed_ms,
        kv_transfer_queue_depth_max=transfer.queue_depth_after,
    )


def _state_from_request(
    request: SimulationRequest,
    arrival_seq: int,
    timeline_mode: str = LEGACY_TIMELINE_MODE,
) -> RequestState:
        return RequestState(
        request_id=request.request_id,
        tenant_id=request.tenant_id,
        instance_uuid=request.instance_uuid,
        arrival_time_ms=request.start_time_ms,
        prompt_tokens=request.prompt_tokens,
        prompt_blocks=request.prompt_blocks,
        effective_block_size=request.effective_block_size or 0,
        model=request.model,
        tokenizer_profile=request.tokenizer_profile,
        arrival_seq=arrival_seq,
        timeline_mode=timeline_mode,
    )


def _validate_timeline_mode(timeline_mode: str) -> None:
    if timeline_mode not in {LEGACY_TIMELINE_MODE, PROGRESSIVE_TIMELINE_MODE}:
        raise ValueError(f"unsupported timeline_mode {timeline_mode!r}")


def _default_materialization_policy(timeline_mode: str) -> MaterializationPolicy:
    if timeline_mode == PROGRESSIVE_TIMELINE_MODE:
        return ProgressiveFullBlockMaterializationPolicy()
    return FinishTimeMaterializationPolicy()


def _validate_materialization_policy(
    policy: MaterializationPolicy,
    *,
    timeline_mode: str,
) -> None:
    if timeline_mode != PROGRESSIVE_TIMELINE_MODE:
        return
    if not getattr(policy, "supports_progressive_chunks", False):
        raise ValueError(
            "progressive timeline mode requires a materialization policy that "
            "supports progressive chunks"
        )


def _record_progressive_result(
    *,
    state: RequestState,
    result: MaterializationResult,
) -> MaterializationResult:
    recorded = state.record_progressive_materialization(result.materialized_blocks)
    if recorded == result.materialized_blocks:
        return result
    return MaterializationResult(materialized_blocks=recorded)
