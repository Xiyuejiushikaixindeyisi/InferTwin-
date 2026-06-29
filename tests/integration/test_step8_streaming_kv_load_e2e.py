from datetime import datetime, timedelta

import pytest

from infertwin.cache.ddr_lru import DDRLRUCache
from infertwin.cache.hbm_lru import HBMCache
from infertwin.cache.tiered import TieredPrefixCache
from infertwin.instance.request import SimulationRequest
from infertwin.latency.fitted_ttft import FittedTTFTLatencyBackend
from infertwin.latency.kv_load import TokenLinearKVLoadLatencyComponent
from infertwin.latency.profile import ServingLatencyProfile
from infertwin.request.block_hasher import build_prefix_blocks
from infertwin.scheduler.config import SchedulerConfig
from infertwin.scheduler.vllm_like import VllmLikeBatchScheduler
from infertwin.streaming.metrics import InMemoryReplayMetricSink
from infertwin.streaming.replay import StreamingBatchAwareReplayEngine
from infertwin.streaming.source import ListRequestSource


def test_streaming_replay_adds_kv_load_latency_for_ddr_hits() -> None:
    zero_load_metrics = _run_streaming_replay(ddr_ms_per_cached_token=0.0)
    token_load_metrics = _run_streaming_replay(ddr_ms_per_cached_token=0.5)

    zero_repeat = zero_load_metrics["r2"]
    token_repeat = token_load_metrics["r2"]
    assert zero_repeat.ddr_hit_tokens == 6
    assert zero_repeat.miss_tokens == 2
    assert token_repeat.ddr_hit_tokens == zero_repeat.ddr_hit_tokens
    assert token_repeat.miss_tokens == zero_repeat.miss_tokens
    assert token_repeat.kv_load_tokens == 6
    assert token_repeat.kv_load_bytes == 60
    assert token_repeat.kv_load_ms == pytest.approx(3.0)
    assert zero_repeat.kv_load_ms == 0.0
    assert zero_repeat.ttft_ms == 2.0
    assert token_repeat.ttft_ms == pytest.approx(5.0)
    assert token_repeat.ttft_ms - zero_repeat.ttft_ms == pytest.approx(3.0)


def _run_streaming_replay(*, ddr_ms_per_cached_token: float):
    requests = [
        _request("r1", start_time_ms=0.0),
        _request("r2", start_time_ms=20.0),
    ]
    sink = InMemoryReplayMetricSink()
    engine = StreamingBatchAwareReplayEngine(
        scheduler=VllmLikeBatchScheduler(
            SchedulerConfig(
                max_num_batched_tokens=16,
                max_num_seqs=4,
                enable_chunked_prefill=True,
            )
        ),
        latency_backend=ServingLatencyProfile(
            profile="step8-streaming-serving",
            ttft_backend=FittedTTFTLatencyBackend(
                intercept_ms=0.0,
                ms_per_uncached_token=1.0,
                model_name="glm-v5",
                hardware_name="local-dev",
                profile="step8-streaming-ttft",
            ),
            kv_load_component=TokenLinearKVLoadLatencyComponent(
                ddr_ms_per_cached_token=ddr_ms_per_cached_token,
            ),
        ),
    )

    stats = engine.run_instance_stream(
        instance_uuid="instance-a",
        request_source=ListRequestSource(requests),
        cache=TieredPrefixCache(
            hbm=HBMCache(capacity_blocks=1),
            ddr=DDRLRUCache(capacity_blocks=32),
        ),
        metric_sink=sink,
    )

    assert stats.emitted_request_count == 2
    assert stats.final_active_requests == 0
    return {metric.request_id: metric for metric in sink.request_metrics}


def _request(request_id: str, *, start_time_ms: float) -> SimulationRequest:
    service_start_time = datetime.fromisoformat("2026-06-05 09:01:23") + timedelta(
        milliseconds=start_time_ms
    )
    token_ids = list(range(8))
    blocks = build_prefix_blocks(
        token_ids=token_ids,
        block_size_tokens=2,
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
        start_time_ms=start_time_ms,
        tokenizer_profile="glm-v5",
        prompt_tokens=len(token_ids),
        prompt_blocks=tuple(blocks),
        kv_bytes_per_token=10,
    )
