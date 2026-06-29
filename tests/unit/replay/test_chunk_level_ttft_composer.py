from datetime import datetime, timedelta

from infertwin.cache.cache_block_conversion import CacheBlockConversionResult
from infertwin.cache.results import PrefixLookupResult
from infertwin.instance.request import SimulationRequest
from infertwin.latency.fitted_ttft import FittedTTFTLatencyBackend
from infertwin.latency.kv_load import TokenLinearKVLoadLatencyComponent
from infertwin.latency.profile import ServingLatencyProfile
from infertwin.replay.event_loop import BatchAwareReplayEngine
from infertwin.replay.timeline import (
    CHUNK_TTFT_GRANULARITY,
    ITERATION_TTFT_GRANULARITY,
    LEGACY_TIMELINE_MODE,
    PROGRESSIVE_TIMELINE_MODE,
)
from infertwin.request.block_hasher import PrefixBlock, build_prefix_blocks
from infertwin.scheduler.config import SchedulerConfig
from infertwin.scheduler.vllm_like import VllmLikeBatchScheduler
from infertwin.streaming.metrics import InMemoryReplayMetricSink
from infertwin.streaming.replay import StreamingBatchAwareReplayEngine
from infertwin.streaming.source import ListRequestSource


def test_legacy_mode_keeps_iteration_granularity_and_zero_counts() -> None:
    request = _request("r1", token_ids=list(range(8)), block_size_tokens=4)

    result = _engine(max_num_batched_tokens=4).run([request])

    (metric,) = result.request_metrics
    assert metric.timeline_mode == LEGACY_TIMELINE_MODE
    assert metric.ttft_granularity == ITERATION_TTFT_GRANULARITY
    assert metric.chunk_count == 0
    assert metric.load_event_count == 0
    assert metric.unattributed_ttft_ms == 0.0
    assert [item.scheduled_chunk_count for item in result.iteration_metrics] == [0, 0]


def test_progressive_multi_chunk_request_composes_ttft_without_residual() -> None:
    request = _request("r1", token_ids=list(range(12)), block_size_tokens=4)

    result = _engine(
        max_num_batched_tokens=4,
        timeline_mode=PROGRESSIVE_TIMELINE_MODE,
    ).run([request])

    (metric,) = result.request_metrics
    assert metric.timeline_mode == PROGRESSIVE_TIMELINE_MODE
    assert metric.ttft_granularity == CHUNK_TTFT_GRANULARITY
    assert metric.chunk_count == 3
    assert metric.load_event_count == 0
    assert metric.prefill_compute_ms == 12.0
    assert metric.uncached_prefill_compute_ms == 12.0
    assert metric.compute_wait_ms == 0.0
    assert metric.kv_load_wait_ms == 0.0
    assert metric.unattributed_ttft_ms == 0.0
    assert metric.ttft_ms == 12.0
    assert [item.scheduled_chunk_count for item in result.iteration_metrics] == [1, 1, 1]


def test_progressive_ddr_only_load_request_has_load_event_without_chunk() -> None:
    request = _ddr_only_request("r1")
    cache = _LookupMapCache(_ddr_lookups(request))

    result = _engine(
        cache=cache,
        max_num_batched_tokens=4,
        timeline_mode=PROGRESSIVE_TIMELINE_MODE,
    ).run([request])

    (metric,) = result.request_metrics
    (iteration,) = result.iteration_metrics
    assert metric.chunk_count == 0
    assert metric.load_event_count == 1
    assert metric.kv_load_wait_ms == 2.0
    assert metric.unattributed_ttft_ms == 0.0
    assert metric.ttft_ms == 2.0
    assert iteration.scheduled_chunk_count == 0


def test_progressive_same_iteration_ddr_loads_expose_unattributed_ttft() -> None:
    first = _ddr_only_request("r1")
    second = _ddr_only_request("r2")
    cache = _LookupMapCache(_ddr_lookups(first, second))

    result = _engine(
        cache=cache,
        max_num_batched_tokens=4,
        timeline_mode=PROGRESSIVE_TIMELINE_MODE,
    ).run([first, second])

    metrics_by_id = {item.request_id: item for item in result.request_metrics}
    assert metrics_by_id["r1"].kv_load_wait_ms == 2.0
    assert metrics_by_id["r1"].unattributed_ttft_ms == 2.0
    assert metrics_by_id["r1"].ttft_ms == 4.0
    assert metrics_by_id["r2"].kv_load_wait_ms == 4.0
    assert metrics_by_id["r2"].unattributed_ttft_ms == 0.0
    assert metrics_by_id["r2"].ttft_ms == 4.0
    assert [item.load_event_count for item in result.request_metrics] == [1, 1]
    assert result.iteration_metrics[0].kv_transfer_queue_depth_max == 2


def test_progressive_compute_wait_kv_load_and_prefill_composition() -> None:
    first = _request("r1", token_ids=list(range(4)), block_size_tokens=4)
    second = _request("r2", token_ids=list(range(8)), block_size_tokens=4)
    cache = _LookupMapCache(
        {
            "r1": PrefixLookupResult(
                hbm_hit_blocks=(),
                ddr_hit_blocks=(),
                miss_blocks=first.prompt_blocks,
            ),
            "r2": PrefixLookupResult(
                hbm_hit_blocks=(),
                ddr_hit_blocks=(second.prompt_blocks[0],),
                miss_blocks=(second.prompt_blocks[1],),
            ),
        }
    )

    result = _engine(
        cache=cache,
        max_num_batched_tokens=4,
        max_num_seqs=1,
        timeline_mode=PROGRESSIVE_TIMELINE_MODE,
    ).run([first, second])

    metrics_by_id = {item.request_id: item for item in result.request_metrics}
    second_metric = metrics_by_id["r2"]
    assert second_metric.compute_wait_ms == 4.0
    assert second_metric.kv_load_wait_ms == 2.0
    assert second_metric.uncached_prefill_compute_ms == 4.0
    assert second_metric.unattributed_ttft_ms == 0.0
    assert second_metric.chunk_count == 1
    assert second_metric.load_event_count == 1
    assert second_metric.ttft_ms == 10.0


def test_streaming_replay_matches_list_replay_for_ttft_composition() -> None:
    first = _ddr_only_request("r1")
    second = _ddr_only_request("r2")
    lookups = _ddr_lookups(first, second)
    requests = [first, second]
    list_result = _engine(
        cache=_LookupMapCache(lookups),
        max_num_batched_tokens=4,
        timeline_mode=PROGRESSIVE_TIMELINE_MODE,
    ).run(requests)
    sink = InMemoryReplayMetricSink()

    _streaming_engine(timeline_mode=PROGRESSIVE_TIMELINE_MODE).run_instance_stream(
        instance_uuid="instance-a",
        request_source=ListRequestSource(requests),
        cache=_LookupMapCache(lookups),
        metric_sink=sink,
    )

    assert sink.request_metrics == list_result.request_metrics
    assert sink.iteration_metrics == list_result.iteration_metrics


def _engine(
    *,
    max_num_batched_tokens: int,
    max_num_seqs: int = 4,
    cache: "_LookupMapCache | None" = None,
    timeline_mode: str = LEGACY_TIMELINE_MODE,
) -> BatchAwareReplayEngine:
    kwargs = {}
    if cache is not None:
        kwargs["cache_factory"] = lambda _instance_uuid: cache
    return BatchAwareReplayEngine(
        scheduler=_scheduler(
            max_num_batched_tokens=max_num_batched_tokens,
            max_num_seqs=max_num_seqs,
        ),
        latency_backend=_latency_backend(),
        timeline_mode=timeline_mode,
        **kwargs,
    )


def _streaming_engine(
    *,
    timeline_mode: str = LEGACY_TIMELINE_MODE,
) -> StreamingBatchAwareReplayEngine:
    return StreamingBatchAwareReplayEngine(
        scheduler=_scheduler(max_num_batched_tokens=4, max_num_seqs=4),
        latency_backend=_latency_backend(),
        timeline_mode=timeline_mode,
    )


def _scheduler(
    *,
    max_num_batched_tokens: int,
    max_num_seqs: int,
) -> VllmLikeBatchScheduler:
    return VllmLikeBatchScheduler(
        SchedulerConfig(
            max_num_batched_tokens=max_num_batched_tokens,
            max_num_seqs=max_num_seqs,
            enable_chunked_prefill=True,
        )
    )


def _latency_backend() -> ServingLatencyProfile:
    return ServingLatencyProfile(
        profile="unit-test-serving",
        ttft_backend=FittedTTFTLatencyBackend(
            intercept_ms=0.0,
            ms_per_uncached_token=1.0,
            model_name="glm-v5",
            hardware_name="local-dev",
            profile="unit-test-ttft",
        ),
        kv_load_component=TokenLinearKVLoadLatencyComponent(
            ddr_ms_per_cached_token=0.5,
        ),
    )


def _ddr_only_request(request_id: str) -> SimulationRequest:
    return _request(
        request_id,
        token_ids=list(range(4)),
        block_size_tokens=4,
        block_conversion_result=_full_prompt_cache_conversion(prompt_tokens=4, block_size=4),
    )


def _request(
    request_id: str,
    *,
    token_ids: list[int],
    block_size_tokens: int,
    block_conversion_result: CacheBlockConversionResult | None = None,
) -> SimulationRequest:
    service_start_time = datetime.fromisoformat("2026-06-05 09:01:23") + timedelta(
        milliseconds=0.0
    )
    blocks = build_prefix_blocks(
        token_ids=token_ids,
        block_size_tokens=block_size_tokens,
        model="glm-v5",
        tenant_id="tenant-a",
        kv_bytes_per_token=10,
    )
    return SimulationRequest(
        request_id=request_id,
        tenant_id="tenant-a",
        instance_uuid="instance-a",
        model="glm-v5",
        service_start_time=service_start_time,
        start_time_ms=0.0,
        tokenizer_profile="glm-v5",
        prompt_tokens=len(token_ids),
        prompt_blocks=tuple(blocks),
        kv_bytes_per_token=10,
        requested_block_size=block_size_tokens,
        runtime_block_size=block_size_tokens,
        effective_block_size=block_size_tokens,
        block_conversion_result=block_conversion_result,
    )


def _full_prompt_cache_conversion(
    *,
    prompt_tokens: int,
    block_size: int,
) -> CacheBlockConversionResult:
    cached_blocks = prompt_tokens // block_size
    return CacheBlockConversionResult(
        requested_block_size=block_size,
        runtime_block_size=block_size,
        effective_block_size=block_size,
        max_cache_hit_length=prompt_tokens,
        max_matchable_blocks=cached_blocks,
        matched_blocks=cached_blocks,
        speculative_drop_blocks=0,
        cached_blocks=cached_blocks,
        cached_tokens=cached_blocks * block_size,
    )


def _ddr_lookups(
    *requests: SimulationRequest,
) -> dict[str, PrefixLookupResult]:
    return {
        request.request_id: PrefixLookupResult(
            hbm_hit_blocks=(),
            ddr_hit_blocks=(request.prompt_blocks[0],),
            miss_blocks=(),
        )
        for request in requests
    }


class _LookupMapCache:
    def __init__(self, lookups_by_request_id: dict[str, PrefixLookupResult]) -> None:
        self._lookups_by_request_id = lookups_by_request_id

    @property
    def resident_blocks(self) -> int:
        return 0

    def contains(self, block_key: str) -> bool:
        return False

    def lookup_prefix(
        self,
        blocks: tuple[PrefixBlock, ...],
        now_ms: float,
        request_id: str = "",
        instance_uuid: str = "",
    ) -> PrefixLookupResult:
        return self._lookups_by_request_id[request_id]

    def materialize(
        self,
        blocks: tuple[PrefixBlock, ...],
        now_ms: float,
        request_id: str = "",
        instance_uuid: str = "",
        reason: str = "finish_time_materialization",
    ) -> None:
        return None

    def take_events(self) -> tuple[object, ...]:
        return ()
