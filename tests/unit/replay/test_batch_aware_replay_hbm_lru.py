from datetime import datetime, timedelta

from infertwin.cache.event_sink import InMemoryCacheEventSink
from infertwin.cache.events import EVICT, LOOKUP_HIT, LOOKUP_MISS, MATERIALIZE
from infertwin.cache.hbm_lru import HBMCache
from infertwin.cache.materialization import FinishTimeMaterializationPolicy
from infertwin.instance.request import SimulationRequest
from infertwin.latency.formula import FormulaLatencyBackend
from infertwin.replay.event_loop import BatchAwareReplayEngine
from infertwin.request.block_hasher import build_prefix_blocks
from infertwin.scheduler.config import SchedulerConfig
from infertwin.scheduler.vllm_like import VllmLikeBatchScheduler


def test_finite_hbm_materialization_is_visible_only_after_finish_time() -> None:
    engine = _engine(max_num_batched_tokens=8, cache_capacity_blocks=4, prefill_token_ms=1.0)
    first = _request("r1", start_time_ms=0.0, token_ids=[1, 2, 3, 4])
    same_iteration = _request("r2", start_time_ms=0.0, token_ids=[1, 2, 3, 4])
    after_finish = _request("r3", start_time_ms=8.0, token_ids=[1, 2, 3, 4])

    result = engine.run([first, same_iteration, after_finish])

    metrics_by_id = {item.request_id: item for item in result.request_metrics}
    assert metrics_by_id["r1"].miss_tokens == 4
    assert metrics_by_id["r2"].miss_tokens == 4
    assert metrics_by_id["r2"].hbm_hit_tokens == 0
    assert metrics_by_id["r3"].hbm_hit_tokens == 0
    assert metrics_by_id["r3"].miss_tokens == 4
    assert result.cache_event_stats.lookup_hit_events == 1


def test_finite_hbm_eviction_prevents_future_prefix_hit() -> None:
    engine = _engine(max_num_batched_tokens=4, cache_capacity_blocks=1, prefill_token_ms=1.0)
    first = _request("r1", start_time_ms=0.0, token_ids=[1, 2, 3, 4])
    second = _request("r2", start_time_ms=10.0, token_ids=[5, 6, 7, 8])
    repeat_first = _request("r3", start_time_ms=20.0, token_ids=[1, 2, 3, 4])

    result = _run_with_events(engine, [first, second, repeat_first])

    metrics_by_id = {item.request_id: item for item in result.request_metrics}
    assert metrics_by_id["r1"].miss_tokens == 4
    assert metrics_by_id["r2"].miss_tokens == 4
    assert metrics_by_id["r3"].miss_tokens == 4
    assert metrics_by_id["r3"].hbm_hit_tokens == 0
    assert EVICT in [event.event_type for event in result.cache_events]


def test_empty_prompt_fast_finish_works_with_finite_hbm() -> None:
    engine = _engine(max_num_batched_tokens=4, cache_capacity_blocks=2, prefill_token_ms=1.0)
    empty = _request("r1", start_time_ms=10.0, token_ids=[])

    result = _run_with_events(engine, [empty])

    (metrics,) = result.request_metrics
    assert metrics.hbm_hit_tokens == 0
    assert metrics.miss_tokens == 0
    assert metrics.scheduled_iteration_count == 0
    assert metrics.finish_time_ms == 10.0
    assert result.iteration_metrics == ()


def test_finite_hbm_cache_is_isolated_by_instance() -> None:
    engine = _engine(max_num_batched_tokens=4, cache_capacity_blocks=2, prefill_token_ms=1.0)
    first = _request("r1", "instance-a", 0.0, [1, 2, 3, 4])
    same_prompt_other_instance = _request("r2", "instance-b", 10.0, [1, 2, 3, 4])

    result = engine.run([first, same_prompt_other_instance])

    metrics_by_id = {item.request_id: item for item in result.request_metrics}
    assert metrics_by_id["r1"].miss_tokens == 4
    assert metrics_by_id["r2"].miss_tokens == 4
    assert metrics_by_id["r2"].hbm_hit_tokens == 0


def test_finite_hbm_replay_emits_cache_events() -> None:
    engine = _engine(max_num_batched_tokens=4, cache_capacity_blocks=1, prefill_token_ms=1.0)
    first = _request("r1", start_time_ms=0.0, token_ids=[1, 2, 3, 4])
    repeat = _request("r2", start_time_ms=10.0, token_ids=[1, 2, 3, 4])

    result = _run_with_events(engine, [first, repeat])

    event_types = [event.event_type for event in result.cache_events]
    assert LOOKUP_MISS in event_types
    assert MATERIALIZE in event_types
    assert LOOKUP_HIT in event_types
    assert all(event.instance_uuid == "instance-a" for event in result.cache_events)
    assert {event.request_id for event in result.cache_events} == {"r1", "r2"}
    assert result.cache_event_stats.total_events == len(result.cache_events)
    assert result.cache_event_stats.lookup_miss_events > 0
    assert result.cache_event_stats.lookup_hit_events > 0


def test_default_batch_aware_replay_still_uses_infinite_hbm_without_events() -> None:
    scheduler = VllmLikeBatchScheduler(SchedulerConfig(max_num_batched_tokens=4, max_num_seqs=4))
    latency_backend = FormulaLatencyBackend(
        iteration_fixed_overhead_ms=0.0,
        iteration_prefill_token_ms=1.0,
        iteration_batch_overhead_ms=0.0,
        iteration_context_token_ms=0.0,
        model_name="glm-v5",
        hardware_name="local-dev",
    )
    engine = BatchAwareReplayEngine(scheduler=scheduler, latency_backend=latency_backend)

    result = engine.run(
        [
            _request("r1", start_time_ms=0.0, token_ids=[1, 2, 3, 4]),
            _request("r2", start_time_ms=10.0, token_ids=[1, 2, 3, 4]),
        ]
    )

    assert result.request_metrics[1].hbm_hit_tokens == 0
    assert result.request_metrics[1].miss_tokens == 4
    assert result.cache_events == ()
    assert result.cache_event_stats.total_events == 0


def test_default_finite_hbm_replay_drains_events_without_storing_them() -> None:
    engine = _engine(max_num_batched_tokens=4, cache_capacity_blocks=1, prefill_token_ms=1.0)
    first = _request("r1", start_time_ms=0.0, token_ids=[1, 2, 3, 4])
    repeat = _request("r2", start_time_ms=10.0, token_ids=[1, 2, 3, 4])

    result = engine.run([first, repeat])

    assert result.request_metrics[1].hbm_hit_tokens == 0
    assert result.request_metrics[1].miss_tokens == 4
    assert result.cache_events == ()
    assert result.cache_event_stats.total_events > 0
    assert result.cache_event_stats.lookup_hit_events > 0


def test_batch_aware_replay_uses_materialization_policy() -> None:
    policy = _RecordingMaterializationPolicy()
    engine = _engine(
        max_num_batched_tokens=4,
        cache_capacity_blocks=2,
        prefill_token_ms=1.0,
        materialization_policy=policy,
    )
    first = _request("r1", start_time_ms=0.0, token_ids=[1, 2, 3, 4])
    repeat = _request("r2", start_time_ms=10.0, token_ids=[1, 2, 3, 4])

    result = engine.run([first, repeat])

    assert policy.calls == [("r1", "instance-a", 4.0, 1)]
    assert result.request_metrics[1].hbm_hit_tokens == 0
    assert result.request_metrics[1].miss_tokens == 4


def _engine(
    *,
    max_num_batched_tokens: int,
    cache_capacity_blocks: int,
    max_num_seqs: int = 8,
    prefill_token_ms: float = 1.0,
    materialization_policy=None,
) -> BatchAwareReplayEngine:
    scheduler = VllmLikeBatchScheduler(
        SchedulerConfig(
            max_num_batched_tokens=max_num_batched_tokens,
            max_num_seqs=max_num_seqs,
            enable_chunked_prefill=True,
        )
    )
    latency_backend = FormulaLatencyBackend(
        iteration_fixed_overhead_ms=0.0,
        iteration_prefill_token_ms=prefill_token_ms,
        iteration_batch_overhead_ms=0.0,
        iteration_context_token_ms=0.0,
        model_name="glm-v5",
        hardware_name="local-dev",
    )
    return BatchAwareReplayEngine(
        scheduler=scheduler,
        latency_backend=latency_backend,
        cache_factory=lambda _instance_uuid: HBMCache(capacity_blocks=cache_capacity_blocks),
        materialization_policy=materialization_policy,
    )


def _run_with_events(
    engine: BatchAwareReplayEngine,
    requests: list[SimulationRequest],
):
    sink = InMemoryCacheEventSink()
    result = engine.run(requests, cache_event_sink=sink)
    assert result.cache_events == sink.snapshot_events()
    assert result.cache_event_stats == sink.stats
    assert result.cache_event_stats is not sink.stats
    return result


def _request(
    request_id: str,
    instance_uuid: str = "instance-a",
    start_time_ms: float = 0.0,
    token_ids: list[int] | None = None,
) -> SimulationRequest:
    if token_ids is None:
        token_ids = [1, 2, 3, 4]
    service_start_time = datetime.fromisoformat("2026-06-05 09:01:23") + timedelta(
        milliseconds=start_time_ms
    )
    blocks = build_prefix_blocks(
        token_ids=token_ids,
        block_size_tokens=4,
        model="glm-v5",
        tenant_id="tenant-a",
        kv_bytes_per_token=1,
    )
    return SimulationRequest(
        request_id=request_id,
        tenant_id="tenant-a",
        instance_uuid=instance_uuid,
        model="glm-v5",
        service_start_time=service_start_time,
        start_time_ms=start_time_ms,
        tokenizer_profile="glm-v5",
        prompt_tokens=len(token_ids),
        prompt_blocks=tuple(blocks),
        kv_bytes_per_token=1,
    )


class _RecordingMaterializationPolicy:
    name = "recording_finish_time"

    def __init__(self) -> None:
        self._delegate = FinishTimeMaterializationPolicy()
        self.calls: list[tuple[str, str, float, int]] = []

    def materialize_finished_request(
        self,
        *,
        cache,
        blocks,
        finish_time_ms: float,
        request_id: str,
        instance_uuid: str,
    ) -> None:
        self.calls.append((request_id, instance_uuid, finish_time_ms, len(blocks)))
        self._delegate.materialize_finished_request(
            cache=cache,
            blocks=blocks,
            finish_time_ms=finish_time_ms,
            request_id=request_id,
            instance_uuid=instance_uuid,
        )
