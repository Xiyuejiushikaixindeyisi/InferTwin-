from datetime import datetime, timedelta

from infertwin.cache.cache_block_conversion import CacheBlockConversionResult
from infertwin.cache.results import PrefixLookupResult
from infertwin.instance.request import SimulationRequest
from infertwin.latency.fitted_ttft import FittedTTFTLatencyBackend
from infertwin.latency.kv_load import TokenLinearKVLoadLatencyComponent
from infertwin.latency.profile import ServingLatencyProfile
from infertwin.replay.event_loop import BatchAwareReplayEngine
from infertwin.request.block_hasher import PrefixBlock, build_prefix_blocks
from infertwin.scheduler.config import SchedulerConfig
from infertwin.scheduler.vllm_like import VllmLikeBatchScheduler


def test_replay_charges_kv_load_on_first_ddr_hit_iteration() -> None:
    request = _request("r1", token_ids=list(range(8)), block_size_tokens=4)
    cache = _LookupMapCache(
        {
            "r1": PrefixLookupResult(
                hbm_hit_blocks=(),
                ddr_hit_blocks=(request.prompt_blocks[0],),
                miss_blocks=(request.prompt_blocks[1],),
            )
        }
    )

    result = _engine(cache=cache, max_num_batched_tokens=4).run([request])

    (request_metric,) = result.request_metrics
    (iteration_metric,) = result.iteration_metrics
    assert request_metric.ddr_hit_tokens == 4
    assert request_metric.kv_load_tokens == 4
    assert request_metric.kv_load_bytes == 40
    assert request_metric.kv_load_ms == 2.0
    assert request_metric.prefill_compute_ms == 4.0
    assert request_metric.miss_tokens == 4
    assert request_metric.ttft_ms == 6.0
    assert iteration_metric.kv_load_tokens == 4
    assert iteration_metric.kv_load_bytes == 40
    assert iteration_metric.kv_load_request_count == 1
    assert iteration_metric.kv_load_ms == 2.0
    assert iteration_metric.prefill_compute_ms == 4.0
    assert iteration_metric.duration_ms == 6.0
    assert "kvload_tokens=4" in iteration_metric.shape_key


def test_replay_charges_kv_load_once_for_chunked_prefill() -> None:
    request = _request("r1", token_ids=list(range(12)), block_size_tokens=4)
    cache = _LookupMapCache(
        {
            "r1": PrefixLookupResult(
                hbm_hit_blocks=(),
                ddr_hit_blocks=(request.prompt_blocks[0],),
                miss_blocks=request.prompt_blocks[1:],
            )
        }
    )

    result = _engine(cache=cache, max_num_batched_tokens=4).run([request])

    assert [item.duration_ms for item in result.iteration_metrics] == [6.0, 4.0]
    assert [item.kv_load_ms for item in result.iteration_metrics] == [2.0, 0.0]
    assert ["kvload_tokens=4" in item.shape_key for item in result.iteration_metrics] == [
        True,
        False,
    ]
    assert result.request_metrics[0].ttft_ms == 10.0
    assert result.request_metrics[0].kv_load_ms == 2.0
    assert result.request_metrics[0].prefill_compute_ms == 8.0


def test_replay_keeps_hbm_only_zero_miss_immediate_finish() -> None:
    request = _request(
        "r1",
        token_ids=list(range(4)),
        block_size_tokens=4,
        block_conversion_result=_full_prompt_cache_conversion(prompt_tokens=4, block_size=4),
    )
    cache = _LookupMapCache(
        {
            "r1": PrefixLookupResult(
                hbm_hit_blocks=(request.prompt_blocks[0],),
                ddr_hit_blocks=(),
                miss_blocks=(),
            )
        }
    )

    result = _engine(cache=cache, max_num_batched_tokens=4).run([request])

    (request_metric,) = result.request_metrics
    assert request_metric.hbm_hit_tokens == 4
    assert request_metric.ddr_hit_tokens == 0
    assert request_metric.kv_load_tokens == 0
    assert request_metric.kv_load_bytes == 0
    assert request_metric.kv_load_ms == 0.0
    assert request_metric.miss_tokens == 0
    assert request_metric.ttft_ms == 0.0
    assert request_metric.scheduled_iteration_count == 0
    assert result.iteration_metrics == ()


def test_replay_runs_load_only_iteration_for_ddr_only_zero_miss() -> None:
    request = _request(
        "r1",
        token_ids=list(range(4)),
        block_size_tokens=4,
        block_conversion_result=_full_prompt_cache_conversion(prompt_tokens=4, block_size=4),
    )
    cache = _LookupMapCache(
        {
            "r1": PrefixLookupResult(
                hbm_hit_blocks=(),
                ddr_hit_blocks=(request.prompt_blocks[0],),
                miss_blocks=(),
            )
        }
    )

    result = _engine(
        cache=cache,
        max_num_batched_tokens=4,
        ttft_intercept_ms=99.0,
    ).run([request])

    (request_metric,) = result.request_metrics
    (iteration_metric,) = result.iteration_metrics
    assert request_metric.ddr_hit_tokens == 4
    assert request_metric.kv_load_tokens == 4
    assert request_metric.kv_load_bytes == 40
    assert request_metric.kv_load_ms == 2.0
    assert request_metric.prefill_compute_ms == 0.0
    assert request_metric.miss_tokens == 0
    assert request_metric.ttft_ms == 2.0
    assert request_metric.scheduled_iteration_count == 1
    assert iteration_metric.scheduled_prefill_tokens == 0
    assert iteration_metric.kv_load_ms == 2.0
    assert iteration_metric.prefill_compute_ms == 0.0
    assert iteration_metric.duration_ms == 2.0
    assert "kvload_tokens=4" in iteration_metric.shape_key


def _engine(
    *,
    cache: "_LookupMapCache",
    max_num_batched_tokens: int,
    ttft_intercept_ms: float = 0.0,
) -> BatchAwareReplayEngine:
    return BatchAwareReplayEngine(
        scheduler=VllmLikeBatchScheduler(
            SchedulerConfig(
                max_num_batched_tokens=max_num_batched_tokens,
                max_num_seqs=4,
                enable_chunked_prefill=True,
            )
        ),
        latency_backend=ServingLatencyProfile(
            profile="unit-test-serving",
            ttft_backend=FittedTTFTLatencyBackend(
                intercept_ms=ttft_intercept_ms,
                ms_per_uncached_token=1.0,
                model_name="glm-v5",
                hardware_name="local-dev",
                profile="unit-test-ttft",
            ),
            kv_load_component=TokenLinearKVLoadLatencyComponent(
                ddr_ms_per_cached_token=0.5,
            ),
        ),
        cache_factory=lambda _instance_uuid: cache,
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
