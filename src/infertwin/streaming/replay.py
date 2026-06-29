"""Streaming batch-aware replay engine."""

from __future__ import annotations

from infertwin.cache.base import PrefixCache
from infertwin.cache.event_sink import CacheEventSink, StatsOnlyCacheEventSink
from infertwin.replay.event_loop import (
    BatchAwareReplayEngine,
    _drain_cache_events,
    _record_initial_compute_wait,
    _record_iteration_compute_wait,
    _state_from_request,
)
from infertwin.replay.kv_transfer import SharedLinkFIFOTransferQueue
from infertwin.replay.metrics import (
    BatchAwareRequestMetrics,
    IterationMetrics,
    LookupMetrics,
)
from infertwin.scheduler.queue import WaitingQueue
from infertwin.scheduler.state import RequestState, RequestStatus
from infertwin.streaming.metrics import ReplayMetricSink, StreamingReplayStats
from infertwin.streaming.source import RequestSource


class StreamingBatchAwareReplayEngine(BatchAwareReplayEngine):
    """Replay one instance from a streaming request source."""

    def run_instance_stream(
        self,
        *,
        instance_uuid: str,
        request_source: RequestSource,
        cache: PrefixCache,
        metric_sink: ReplayMetricSink,
        cache_event_sink: CacheEventSink | None = None,
    ) -> StreamingReplayStats:
        sink = cache_event_sink or StatsOnlyCacheEventSink()
        next_request = request_source.peek()
        if next_request is None:
            return StreamingReplayStats(
                emitted_request_count=0,
                emitted_iteration_count=0,
                max_active_requests=0,
                final_active_requests=0,
            )

        now_ms = next_request.start_time_ms
        iteration_id = 0
        arrival_seq = 0
        max_active_requests = 0
        emitted_request_count = 0
        emitted_iteration_count = 0
        waiting = WaitingQueue()
        running: list[RequestState] = []
        states_by_id: dict[str, RequestState] = {}
        requests_by_id = {}
        lookup_by_id: dict[str, LookupMetrics] = {}
        transfer_queue = SharedLinkFIFOTransferQueue(instance_uuid=instance_uuid)

        while request_source.peek() is not None or waiting or running:
            arrival_seq = _move_arrivals_from_source(
                instance_uuid=instance_uuid,
                request_source=request_source,
                now_ms=now_ms,
                arrival_seq=arrival_seq,
                waiting=waiting,
                states_by_id=states_by_id,
                requests_by_id=requests_by_id,
                timeline_mode=self.timeline_mode,
            )
            max_active_requests = max(max_active_requests, len(states_by_id))

            if not waiting and not running:
                next_request = request_source.peek()
                if next_request is None:
                    break
                _require_instance(next_request.instance_uuid, expected=instance_uuid)
                now_ms = next_request.start_time_ms
                continue

            request_metrics: list[BatchAwareRequestMetrics] = []
            running = self._prepare_scheduler_frontier(
                waiting=waiting,
                running=running,
                cache=cache,
                now_ms=now_ms,
                lookup_by_id=lookup_by_id,
                requests_by_id=requests_by_id,
                request_metrics=request_metrics,
            )
            emitted_request_count += _emit_request_metrics(
                request_metrics,
                sink=metric_sink,
                states_by_id=states_by_id,
                requests_by_id=requests_by_id,
                lookup_by_id=lookup_by_id,
            )
            _drain_cache_events(cache=cache, sink=sink)

            if not waiting and not running:
                next_request = request_source.peek()
                if next_request is None:
                    break
                _require_instance(next_request.instance_uuid, expected=instance_uuid)
                now_ms = max(now_ms, next_request.start_time_ms)
                continue

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
            request_metrics = []
            iteration_metrics: list[IterationMetrics] = []
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
            emitted_request_count += _emit_request_metrics(
                request_metrics,
                sink=metric_sink,
                states_by_id=states_by_id,
                requests_by_id=requests_by_id,
                lookup_by_id=lookup_by_id,
            )
            emitted_iteration_count += _emit_iteration_metrics(
                iteration_metrics,
                sink=metric_sink,
            )
            _drain_cache_events(cache=cache, sink=sink)
            running = [state for state in running if state.status != RequestStatus.FINISHED]
            now_ms = finish_ms
            iteration_id += 1

        _drain_cache_events(cache=cache, sink=sink)
        return StreamingReplayStats(
            emitted_request_count=emitted_request_count,
            emitted_iteration_count=emitted_iteration_count,
            max_active_requests=max_active_requests,
            final_active_requests=len(states_by_id),
        )


def _move_arrivals_from_source(
    *,
    instance_uuid: str,
    request_source: RequestSource,
    now_ms: float,
    arrival_seq: int,
    waiting: WaitingQueue,
    states_by_id: dict[str, RequestState],
    requests_by_id: dict,
    timeline_mode: str,
) -> int:
    while True:
        request = request_source.peek()
        if request is None or request.start_time_ms > now_ms:
            return arrival_seq
        request = request_source.pop()
        _require_instance(request.instance_uuid, expected=instance_uuid)
        if request.request_id in states_by_id:
            raise ValueError(f"duplicate active request_id {request.request_id!r}")
        state = _state_from_request(
            request,
            arrival_seq=arrival_seq,
            timeline_mode=timeline_mode,
        )
        _record_initial_compute_wait(state=state, now_ms=now_ms)
        waiting.append(state)
        states_by_id[state.request_id] = state
        requests_by_id[state.request_id] = request
        arrival_seq += 1


def _emit_request_metrics(
    metrics: list[BatchAwareRequestMetrics],
    *,
    sink: ReplayMetricSink,
    states_by_id: dict[str, RequestState],
    requests_by_id: dict,
    lookup_by_id: dict[str, LookupMetrics],
) -> int:
    for metric in metrics:
        sink.on_request(metric)
        states_by_id.pop(metric.request_id, None)
        requests_by_id.pop(metric.request_id, None)
        lookup_by_id.pop(metric.request_id, None)
    return len(metrics)


def _emit_iteration_metrics(
    metrics: list[IterationMetrics],
    *,
    sink: ReplayMetricSink,
) -> int:
    for metric in metrics:
        sink.on_iteration(metric)
    return len(metrics)


def _require_instance(instance_uuid: str, *, expected: str) -> None:
    if instance_uuid != expected:
        raise ValueError(
            f"request source for instance {expected!r} yielded request for "
            f"instance {instance_uuid!r}"
        )
