from datetime import datetime, timedelta

from hitfloor.instance.replay import InfiniteHBMReplayEngine
from hitfloor.instance.request import build_simulation_requests
from hitfloor.request.tokenizer_registry import TokenizerRegistry
from hitfloor.trace.schema import TraceRecord


def test_infinite_hbm_hits_only_within_same_instance() -> None:
    base = datetime.fromisoformat("2026-06-05 09:01:23")
    records = [
        _record("r1", "instance-a", base),
        _record("r2", "instance-a", base + timedelta(seconds=1)),
        _record("r3", "instance-b", base + timedelta(seconds=2)),
    ]
    registry = TokenizerRegistry.from_root("tokenizers", default_profile="glm-v5")
    requests = build_simulation_requests(records, registry, block_size_tokens=4)

    metrics = InfiniteHBMReplayEngine().run(requests)

    assert metrics[0].miss_tokens == metrics[0].prompt_tokens
    assert metrics[1].hbm_hit_tokens == metrics[1].prompt_tokens
    assert metrics[1].effective_hit_rate == 1.0
    assert metrics[2].miss_tokens == metrics[2].prompt_tokens


def test_materialization_is_not_visible_before_finish_time() -> None:
    base = datetime.fromisoformat("2026-06-05 09:01:23")
    records = [
        _record("r1", "instance-a", base),
        _record("r2", "instance-a", base + timedelta(milliseconds=50)),
        _record("r3", "instance-a", base + timedelta(milliseconds=150)),
    ]
    registry = TokenizerRegistry.from_root("tokenizers", default_profile="glm-v5")
    requests = build_simulation_requests(records, registry, block_size_tokens=4)

    metrics = InfiniteHBMReplayEngine(default_ttft_ms=100.0).run(requests)

    assert metrics[0].miss_tokens == metrics[0].prompt_tokens
    assert metrics[1].miss_tokens == metrics[1].prompt_tokens
    assert metrics[2].hbm_hit_tokens == metrics[2].prompt_tokens


def _record(request_id: str, instance_uuid: str, timestamp: datetime) -> TraceRecord:
    return TraceRecord(
        request_id=request_id,
        tenant_id="tenant-a",
        instance_uuid=instance_uuid,
        request_params=(
            '{"model":"glm-v5","messages":[{"role":"user","content":"same prompt"}],"tools":[]}'
        ),
        service_start_time=timestamp,
    )
