import pytest

from infertwin.cache.event_sink import CacheEventStats
from infertwin.experiment.sweep import (
    INSTANCE_SCOPE,
    TRACE_SCOPE,
    build_capacity_rows,
    build_capacity_sweep_config,
    percentile,
    sort_capacity_rows,
)
from infertwin.replay.metrics import BatchAwareRequestMetrics, IterationMetrics


def test_build_capacity_rows_aggregates_trace_and_instances() -> None:
    stats = CacheEventStats(total_events=12)
    rows = build_capacity_rows(
        capacity=8,
        request_metrics=(
            _request_metric(
                "r1",
                "instance-b",
                prompt_tokens=10,
                hbm_hit_tokens=4,
                ttft_ms=30.0,
                kv_load_ms=3.0,
            ),
            _request_metric(
                "r2",
                "instance-a",
                prompt_tokens=10,
                hbm_hit_tokens=10,
                ttft_ms=10.0,
                kv_load_ms=0.0,
            ),
            _request_metric(
                "r3",
                "instance-a",
                prompt_tokens=10,
                hbm_hit_tokens=0,
                ttft_ms=20.0,
                kv_load_ms=2.0,
            ),
        ),
        iteration_metrics=(
            _iteration_metric("instance-a", 0),
            _iteration_metric("instance-b", 0),
            _iteration_metric("instance-a", 1),
        ),
        cache_event_stats=stats,
    )

    trace_row = rows[0]
    assert trace_row.scope == TRACE_SCOPE
    assert trace_row.instance_uuid == ""
    assert trace_row.request_count == 3
    assert trace_row.iteration_count == 3
    assert trace_row.total_prompt_tokens == 30
    assert trace_row.hbm_hit_tokens == 14
    assert trace_row.ddr_hit_tokens == 0
    assert trace_row.miss_tokens == 16
    assert trace_row.total_hit_tokens == 14
    assert trace_row.kv_hit_rate == 14 / 30
    assert trace_row.hbm_hit_rate == 14 / 30
    assert trace_row.ddr_hit_rate == 0.0
    assert trace_row.p50_ttft_ms == 20.0
    assert trace_row.p90_ttft_ms == 30.0
    assert trace_row.total_kv_load_ms == 5.0
    assert trace_row.avg_kv_load_ms == pytest.approx(5.0 / 3)
    assert trace_row.p50_kv_load_ms == 2.0
    assert trace_row.p90_kv_load_ms == 3.0
    assert trace_row.p99_kv_load_ms == 3.0
    assert trace_row.cache_event_count == 12

    instance_rows = {row.instance_uuid: row for row in rows[1:]}
    assert list(instance_rows) == ["instance-a", "instance-b"]
    assert instance_rows["instance-a"].scope == INSTANCE_SCOPE
    assert instance_rows["instance-a"].request_count == 2
    assert instance_rows["instance-a"].iteration_count == 2
    assert instance_rows["instance-a"].total_kv_load_ms == 2.0
    assert instance_rows["instance-a"].p90_kv_load_ms == 2.0
    assert instance_rows["instance-a"].cache_event_count == 0
    assert instance_rows["instance-b"].request_count == 1
    assert instance_rows["instance-b"].iteration_count == 1
    assert instance_rows["instance-b"].total_kv_load_ms == 3.0
    assert instance_rows["instance-b"].cache_event_count == 0


def test_sort_capacity_rows_is_deterministic() -> None:
    rows = (
        _row(capacity=16, scope=INSTANCE_SCOPE, instance_uuid="b"),
        _row(capacity=8, scope=INSTANCE_SCOPE, instance_uuid="b"),
        _row(capacity=8, scope=TRACE_SCOPE, instance_uuid=""),
        _row(capacity=8, scope=INSTANCE_SCOPE, instance_uuid="a"),
    )

    sorted_rows = sort_capacity_rows(rows)

    assert [(row.hbm_capacity_blocks, row.scope, row.instance_uuid) for row in sorted_rows] == [
        (8, TRACE_SCOPE, ""),
        (8, INSTANCE_SCOPE, "a"),
        (8, INSTANCE_SCOPE, "b"),
        (16, INSTANCE_SCOPE, "b"),
    ]


def test_percentile_uses_nearest_rank() -> None:
    assert percentile([30.0, 10.0, 20.0], 50) == 20.0
    assert percentile([30.0, 10.0, 20.0], 90) == 30.0
    assert percentile([], 90) == 0.0


def test_capacity_sweep_config_rejects_targets_and_duplicates() -> None:
    with pytest.raises(ValueError, match="does not support targets"):
        build_capacity_sweep_config(
            {
                "simulation": {"mode": "capacity_sweep"},
                "sweep": {"hbm_capacity_blocks": [8]},
                "targets": {"p90_ttft_ms": [100]},
            }
        )

    with pytest.raises(ValueError, match="must not contain duplicate"):
        build_capacity_sweep_config(
            {
                "simulation": {"mode": "capacity_sweep"},
                "sweep": {"hbm_capacity_blocks": [8, 8]},
            }
        )


def test_capacity_sweep_config_validates_cache_event_capacities() -> None:
    config = build_capacity_sweep_config(
        {
            "simulation": {"mode": "capacity_sweep"},
            "sweep": {"hbm_capacity_blocks": [8, 16]},
            "output": {"cache_events": True, "cache_event_capacities": [16]},
        }
    )
    assert config.capacities == (8, 16)
    assert config.cache_events is True
    assert config.cache_event_capacities == (16,)

    with pytest.raises(ValueError, match="non-empty"):
        build_capacity_sweep_config(
            {
                "simulation": {"mode": "capacity_sweep"},
                "sweep": {"hbm_capacity_blocks": [8]},
                "output": {"cache_events": True},
            }
        )

    with pytest.raises(ValueError, match="subset"):
        build_capacity_sweep_config(
            {
                "simulation": {"mode": "capacity_sweep"},
                "sweep": {"hbm_capacity_blocks": [8]},
                "output": {"cache_events": True, "cache_event_capacities": [16]},
            }
        )


def test_capacity_sweep_config_rejects_parallel_instances() -> None:
    with pytest.raises(ValueError, match="parallel_instances is reserved"):
        build_capacity_sweep_config(
            {
                "simulation": {"mode": "capacity_sweep"},
                "sweep": {"hbm_capacity_blocks": [8], "parallel_instances": True},
            }
        )


def _request_metric(
    request_id: str,
    instance_uuid: str,
    *,
    prompt_tokens: int,
    hbm_hit_tokens: int,
    ttft_ms: float,
    kv_load_ms: float = 0.0,
) -> BatchAwareRequestMetrics:
    return BatchAwareRequestMetrics(
        request_id=request_id,
        tenant_id="tenant-a",
        instance_uuid=instance_uuid,
        model="glm-v5",
        tokenizer_profile="glm-v5",
        arrival_time_ms=0.0,
        first_scheduled_time_ms=0.0,
        finish_time_ms=ttft_ms,
        scheduler_wait_ms=0.0,
        ttft_ms=ttft_ms,
        prompt_tokens=prompt_tokens,
        prompt_blocks=1,
        hbm_hit_tokens=hbm_hit_tokens,
        ddr_hit_tokens=0,
        miss_tokens=prompt_tokens - hbm_hit_tokens,
        effective_hit_rate=hbm_hit_tokens / prompt_tokens,
        scheduled_iteration_count=1,
        kv_load_ms=kv_load_ms,
    )


def _iteration_metric(instance_uuid: str, iteration_id: int) -> IterationMetrics:
    return IterationMetrics(
        instance_uuid=instance_uuid,
        iteration_id=iteration_id,
        start_time_ms=float(iteration_id),
        finish_time_ms=float(iteration_id + 1),
        duration_ms=1.0,
        batch_size=1,
        scheduled_prefill_tokens=1,
        scheduled_decode_tokens=0,
        max_query_len=1,
        total_context_tokens=1,
        backend="fitted_ttft",
        shape_key="shape",
        memoized=False,
        request_ids=(f"r{iteration_id}",),
    )


def _row(capacity: int, scope: str, instance_uuid: str):
    return build_capacity_rows(
        capacity=capacity,
        request_metrics=(
            _request_metric(
                f"{capacity}-{scope}-{instance_uuid or 'trace'}",
                instance_uuid or "instance-a",
                prompt_tokens=1,
                hbm_hit_tokens=0,
                ttft_ms=1.0,
            ),
        ),
        iteration_metrics=(),
        cache_event_stats=CacheEventStats(),
    )[0 if scope == TRACE_SCOPE else 1]
