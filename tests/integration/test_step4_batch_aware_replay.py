from datetime import datetime, timedelta

from hitfloor.instance.request import SimulationRequest
from hitfloor.latency.formula import FormulaLatencyBackend
from hitfloor.replay.event_loop import BatchAwareReplayEngine
from hitfloor.request.block_hasher import build_prefix_blocks
from hitfloor.scheduler.config import SchedulerConfig
from hitfloor.scheduler.vllm_like import VllmLikeBatchScheduler


def test_step4_batch_aware_replay_outputs_request_and_iteration_metrics() -> None:
    engine = BatchAwareReplayEngine(
        scheduler=VllmLikeBatchScheduler(SchedulerConfig(max_num_batched_tokens=8, max_num_seqs=4)),
        latency_backend=FormulaLatencyBackend(
            iteration_fixed_overhead_ms=0.0,
            iteration_prefill_token_ms=0.5,
            iteration_batch_overhead_ms=0.0,
            iteration_context_token_ms=0.0,
            model_name="glm-v5",
            hardware_name="local-dev",
        ),
    )
    requests = [
        _request("r1", "instance-a", 0.0, [1, 2, 3, 4]),
        _request("r2", "instance-a", 0.0, [5, 6, 7, 8]),
        _request("r3", "instance-a", 10.0, [1, 2, 3, 4]),
        _request("r4", "instance-b", 10.0, [1, 2, 3, 4]),
    ]

    result = engine.run(requests)

    assert len(result.request_metrics) == 4
    assert len(result.iteration_metrics) == 2
    assert sum(item.scheduled_prefill_tokens for item in result.iteration_metrics) == sum(
        item.miss_tokens for item in result.request_metrics
    )

    metrics_by_id = {item.request_id: item for item in result.request_metrics}
    assert metrics_by_id["r3"].hbm_hit_tokens == 4
    assert metrics_by_id["r3"].miss_tokens == 0
    assert metrics_by_id["r4"].hbm_hit_tokens == 0
    assert metrics_by_id["r4"].miss_tokens == 4


def _request(
    request_id: str,
    instance_uuid: str,
    start_time_ms: float,
    token_ids: list[int],
) -> SimulationRequest:
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
